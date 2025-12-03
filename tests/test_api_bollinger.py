# tests/test_api_bollinger.py
import datetime as dt

import pytest
from fastapi import HTTPException

import config
from engines import bollinger as boll_engine
from routes import bollinger as boll_routes
from database import SessionLocal, BollSnapshot


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
    # routes/bollinger uses config.boll_client
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    # start with some history & state
    boll_engine.boll_price_history.clear()
    boll_engine.boll_ts_history.clear()
    boll_engine.boll_price_history.extend([1.0, 1.1, 1.2])
    boll_engine.boll_ts_history.extend(
        [dt.datetime.utcnow() - dt.timedelta(seconds=i) for i in range(3)]
    )
    boll_engine.current_boll_symbol = "OLD"

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
        "use_testnet": True,
    }

    r = client.post("/boll_config", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "HBARUSDC"
    # history should have been reset because symbol changed
    assert boll_engine.boll_price_history == []
    assert boll_engine.boll_ts_history == []
    assert boll_engine.current_boll_symbol == "HBARUSDC"


def test_boll_status_no_symbol_returns_empty(client, monkeypatch):
    boll_engine.boll_config.symbol = ""
    r = client.get("/boll_status")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == ""
    assert data["position"] == "FLAT"
    assert data["price"] == 0.0
    assert data["use_testnet"] == boll_engine.boll_config.use_testnet


def test_boll_status_with_symbol(client, monkeypatch):
    fake_client = FakeBollClient()
    # routes use config.boll_client
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    boll_engine.boll_config.symbol = "HBARUSDC"
    boll_engine.boll_config.window_size = 5
    boll_engine.boll_config.num_std = 2.0

    # price history for MA/std
    boll_engine.boll_price_history.clear()
    boll_engine.boll_ts_history.clear()
    prices = [1.0, 1.1, 1.2, 1.3, 1.4]
    now = dt.datetime.utcnow()
    for i, p in enumerate(prices):
        boll_engine.boll_price_history.append(p)
        boll_engine.boll_ts_history.append(
            now - dt.timedelta(seconds=(len(prices) - i))
        )

    # /boll_status uses a helper for price (imported into routes)
    monkeypatch.setattr(
    config.boll_client,
    "get_symbol_ticker",
    lambda s: {"symbol": s, "price": "1.5"},
    raising=False,
    )


    # NOTE: quote balance is now taken from FakeBollClient.get_account()
    # which returns 50.0 USDC, so we assert 50.0 to match real logic.
    r = client.get("/boll_status")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "HBARUSDC"
    assert data["quote_asset"] == "USDC"
    assert data["quote_balance"] == pytest.approx(50.0)
    assert data["price"] == pytest.approx(1.5)
    assert data["use_testnet"] == boll_engine.boll_config.use_testnet


def test_boll_config_switches_env(client, monkeypatch):
    fake_client = FakeBollClient()
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    called = {}

    def fake_switch(flag):
        called["val"] = flag
        config.BOLL_USE_TESTNET = flag

    monkeypatch.setattr(config, "switch_boll_env", fake_switch, raising=False)

    boll_engine.boll_config.use_testnet = True

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
        "use_testnet": False,
    }

    r = client.post("/boll_config", json=payload)
    assert r.status_code == 200

    assert called["val"] is False
    assert r.json()["use_testnet"] is False
    assert boll_engine.boll_config.use_testnet is False


def test_boll_history_computes_bands(monkeypatch):
    boll_engine.boll_config.symbol = "HBARUSDC"
    boll_engine.boll_config.window_size = 3
    boll_engine.boll_config.num_std = 2.0

    boll_engine.boll_price_history.clear()
    boll_engine.boll_ts_history.clear()
    now = dt.datetime.utcnow()
    prices = [1.0, 1.1, 1.2]
    for i, p in enumerate(prices):
        boll_engine.boll_price_history.append(p)
        boll_engine.boll_ts_history.append(
            now - dt.timedelta(seconds=(len(prices) - i))
        )

    # boll_history lives in routes.bollinger (not engines.bollinger)
    r = boll_routes.boll_history(limit=10)
    assert len(r) == 3
    last = r[-1]
    assert last.price == pytest.approx(1.2)
    # sanity: upper > ma > lower
    assert last.upper > last.ma > last.lower


