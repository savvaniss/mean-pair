"""Liquidity grab / liquidation hunt detection engine.

This module turns the stop-hunt rules the user provided into concrete,
testable logic:

1) build liquidity clusters from recent highs/lows (where stops collect)
2) watch for wicks that sweep those clusters and close back inside
3) emit an actionable entry/stop/target suggestion plus heatmap buckets

The engine runs passively (no orders are placed) and only surfaces
signals + analytics to the frontend.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from binance.client import Client
from pydantic import BaseModel

import config


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class LiquidityCluster:
    level: float
    touches: int
    side: str  # "long_liquidity" (below price) or "short_liquidity" (above price)


@dataclass
class StopHuntSignal:
    direction: str  # "LONG" or "SHORT"
    sweep_level: float
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    reclaim_confirmed: bool


class LiquidationConfig(BaseModel):
    enabled: bool = True
    symbol: str = "BTCUSDT"
    poll_interval_sec: int = 30
    lookback_candles: int = 200
    cluster_tolerance_bps: float = 12.0  # how close highs/lows must be to sit in same pool
    wick_body_ratio: float = 2.2  # wick must be this many times the body to qualify as a sweep
    reclaim_confirm_bars: int = 1  # close back inside by this many bars after sweep
    risk_reward: float = 2.5  # target multiple of risk
    max_heatmap_levels: int = 12


liq_config = LiquidationConfig()
liq_lock = threading.Lock()
liq_thread: Optional[threading.Thread] = None
liq_stop_flag = False
latest_clusters: List[LiquidityCluster] = []
latest_signal: Optional[StopHuntSignal] = None
latest_candles: List[Candle] = []


# ==========================================
# Data plumbing
# ==========================================


def _client() -> Optional[Client]:
    return config.boll_client


def fetch_recent_candles(symbol: str, limit: int) -> List[Candle]:
    if not _client():
        return []

    try:
        raw = _client().get_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_1MINUTE,
            limit=limit,
        )
    except TypeError:
        raw = _client().get_klines(symbol, Client.KLINE_INTERVAL_1MINUTE, limit)

    candles: List[Candle] = []
    for k in raw:
        ts = datetime.fromtimestamp(k[0] / 1000)
        candles.append(
            Candle(
                ts=ts,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
            )
        )
    return candles


# ==========================================
# Core detection logic
# ==========================================


def _swing_levels(candles: List[Candle]) -> Tuple[List[float], List[float]]:
    """Return swing highs and lows (naïve pivot detection)."""

    highs: List[float] = []
    lows: List[float] = []
    for i in range(1, len(candles) - 1):
        prev_c = candles[i - 1]
        c = candles[i]
        next_c = candles[i + 1]
        if c.high >= prev_c.high and c.high >= next_c.high:
            highs.append(c.high)
        if c.low <= prev_c.low and c.low <= next_c.low:
            lows.append(c.low)
    return highs, lows


def _cluster_levels(levels: List[float], tolerance_bps: float, side: str) -> List[LiquidityCluster]:
    clusters: List[LiquidityCluster] = []
    for level in sorted(levels):
        matched = None
        for c in clusters:
            tolerance = c.level * tolerance_bps / 10_000
            if abs(level - c.level) <= tolerance:
                matched = c
                break
        if matched:
            matched.touches += 1
            matched.level = (matched.level * (matched.touches - 1) + level) / matched.touches
        else:
            clusters.append(LiquidityCluster(level=level, touches=1, side=side))
    clusters.sort(key=lambda c: c.touches, reverse=True)
    return clusters


def build_liquidity_clusters(
    candles: List[Candle], tolerance_bps: float
) -> List[LiquidityCluster]:
    highs, lows = _swing_levels(candles)
    up = _cluster_levels(highs, tolerance_bps, side="short_liquidity")
    down = _cluster_levels(lows, tolerance_bps, side="long_liquidity")
    return up + down


def _wick_lengths(c: Candle) -> Tuple[float, float, float]:
    body = abs(c.close - c.open)
    upper = c.high - max(c.close, c.open)
    lower = min(c.close, c.open) - c.low
    return body, upper, lower


def detect_stop_hunt(
    candles: List[Candle],
    clusters: List[LiquidityCluster],
    wick_body_ratio: float,
    risk_reward: float,
    reclaim_confirm_bars: int,
) -> Optional[StopHuntSignal]:
    if len(candles) < 5 or not clusters:
        return None

    last = candles[-1]
    body, upper_wick, lower_wick = _wick_lengths(last)
    if body == 0:
        return None

    sorted_clusters = sorted(clusters, key=lambda c: c.touches, reverse=True)
    top_touch = sorted_clusters[0].touches

    for cl in sorted_clusters:
        tolerance = cl.level * 0.0005  # small buffer when evaluating sweeps
        is_long_sweep = (
            cl.side == "long_liquidity"
            and last.low <= cl.level + tolerance
            and last.close > cl.level
            and lower_wick >= wick_body_ratio * body
            and last.close > last.open
        )
        if is_long_sweep:
            stop = last.low
            risk = last.close - stop
            tp = last.close + risk_reward * risk
            confidence = min(1.0, cl.touches / max(1.0, top_touch))
            reclaim_ok = _reclaimed_after_sweep(
                candles, cl.level, reclaim_confirm_bars, direction="long"
            )
            return StopHuntSignal(
                direction="LONG",
                sweep_level=cl.level,
                entry=last.close,
                stop_loss=stop,
                take_profit=tp,
                confidence=confidence,
                reclaim_confirmed=reclaim_ok,
            )

        is_short_sweep = (
            cl.side == "short_liquidity"
            and last.high >= cl.level - tolerance
            and last.close < cl.level
            and upper_wick >= wick_body_ratio * body
            and last.close < last.open
        )
        if is_short_sweep:
            stop = last.high
            risk = stop - last.close
            tp = last.close - risk_reward * risk
            confidence = min(1.0, cl.touches / max(1.0, top_touch))
            reclaim_ok = _reclaimed_after_sweep(
                candles, cl.level, reclaim_confirm_bars, direction="short"
            )
            return StopHuntSignal(
                direction="SHORT",
                sweep_level=cl.level,
                entry=last.close,
                stop_loss=stop,
                take_profit=tp,
                confidence=confidence,
                reclaim_confirmed=reclaim_ok,
            )
    return None


def _reclaimed_after_sweep(
    candles: List[Candle], level: float, bars: int, direction: str
) -> bool:
    if bars <= 0 or len(candles) < 2:
        return True
    last = candles[-1]
    start_idx = max(0, len(candles) - 1 - bars)
    recent = candles[start_idx:]
    if direction == "long":
        return all(c.close >= level for c in recent)
    return all(c.close <= level for c in recent)


def build_heatmap(clusters: List[LiquidityCluster], max_levels: int) -> Dict[str, List[Dict]]:
    heatmap: Dict[str, List[Dict]] = defaultdict(list)
    top_clusters = sorted(clusters, key=lambda c: c.touches, reverse=True)[:max_levels]
    if not top_clusters:
        return {"long": [], "short": []}

    max_touch = max(c.touches for c in top_clusters) or 1
    for cl in top_clusters:
        intensity = cl.touches / max_touch
        bucket = {
            "price": round(cl.level, 2),
            "strength": round(intensity, 3),
            "touches": cl.touches,
        }
        if cl.side == "long_liquidity":
            heatmap["long"].append(bucket)
        else:
            heatmap["short"].append(bucket)
    heatmap["long"] = sorted(heatmap["long"], key=lambda b: b["price"])
    heatmap["short"] = sorted(heatmap["short"], key=lambda b: b["price"])
    return heatmap


# ==========================================
# Threaded loop
# ==========================================


def liquidation_loop():
    global latest_clusters, latest_signal, latest_candles
    session_poll = liq_config.poll_interval_sec
    while not liq_stop_flag:
        if not liq_config.enabled:
            time.sleep(1)
            continue

        candles = fetch_recent_candles(liq_config.symbol, liq_config.lookback_candles)
        clusters = build_liquidity_clusters(candles, liq_config.cluster_tolerance_bps)
        signal = detect_stop_hunt(
            candles,
            clusters,
            wick_body_ratio=liq_config.wick_body_ratio,
            risk_reward=liq_config.risk_reward,
            reclaim_confirm_bars=liq_config.reclaim_confirm_bars,
        )

        with liq_lock:
            latest_candles = candles
            latest_clusters = clusters
            latest_signal = signal

        time.sleep(session_poll)


def start_liquidation_thread():
    global liq_thread, liq_stop_flag
    if liq_thread and liq_thread.is_alive():
        return
    liq_stop_flag = False
    liq_thread = threading.Thread(target=liquidation_loop, daemon=True)
    liq_thread.start()


def stop_liquidation_thread():
    global liq_stop_flag
    liq_stop_flag = True
    if liq_thread:
        liq_thread.join(timeout=1)


def latest_status() -> Dict:
    with liq_lock:
        clusters = list(latest_clusters)
        signal = latest_signal
        candles = list(latest_candles)

    heatmap = build_heatmap(clusters, liq_config.max_heatmap_levels)
    return {
        "symbol": liq_config.symbol,
        "heatmap": heatmap,
        "has_signal": bool(signal),
        "signal": signal.__dict__ if signal else None,
        "cluster_count": len(clusters),
        "recent_candles": [
            {
                "ts": c.ts.isoformat(),
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
            }
            for c in candles[-60:]
        ],
    }


def update_config(cfg: Dict) -> LiquidationConfig:
    global liq_config
    liq_config = liq_config.copy(update=cfg)
    return liq_config

