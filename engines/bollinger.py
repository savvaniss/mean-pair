# engines/bollinger.py
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

from binance.exceptions import BinanceAPIException
from pydantic import BaseModel

import config
from database import SessionLocal, BollState, BollTrade

# Bollinger in-memory history
boll_ts_history: List[datetime] = []
boll_price_history: List[float] = []
BOLL_MAX_HISTORY = 500
MIN_HISTORY_FRACTION = 0.5

boll_lock = threading.Lock()
boll_thread: Optional[threading.Thread] = None
boll_stop_flag = False
boll_last_trade_ts: float = 0.0
current_boll_symbol: str = ""


class BollConfig(BaseModel):
    enabled: bool = False
    symbol: str = "BNBUSDC"
    poll_interval_sec: int = 20
    window_size: int = 70
    num_std: float = 3.0
    max_position_usd: float = 50.0
    use_all_balance: bool = True
    stop_loss_pct: float = 0.15
    take_profit_pct: float = 0.15
    cooldown_sec: int = 80


boll_config = BollConfig()


def boll_required_history_len() -> int:
    return max(5, int(boll_config.window_size * MIN_HISTORY_FRACTION))


def boll_has_enough_history() -> bool:
    return len(boll_price_history) >= boll_required_history_len()


# ========== Binance helpers ==========


def get_symbol_price_boll(symbol: str) -> float:
    ticker = config.boll_client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def get_free_balance_boll(asset: str) -> float:
    acc = config.boll_client.get_account()
    for b in acc["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


def adjust_quantity_boll(symbol: str, qty: float) -> float:
    info = config.boll_client.get_symbol_info(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot_filter["stepSize"])
    min_qty = float(lot_filter["minQty"])

    steps = int(qty / step_size)
    adj = steps * step_size
    if adj < min_qty:
        return 0.0
    return adj


def parse_symbol_assets(symbol: str) -> Tuple[str, str]:
    info = config.boll_client.get_symbol_info(symbol)
    return info["baseAsset"], info["quoteAsset"]


def place_market_order_boll(symbol: str, side: str, quantity: float):
    try:
        return config.boll_client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"[BOLL] Binance error: {e}")
        return None


# ========== Bands / history ==========


def compute_ma_std_window(prices: List[float], window: int):
    if not prices:
        return 0.0, 0.0
    w = prices[-window:] if len(prices) > window else prices
    mean_p = sum(w) / len(w)
    var = sum((p - mean_p) ** 2 for p in w) / len(w)
    std = var ** 0.5 if var > 0 else 0.0
    return mean_p, std


