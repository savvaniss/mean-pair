from fastapi import APIRouter, HTTPException

from engines import liquidation_hunt as lh

router = APIRouter()


@router.get("/liquidation/status")
def liquidation_status():
    return lh.latest_status()


@router.post("/liquidation/config")
def liquidation_config(cfg: dict):
    try:
        updated = lh.update_config(cfg)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return updated


@router.post("/liquidation/scan")
def liquidation_scan():
    # Immediate manual scan for the UI/tests without waiting for the thread interval
    candles = lh.fetch_recent_candles(lh.liq_config.symbol, lh.liq_config.lookback_candles)
    clusters = lh.build_liquidity_clusters(candles, lh.liq_config.cluster_tolerance_bps)
    signal = lh.detect_stop_hunt(
        candles,
        clusters,
        wick_body_ratio=lh.liq_config.wick_body_ratio,
        risk_reward=lh.liq_config.risk_reward,
        reclaim_confirm_bars=lh.liq_config.reclaim_confirm_bars,
    )
    with lh.liq_lock:
        lh.latest_candles = candles
        lh.latest_clusters = clusters
        lh.latest_signal = signal
    return lh.latest_status()

