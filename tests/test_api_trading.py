import pytest
import config
from database import SessionLocal, Trade


class StubClient:
    def __init__(self, name: str, env: bool):
        self.name = name
        self.env = env
        self.orders = []

    def get_account(self):
        return {
            "balances": [
                {"asset": f"{self.name.upper()}_ASSET", "free": "5.0", "locked": "1.0"},
                {"asset": "USDC", "free": "12.5", "locked": "0.0"},
            ]
        }

    def get_symbol_info(self, symbol):
        return {
            "symbol": symbol,
            "baseAsset": symbol[:-4],
            "quoteAsset": symbol[-4:],
            "filters": [
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.1",
                    "stepSize": "0.1",
                    "maxQty": "1000",
                }
            ],
        }

    def get_symbol_ticker(self, symbol):
        return {"symbol": symbol, "price": "10.0"}

    def order_market(self, symbol, side, quantity):
        self.orders.append({"symbol": symbol, "side": side, "quantity": quantity})
        return {"status": "FILLED", "executedQty": str(quantity)}


def test_trading_balances_lists_accounts(monkeypatch, client):
    mr_client = StubClient("mr", env=True)
    boll_client = StubClient("boll", env=True)

    monkeypatch.setattr(config, "create_mr_client", lambda use_testnet: mr_client)
    monkeypatch.setattr(config, "create_boll_client", lambda use_testnet: boll_client)

    resp = client.get("/trading/balances?use_testnet=true")
    assert resp.status_code == 200
    payload = resp.json()

    assert len(payload) == 2
    accounts = {entry["account"]: entry for entry in payload}

    assert accounts["mr"]["use_testnet"] is True
    assert accounts["boll"]["use_testnet"] is True
    assert accounts["mr"]["balances"][0]["asset"] == "MR_ASSET"
    assert any(b["asset"] == "USDC" for b in accounts["boll"]["balances"])


def test_trading_order_adjusts_qty_and_logs(monkeypatch, client):
    test_client = StubClient("mr", env=False)
    monkeypatch.setattr(config, "create_mr_client", lambda use_testnet: test_client)
    monkeypatch.setattr(config, "create_boll_client", lambda use_testnet: test_client)

    # Clean trades table before test
    session = SessionLocal()
    try:
        session.query(Trade).delete()
        session.commit()
    finally:
        session.close()

    body = {
        "account": "mr",
        "use_testnet": False,
        "symbol": "HBARUSDT",
        "side": "BUY",
        "qty_base": 1.23,
    }

    resp = client.post("/trading/order", json=body)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "ok"
    assert data["qty_executed"] == pytest.approx(1.2)  # rounded to LOT_SIZE step
    assert data["price_used"] == pytest.approx(10.0)
    assert data["notional"] == pytest.approx(12.0)
    assert data["quote_asset"] == "USDT"

    # Trade should have been persisted
    session = SessionLocal()
    try:
        trades = session.query(Trade).all()
        assert len(trades) == 1
        assert trades[0].qty_from == pytest.approx(1.2)
        assert trades[0].is_testnet == 0
    finally:
        session.query(Trade).delete()
        session.commit()
        session.close()


def test_trading_balances_survives_missing_account(monkeypatch, client):
    boll_client = StubClient("boll", env=False)

    def boom(_use_testnet: bool):
        raise RuntimeError("no MR keys configured")

    monkeypatch.setattr(config, "create_mr_client", boom)
    monkeypatch.setattr(config, "create_boll_client", lambda env: boll_client)

    resp = client.get("/trading/balances?use_testnet=false")
    assert resp.status_code == 200

    payload = resp.json()
    accounts = {entry["account"]: entry for entry in payload}

    assert accounts["mr"]["balances"] == []
    assert accounts["mr"].get("error")

    boll = accounts["boll"]
    assert boll["error"] is None
    assert len(boll["balances"]) == 2
