import logging
from datetime import datetime
from typing import List

import httpx

from engines.listings_common import Listing

logger = logging.getLogger(__name__)


class CexListingsCollector:
    """Collects listings from a set of CEX endpoints (KuCoin/OKX/etc.)."""

    name = "CEX Aggregator"
    exchange_type = "cex"

    def __init__(self, endpoints: dict[str, str] | None = None, client: httpx.Client | None = None):
        self.client = client or httpx.Client()
        # endpoint name -> url
        self.endpoints = endpoints or {
            "KuCoin": "https://api.kucoin.com/api/v1/market/allTickers",
            "OKX": "https://www.okx.com/api/v5/public/instruments?instType=SPOT",
        }

    def fetch(self, limit_per_exchange: int = 10) -> List[Listing]:
        listings: List[Listing] = []
        now = datetime.utcnow()
        for exchange, url in self.endpoints.items():
            try:
                resp = self.client.get(url, timeout=10.0)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # pragma: no cover - logged at service level
                logger.warning("%s listing poll failed: %s", exchange, exc)
                continue

            parsed = self._parse_payload(exchange, payload, limit_per_exchange)
            for item in parsed:
                listings.append(
                    Listing(
                        symbol=item.get("symbol", ""),
                        name=item.get("name") or item.get("symbol", ""),
                        pair=item.get("pair", ""),
                        network=item.get("network"),
                        listed_at=item.get("listed_at", now),
                        source=exchange,
                        url=item.get("url") or url,
                        exchange_type=self.exchange_type,
                    )
                )
        return listings

    def _parse_payload(self, exchange: str, payload: dict, limit: int) -> List[dict]:
        if exchange == "KuCoin":
            data = payload.get("data", {}).get("ticker", [])
            latest = sorted(data, key=lambda d: float(d.get("changeRate", 0) or 0), reverse=True)[:limit]
            return [
                {
                    "symbol": item.get("symbol", ""),
                    "pair": item.get("symbol", ""),
                    "listed_at": datetime.utcnow(),
                    "url": f"https://www.kucoin.com/trade/{item.get('symbol', '').replace('-', '')}",
                }
                for item in latest
            ]
        if exchange == "OKX":
            data = payload.get("data", [])
            latest = data[:limit]
            return [
                {
                    "symbol": item.get("instId", ""),
                    "pair": item.get("instId", ""),
                    "listed_at": datetime.utcnow(),
                    "network": item.get("settleCcy"),
                    "url": "https://www.okx.com/markets/prices",
                }
                for item in latest
            ]
        return []
