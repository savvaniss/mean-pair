# tests/test_stats.py
import math
import pytest

import config
from engines import mean_reversion as mr_eng
from engines.common import compute_ma_std_window
from engines.mean_reversion import State


def test_compute_stats_single_value():
    mr_eng.ratio_history.clear()
    mean, std, z, is_outlier = mr_eng.compute_stats(1.23)
    assert math.isclose(mean, 1.23)
    assert std == 0.0
    assert z == 0.0
    assert is_outlier is False
    assert len(mr_eng.ratio_history) == 1


def test_compute_stats_multiple_values():
    mr_eng.ratio_history.clear()
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    last = None
    for v in values:
        last = mr_eng.compute_stats(v)

    mean, std, z, is_outlier = last
    assert math.isclose(mean, 3.0, rel_tol=1e-9)
    assert std > 0.0
    assert z > 0.0
    assert is_outlier is False
    assert len(mr_eng.ratio_history) == len(values)


def test_compute_stats_insufficient_history_returns_last_ratio():
    mr_eng.ratio_history.clear()
    values = [1.0, 2.0, 3.0]  # < 5 samples
    last = None
    for v in values:
        last = mr_eng.compute_stats(v)

    mean, std, z, is_outlier = last

    assert mean == values[-1]
    assert std == 0.0
    assert z == 0.0
    assert is_outlier is False


def test_decide_signal_uses_z_score_for_hbar_sell(monkeypatch):
    # ensure we "have enough history"
    monkeypatch.setattr(mr_eng, "has_enough_history", lambda: True)

    st = State(
        current_asset="HBAR",
        current_qty=100.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    mr_eng.bot_config.use_ratio_thresholds = False
    mr_eng.bot_config.z_entry = 2.0
    mr_eng.bot_config.z_exit = 0.5
    mr_eng.mr_rearm_ready = True

    ratio = 1.0
    mean_r = 1.0
    std_r = 0.1
    z = 3.0  # > z_entry

    sell, buy, reason = mr_eng.decide_signal(ratio, mean_r, std_r, z, st)
    assert sell is True
    assert buy is False
    assert reason == "z_score"


def test_decide_signal_uses_z_score_for_doge_buy(monkeypatch):
    monkeypatch.setattr(mr_eng, "has_enough_history", lambda: True)

    st = State(
        current_asset="DOGE",
        current_qty=100.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    mr_eng.bot_config.use_ratio_thresholds = False
    mr_eng.bot_config.z_entry = 2.0
    mr_eng.bot_config.z_exit = 0.5
    mr_eng.mr_rearm_ready = True

    ratio = 1.0
    mean_r = 1.0
    std_r = 0.1
    z = -3.0  # < -z_entry

    sell, buy, _ = mr_eng.decide_signal(ratio, mean_r, std_r, z, st)
    assert sell is False
    assert buy is True


def test_decide_signal_no_trade_when_not_hbar_or_doge(monkeypatch):
    monkeypatch.setattr(mr_eng, "has_enough_history", lambda: True)

    st = State(
        current_asset=config.BASE_ASSET,   # e.g. USDC
        current_qty=100.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    mr_eng.bot_config.use_ratio_thresholds = False
    ratio = 1.0
    mean_r = 1.0
    std_r = 0.1
    z = 5.0

    sell, buy, _ = mr_eng.decide_signal(ratio, mean_r, std_r, z, st)
    assert sell is False
    assert buy is False


def test_compute_ma_std_window_basic():
    prices = [10, 12, 14, 16]
    mean, std = compute_ma_std_window(prices, window=2)
    assert math.isclose(mean, (14 + 16) / 2)
    assert std > 0

    mean_all, std_all = compute_ma_std_window(prices, window=10)


def test_outlier_detection_clamps_large_jump(monkeypatch):
    history = [0.94 + 0.001 * (i % 5) for i in range(50)]
    mr_eng.ratio_history[:] = history

    orig_sigma = mr_eng.bot_config.outlier_sigma
    orig_jump = mr_eng.bot_config.max_ratio_jump
    mr_eng.bot_config.outlier_sigma = 3.0
    mr_eng.bot_config.max_ratio_jump = 0.05

    jump_ratio = 1.02
    filtered, flagged = mr_eng._filter_outlier(jump_ratio)
    assert flagged is True
    assert filtered < jump_ratio  # clamped

    mr_eng.ratio_history[:] = history
    _, _, _, is_outlier = mr_eng.compute_stats(jump_ratio)
    assert is_outlier is True
    assert mr_eng.ratio_history[-1] == pytest.approx(filtered)

    mr_eng.bot_config.outlier_sigma = orig_sigma
    mr_eng.bot_config.max_ratio_jump = orig_jump


def test_decide_signal_rearm_after_exit(monkeypatch):
    monkeypatch.setattr(mr_eng, "has_enough_history", lambda: True)
    mr_eng.bot_config.use_ratio_thresholds = False
    mr_eng.bot_config.z_entry = 2.0
    mr_eng.bot_config.z_exit = 0.5
    mr_eng.mr_rearm_ready = True

    st = State(
        current_asset="HBAR",
        current_qty=1.0,
        last_ratio=0.0,
        last_z=0.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
    )

    # First trigger arms and then blocks subsequent without reset
    sell, buy, _ = mr_eng.decide_signal(1.0, 1.0, 0.1, 3.0, st)
    assert sell is True and buy is False
    sell_again, _, _ = mr_eng.decide_signal(1.0, 1.0, 0.1, 2.5, st)
    assert sell_again is False

    # Crossing back inside exit band re-arms
    mr_eng.decide_signal(1.0, 1.0, 0.1, 0.1, st)
    sell_after_reset, _, _ = mr_eng.decide_signal(1.0, 1.0, 0.1, 3.1, st)
    assert sell_after_reset is True
