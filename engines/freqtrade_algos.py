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


class AlgoConfig(BaseModel):
    enabled: bool = False
    symbol: str = "BTCUSDT"
    timeframe: str = "1d"
    poll_interval_sec: int = 60
    max_position_usd: float = 50.0
    use_testnet: bool = config.BOLL_USE_TESTNET
    # PatternRecognition specific
    buy_threshold: int = -100


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
}

algo_states: Dict[str, AlgoState] = {
    PATTERN_RECOGNITION: AlgoState(),
    STRATEGY_001: AlgoState(),
}

ft_lock = threading.Lock()
ft_thread: Optional[threading.Thread] = None
ft_stop_flag = False
next_runs: Dict[str, float] = {PATTERN_RECOGNITION: 0.0, STRATEGY_001: 0.0}


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
                else:
                    _process_strategy001(cfg, state, session)

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
