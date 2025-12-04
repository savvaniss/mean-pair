from engines import relative_strength as rs_engine


def test_compute_relative_strength_positive():
    prices = [10.0, 11.0, 12.0, 13.0]
    rs = rs_engine.compute_relative_strength(prices, window=3)
    assert rs > 0


def test_build_spreads_respects_gap():
    ranked = [("AAAUSDT", 1.5, 10.0), ("BBBUSDT", 0.1, 5.0)]
    old_gap = rs_engine.rs_config.min_rs_gap
    old_top = rs_engine.rs_config.top_n
    old_bottom = rs_engine.rs_config.bottom_n
    try:
        rs_engine.rs_config.min_rs_gap = 0.2
        rs_engine.rs_config.top_n = 1
        rs_engine.rs_config.bottom_n = 1
        spreads = rs_engine._build_spreads(ranked)
        assert spreads

        rs_engine.rs_config.min_rs_gap = 5.0
        spreads_none = rs_engine._build_spreads(ranked)
        assert spreads_none == []
    finally:
        rs_engine.rs_config.min_rs_gap = old_gap
        rs_engine.rs_config.top_n = old_top
        rs_engine.rs_config.bottom_n = old_bottom
