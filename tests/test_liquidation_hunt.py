from datetime import datetime, timedelta

from engines import liquidation_hunt as lh


def _candle(ts_offset: int, o: float, h: float, l: float, c: float):
    return lh.Candle(datetime(2024, 1, 1) + timedelta(minutes=ts_offset), o, h, l, c)


def test_clusters_group_by_tolerance_and_side():
    candles = [
        _candle(0, 100, 105, 99, 104),
        _candle(1, 104, 105, 100, 104),
        _candle(2, 104, 106, 98.8, 105),  # swing low
        _candle(3, 105, 107, 99.3, 106),
        _candle(4, 106, 108, 99.0, 107),  # swing low near previous
        _candle(5, 107, 109, 103, 108),
        _candle(6, 108, 110, 111, 109),  # swing high far away
        _candle(7, 109, 109.5, 105, 106),
    ]

    clusters = lh.build_liquidity_clusters(candles, tolerance_bps=25)
    long_clusters = [c for c in clusters if c.side == "long_liquidity"]
    short_clusters = [c for c in clusters if c.side == "short_liquidity"]

    assert len(long_clusters) == 1  # lows were merged
    assert long_clusters[0].touches == 2
    assert len(short_clusters) == 1
    assert short_clusters[0].touches == 1


def test_detects_long_stop_hunt_and_targets():
    candles = [
        _candle(0, 100, 105, 99, 104),
        _candle(1, 104, 105, 100, 104),
        _candle(2, 104, 106, 98.8, 105),
        _candle(3, 105, 107, 99.3, 106),
        _candle(4, 106, 108, 99.0, 107),
        _candle(5, 107, 109, 99.2, 108),
        _candle(6, 108, 110, 98.5, 109.5),  # sweep candle
    ]

    clusters = lh.build_liquidity_clusters(candles, tolerance_bps=15)
    sig = lh.detect_stop_hunt(
        candles,
        clusters,
        wick_body_ratio=2.0,
        risk_reward=2.0,
        reclaim_confirm_bars=1,
    )

    assert sig is not None
    assert sig.direction == "LONG"
    assert sig.entry == candles[-1].close
    assert sig.stop_loss == candles[-1].low
    assert sig.take_profit > sig.entry
    assert 0 < sig.confidence <= 1


def test_heatmap_normalises_strengths():
    clusters = [
        lh.LiquidityCluster(level=100.0, touches=5, side="long_liquidity"),
        lh.LiquidityCluster(level=110.0, touches=10, side="short_liquidity"),
    ]

    heatmap = lh.build_heatmap(clusters, max_levels=5)
    assert heatmap["long"][0]["strength"] == 0.5
    assert heatmap["short"][0]["strength"] == 1.0

