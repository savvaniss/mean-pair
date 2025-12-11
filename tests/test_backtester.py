from datetime import datetime, timedelta

from engines import backtester


def _build_candles(prices):
    base = datetime(2024, 1, 1)
    return [
        backtester.Candle(ts=base + timedelta(hours=idx), open=p, high=p, low=p, close=p)
        for idx, p in enumerate(prices)
    ]


def test_mean_reversion_ratio_threshold_backtest(monkeypatch):
    a_prices = [100, 90, 95, 100, 110, 103]
    b_prices = [100, 100, 100, 100, 100, 100]

    candles = {
        "AAAUSDT": _build_candles(a_prices),
        "BBBUSDT": _build_candles(b_prices),
    }

    monkeypatch.setattr(backtester, "_mr_quote", lambda: "USDT")
    monkeypatch.setattr(
        backtester,
        "_fetch_klines",
        lambda symbol, interval, start, end: candles[symbol],
    )

    result = backtester.backtest_mean_reversion(
        asset_a="AAA",
        asset_b="BBB",
        interval="1h",
        window=2,
        z_entry=2.0,
        z_exit=0.5,
        use_ratio_thresholds=True,
        sell_ratio_threshold=1.05,
        buy_ratio_threshold=0.95,
        lookback_days=1,
        starting_balance=1000.0,
        fee_rate=0.0,
        position_pct=1.0,
    )

    assert result.strategy == "mean_reversion"
    assert result.trades[0].action == "LONG_A_SHORT_B"
    assert any(t.action == "LONG_B_SHORT_A" for t in result.trades)
    assert result.trades[-1].action == "EXIT"
    assert result.final_balance > 0
