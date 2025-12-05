from datetime import datetime, timedelta

from database import ListingEvent, SessionLocal
from engines.binance_listings import BinanceListingsCollector
from engines import listing_scout
from engines.listings_common import Listing
from engines import listings_service


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.last_url = None
        self.last_params = None

    def get(self, *args, **kwargs):
        self.last_url = args[0] if args else None
        self.last_params = kwargs.get("params")
        return FakeResponse(self.payload)

    def close(self):
        return None


def cleanup_source(source: str):
    session = SessionLocal()
    try:
        session.query(ListingEvent).filter(ListingEvent.source == source).delete()
        session.commit()
    finally:
        session.close()


def test_binance_collector_parses_listing():
    payload = {
        "data": {
            "articles": [
                {"title": "Binance Will List TEST", "releaseDate": 1700000000000, "code": "abc-123"}
            ]
        }
    }
    collector = BinanceListingsCollector(client=FakeClient(payload))
    listings = collector.fetch()

    assert len(listings) == 1
    item = listings[0]
    assert item.symbol == "TEST"
    assert item.source == "Binance"
    assert item.url.endswith("abc-123")


def test_binance_collector_uses_listing_catalog():
    payload = {"data": {"articles": []}}
    client = FakeClient(payload)
    collector = BinanceListingsCollector(client=client)

    collector.fetch(limit=5)

    assert client.last_url == "/bapi/composite/v1/public/cms/article/catalog/list/query"
    assert client.last_params == {"catalogId": 48, "pageSize": 5, "pageNo": 1}


def test_binance_collector_extracts_symbol_from_parentheses():
    title = "Binance Will List Renzo Restaked ETH (EZETH)"
    assert BinanceListingsCollector._extract_symbol(title) == "EZETH"


def test_listing_scout_prefers_preferred_quote():
    calls = []

    class FakeClient:
        def get_symbol_info(self, symbol):
            calls.append(symbol)
            if symbol.endswith("USDC"):
                return {"filters": []}
            if symbol.endswith("USDT"):
                return None
            return None

    symbol, info = listing_scout._resolve_symbol(
        FakeClient(), base="NEW", fallback_pair="NEWUSDT", preferred_quote="USDC"
    )

    assert symbol == "NEWUSDC"
    assert info == {"filters": []}
    assert calls[0] == "NEWUSDC"


def test_run_collector_persists_and_filters():
    class DummyCollector:
        name = "Dummy"
        exchange_type = "cex"

        def fetch(self):
            return [
                Listing(
                    symbol="NEW",
                    name="New Token",
                    pair="NEWUSDT",
                    network="ETH",
                    listed_at=datetime.utcnow(),
                    source=self.name,
                    url="https://dummy.exchange/NEW",
                    exchange_type=self.exchange_type,
                )
            ]

    collector = DummyCollector()
    listings_service.run_collector(collector)
    events = listings_service.get_recent_listings(exchange="Dummy", minutes=1440)

    try:
        assert any(e.symbol == "NEW" for e in events)
    finally:
        cleanup_source("Dummy")


def test_listings_api_returns_data(client):
    session = SessionLocal()
    event = ListingEvent(
        symbol="API",
        name="API Coin",
        pair="APIUSDT",
        network="Base",
        listed_at=datetime.utcnow() - timedelta(minutes=10),
        fetched_at=datetime.utcnow(),
        exchange_type="dex",
        source="API Source",
        url="https://example.com/api",
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    session.close()

    try:
        resp = client.get("/api/listings/latest", params={"exchange": "API Source", "minutes": 60})
        assert resp.status_code == 200
        data = resp.json()
        assert any(item["symbol"] == "API" for item in data)

        page_resp = client.get("/listings")
        assert page_resp.status_code == 200
    finally:
        cleanup_source("API Source")


def test_listing_scout_config_roundtrip(client):
    original = listing_scout.get_config()
    try:
        resp = client.get("/api/listings/binance/scout/config")
        assert resp.status_code == 200
        payload = resp.json()
        assert "target_notional_eur" in payload
        assert "pump_profit_pct" in payload

        update = {"target_notional_eur": 25.5, "pump_profit_pct": 0.12}
        save_resp = client.post("/api/listings/binance/scout/config", json=update)
        assert save_resp.status_code == 200
        saved = save_resp.json()
        assert saved["target_notional_eur"] == update["target_notional_eur"]
        assert saved["pump_profit_pct"] == update["pump_profit_pct"]

        status = client.get("/api/listings/binance/scout/status").json()
        assert status["target_notional_eur"] == update["target_notional_eur"]
        assert status["pump_profit_pct"] == update["pump_profit_pct"]
    finally:
        listing_scout.update_config(
            original["target_notional_eur"], original["pump_profit_pct"]
        )
