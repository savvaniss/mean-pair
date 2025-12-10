from datetime import datetime, timedelta

import pytest

from engines import amplification
from engines import backtester


def _candles(prices):
    start = datetime(2024, 1, 1)
    return [
        backtester.Candle(ts=start + timedelta(days=i), open=p, high=p, low=p, close=p)
        for i, p in enumerate(prices)
    ]


def test_compute_stat_beta_above_one():
    base = [100, 102, 104, 106, 108]
    alt = [100, 104, 108, 112, 116]  # roughly 2x moves
    stat = amplification.compute_stat(base, alt)

    assert stat.beta > 1.8
    assert stat.correlation > 0.9
    assert stat.sample_size == 4


def test_summary_and_api(monkeypatch, client):
    series = {
        "BTCUSDC": [100, 102, 104, 106, 108, 110],
        "ALT1USDC": [100, 105, 110, 115, 120, 125],
        "ALT2USDC": [100, 101, 102, 103, 104, 105],
    }

    def fake_fetch(symbol, interval, start, end):
        prices = series.get(symbol)
        if not prices:
            return []
        return _candles(prices)

    monkeypatch.setattr(backtester, "_fetch_klines", fake_fetch)
    amplification.set_config(
        {
            "base_symbol": "BTCUSDC",
            "symbols": ["ALT1USDC", "ALT2USDC"],
            "lookback_days": 30,
            "interval": "1d",
            "min_beta": 1.1,
            "suggest_top_n": 2,
            "conversion_symbol": None,
        }
    )

    summary = amplification.summarize_amplification()
    assert summary["suggestions"] == ["ALT1USDC"]
    assert summary["conversion_symbol"] == "ALT1USDC"
    assert any(row["symbol"] == "ALT1USDC" for row in summary["stats"])

    resp = client.get("/amplification/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["suggestions"] == ["ALT1USDC"]


def test_backtest_respects_conversion_and_cooldown(monkeypatch):
    series = {
        "BTCUSDC": [100, 102, 104, 101, 105, 104],
        "ALT1USDC": [100, 104, 108, 105, 110, 108],
        "ALT2USDC": [100, 103, 106, 104, 108, 107],
    }

    def fake_fetch(symbol, interval, start, end):
        prices = series.get(symbol)
        if not prices:
            return []
        return _candles(prices)

    monkeypatch.setattr(backtester, "_fetch_klines", fake_fetch)

    result = backtester.backtest_amplification(
        base_symbol="BTCUSDC",
        symbols=["ALT1USDC", "ALT2USDC"],
        interval="1d",
        lookback_days=30,
        momentum_window=1,
        min_beta=1.0,
        conversion_symbol="ALT2USDC",
        switch_cooldown=2,
        starting_balance=1000.0,
    )

    assert any(t.action.startswith("BUY_ALT2USDC") for t in result.trades)
    # Cooldown should delay an exit until later negative momentum
    exits = [t for t in result.trades if t.action.startswith("EXIT")]
    assert exits
    assert exits[-1].ts == result.trades[-1].ts
