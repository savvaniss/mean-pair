"""Lightweight adapters for the shared PatternRecognition and Strategy001 examples.

The loop emulates the key decision rules from the provided Freqtrade snippets
and stores both indicator snapshots and simulated trades to the database so the
UI can chart activity and show transaction history.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

import config
from database import AlgoSnapshot, AlgoTrade, SessionLocal

# Strategy identifiers exposed to the frontend
PATTERN_RECOGNITION = "pattern_recognition"
STRATEGY_001 = "strategy001"
STRATEGY_002 = "strategy002"
STRATEGY_003 = "strategy003"
SUPERTREND = "supertrend"


class AlgoConfig(BaseModel):
    enabled: bool = False
    symbol: str = "BTCUSDT"
    timeframe: str = "1d"
    poll_interval_sec: int = 60
    max_position_usd: float = 50.0
    use_testnet: bool = config.BOLL_USE_TESTNET
    # PatternRecognition specific
    buy_threshold: int = -100


class SupertrendConfig(AlgoConfig):
    buy_m1: int = 4
    buy_m2: int = 7
    buy_m3: int = 1
    buy_p1: int = 8
    buy_p2: int = 9
    buy_p3: int = 8
    sell_m1: int = 1
    sell_m2: int = 3
    sell_m3: int = 6
    sell_p1: int = 16
    sell_p2: int = 18
    sell_p3: int = 18


class AlgoState(BaseModel):
    position: str = "FLAT"
    qty_asset: float = 0.0
    entry_price: float = 0.0
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    last_signal: float = 0.0


algo_configs: Dict[str, AlgoConfig] = {
    PATTERN_RECOGNITION: AlgoConfig(
        symbol="BTCUSDT", timeframe="1d", poll_interval_sec=120, max_position_usd=75.0
    ),
    STRATEGY_001: AlgoConfig(
        symbol="ETHUSDT", timeframe="5m", poll_interval_sec=45, max_position_usd=50.0
    ),
    STRATEGY_002: AlgoConfig(
        symbol="ETHUSDT", timeframe="5m", poll_interval_sec=45, max_position_usd=50.0
    ),
    STRATEGY_003: AlgoConfig(
        symbol="BTCUSDT", timeframe="5m", poll_interval_sec=45, max_position_usd=60.0
    ),
    SUPERTREND: SupertrendConfig(
        symbol="BTCUSDT", timeframe="1h", poll_interval_sec=120, max_position_usd=80.0
    ),
}

algo_states: Dict[str, AlgoState] = {
    PATTERN_RECOGNITION: AlgoState(),
    STRATEGY_001: AlgoState(),
    STRATEGY_002: AlgoState(),
    STRATEGY_003: AlgoState(),
    SUPERTREND: AlgoState(),
}

ft_lock = threading.Lock()
ft_thread: Optional[threading.Thread] = None
ft_stop_flag = False
next_runs: Dict[str, float] = {
    PATTERN_RECOGNITION: 0.0,
    STRATEGY_001: 0.0,
    STRATEGY_002: 0.0,
    STRATEGY_003: 0.0,
    SUPERTREND: 0.0,
}


def _get_client():
    return config.boll_client


def _ema(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    window = max(1, min(window, len(values)))
    k = 2 / (window + 1)
    ema = values[-window]
    for price in values[-window + 1 :]:
        ema = price * k + ema * (1 - k)
    return ema


def _sma(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    window = max(1, min(window, len(values)))
    return sum(values[-window:]) / window


def _rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _stochastic(highs: List[float], lows: List[float], closes: List[float], period: int = 14):
    if len(closes) < period:
        return 50.0, 50.0
    high_n = max(highs[-period:])
    low_n = min(lows[-period:])
    k = 0.0 if high_n == low_n else (closes[-1] - low_n) / (high_n - low_n) * 100
    prev_ks = [
        (closes[i] - min(lows[i - period + 1 : i + 1]))
        / (max(highs[i - period + 1 : i + 1]) - min(lows[i - period + 1 : i + 1]) or 1)
        * 100
        for i in range(len(closes) - period, len(closes))
    ]
    d = sum(prev_ks[-3:]) / min(3, len(prev_ks)) if prev_ks else k
    return k, d


def _typical_price(open_p: float, high: float, low: float, close: float) -> float:
    return (high + low + close) / 3


def _bollinger(prices: List[float], window: int = 20, stds: int = 2):
    if len(prices) < window:
        return 0.0, 0.0, 0.0
    slice_ = prices[-window:]
    mean = sum(slice_) / window
    variance = sum((p - mean) ** 2 for p in slice_) / window
    std = math.sqrt(variance)
    upper = mean + stds * std
    lower = mean - stds * std
    return mean, upper, lower


def _inverse_fisher_rsi(rsi: float) -> float:
    scaled = 0.1 * (rsi - 50)
    exp_val = math.exp(2 * scaled)
    return (exp_val - 1) / (exp_val + 1)


def _mfi(highs: List[float], lows: List[float], closes: List[float], volumes: List[float], period: int = 14) -> float:
    if len(closes) < period:
        return 50.0
    typical_prices = [_typical_price(0, h, l, c) for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
    raw_money = [tp * volumes[-period + idx] for idx, tp in enumerate(typical_prices)]
    positive = []
    negative = []
    for i in range(1, len(raw_money)):
        if typical_prices[i] > typical_prices[i - 1]:
            positive.append(raw_money[i])
        else:
            negative.append(raw_money[i])
    pos_flow = sum(positive)
    neg_flow = sum(negative)
    if neg_flow == 0:
        return 100.0
    mfr = pos_flow / neg_flow
    return 100 - (100 / (1 + mfr))


def _sar(highs: List[float], lows: List[float], accel: float = 0.02, accel_max: float = 0.2) -> float:
    # Lightweight SAR approximation using a single pass; sufficient for simulated exits
    if len(highs) < 2:
        return highs[-1] if highs else 0.0
    uptrend = highs[-1] >= highs[-2]
    ep = max(highs[-5:]) if uptrend else min(lows[-5:])
    sar = min(lows[-5:]) if uptrend else max(highs[-5:])
    af = accel
    sar_list = []
    for i in range(len(highs)):
        sar = sar + af * (ep - sar)
        sar_list.append(sar)
        if uptrend and highs[i] > ep:
            ep = highs[i]
            af = min(af + accel, accel_max)
        elif not uptrend and lows[i] < ep:
            ep = lows[i]
            af = min(af + accel, accel_max)
    return sar_list[-1]


def _heikin_ashi(candles: List[List[float]]):
    if not candles:
        return 0.0, 0.0
    ha_close = sum(candles[-1][1:5]) / 4
    prev_ha_open = sum(candles[-2][1:3]) / 2 if len(candles) > 1 else ha_close
    ha_open = (prev_ha_open + ha_close) / 2
    return ha_open, ha_close


def _highwave_score(open_p: float, high: float, low: float, close: float) -> int:
    body = abs(close - open_p)
    upper_shadow = high - max(open_p, close)
    lower_shadow = min(open_p, close) - low
    if body <= 0:
        body = 1e-9
    long_shadows = upper_shadow >= 2 * body and lower_shadow >= 2 * body
    if not long_shadows:
        return 0
    return -100 if close < open_p else 100


def _hammer_score(open_p: float, high: float, low: float, close: float) -> int:
    body = abs(close - open_p)
    upper_shadow = high - max(open_p, close)
    lower_shadow = min(open_p, close) - low
    if body <= 0:
        body = 1e-9
    long_lower = lower_shadow >= 2 * body
    tiny_upper = upper_shadow <= body * 0.3
    if long_lower and tiny_upper:
        return 100 if close > open_p else -100
    return 0


def _fetch_klines(symbol: str, interval: str, limit: int = 120) -> List[List[float]]:
    client = _get_client()
    if client is None or config.DISABLE_BINANCE_CLIENT:
        now = int(time.time() * 1000)
        synthetic = []
        for i in range(limit):
            base = 100 + math.sin(i / 5) * 2
            synthetic.append(
                [
                    now - (limit - i) * 60_000,
                    base,
                    base + 1,
                    base - 1,
                    base + math.sin(i) * 0.3,
                    1.0,
                ]
            )
        return synthetic

    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return [[float(x) if i > 0 else x for i, x in enumerate(item)] for item in raw]


def _record_snapshot(
    session, strategy: str, symbol: str, price: float, indicators: List[float]
):
    padded = indicators + [0.0] * max(0, 4 - len(indicators))
    snap = AlgoSnapshot(
        ts=datetime.utcnow(),
        strategy=strategy,
        symbol=symbol,
        price=price,
        indicator_a=padded[0],
        indicator_b=padded[1],
        indicator_c=padded[2],
        indicator_d=padded[3],
    )
    session.add(snap)


def _execute_trade(session, strategy: str, symbol: str, side: str, price: float, qty: float, cfg: AlgoConfig, state: AlgoState):
    notional = qty * price
    pnl = 0.0
    if side == "SELL":
        pnl = (price - state.entry_price) * qty
        state.realized_pnl_usd += pnl
        state.position = "FLAT"
        state.qty_asset = 0.0
        state.entry_price = 0.0
        state.unrealized_pnl_usd = 0.0
    else:
        state.position = "LONG"
        state.qty_asset = qty
        state.entry_price = price
        state.unrealized_pnl_usd = 0.0

    trade = AlgoTrade(
        ts=datetime.utcnow(),
        strategy=strategy,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        notional=notional,
        pnl_usd=pnl,
        is_testnet=1 if cfg.use_testnet else 0,
    )
    session.add(trade)


def _process_pattern(cfg: AlgoConfig, state: AlgoState, session) -> None:
    candles = _fetch_klines(cfg.symbol, cfg.timeframe, limit=60)
    if not candles:
        return

    last = candles[-1]
    open_p, high, low, close = last[1], last[2], last[3], last[4]
    signal = _highwave_score(open_p, high, low, close)
    state.last_signal = signal
    price = close

    if state.position == "LONG":
        state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset

    _record_snapshot(session, PATTERN_RECOGNITION, cfg.symbol, price, [signal])

    if state.position == "FLAT" and signal == cfg.buy_threshold:
        qty = cfg.max_position_usd / price if price > 0 else 0.0
        if qty > 0:
            _execute_trade(
                session,
                PATTERN_RECOGNITION,
                cfg.symbol,
                "BUY",
                price,
                qty,
                cfg,
                state,
            )
    elif state.position == "LONG" and signal > 0:
        qty = state.qty_asset
        if qty > 0:
            _execute_trade(
                session,
                PATTERN_RECOGNITION,
                cfg.symbol,
                "SELL",
                price,
                qty,
                cfg,
                state,
            )


def _process_strategy001(cfg: AlgoConfig, state: AlgoState, session) -> None:
    candles = _fetch_klines(cfg.symbol, cfg.timeframe, limit=150)
    if len(candles) < 120:
        return

    closes = [c[4] for c in candles]
    price = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema100 = _ema(closes, 100)
    prev_ema20 = _ema(closes[:-1], 20)
    prev_ema50 = _ema(closes[:-1], 50)
    ha_open, ha_close = _heikin_ashi(candles[-3:])

    if state.position == "LONG":
        state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset

    _record_snapshot(
        session,
        STRATEGY_001,
        cfg.symbol,
        price,
        [ema20, ema50, ema100, ha_close],
    )

    crossed_up = prev_ema20 <= prev_ema50 and ema20 > ema50
    crossed_down = prev_ema50 <= _ema(closes[:-1], 100) and ema50 > ema100

    green_bar = ha_close > ha_open
    red_bar = ha_close < ha_open

    if state.position == "FLAT" and crossed_up and ha_close > ema20 and green_bar:
        qty = cfg.max_position_usd / price if price > 0 else 0.0
        if qty > 0:
            _execute_trade(session, STRATEGY_001, cfg.symbol, "BUY", price, qty, cfg, state)
    elif (
        state.position == "LONG"
        and crossed_down
        and ha_close < ema20
        and red_bar
        and state.qty_asset > 0
    ):
        _execute_trade(
            session, STRATEGY_001, cfg.symbol, "SELL", price, state.qty_asset, cfg, state
        )


def _process_strategy002(cfg: AlgoConfig, state: AlgoState, session) -> None:
    candles = _fetch_klines(cfg.symbol, cfg.timeframe, limit=160)
    if len(candles) < 40:
        return

    opens = [c[1] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    price = closes[-1]
    rsi = _rsi(closes)
    slowk, _ = _stochastic(highs, lows, closes)
    _, _, bb_lower = _bollinger([
        _typical_price(c[1], c[2], c[3], c[4]) for c in candles
    ])
    fisher_rsi = _inverse_fisher_rsi(rsi)
    sar = _sar(highs, lows)
    hammer = _hammer_score(opens[-1], highs[-1], lows[-1], closes[-1])

    if state.position == "LONG":
        state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset

    _record_snapshot(
        session,
        STRATEGY_002,
        cfg.symbol,
        price,
        [rsi, slowk, bb_lower, sar],
    )
    state.last_signal = fisher_rsi

    if (
        state.position == "FLAT"
        and rsi < 30
        and slowk < 20
        and bb_lower > price
        and hammer == 100
    ):
        qty = cfg.max_position_usd / price if price > 0 else 0.0
        if qty > 0:
            _execute_trade(session, STRATEGY_002, cfg.symbol, "BUY", price, qty, cfg, state)
    elif state.position == "LONG" and sar > price and fisher_rsi > 0.3 and state.qty_asset > 0:
        _execute_trade(
            session, STRATEGY_002, cfg.symbol, "SELL", price, state.qty_asset, cfg, state
        )


def _process_strategy003(cfg: AlgoConfig, state: AlgoState, session) -> None:
    candles = _fetch_klines(cfg.symbol, cfg.timeframe, limit=200)
    if len(candles) < 60:
        return

    opens = [c[1] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    volumes = [c[5] for c in candles]

    price = closes[-1]
    rsi = _rsi(closes)
    fisher_rsi = _inverse_fisher_rsi(rsi)
    fastk, fastd = _stochastic(highs, lows, closes)
    _, _, boll_lower = _bollinger([
        _typical_price(c[1], c[2], c[3], c[4]) for c in candles
    ])
    ema5 = _ema(closes, 5)
    ema10 = _ema(closes, 10)
    ema50 = _ema(closes, 50)
    ema100 = _ema(closes, 100)
    sma40 = _sma(closes, 40)
    sar = _sar(highs, lows)
    mfi = _mfi(highs, lows, closes, volumes)

    if state.position == "LONG":
        state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset

    _record_snapshot(
        session,
        STRATEGY_003,
        cfg.symbol,
        price,
        [rsi, fisher_rsi, ema50, ema100],
    )
    state.last_signal = fisher_rsi

    crossed = ema50 > ema100 or ema5 > ema10
    if (
        state.position == "FLAT"
        and 0 < rsi < 28
        and price < sma40
        and fisher_rsi < -0.94
        and mfi < 16
        and crossed
        and fastd > fastk
        and fastd > 0
    ):
        qty = cfg.max_position_usd / price if price > 0 else 0.0
        if qty > 0:
            _execute_trade(session, STRATEGY_003, cfg.symbol, "BUY", price, qty, cfg, state)
    elif state.position == "LONG" and sar > price and fisher_rsi > 0.3 and state.qty_asset > 0:
        _execute_trade(
            session, STRATEGY_003, cfg.symbol, "SELL", price, state.qty_asset, cfg, state
        )


def _supertrend_lines(highs: List[float], lows: List[float], closes: List[float], multiplier: int, period: int):
    if len(closes) < period + 1:
        return [0.0] * len(closes), ["down"] * len(closes)

    trs = [highs[i] - lows[i] for i in range(len(highs))]
    atrs = []
    for i in range(len(trs)):
        window = trs[max(0, i - period + 1) : i + 1]
        atrs.append(sum(window) / len(window))

    final_ub = [0.0] * len(closes)
    final_lb = [0.0] * len(closes)
    supertrend_vals = [0.0] * len(closes)
    stx = ["down"] * len(closes)

    for i in range(period, len(closes)):
        basic_ub = (highs[i] + lows[i]) / 2 + multiplier * atrs[i]
        basic_lb = (highs[i] + lows[i]) / 2 - multiplier * atrs[i]

        final_ub[i] = (
            basic_ub
            if i == period
            else basic_ub
            if basic_ub < final_ub[i - 1] or closes[i - 1] > final_ub[i - 1]
            else final_ub[i - 1]
        )
        final_lb[i] = (
            basic_lb
            if i == period
            else basic_lb
            if basic_lb > final_lb[i - 1] or closes[i - 1] < final_lb[i - 1]
            else final_lb[i - 1]
        )

        if supertrend_vals[i - 1] == final_ub[i - 1] and closes[i] <= final_ub[i]:
            supertrend_vals[i] = final_ub[i]
        elif supertrend_vals[i - 1] == final_ub[i - 1] and closes[i] > final_ub[i]:
            supertrend_vals[i] = final_lb[i]
        elif supertrend_vals[i - 1] == final_lb[i - 1] and closes[i] >= final_lb[i]:
            supertrend_vals[i] = final_lb[i]
        else:
            supertrend_vals[i] = final_ub[i]

        stx[i] = "down" if closes[i] < supertrend_vals[i] else "up"

    return supertrend_vals, stx


def _process_supertrend(cfg: SupertrendConfig, state: AlgoState, session) -> None:
    candles = _fetch_klines(cfg.symbol, cfg.timeframe, limit=260)
    if len(candles) < 220:
        return

    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    price = closes[-1]

    # Compute selected parameter combinations only (not full ranges)
    _, stx1 = _supertrend_lines(highs, lows, closes, cfg.buy_m1, cfg.buy_p1)
    _, stx2 = _supertrend_lines(highs, lows, closes, cfg.buy_m2, cfg.buy_p2)
    _, stx3 = _supertrend_lines(highs, lows, closes, cfg.buy_m3, cfg.buy_p3)
    _, stx1_sell = _supertrend_lines(highs, lows, closes, cfg.sell_m1, cfg.sell_p1)
    _, stx2_sell = _supertrend_lines(highs, lows, closes, cfg.sell_m2, cfg.sell_p2)
    _, stx3_sell = _supertrend_lines(highs, lows, closes, cfg.sell_m3, cfg.sell_p3)

    buy_up = (
        stx1[-1] == "up"
        and stx2[-1] == "up"
        and stx3[-1] == "up"
    )
    sell_down = (
        stx1_sell[-1] == "down"
        and stx2_sell[-1] == "down"
        and stx3_sell[-1] == "down"
    )

    if state.position == "LONG":
        state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset

    st_score = (1 if stx1[-1] == "up" else -1) + (1 if stx2[-1] == "up" else -1) + (1 if stx3[-1] == "up" else -1)
    _record_snapshot(
        session,
        SUPERTREND,
        cfg.symbol,
        price,
        [st_score, 0.0, 0.0, 0.0],
    )
    state.last_signal = st_score

    if state.position == "FLAT" and buy_up:
        qty = cfg.max_position_usd / price if price > 0 else 0.0
        if qty > 0:
            _execute_trade(session, SUPERTREND, cfg.symbol, "BUY", price, qty, cfg, state)
    elif state.position == "LONG" and sell_down and state.qty_asset > 0:
        _execute_trade(
            session, SUPERTREND, cfg.symbol, "SELL", price, state.qty_asset, cfg, state
        )


def freqtrade_loop():
    global ft_stop_flag
    session = SessionLocal()
    try:
        while not ft_stop_flag:
            now = time.time()
            for strategy, cfg in algo_configs.items():
                if now < next_runs[strategy]:
                    continue

                # Ensure environment toggles propagate
                if cfg.use_testnet != config.BOLL_USE_TESTNET:
                    config.switch_boll_env(cfg.use_testnet)

                state = algo_states[strategy]
                if not cfg.enabled or not cfg.symbol:
                    next_runs[strategy] = now + max(5, cfg.poll_interval_sec)
                    continue

                if strategy == PATTERN_RECOGNITION:
                    _process_pattern(cfg, state, session)
                elif strategy == STRATEGY_001:
                    _process_strategy001(cfg, state, session)
                elif strategy == STRATEGY_002:
                    _process_strategy002(cfg, state, session)
                elif strategy == STRATEGY_003:
                    _process_strategy003(cfg, state, session)
                else:
                    _process_supertrend(cfg, state, session)  # type: ignore[arg-type]

                next_runs[strategy] = now + max(5, cfg.poll_interval_sec)

            session.commit()
            time.sleep(1)
    finally:
        session.close()


def start_freqtrade_thread():
    global ft_thread, ft_stop_flag
    if ft_thread and ft_thread.is_alive():
        return
    ft_stop_flag = False
    ft_thread = threading.Thread(target=freqtrade_loop, daemon=True)
    ft_thread.start()


def stop_freqtrade_thread():
    global ft_stop_flag
    ft_stop_flag = True
    if ft_thread:
        ft_thread.join(timeout=1)


def update_config(strategy: str, data: dict) -> AlgoConfig:
    if strategy not in algo_configs:
        raise ValueError("Unknown strategy")
    cfg = algo_configs[strategy]
    env_changed = False
    if "use_testnet" in data and data["use_testnet"] != cfg.use_testnet:
        env_changed = True
        config.switch_boll_env(bool(data["use_testnet"]))

    for field, value in data.items():
        if hasattr(cfg, field):
            setattr(cfg, field, value)

    if env_changed:
        cfg.use_testnet = data["use_testnet"]

    return cfg


def set_enabled(strategy: str, enabled: bool) -> None:
    if strategy not in algo_configs:
        raise ValueError("Unknown strategy")
    algo_configs[strategy].enabled = enabled


def get_status() -> List[dict]:
    results = []
    session = SessionLocal()
    try:
        for strategy, cfg in algo_configs.items():
            state = algo_states[strategy]
            last_snap = (
                session.query(AlgoSnapshot)
                .filter(AlgoSnapshot.strategy == strategy)
                .order_by(AlgoSnapshot.ts.desc())
                .first()
            )
            results.append(
                {
                    "strategy": strategy,
                    "symbol": cfg.symbol,
                    "price": last_snap.price if last_snap else 0.0,
                    "last_signal": state.last_signal,
                    "position": state.position,
                    "qty_asset": state.qty_asset,
                    "entry_price": state.entry_price,
                    "realized_pnl_usd": state.realized_pnl_usd,
                    "unrealized_pnl_usd": state.unrealized_pnl_usd,
                    "enabled": cfg.enabled,
                    "use_testnet": cfg.use_testnet,
                    "timeframe": cfg.timeframe,
                }
            )
        session.commit()
        return results
    finally:
        session.close()


def get_history(strategy: str, limit: int = 200) -> List[AlgoSnapshot]:
    session = SessionLocal()
    try:
        rows = (
            session.query(AlgoSnapshot)
            .filter(AlgoSnapshot.strategy == strategy)
            .order_by(AlgoSnapshot.ts.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))
    finally:
        session.close()


def get_trades(strategy: Optional[str] = None, limit: int = 200) -> List[AlgoTrade]:
    session = SessionLocal()
    try:
        query = session.query(AlgoTrade).order_by(AlgoTrade.ts.desc())
        if strategy:
            query = query.filter(AlgoTrade.strategy == strategy)
        return list(reversed(query.limit(limit).all()))
    finally:
        session.close()
