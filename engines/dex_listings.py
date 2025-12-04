import logging
from datetime import datetime
from typing import List

import httpx

from engines.listings_common import Listing

logger = logging.getLogger(__name__)


class DexListingsCollector:
    """Collect listings from DEX aggregators (e.g. GeckoTerminal)."""

    name = "DEX Aggregator"
    exchange_type = "dex"

    def __init__(self, client: httpx.Client | None = None, networks: list[str] | None = None):
        self.client = client or httpx.Client(base_url="https://api.geckoterminal.com")
        self.networks = networks or ["eth", "bsc", "base"]

    def fetch(self, limit: int = 5) -> List[Listing]:
        listings: List[Listing] = []
        for network in self.networks:
            try:
                resp = self.client.get(
                    f"/api/v2/networks/{network}/trending_pools",
                    params={"page": 1},
                    timeout=10.0,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # pragma: no cover - logged at service level
                logger.warning("DEX listing poll failed for %s: %s", network, exc)
                continue

            pools = payload.get("data", []) if isinstance(payload, dict) else []
            for pool in pools[:limit]:
                attrs = pool.get("attributes", {})
                symbol = attrs.get("base_token_symbol", "")
                name = attrs.get("base_token_name", symbol)
                url = attrs.get("pool_url") or attrs.get("explorer_base_url") or ""
                listed_at_str = attrs.get("pool_created_at") or attrs.get("last_trade_at")
                try:
                    listed_at = (
                        datetime.fromisoformat(listed_at_str.replace("Z", "+00:00"))
                        if listed_at_str
                        else datetime.utcnow()
                    )
                except Exception:
                    listed_at = datetime.utcnow()

                listings.append(
                    Listing(
                        symbol=symbol,
                        name=name,
                        pair=attrs.get("name", ""),
                        network=network,
                        listed_at=listed_at,
                        source=f"GeckoTerminal-{network}",
                        url=url,
                        exchange_type=self.exchange_type,
                    )
                )
        return listings
