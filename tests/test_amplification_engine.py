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
        "BTCUSDT": [100, 102, 104, 106, 108, 110],
        "ALT1USDT": [100, 105, 110, 115, 120, 125],
        "ALT2USDT": [100, 101, 102, 103, 104, 105],
    }

    def fake_fetch(symbol, interval, start, end):
        prices = series.get(symbol)
        if not prices:
            return []
        return _candles(prices)

    monkeypatch.setattr(backtester, "_fetch_klines", fake_fetch)
    amplification.set_config(
        {
            "base_symbol": "BTCUSDT",
            "symbols": ["ALT1USDT", "ALT2USDT"],
            "lookback_days": 30,
            "interval": "1d",
            "min_beta": 1.1,
            "suggest_top_n": 2,
        }
    )

    summary = amplification.summarize_amplification()
    assert summary["suggestions"] == ["ALT1USDT"]
    assert any(row["symbol"] == "ALT1USDT" for row in summary["stats"])

    resp = client.get("/amplification/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["suggestions"] == ["ALT1USDT"]