def test_symbols_grouped_uses_exchange_info(client, monkeypatch):
    fake_client = FakeBollClient()
    # /symbols_grouped uses config.boll_client.get_exchange_info()
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    r = client.get("/symbols_grouped")
    assert r.status_code == 200
    data = r.json()
    # Only USDC group should contain our symbol
    assert "USDC" in data
    assert any(s["symbol"] == "HBARUSDC" for s in data["USDC"])


def test_bollinger_manual_sell_success(client, monkeypatch):
    fake_client = FakeBollClient()
    # config client for symbol info / balance if needed
    monkeypatch.setattr(config, "boll_client", fake_client, raising=False)

    # enough HBAR to sell – manual sell endpoint uses helpers imported in routes
    monkeypatch.setattr(
        boll_routes, "get_free_balance_boll", lambda asset: 5.0, raising=False
    )

    # stub out actual order placement (no real Binance)
    def fake_place(symbol, side, quantity):
        return {"orderId": 123, "symbol": symbol, "side": side, "origQty": quantity}

    monkeypatch.setattr(
        boll_routes, "place_market_order_boll", fake_place, raising=False
    )

    payload = {"symbol": "HBARUSDC", "qty_base": 1.5}
    r = client.post("/bollinger_manual_sell", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["symbol"] == "HBARUSDC"
    assert data["qty_sold"] > 0
    assert data["quote_received_est"] > 0


def test_boll_history_reads_persisted_snapshots(monkeypatch):
    boll_engine.boll_config.symbol = "HBARUSDC"
    sess = SessionLocal()
    try:
        sess.query(BollSnapshot).delete()
        now = dt.datetime.utcnow()
        for i in range(3):
            sess.add(
                BollSnapshot(
                    ts=now - dt.timedelta(minutes=3 - i),
                    symbol="HBARUSDC",
                    price=1.0 + 0.1 * i,
                    ma=1.0 + 0.05 * i,
                    upper=2.0,
                    lower=0.5,
                    std=0.1 + 0.01 * i,
                )
            )
        sess.commit()
    finally:
        sess.close()

    rows = boll_routes.boll_history(symbol="HBARUSDC", limit=10)
    assert len(rows) == 3
    assert rows[0].price == pytest.approx(1.0)
    assert rows[-1].price == pytest.approx(1.2)


def test_boll_history_defaults_to_saved_symbol_and_errors_when_missing(monkeypatch):
    # no symbol configured -> HTTPException
    boll_engine.boll_config.symbol = ""
    with pytest.raises(HTTPException):
        boll_routes.boll_history()

    # when symbol set, it should default to that symbol without passing param
    boll_engine.boll_config.symbol = "HBARUSDC"
    boll_engine.boll_config.window_size = 2
    boll_engine.boll_config.num_std = 2.0

    boll_engine.boll_price_history.clear()
    boll_engine.boll_ts_history.clear()
    now = dt.datetime.utcnow()
    boll_engine.boll_price_history.extend([1.0, 1.2])
    boll_engine.boll_ts_history.extend([now - dt.timedelta(seconds=1), now])

    rows = boll_routes.boll_history(limit=5)
    assert len(rows) == 2
    assert all(point.ts for point in rows)


def test_boll_config_best_from_history(monkeypatch):
    boll_engine.boll_config.symbol = "HBARUSDC"
    sess = SessionLocal()
    try:
        sess.query(BollSnapshot).delete()
        now = dt.datetime.utcnow()
        # build a range of zscores by varying std slightly
        for i in range(40):
            price = 1.0 + (i % 5) * 0.05
            ma = 1.0
            std = 0.02 + (i % 3) * 0.01
            sess.add(
                BollSnapshot(
                    ts=now - dt.timedelta(seconds=i),
                    symbol="HBARUSDC",
                    price=price,
                    ma=ma,
                    upper=ma + 3 * std,
                    lower=ma - 3 * std,
                    std=std,
                )
            )
        sess.commit()
    finally:
        sess.close()

    cfg = boll_routes.boll_config_best(symbol="HBARUSDC")
    assert cfg.symbol == "HBARUSDC"
    assert 1.5 <= cfg.num_std <= 4.0
    assert cfg.window_size >= 20