def get_boll_state(session) -> BollState:
    st = session.query(BollState).first()
    if not st:
        st = BollState(
            symbol=boll_config.symbol or "",
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


# ========== Bot loop ==========


def boll_loop():
    global boll_stop_flag, boll_last_trade_ts
    session = SessionLocal()
    try:
        while not boll_stop_flag:
            if not boll_config.enabled or not boll_config.symbol:
                time.sleep(1)
                continue

            try:
                ts = datetime.utcnow()
                symbol = boll_config.symbol

                price = get_symbol_price_boll(symbol)
                base_asset, quote_asset = parse_symbol_assets(symbol)

                with boll_lock:
                    boll_ts_history.append(ts)
                    boll_price_history.append(price)
                    if len(boll_price_history) > BOLL_MAX_HISTORY:
                        del boll_price_history[
                            0 : len(boll_price_history) - BOLL_MAX_HISTORY
                        ]
                        del boll_ts_history[0 : len(boll_ts_history) - BOLL_MAX_HISTORY]

                    history_ok = boll_has_enough_history()
                    ma, std = compute_ma_std_window(
                        boll_price_history, max(5, boll_config.window_size)
                    )
                    upper = ma + boll_config.num_std * std
                    lower = ma - boll_config.num_std * std

                state = get_boll_state(session)
                state.symbol = symbol

                if state.position == "LONG" and state.qty_asset > 0 and state.entry_price > 0:
                    state.unrealized_pnl_usd = (
                        price - state.entry_price
                    ) * state.qty_asset
                else:
                    state.unrealized_pnl_usd = 0.0

                if not history_ok:
                    session.commit()
                    time.sleep(boll_config.poll_interval_sec)
                    continue

                now_ts = time.time()
                if now_ts - boll_last_trade_ts < boll_config.cooldown_sec:
                    session.commit()
                    time.sleep(boll_config.poll_interval_sec)
                    continue

                action = None  # "BUY" or "SELL"

                if state.position == "LONG" and state.qty_asset > 0:
                    if (
                        boll_config.stop_loss_pct > 0
                        and price <= state.entry_price * (1 - boll_config.stop_loss_pct)
                    ):
                        action = "SELL"
                    elif (
                        boll_config.take_profit_pct > 0
                        and price >= state.entry_price * (1 + boll_config.take_profit_pct)
                    ):
                        action = "SELL"
                    elif price > upper:
                        action = "SELL"
                else:
                    if price < lower:
                        action = "BUY"

                if action == "BUY":
                    quote_bal = get_free_balance_boll(quote_asset)
                    if quote_bal > 0:
                        notional = min(quote_bal, boll_config.max_position_usd)
                        if notional > 0:
                            qty = notional / price
                            qty = adjust_quantity_boll(symbol, qty)
                            if qty > 0:
                                order = place_market_order_boll(symbol, "BUY", qty)
                                if order:
                                    notional_filled = qty * price
                                    state.position = "LONG"
                                    state.qty_asset = qty
                                    state.entry_price = price
                                    state.unrealized_pnl_usd = 0.0
                                    tr = BollTrade(
                                        ts=ts,
                                        symbol=symbol,
                                        side="BUY",
                                        qty=qty,
                                        price=price,
                                        notional=notional_filled,
                                        pnl_usd=0.0,
                                        is_testnet=int(config.USE_TESTNET),
                                    )
                                    session.add(tr)
                                    boll_last_trade_ts = now_ts

                elif (
                    action == "SELL"
                    and state.position == "LONG"
                    and state.qty_asset > 0
                ):
                    free_base = get_free_balance_boll(base_asset)
                    qty_req = min(state.qty_asset, free_base)
                    qty = adjust_quantity_boll(symbol, qty_req)

                    if qty <= 0:
                        print(
                            f"Bollinger: qty {state.qty_asset} is dust (below LOT_SIZE). "
                            "Marking position as FLAT."
                        )
                        if state.entry_price > 0 and state.qty_asset > 0:
                            pnl_dust = (price - state.entry_price) * state.qty_asset
                            state.realized_pnl_usd += pnl_dust
                        state.qty_asset = 0.0
                        state.position = "FLAT"
                        state.entry_price = 0.0
                        state.unrealized_pnl_usd = 0.0
                    else:
                        order = place_market_order_boll(symbol, "SELL", qty)
                        if order:
                            filled_qty = qty
                            notional_filled = filled_qty * price
                            pnl = (price - state.entry_price) * filled_qty
                            state.realized_pnl_usd += pnl
                            state.qty_asset -= filled_qty
                            if state.qty_asset < 1e-12:
                                state.qty_asset = 0.0
                                state.position = "FLAT"
                                state.entry_price = 0.0
                            state.unrealized_pnl_usd = 0.0
                            tr = BollTrade(
                                ts=ts,
                                symbol=symbol,
                                side="SELL",
                                qty=filled_qty,
                                price=price,
                                notional=notional_filled,
                                pnl_usd=pnl,
                                is_testnet=int(config.USE_TESTNET),
                            )
                            session.add(tr)
                            boll_last_trade_ts = now_ts

                session.commit()

            except Exception as e:
                print(f"Error in Bollinger loop: {e}")
                session.rollback()

            time.sleep(boll_config.poll_interval_sec)
    finally:
        session.close()


def start_boll_thread():
    global boll_thread, boll_stop_flag
    if boll_thread and boll_thread.is_alive():
        return
    boll_stop_flag = False
    boll_thread = threading.Thread(target=boll_loop, daemon=True)
    boll_thread.start()


def stop_boll_thread():
    global boll_stop_flag
    boll_stop_flag = True
