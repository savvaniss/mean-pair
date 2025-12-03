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


class DummyClient:
    def __init__(self):
        self.orders = []

    def get_symbol_ticker(self, symbol):
        return {"symbol": symbol, "price": "100"}

    def get_symbol_info(self, symbol):
        return {
            "baseAsset": symbol[:-4],
            "quoteAsset": symbol[-4:],
            "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"}],
        }

    def order_market(self, symbol, side, quantity):
        self.orders.append((symbol, side, quantity))
        return {"status": "FILLED"}


def test_manual_execute_places_order_and_records(monkeypatch):
    orig_client = lh.liq_client
    orig_cfg = lh.liq_config
    dummy = DummyClient()
    try:
        lh.liq_client = dummy
        lh.liq_config = lh.liq_config.copy(update={"symbol": "BTCUSDT", "trade_notional_usd": 50})
        with lh.liq_lock:
            lh.latest_signal = lh.StopHuntSignal(
                direction="LONG",
                sweep_level=99.0,
                entry=100.0,
                stop_loss=98.5,
                take_profit=105.0,
                confidence=0.8,
                reclaim_confirmed=True,
            )
        res = lh.manual_execute()
        assert res is not None
        assert dummy.orders and dummy.orders[0][1] == "BUY"
    finally:
        lh.liq_client = orig_client
        lh.liq_config = orig_cfg
        lh.latest_signal = None


def test_auto_trade_skips_duplicates(monkeypatch):
    orig_client = lh.liq_client
    orig_cfg = lh.liq_config
    dummy = DummyClient()
    try:
        lh.liq_client = dummy
        lh.liq_config = lh.liq_config.copy(
            update={"symbol": "BTCUSDT", "trade_notional_usd": 25, "auto_trade": True}
        )
        signal = lh.StopHuntSignal(
            direction="SHORT",
            sweep_level=101.0,
            entry=100.0,
            stop_loss=102.0,
            take_profit=95.0,
            confidence=0.6,
            reclaim_confirmed=True,
        )
        ts = datetime.utcnow()
        lh.last_execution_signature = None
        lh.latest_execution = None

        first = lh.maybe_execute_trade(signal, ts)
        second = lh.maybe_execute_trade(signal, ts)

        assert first is not None
        assert second is None
        assert len(dummy.orders) == 1
    finally:
        lh.liq_client = orig_client
        lh.liq_config = orig_cfg
        lh.latest_execution = None

