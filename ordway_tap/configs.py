from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from singer.catalog import Catalog

api_credentials: Dict[str, str] = {}
kafka_credentials: Dict[str, str] = {}
catalog: Optional["Catalog"] = None
api_version: Optional[str] = None
staging = False
api_url: Optional[str] = None
start_date: str
