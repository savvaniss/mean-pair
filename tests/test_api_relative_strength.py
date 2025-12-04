import datetime as dt

import pytest

import config
from engines import relative_strength as rs_engine
from routes import relative_strength as rs_routes


class FakeRSClient:
    def __init__(self):
        self.tickers = {
            "BTCUSDT": "100.0",
            "ETHUSDT": "10.0",
            "ADAUSDT": "1.0",
        }

    def get_all_tickers(self):
        return [{"symbol": k, "price": v} for k, v in self.tickers.items()]

    def get_symbol_info(self, symbol):
        return {
            "symbol": symbol,
            "baseAsset": symbol[:-4],
            "quoteAsset": "USDT",
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            ],
        }

    def get_account(self):
        return {"balances": [{"asset": "USDT", "free": "200.0", "locked": "0"}]}

    def create_order(self, **kwargs):
        return {"cummulativeQuoteQty": "50.0", "executedQty": "5.0"}


def test_rs_config_resets_history_and_env(monkeypatch, client):
    fake = FakeRSClient()
    monkeypatch.setattr(config, "boll_client", fake, raising=False)

    rs_engine.rs_price_history["BTCUSDT"] = [1.0, 1.1]

    payload = {
        "enabled": False,
        "poll_interval_sec": 15,
        "lookback_window": 20,
        "rebalance_interval_sec": 120,
        "top_n": 2,
        "bottom_n": 2,
        "min_rs_gap": 0.3,
        "max_notional_usd": 75.0,
        "use_all_balance": True,
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "use_testnet": False,
    }

    called = {}

    def fake_switch(flag):
        called["val"] = flag
        config.BOLL_USE_TESTNET = flag

    monkeypatch.setattr(config, "switch_boll_env", fake_switch, raising=False)

    resp = client.post("/rs_config", json=payload)
    assert resp.status_code == 200
    assert rs_engine.rs_price_history == {}
    assert called["val"] is False


def test_rs_status_with_rankings(monkeypatch, client):
    fake = FakeRSClient()
    monkeypatch.setattr(config, "boll_client", fake, raising=False)

    rs_engine.rs_config.symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
    with rs_engine.rs_lock:
        rs_engine.rs_price_history["BTCUSDT"] = [90.0, 100.0, 105.0]
        rs_engine.rs_price_history["ETHUSDT"] = [8.0, 9.0, 10.0]
        rs_engine.rs_price_history["ADAUSDT"] = [1.0, 0.99, 0.98]

    resp = client.get("/rs_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["quote_asset"] == "USDT"
    assert data["quote_balance"] == pytest.approx(200.0)
    assert len(data["top_symbols"]) >= 1
    assert len(data["bottom_symbols"]) >= 1


def test_rs_history_endpoint(monkeypatch, client):
    fake = FakeRSClient()
    monkeypatch.setattr(config, "boll_client", fake, raising=False)
    ts = dt.datetime.utcnow()
    session = rs_routes.SessionLocal()
    try:
        session.query(rs_routes.RSSnapshot).delete()
        session.add(
            rs_routes.RSSnapshot(
                ts=ts,
                symbol="BTCUSDT",
                price=100.0,
                rs=1.2,
            )
        )
        session.commit()
    finally:
        session.close()

    resp = client.get("/rs_history")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert rows[-1]["symbol"] == "BTCUSDT"
