from typing import TYPE_CHECKING, Dict, Any, Optional, Generator, Union, List
from singer import get_logger
from singer.metrics import http_request_timer
from singer.utils import strftime, ratelimit
from backoff import expo, on_exception as backoff_on_exception
import requests
import ordway_tap.configs as TAP_CONFIG
from ..__version__ import __version__ as VERSION
from .consts import (
    BASE_API_URL,
    BASE_STAGING_URL,
    DEFAULT_API_VERSION,
    DEFAULT_TIMEOUT_SECS,
)

LOGGER = get_logger()

if TYPE_CHECKING:
    from ..base import DataContext
    from typing_extensions import TypedDict

    _DEFAULT_QUERY_PARAMS = TypedDict(
        "_DEFAULT_QUERY_PARAMS",
        {"page": int, "sort": Optional[str], "size": int},
        total=False,
    )

@ratelimit(limit=1, every=0.5)
@backoff_on_exception(expo, requests.exceptions.RequestException, max_tries=3)
def _get(
    path: str, params: Dict[str, str]
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """ Perform a GET request with Ordway-related headers """

    response = requests.get(
        _get_url(path),
        headers=_get_headers(),
        params=params,
        timeout=DEFAULT_TIMEOUT_SECS,
    )

    if response.status_code != 200:
        LOGGER.critical(
            'Ordway responded with status code "%d" and a body of "%s" for request "%s"',
            response.status_code,
            response.text,
            response.request.url,
        )

        response.raise_for_status()

    return response.json()


def _get_headers() -> Dict[str, str]:
    """ Constructs Ordway-related headers """

    headers = {
        "X-User-Company": TAP_CONFIG.api_credentials["x_company"],
        "X-User-Token": TAP_CONFIG.api_credentials["x_user_token"],
        "X-User-Email": TAP_CONFIG.api_credentials["x_user_email"],
        "X-API-KEY": TAP_CONFIG.api_credentials["x_api_key"],
        "User-Agent": f"ordway-tap v{VERSION} (https://github.com/ordwaylabs/ordway-tap)",
        "Accept": "application/json",
    }

    if "x_company_token" in TAP_CONFIG.api_credentials:
        headers["X-Company-Token"] = TAP_CONFIG.api_credentials["x_company_token"]

    return headers


def _get_api_version() -> str:
    """ Gets Ordway API version - formatting if necessary """

    if TAP_CONFIG.api_version is None:
        return DEFAULT_API_VERSION

    return (
        TAP_CONFIG.api_version
        if TAP_CONFIG.api_version.lower().startswith("v")
        else f"v{TAP_CONFIG.api_version}"
    )


def _get_url(path: str) -> str:
    """ Constructs Ordway API URL """

    if TAP_CONFIG.api_url is not None:
        base_url = TAP_CONFIG.api_url

        if not base_url.endswith("/"):
            base_url = f"{base_url}/"
    else:
        base_url = BASE_STAGING_URL if TAP_CONFIG.staging else BASE_API_URL
        base_url = f"{base_url}/{_get_api_version()}/"

    if path.startswith("/"):
        path = path[1:]

    return f"{base_url}{path}"


class RequestHandler:
    """ Handles requests to Ordway """

    def __init__(
        self,
        endpoint_template: str,
        page_size: int = 50,
        sort: Optional[str] = None,
    ):
        self.endpoint_template = endpoint_template
        self.page_size = page_size
        self.sort = sort

        self._exhausted = False

    def resolve_endpoint(self, context: "DataContext") -> str:
        if context.parent_record is None:
            return self.endpoint_template

        try:
            return self.endpoint_template.format(**context.parent_record)
        except KeyError as err:
            raise err

    def resolve_params(self, context: "DataContext") -> Dict[str, Optional[str]]:
        """ Returns query params to send with the Ordway synchronization request """

        params = {}

        if context.stream.is_valid_incremental:
            if self.sort is None:
                params["sort"] = context.stream.replication_key

            params[f"{context.stream.replication_key}>"] = strftime(
                context.filter_datetime
            )

            return params

        return params

    def fetch(self, context: "DataContext") -> Generator[Dict[str, Any], None, None]:
        """ Fetches all pages constrained by `resolve_params` """

        self._exhausted = False

        default_params: "_DEFAULT_QUERY_PARAMS" = {
            "sort": self.sort,
            "size": self.page_size,
            "page": 1,
        }
        default_params.update(self.resolve_params(context))  # type: ignore

        endpoint = self.resolve_endpoint(context)

        while not self._exhausted:
            with http_request_timer(endpoint=endpoint):
                results = _get(endpoint, default_params)

            if isinstance(results, dict):
                results = [results]

            for result in results:
                yield result

            if len(results) == 0:
                self._exhausted = True
            else:
                default_params["page"] += 1
