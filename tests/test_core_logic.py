# tests/test_core_logic.py
import math
import types

import pytest
import app


def test_required_history_len_and_has_enough_history():
    app.ratio_history.clear()
    app.bot_config.window_size = 100

    # required = max(5, window * 0.5) = 50
    assert app.required_history_len() == 50
    assert not app.has_enough_history()

    # push 49 points -> still not enough
    app.ratio_history.extend([1.0] * 49)
    assert not app.has_enough_history()

    # add one more -> now enough
    app.ratio_history.append(1.0)
    assert app.has_enough_history()


def test_init_state_from_balances_picks_highest_value(monkeypatch):
    # balances: HBAR has highest USD value
    balances = {
        app.BASE_ASSET: 5.0,   #  use the real base asset
        "HBAR": 10.0,
        "DOGE": 1.0,
    }

    def fake_balance(asset: str) -> float:
        return balances.get(asset, 0.0)

    # prices: HBAR = 1, DOGE = 0.1
    def fake_prices():
        return 30000.0, 1.0, 0.1  # btc, hbar, doge

    monkeypatch.setattr(app, "get_free_balance_mr", fake_balance)
    monkeypatch.setattr(app, "get_prices", fake_prices)

    st = app.State()
    app.init_state_from_balances(st)

    # HBAR value = 10, DOGE = 0.1, base = 5 => HBAR wins
    assert st.current_asset == "HBAR"
    assert st.current_qty == pytest.approx(10.0)
    # realized_pnl_usd is set to total account value
    assert st.realized_pnl_usd == pytest.approx(5 + 10 + 0.1, rel=1e-9)


def _dummy_state(asset: str):
    s = types.SimpleNamespace()
    s.current_asset = asset
    return s


def test_decide_signal_ratio_thresholds_sell_and_buy(monkeypatch):
    # enough history so we are allowed to trade
    app.ratio_history.clear()
    app.ratio_history.extend([1.0] * app.required_history_len())

    # enable explicit thresholds
    app.bot_config.use_ratio_thresholds = True
    app.bot_config.sell_ratio_threshold = 1.2
    app.bot_config.buy_ratio_threshold = 0.8

    # SELL case: ratio above sell threshold, holding HBAR
    s = _dummy_state("HBAR")
    sell, buy, reason = app.decide_signal(
        ratio=1.25, mean_r=1.0, std_r=0.1, z=2.5, state=s
    )
    assert sell is True
    assert buy is False
    assert reason == "ratio_thresholds"

    # BUY case: ratio below buy threshold, holding DOGE
    s.current_asset = "DOGE"
    sell, buy, reason = app.decide_signal(
        ratio=0.75, mean_r=1.0, std_r=0.1, z=-2.5, state=s
    )
    assert sell is False
    assert buy is True
    assert reason == "ratio_thresholds"


def test_decide_signal_zscore_path(monkeypatch):
    # fallback to z-score path
    app.ratio_history.clear()
    app.ratio_history.extend([1.0] * app.required_history_len())

    app.bot_config.use_ratio_thresholds = False
    app.bot_config.z_entry = 1.5

    s = _dummy_state("HBAR")
    # z > z_entry → sell signal
    sell, buy, reason = app.decide_signal(
        ratio=1.3, mean_r=1.0, std_r=0.1, z=3.0, state=s
    )
    assert sell is True
    assert buy is False
    assert reason == "z_score"   #  here

    # z < -z_entry → buy signal (if holding DOGE)
    s.current_asset = "DOGE"
    sell, buy, reason = app.decide_signal(
        ratio=0.7, mean_r=1.0, std_r=0.1, z=-3.0, state=s
    )
    assert sell is False
    assert buy is True
    assert reason == "z_score"   # and here


def test_adjust_quantity_respects_lot_size(monkeypatch):
    class FakeClient:
        def get_symbol_info(self, symbol):
            return {
                "symbol": symbol,
                "filters": [
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.1",
                        "stepSize": "0.1",
                    }
                ],
            }

    # use MR client path
    monkeypatch.setattr(app, "mr_client", FakeClient())
    q = app.adjust_quantity("HBARUSDC", 0.35)
    # 0.35 -> steps = 3, qty = 0.3
    assert q == pytest.approx(0.3)

    # quantity below minQty should become 0
    q2 = app.adjust_quantity("HBARUSDC", 0.05)
    assert q2 == 0.0


def test_boll_history_len_helpers():
    app.boll_price_history.clear()
    app.boll_config.window_size = 70  # default

    # required = max(5, 70 * 0.5) = 35
    assert app.boll_required_history_len() == 35
    assert not app.boll_has_enough_history()

    app.boll_price_history.extend([1.0] * 35)
    assert app.boll_has_enough_history()
