"""Trend-following engine using EMA crossovers with ATR-based risk limits."""

import threading
import time
from datetime import datetime
from typing import List, Optional

from binance.exceptions import BinanceAPIException
from pydantic import BaseModel

import config
from database import SessionLocal, TrendSnapshot, TrendState, TrendTrade
from engines.common import clamp_to_step

tf_price_history: List[float] = []
tf_ts_history: List[datetime] = []
TF_MAX_HISTORY = 500

tf_lock = threading.Lock()
tf_thread: Optional[threading.Thread] = None
tf_stop_flag = False
tf_last_trade_ts: float = 0.0
current_trend_symbol: str = ""


def _ema(prices: List[float], window: int) -> float:
    if not prices:
        return 0.0
    window = max(1, min(window, len(prices)))
    k = 2 / (window + 1)
    ema = prices[-window]
    for price in prices[-window + 1 :]:
        ema = price * k + ema * (1 - k)
    return ema


def _atr(prices: List[float], window: int) -> float:
    if len(prices) < 2:
        return 0.0
    window = max(1, min(window, len(prices) - 1))
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    return sum(trs[-window:]) / window if trs else 0.0


class TrendConfig(BaseModel):
    enabled: bool = False
    symbol: str = "BTCUSDT"
    poll_interval_sec: int = 20
    fast_window: int = 12
    slow_window: int = 26
    atr_window: int = 14
    atr_stop_mult: float = 2.0
    max_position_usd: float = 100.0
    use_all_balance: bool = True
    cooldown_sec: int = 60
    use_testnet: bool = config.BOLL_USE_TESTNET


trend_config = TrendConfig()


def get_symbol_price(symbol: str) -> float:
    try:
        ticker = config.boll_client.get_symbol_ticker(symbol=symbol)
    except TypeError:
        ticker = config.boll_client.get_symbol_ticker(symbol)
    return float(ticker["price"])


def get_symbol_info(symbol: str):
    return config.boll_client.get_symbol_info(symbol)


def parse_symbol_assets(symbol: str):
    info = get_symbol_info(symbol)
    return info["baseAsset"], info["quoteAsset"]


def get_free_balance(asset: str) -> float:
    acc = config.boll_client.get_account()
    for bal in acc["balances"]:
        if bal["asset"] == asset:
            return float(bal["free"])
    return 0.0


def adjust_quantity(symbol: str, qty: float) -> float:
    info = get_symbol_info(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    return clamp_to_step(qty, lot_filter["stepSize"], lot_filter["minQty"])


def place_market_order(symbol: str, side: str, quantity: float):
    try:
        return config.boll_client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"[TREND] Binance error: {e}")
        return None


def get_trend_state(session) -> TrendState:
    st = session.query(TrendState).first()
    if not st:
        st = TrendState(
            symbol=trend_config.symbol or "",
            position="FLAT",
            qty_asset=0.0,
            entry_price=0.0,
            realized_pnl_usd=0.0,
            unrealized_pnl_usd=0.0,
        )
        session.add(st)
        session.commit()
        session.refresh(st)
    return st


def _compute_signals() -> tuple[float, float, float]:
    with tf_lock:
        prices = list(tf_price_history)
    fast = _ema(prices, trend_config.fast_window)
    slow = _ema(prices, trend_config.slow_window)
    atr = _atr(prices, trend_config.atr_window)
    return fast, slow, atr


