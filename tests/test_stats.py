# tests/test_stats.py
import math
import app
from app import State


def test_compute_stats_single_value():
    app.ratio_history.clear()
    mean, std, z = app.compute_stats(1.23)
    assert math.isclose(mean, 1.23)
    assert std == 0.0
    assert z == 0.0
    assert len(app.ratio_history) == 1


def test_compute_stats_multiple_values():
    app.ratio_history.clear()
    values = [1.0, 2.0, 3.0]
    last = None
    for v in values:
        last = app.compute_stats(v)

    mean, std, z = last
    assert math.isclose(mean, 2.0, rel_tol=1e-9)
    # population std of [1,2,3] = sqrt(2/3)
    assert math.isclose(std, math.sqrt(2 / 3), rel_tol=1e-9)
    # last value is 3 → positive z
    assert z > 0


def test_decide_signal_uses_z_score_for_hbar_sell(monkeypatch):
    # ensure we "have enough history"
    monkeypatch.setattr(app, "has_enough_history", lambda: True)

    st = State(
        current_asset="HBAR",
        current_qty=100.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    app.bot_config.use_ratio_thresholds = False
    app.bot_config.z_entry = 2.0

    ratio = 1.0
    mean_r = 1.0
    std_r = 0.1
    z = 3.0  # > z_entry

    sell, buy, reason = app.decide_signal(ratio, mean_r, std_r, z, st)
    assert sell is True
    assert buy is False
    assert reason in ("z_score", "ratio_thresholds")  # depending on logic


def test_decide_signal_uses_z_score_for_doge_buy(monkeypatch):
    monkeypatch.setattr(app, "has_enough_history", lambda: True)

    st = State(
        current_asset="DOGE",
        current_qty=100.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    app.bot_config.use_ratio_thresholds = False
    app.bot_config.z_entry = 2.0

    ratio = 1.0
    mean_r = 1.0
    std_r = 0.1
    z = -3.0  # < -z_entry

    sell, buy, _ = app.decide_signal(ratio, mean_r, std_r, z, st)
    assert sell is False
    assert buy is True


def test_decide_signal_no_trade_when_not_hbar_or_doge(monkeypatch):
    monkeypatch.setattr(app, "has_enough_history", lambda: True)

    st = State(
        current_asset="USDT",   # base asset
        current_qty=100.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    app.bot_config.use_ratio_thresholds = False
    ratio = 1.0
    mean_r = 1.0
    std_r = 0.1
    z = 5.0

    sell, buy, _ = app.decide_signal(ratio, mean_r, std_r, z, st)
    assert sell is False
    assert buy is False


def test_compute_ma_std_window_basic():
    prices = [10, 12, 14, 16]
    mean, std = app.compute_ma_std_window(prices, window=2)
    assert math.isclose(mean, (14 + 16) / 2)
    assert std > 0

    mean_all, std_all = app.compute_ma_std_window(prices, window=10)
    assert math.isclose(mean_all, sum(prices) / len(prices))
