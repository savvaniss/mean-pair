import datetime as dt

import pytest

import config
from engines import trend_following as tf_engine
from routes import trend_following as tf_routes


class FakeTrendClient:
    def __init__(self):
        self._exchange_info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                    ],
                }
            ]
        }

    def get_symbol_info(self, symbol):
        for s in self._exchange_info["symbols"]:
            if s["symbol"] == symbol:
                return s
        return None

    def get_symbol_ticker(self, symbol):
        return {"symbol": symbol, "price": "10.0"}

    def get_account(self):
        return {"balances": [{"asset": "USDT", "free": "200.0", "locked": "0"}]}

    def create_order(self, **kwargs):
        return {"cummulativeQuoteQty": "100.0", "executedQty": "10.0"}


def test_trend_config_resets_history_and_env(client, monkeypatch):
    fake_client = FakeTrendClient()
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    tf_engine.tf_price_history.extend([1.0, 2.0])
    tf_engine.tf_ts_history.extend([dt.datetime.utcnow()] * 2)
    tf_engine.current_trend_symbol = "OLD"

    payload = {
        "enabled": False,
        "symbol": "BTCUSDT",
        "poll_interval_sec": 10,
        "fast_window": 5,
        "slow_window": 15,
        "atr_window": 10,
        "atr_stop_mult": 1.5,
        "max_position_usd": 100.0,
        "use_all_balance": True,
        "cooldown_sec": 30,
        "use_testnet": False,
    }

    called = {}

    def fake_switch(flag):
        called["val"] = flag
        config.USE_TESTNET = flag

    monkeypatch.setattr(config, "switch_env", fake_switch, raising=False)

    resp = client.post("/trend_config", json=payload)
    assert resp.status_code == 200
    assert called["val"] is False
    assert tf_engine.tf_price_history == []
    assert tf_engine.current_trend_symbol == "BTCUSDT"


def test_trend_status_empty_symbol(client):
    tf_engine.trend_config.symbol = ""
    resp = client.get("/trend_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == ""
    assert data["position"] == "FLAT"


def test_trend_status_with_history(client, monkeypatch):
    fake_client = FakeTrendClient()
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    tf_engine.trend_config.symbol = "BTCUSDT"
    tf_engine.trend_config.fast_window = 2
    tf_engine.trend_config.slow_window = 3
    tf_engine.tf_price_history[:] = [10.0, 11.0, 12.0]

    resp = client.get("/trend_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["quote_asset"] == "USDT"
    assert data["fast_ema"] > 0
    assert data["slow_ema"] > 0


def test_trend_history_returns_snapshots(client, monkeypatch):
    fake_client = FakeTrendClient()
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    tf_engine.trend_config.symbol = "BTCUSDT"
    session = tf_routes.SessionLocal()
    try:
        ts = dt.datetime.utcnow()
        session.add(
            tf_routes.TrendSnapshot(
                ts=ts,
                symbol="BTCUSDT",
                price=10.0,
                fast_ema=10.0,
                slow_ema=9.5,
                atr=0.5,
            )
        )
        session.commit()
    finally:
        session.close()

    resp = client.get("/trend_history")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert rows[-1]["price"] == pytest.approx(10.0)
