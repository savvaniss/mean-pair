# engines/bollinger.py
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

from binance.exceptions import BinanceAPIException
from pydantic import BaseModel

import config
from database import SessionLocal, BollState, BollTrade, BollSnapshot
from engines.common import clamp_to_step, compute_ma_std_window

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
    use_testnet: bool = config.USE_TESTNET


boll_config = BollConfig()


def boll_required_history_len() -> int:
    return max(5, int(boll_config.window_size * MIN_HISTORY_FRACTION))


def boll_has_enough_history() -> bool:
    return len(boll_price_history) >= boll_required_history_len()


# ========== Binance helpers ==========
def get_symbol_price_boll(symbol: str) -> float:
    """
    Return latest price for the given symbol using the Bollinger client.

    Supports both:
      - Real Binance Client (expects keyword 'symbol')
      - Test monkeypatches that define get_symbol_ticker(symbol) positional-only
    """
    # Try keyword (real Binance client)
    try:
        ticker = config.boll_client.get_symbol_ticker(symbol=symbol)
    except TypeError:
        # Fallback for tests where get_symbol_ticker is monkeypatched
        ticker = config.boll_client.get_symbol_ticker(symbol)

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
    step_size = lot_filter["stepSize"]
    min_qty = lot_filter["minQty"]

    return clamp_to_step(qty, step_size, min_qty)


def _min_notional(symbol: str) -> float:
    info = config.boll_client.get_symbol_info(symbol)
    min_notional_filter = next(
        (f for f in info["filters"] if f["filterType"] == "MIN_NOTIONAL"), None
    )
    return float(min_notional_filter.get("minNotional", 0.0)) if min_notional_filter else 0.0


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

                snap = BollSnapshot(
                    ts=ts,
                    symbol=symbol,
                    price=price,
                    ma=ma,
                    upper=upper,
                    lower=lower,
                    std=std,
                )
                session.add(snap)

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
                        spendable = min(quote_bal * 0.98, boll_config.max_position_usd)
                        min_notional = _min_notional(symbol)
                        if spendable >= min_notional:
                            qty = spendable / price
                            qty = adjust_quantity_boll(symbol, qty)
                            if qty > 0 and qty * price >= min_notional:
                                order = place_market_order_boll(symbol, "BUY", qty)
                                if order:
                                    filled_quote = float(order.get("cummulativeQuoteQty", qty * price))
                                    filled_qty = float(order.get("executedQty", qty))
                                    notional_filled = filled_quote if filled_quote > 0 else qty * price
                                    state.position = "LONG"
                                    state.qty_asset = filled_qty
                                    state.entry_price = price
                                    state.unrealized_pnl_usd = 0.0
                                    tr = BollTrade(
                                        ts=ts,
                                        symbol=symbol,
                                        side="BUY",
                                        qty=filled_qty,
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
                        min_notional = _min_notional(symbol)
                        if qty * price < min_notional:
                            print(
                                f"Bollinger: qty {qty} below MIN_NOTIONAL for {symbol}, flattening state"
                            )
                            state.qty_asset = 0.0
                            state.position = "FLAT"
                            state.entry_price = 0.0
                        else:
                            order = place_market_order_boll(symbol, "SELL", qty)
                            if order:
                                filled_qty = float(order.get("executedQty", qty))
                                notional_filled = float(order.get("cummulativeQuoteQty", qty * price))
                                pnl = (price - state.entry_price) * filled_qty
                                state.realized_pnl_usd += pnl
                                state.qty_asset = max(0.0, state.qty_asset - filled_qty)
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
        
def boll_history(limit: int = 200):
    """
    Return recent Bollinger history with computed bands.
    Used by tests and UI.
    """
    hist = []
    n = len(boll_price_history)
    if n == 0:
        return hist

    start = max(0, n - limit)
    prices = boll_price_history[start:]
    times = boll_ts_history[start:]

    for i in range(len(prices)):
        window_prices = prices[max(0, i - boll_config.window_size + 1) : i + 1]
        ma, std = compute_ma_std_window(window_prices, window=len(window_prices))
        upper = ma + boll_config.num_std * std
        lower = ma - boll_config.num_std * std
        hist.append(
            BollHistoryItem(
                ts=times[i],
                price=prices[i],
                ma=ma,
                upper=upper,
                lower=lower,
            )
        )

    return hist


def generate_best_boll_config_from_history(
    session, symbol: Optional[str] = None, lookback: int = 400
) -> BollConfig:
    """Suggest conservative Bollinger parameters derived from stored history."""

    target_symbol = symbol or boll_config.symbol
    if not target_symbol:
        raise ValueError("Set a symbol first to generate config")

    rows = (
        session.query(BollSnapshot)
        .filter(BollSnapshot.symbol == target_symbol)
        .order_by(BollSnapshot.ts.desc())
        .limit(lookback)
        .all()
    )

    if len(rows) < 10:
        raise ValueError("Not enough history to suggest config")

    zscores = [
        abs((r.price - r.ma) / r.std)
        for r in rows
        if r.std is not None and r.std > 0
    ]

    if not zscores:
        raise ValueError("Historical bands have zero standard deviation")

    zscores.sort()
    idx = max(0, int(len(zscores) * 0.85) - 1)
    num_std = max(1.5, min(4.0, zscores[idx]))

    window_guess = min(max(20, len(rows) // 3), 500)

    cfg = BollConfig(**boll_config.dict())
    cfg.symbol = target_symbol
    cfg.window_size = window_guess
    cfg.num_std = round(num_std, 2)
    cfg.max_position_usd = boll_config.max_position_usd
    cfg.use_all_balance = boll_config.use_all_balance

    # tighten stops based on observed band excursions
    cfg.stop_loss_pct = round(min(0.5, num_std / 10), 3)
    cfg.take_profit_pct = round(min(0.5, num_std / 8), 3)

    return cfg

def start_boll_thread():
    global boll_thread, boll_stop_flag
    if boll_thread and boll_thread.is_alive():
        return
    boll_stop_flag = False
    boll_thread = threading.Thread(target=boll_loop, daemon=True)
    boll_thread.start()


def stop_boll_thread():
    global boll_stop_flag, boll_thread
    boll_stop_flag = True
    if boll_thread:
        boll_thread.join(timeout=5)
        boll_thread = None

