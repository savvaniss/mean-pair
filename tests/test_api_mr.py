# tests/test_api_mr.py
import math
import types
import pytest

import config
from engines import mean_reversion as mr
from routes import mean_reversion as mr_routes


def test_get_config_roundtrip(client):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()

    assert "poll_interval_sec" in data
    assert data["window_size"] == mr.bot_config.window_size

    payload = data.copy()
    payload["window_size"] = data["window_size"] + 5
    payload["z_entry"] = 2.0

    r2 = client.post("/config", json=payload)
    assert r2.status_code == 200

    updated = r2.json()
    assert updated["window_size"] == payload["window_size"]
    assert mr.bot_config.window_size == payload["window_size"]
    assert mr.bot_config.z_entry == pytest.approx(2.0)


def test_status_basic(client, monkeypatch):
    mr.ratio_history.clear()

    def fake_prices():
        # btc, hbar, doge
        return 30000.0, 0.12, 0.24

    balances = {"USDC": 100.0, "HBAR": 10.0, "DOGE": 0.0}

    def fake_balance(asset: str):
        return balances.get(asset, 0.0)

    monkeypatch.setattr(mr, "get_prices", fake_prices)
    monkeypatch.setattr(mr, "get_free_balance_mr", fake_balance)

    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()

    # ratio = hbar/doge = 0.5
    assert math.isclose(data["ratio"], 0.5)
    assert "zscore" in data
    assert "unrealized_pnl_usd" in data
    assert data["btc"] == pytest.approx(30000.0)


def test_next_signal_not_enough_history(client, monkeypatch):
    mr.ratio_history.clear()
    mr.bot_config.window_size = 100

    def fake_prices():
        return 30000.0, 0.1, 0.2

    monkeypatch.setattr(mr, "get_prices", fake_prices)
    monkeypatch.setattr(mr, "get_free_balance_mr", lambda asset: 0.0)
    monkeypatch.setattr(mr, "adjust_quantity", lambda symbol, qty, **kw: qty)

    def fake_get_state(_session):
        s = types.SimpleNamespace()
        s.current_asset = "HBAR"
        s.current_qty = 0.0
        s.realized_pnl_usd = 0.0
        s.unrealized_pnl_usd = 0.0
        s.last_ratio = 0.0
        s.last_z = 0.0
        return s

    monkeypatch.setattr(mr, "get_state", fake_get_state)

    r = client.get("/next_signal")
    assert r.status_code == 200
    data = r.json()
    assert data["direction"] == "NONE"
    assert data["reason"].startswith("not_enough_history")


def test_next_signal_with_sell_direction(client, monkeypatch):
    mr.ratio_history.clear()
    mr.bot_config.window_size = 20
    mr.bot_config.use_ratio_thresholds = True
    mr.bot_config.sell_ratio_threshold = 1.1
    mr.bot_config.buy_ratio_threshold = 0.9

    mr.ratio_history.extend([1.0] * mr.required_history_len())

    def fake_prices():
        # hbar/doge ratio = 1.2 > threshold → SELL HBAR
        return 30000.0, 1.2, 1.0

    monkeypatch.setattr(mr, "get_prices", fake_prices)
    monkeypatch.setattr(mr, "get_free_balance_mr", lambda asset: 100.0)
    monkeypatch.setattr(mr, "adjust_quantity", lambda symbol, qty, **kw: qty)

    def fake_get_state(_session):
        s = types.SimpleNamespace()
        s.current_asset = "HBAR"
        s.current_qty = 10.0
        s.realized_pnl_usd = 0.0
        s.unrealized_pnl_usd = 0.0
        s.last_ratio = 0.0
        s.last_z = 0.0
        return s

    monkeypatch.setattr(mr, "get_state", fake_get_state)

    r = client.get("/next_signal")
    assert r.status_code == 200
    data = r.json()

    assert data["direction"] == "HBAR->DOGE"
    assert data["from_asset"] == "HBAR"
    assert data["to_asset"] == "DOGE"
    assert data["qty_from"] > 0
    assert data["qty_to"] > 0


def test_manual_trade_uses_mr_path(client, monkeypatch):
    called = {}

    def fake_prices():
        return 30000.0, 0.1, 0.2

    def fake_balance(asset):
        return 100.0

    def fake_adjust(symbol, qty, **kw):
        return qty

    def fake_place(symbol, side, quantity):
        called["symbol"] = symbol
        called["side"] = side
        called["qty"] = quantity
        return {"orderId": 123}

    monkeypatch.setattr(mr, "get_prices", fake_prices)
    monkeypatch.setattr(mr, "get_free_balance_mr", fake_balance)
    monkeypatch.setattr(mr, "adjust_quantity", fake_adjust)
    monkeypatch.setattr(mr, "place_market_order_mr", fake_place)

    payload = {"direction": "HBAR->DOGE", "notional_usd": 10.0}
    r = client.post("/manual_trade", json=payload)
    assert r.status_code == 200
    data = r.json()

    assert data["status"] == "ok"
    assert called["symbol"].endswith(mr.get_mr_quote())
    assert called["side"] in ("BUY", "SELL")
    assert called["qty"] > 0