def trend_loop():
    global tf_stop_flag, tf_last_trade_ts
    session = SessionLocal()
    try:
        while not tf_stop_flag:
            if not trend_config.enabled or not trend_config.symbol:
                time.sleep(1)
                continue

            ts = datetime.utcnow()
            symbol = trend_config.symbol
            price = get_symbol_price(symbol)
            base_asset, quote_asset = parse_symbol_assets(symbol)

            with tf_lock:
                tf_ts_history.append(ts)
                tf_price_history.append(price)
                if len(tf_price_history) > TF_MAX_HISTORY:
                    del tf_price_history[0 : len(tf_price_history) - TF_MAX_HISTORY]
                    del tf_ts_history[0 : len(tf_ts_history) - TF_MAX_HISTORY]

            fast, slow, atr = _compute_signals()

            snap = TrendSnapshot(
                ts=ts,
                symbol=symbol,
                price=price,
                fast_ema=fast,
                slow_ema=slow,
                atr=atr,
            )
            session.add(snap)

            state = get_trend_state(session)
            state.symbol = symbol
            if state.position == "LONG" and state.qty_asset > 0 and state.entry_price > 0:
                state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset
            else:
                state.unrealized_pnl_usd = 0.0

            if len(tf_price_history) < max(trend_config.fast_window, trend_config.slow_window):
                session.commit()
                time.sleep(trend_config.poll_interval_sec)
                continue

            now_ts = time.time()
            if now_ts - tf_last_trade_ts < trend_config.cooldown_sec:
                session.commit()
                time.sleep(trend_config.poll_interval_sec)
                continue

            action: Optional[str] = None
            if state.position == "LONG" and state.qty_asset > 0:
                atr_stop = state.entry_price - trend_config.atr_stop_mult * atr
                if atr > 0 and price <= atr_stop:
                    action = "SELL"
                elif fast < slow:
                    action = "SELL"
            else:
                if fast > slow:
                    action = "BUY"

            if action == "BUY":
                quote_bal = get_free_balance(quote_asset)
                spendable = quote_bal if trend_config.use_all_balance else trend_config.max_position_usd
                if spendable > 0:
                    qty = spendable / price
                    qty = adjust_quantity(symbol, qty)
                    if qty > 0:
                        order = place_market_order(symbol, "BUY", qty)
                        if order:
                            filled_quote = float(order.get("cummulativeQuoteQty", qty * price))
                            filled_qty = float(order.get("executedQty", qty))
                            notional = filled_quote if filled_quote > 0 else qty * price
                            state.position = "LONG"
                            state.qty_asset = filled_qty
                            state.entry_price = price
                            state.unrealized_pnl_usd = 0.0
                            tr = TrendTrade(
                                ts=ts,
                                symbol=symbol,
                                side="BUY",
                                qty=filled_qty,
                                price=price,
                                notional=notional,
                                pnl_usd=0.0,
                                is_testnet=1 if trend_config.use_testnet else 0,
                            )
                            session.add(tr)
                            tf_last_trade_ts = now_ts

            elif action == "SELL" and state.qty_asset > 0:
                qty = adjust_quantity(symbol, state.qty_asset)
                if qty > 0:
                    order = place_market_order(symbol, "SELL", qty)
                    if order:
                        filled_quote = float(order.get("cummulativeQuoteQty", qty * price))
                        pnl = filled_quote - state.entry_price * qty
                        tr = TrendTrade(
                            ts=ts,
                            symbol=symbol,
                            side="SELL",
                            qty=qty,
                            price=price,
                            notional=filled_quote,
                            pnl_usd=pnl,
                            is_testnet=1 if trend_config.use_testnet else 0,
                        )
                        state.realized_pnl_usd += pnl
                        state.position = "FLAT"
                        state.qty_asset = 0.0
                        state.entry_price = 0.0
                        state.unrealized_pnl_usd = 0.0
                        session.add(tr)
                        tf_last_trade_ts = now_ts

            session.commit()
            time.sleep(trend_config.poll_interval_sec)
    finally:
        session.close()


def start_trend_thread():
    global tf_thread, tf_stop_flag
    if tf_thread and tf_thread.is_alive():
        return
    tf_stop_flag = False
    tf_thread = threading.Thread(target=trend_loop, daemon=True)
    tf_thread.start()


def stop_trend_thread():
    global tf_stop_flag
    tf_stop_flag = True
    if tf_thread:
        tf_thread.join(timeout=1)
