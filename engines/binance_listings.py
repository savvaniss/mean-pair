import logging
import re
from datetime import datetime
from typing import List

import httpx

from engines.listings_common import Listing

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ListingsBot/1.0)",
    "Accept-Language": "en",
}


class BinanceListingsCollector:
    """Collect listings from Binance announcements API."""

    name = "Binance"
    exchange_type = "cex"

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            base_url="https://www.binance.com",
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        )

    def fetch(self, limit: int = 15) -> List[Listing]:
        """Return the most recent listing announcements."""
        url = "/bapi/composite/v1/public/cms/article/catalog/list/query"
        params = {"catalogId": 48, "pageSize": limit, "pageNo": 1}
        resp = self.client.get(url, params=params, timeout=10.0)

        if resp.status_code == 403:
            raise RuntimeError(
                "Binance listings endpoint returned 403; add a browser-like User-Agent or VPN to bypass edge blocking."
            )

        if resp.status_code == 404:
            raise RuntimeError(
                "Binance listings endpoint returned 404; the public CMS API path likely changed."
            )

        resp.raise_for_status()
        payload = resp.json()
        articles = payload.get("data", {}).get("articles", []) if isinstance(payload, dict) else []

        listings: List[Listing] = []
        for article in articles:
            title = article.get("title", "")
            symbol = self._extract_symbol(title)
            name = title.replace("Binance Will List ", "").strip()
            created_ms = article.get("releaseDate", 0)
            listed_at = datetime.utcfromtimestamp(int(created_ms) / 1000) if created_ms else datetime.utcnow()
            url_suffix = article.get("code") or article.get("id") or ""
            link = f"https://www.binance.com/en/support/announcement/{url_suffix}" if url_suffix else "https://www.binance.com/en/support/announcement"

            listings.append(
                Listing(
                    symbol=symbol or name,
                    name=name or symbol,
                    pair=symbol + "USDT" if symbol else "",
                    network=None,
                    listed_at=listed_at,
                    source=self.name,
                    url=link,
                    exchange_type=self.exchange_type,
                )
            )
        return listings

    @staticmethod
    def _extract_symbol(title: str) -> str:
        upper = title.upper()
        # Prefer explicit ticker in parentheses, e.g. "Binance Will List IO.NET (IO)"
        match = re.search(r"\(([^)]+)\)", upper)
        if match:
            return match.group(1).replace("/USDT", "").strip()

        parts = upper.replace("BINANCE WILL LIST", "").replace("BINANCE WILL ADD", "").split()
        return parts[0].replace("/USDT", "") if parts else ""
