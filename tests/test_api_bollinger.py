# tests/test_api_bollinger.py
import datetime as dt

import pytest
import app


class FakeBollClient:
    """Minimal stub for boll_client used in these tests."""

    def __init__(self):
        self._exchange_info = {
            "symbols": [
                {
                    "symbol": "HBARUSDC",
                    "baseAsset": "HBAR",
                    "quoteAsset": "USDC",
                    "status": "TRADING",
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.1",
                            "stepSize": "0.1",
                        },
                        {
                            "filterType": "MIN_NOTIONAL",
                            "minNotional": "1.0",
                        },
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
        return {"symbol": symbol, "price": "2.0"}

    def get_exchange_info(self):
        return self._exchange_info

    def get_account(self):
        return {
            "balances": [
                {"asset": "USDC", "free": "50.0", "locked": "0.0"},
                {"asset": "HBAR", "free": "10.0", "locked": "0.0"},
            ]
        }


def test_boll_config_symbol_validation_and_reset(client, monkeypatch):
    fake_client = FakeBollClient()
    monkeypatch.setattr(app, "boll_client", fake_client)

    # start with some history & state
    app.boll_price_history.clear()
    app.boll_ts_history.clear()
    app.boll_price_history.extend([1.0, 1.1, 1.2])
    app.boll_ts_history.extend(
        [dt.datetime.utcnow() - dt.timedelta(seconds=i) for i in range(3)]
    )
    app.current_boll_symbol = "OLD"

    payload = {
        "enabled": False,
        "symbol": "HBARUSDC",
        "poll_interval_sec": 20,
        "window_size": 10,
        "num_std": 2.0,
        "max_position_usd": 50.0,
        "use_all_balance": True,
        "stop_loss_pct": 0.2,
        "take_profit_pct": 0.2,
        "cooldown_sec": 60,
    }

    r = client.post("/boll_config", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "HBARUSDC"
    # history should have been reset because symbol changed
    assert app.boll_price_history == []
    assert app.boll_ts_history == []
    assert app.current_boll_symbol == "HBARUSDC"


def test_boll_status_no_symbol_returns_empty(client, monkeypatch):
    app.boll_config.symbol = ""
    r = client.get("/boll_status")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == ""
    assert data["position"] == "FLAT"
    assert data["price"] == 0.0


def test_boll_status_with_symbol(client, monkeypatch):
    fake_client = FakeBollClient()
    monkeypatch.setattr(app, "boll_client", fake_client)

    app.boll_config.symbol = "HBARUSDC"
    app.boll_config.window_size = 5
    app.boll_config.num_std = 2.0

    # price history for MA/std
    app.boll_price_history.clear()
    app.boll_ts_history.clear()
    prices = [1.0, 1.1, 1.2, 1.3, 1.4]
    now = dt.datetime.utcnow()
    for i, p in enumerate(prices):
        app.boll_price_history.append(p)
        app.boll_ts_history.append(now - dt.timedelta(seconds=(len(prices) - i)))

    # no open position in DB yet, but function should still work
    monkeypatch.setattr(app, "get_symbol_price_boll", lambda symbol: 1.5)
    monkeypatch.setattr(app, "get_free_balance_boll", lambda asset: 42.0)

    r = client.get("/boll_status")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "HBARUSDC"
    assert data["quote_asset"] == "USDC"
    assert data["quote_balance"] == pytest.approx(42.0)
    assert data["price"] == pytest.approx(1.5)


def test_boll_history_computes_bands(monkeypatch):
    app.boll_config.symbol = "HBARUSDC"
    app.boll_config.window_size = 3
    app.boll_config.num_std = 2.0

    app.boll_price_history.clear()
    app.boll_ts_history.clear()
    now = dt.datetime.utcnow()
    prices = [1.0, 1.1, 1.2]
    for i, p in enumerate(prices):
        app.boll_price_history.append(p)
        app.boll_ts_history.append(now - dt.timedelta(seconds=(len(prices) - i)))

    r = app.boll_history(limit=10)
    assert len(r) == 3
    last = r[-1]
    assert last.price == pytest.approx(1.2)
    # sanity: upper > ma > lower
    assert last.upper > last.ma > last.lower


def test_symbols_grouped_uses_exchange_info(client, monkeypatch):
    fake_client = FakeBollClient()
    monkeypatch.setattr(app, "boll_client", fake_client)

    r = client.get("/symbols_grouped")
    assert r.status_code == 200
    data = r.json()
    # Only USDC group should contain our symbol
    assert "USDC" in data
    assert any(s["symbol"] == "HBARUSDC" for s in data["USDC"])


def test_bollinger_manual_sell_success(client, monkeypatch):
    fake_client = FakeBollClient()
    monkeypatch.setattr(app, "boll_client", fake_client)

    # enough HBAR to sell
    monkeypatch.setattr(app, "get_free_balance_boll", lambda asset: 5.0)

    # reuse adjust_quantity but make sure it uses our fake client
    monkeypatch.setattr(app, "boll_client", fake_client)

    # stub out actual order placement (no real Binance)
    def fake_place(symbol, side, quantity):
        return {"orderId": 123, "symbol": symbol, "side": side, "origQty": quantity}

    monkeypatch.setattr(app, "place_market_order_boll", fake_place)

    payload = {"symbol": "HBARUSDC", "qty_base": 1.5}
    r = client.post("/bollinger_manual_sell", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["symbol"] == "HBARUSDC"
    assert data["qty_sold"] > 0
    assert data["quote_received_est"] > 0
