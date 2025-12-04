from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Listing:
    symbol: str
    name: str
    pair: str
    network: Optional[str]
    listed_at: datetime
    source: str
    url: str
    exchange_type: str  # "cex" or "dex"
