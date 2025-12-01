# tests/test_api_mr.py
import math
import types

import pytest
import app


def test_get_config_roundtrip(client):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()
    assert "poll_interval_sec" in data
    assert data["window_size"] == app.bot_config.window_size

    payload = data.copy()
    payload["window_size"] = data["window_size"] + 5
    payload["z_entry"] = 2.0

    r2 = client.post("/config", json=payload)
    assert r2.status_code == 200
    updated = r2.json()
    assert updated["window_size"] == payload["window_size"]
    assert app.bot_config.window_size == payload["window_size"]
    assert app.bot_config.z_entry == pytest.approx(2.0)


def test_status_basic(client, monkeypatch):
    app.ratio_history.clear()

    def fake_prices():
        # btc, hbar, doge
        return 30000.0, 0.12, 0.24

    balances = {"USDC": 100.0, "HBAR": 10.0, "DOGE": 0.0}

    def fake_balance(asset: str) -> float:
        return balances.get(asset, 0.0)

    monkeypatch.setattr(app, "get_prices", fake_prices)
    monkeypatch.setattr(app, "get_free_balance_mr", fake_balance)

    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()
    # ratio = hbar/doge = 0.5
    assert math.isclose(data["ratio"], 0.5)
    assert "zscore" in data
    assert "unrealized_pnl_usd" in data
    assert data["btc"] == pytest.approx(30000.0)



def test_next_signal_not_enough_history(client, monkeypatch):
    # Ensure history is short
    app.ratio_history.clear()
    app.bot_config.window_size = 100

    def fake_prices():
        return 30000.0, 0.1, 0.2

    monkeypatch.setattr(app, "get_prices", fake_prices)

    # Avoid touching Binance for balances & quantities
    monkeypatch.setattr(app, "get_free_balance_mr", lambda asset: 0.0)
    monkeypatch.setattr(app, "adjust_quantity", lambda symbol, qty, **kw: qty)

    # Provide a dummy state so decide_signal sees "HBAR"
    def fake_get_state(_session):
        s = types.SimpleNamespace()
        s.current_asset = "HBAR"
        s.current_qty = 0.0
        s.realized_pnl_usd = 0.0
        s.unrealized_pnl_usd = 0.0
        s.last_ratio = 0.0
        s.last_z = 0.0
        return s

    monkeypatch.setattr(app, "get_state", fake_get_state)

    r = client.get("/next_signal")
    assert r.status_code == 200
    data = r.json()
    assert data["direction"] == "NONE"
    assert data["reason"].startswith("not_enough_history")


def test_next_signal_with_sell_direction(client, monkeypatch):
    # Make sure we have enough history
    app.ratio_history.clear()
    app.bot_config.window_size = 20
    app.bot_config.use_ratio_thresholds = True
    app.bot_config.sell_ratio_threshold = 1.1
    app.bot_config.buy_ratio_threshold = 0.9

    # need history >= required_history_len
    app.ratio_history.extend([1.0] * app.required_history_len())

    def fake_prices():
        # hbar/doge ratio = 1.2 > sell_threshold
        return 30000.0, 1.2, 1.0

    monkeypatch.setattr(app, "get_prices", fake_prices)
    monkeypatch.setattr(app, "get_free_balance_mr", lambda asset: 100.0)

    # make quantity calculation trivial
    monkeypatch.setattr(app, "adjust_quantity", lambda symbol, qty, **kw: qty)

    def fake_get_state(_session):
        s = types.SimpleNamespace()
        s.current_asset = "HBAR"
        s.current_qty = 10.0
        s.realized_pnl_usd = 0.0
        s.unrealized_pnl_usd = 0.0
        s.last_ratio = 0.0
        s.last_z = 0.0
        return s

    monkeypatch.setattr(app, "get_state", fake_get_state)

    r = client.get("/next_signal")
    assert r.status_code == 200
    data = r.json()
    assert data["direction"] == "HBAR->DOGE"
    assert data["from_asset"] == "HBAR"
    assert data["to_asset"] == "DOGE"
    assert data["qty_from"] > 0
    assert data["qty_to"] > 0


def test_manual_trade_uses_mr_path(client, monkeypatch):
    # manual trade endpoint should validate input and call place_market_order_mr

    called = {}

    def fake_prices():
        return 30000.0, 0.1, 0.2

    def fake_balance(asset):
        # Give enough balance in both assets
        return 100.0

    def fake_adjust(symbol, qty, **kw):
        return qty  # no clamping

    def fake_place(symbol, side, quantity):
        called["symbol"] = symbol
        called["side"] = side
        called["qty"] = quantity
        return {"orderId": 123}

    monkeypatch.setattr(app, "get_prices", fake_prices)
    monkeypatch.setattr(app, "get_free_balance_mr", fake_balance)
    monkeypatch.setattr(app, "adjust_quantity", fake_adjust)
    monkeypatch.setattr(app, "place_market_order_mr", fake_place)

    payload = {"direction": "HBAR->DOGE", "notional_usd": 10.0}
    r = client.post("/manual_trade", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert called["symbol"].endswith(app.get_mr_quote())
    assert called["side"] in ("BUY", "SELL")
    assert called["qty"] > 0
