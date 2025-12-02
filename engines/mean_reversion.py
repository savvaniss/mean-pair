# engines/mean_reversion.py
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

from binance.exceptions import BinanceAPIException
from pydantic import BaseModel

import config
from config import BASE_ASSET, mr_symbol, get_mr_quote
from database import SessionLocal, State, PriceSnapshot, Trade

# Rolling window (in memory)
ratio_history: List[float] = []

# Lock & thread
mr_lock = threading.Lock()
bot_thread: Optional[threading.Thread] = None
bot_stop_flag = False

MIN_HISTORY_FRACTION = 0.5  # at least 50% of window_size and >=5 points


class BotConfig(BaseModel):
    enabled: bool = False
    poll_interval_sec: int = 20
    window_size: int = 100
    z_entry: float = 3
    z_exit: float = 0.4
    trade_notional_usd: float = 50.0
    use_all_balance: bool = True
    use_testnet: bool = False
    use_ratio_thresholds: bool = False
    sell_ratio_threshold: float = 0.0
    buy_ratio_threshold: float = 0.0


bot_config = BotConfig()
bot_config.use_testnet = config.USE_TESTNET
bot_config.enabled = config.AUTO_START


def required_history_len() -> int:
    return max(5, int(bot_config.window_size * MIN_HISTORY_FRACTION))


def has_enough_history() -> bool:
    return len(ratio_history) >= required_history_len()


# ========= Binance client access =========


def get_mr_client():
    """
    Small indirection so tests / config can control the actual client instance.
    In normal runtime this returns config.mr_client.
    """
    return config.mr_client


# ========== Binance helpers ==========


def get_prices() -> Tuple[float, float, float]:
    """
    Mean reversion bot prices – uses MR client and the configured quote asset.
    (e.g. BTCUSDT/HBARUSDT/DOGEUSDT on testnet, BTCUSDC/HBARUSDC/DOGEUSDC on mainnet)
    """
    client = get_mr_client()
    if client is None:
        raise RuntimeError("mr_client is None – configure it, or mock get_prices in tests")

    quote = get_mr_quote()
    tickers = client.get_all_tickers()
    price_map = {t["symbol"]: float(t["price"]) for t in tickers}
    btc = price_map.get(f"BTC{quote}")
    hbar = price_map.get(f"HBAR{quote}")
    doge = price_map.get(f"DOGE{quote}")
    if btc is None or hbar is None or doge is None:
        raise RuntimeError(
            f"Missing one of BTC{quote} / HBAR{quote} / DOGE{quote} from Binance"
        )
    return btc, hbar, doge


def get_free_balance_mr(asset: str) -> float:
    client = get_mr_client()
    if client is None:
        raise RuntimeError("mr_client is None – configure it, or mock get_free_balance_mr in tests")

    acc = client.get_account()
    for b in acc["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


def adjust_quantity(symbol: str, qty: float) -> float:
    """Clamp qty to Binance LOT_SIZE filter (minQty/stepSize)."""
    client = get_mr_client()
    if client is None:
        raise RuntimeError("mr_client is None – configure it, or mock adjust_quantity in tests")

    info = client.get_symbol_info(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot_filter["stepSize"])
    min_qty = float(lot_filter["minQty"])

    steps = int(qty / step_size)
    adj = steps * step_size
    if adj < min_qty:
        return 0.0
    return adj


def place_market_order_mr(symbol: str, side: str, quantity: float):
    client = get_mr_client()
    if client is None:
        raise RuntimeError("mr_client is None – configure it, or mock place_market_order_mr in tests")

    try:
        return client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"[MR] Binance error: {e}")
        return None


# ========== Core stats / state logic ==========


def compute_stats(ratio: float):
    """
    Update ratio history and compute mean/std/z.
    For len(history) < 5, returns (last_ratio, 0, 0) to avoid unstable stats.
    """
    global ratio_history
    ratio_history.append(ratio)
    if len(ratio_history) > bot_config.window_size:
        ratio_history = ratio_history[-bot_config.window_size :]

    if len(ratio_history) < 5:
        return ratio, 0.0, 0.0

    mean_r = sum(ratio_history) / len(ratio_history)
    var = sum((r - mean_r) ** 2 for r in ratio_history) / len(ratio_history)
    std = var ** 0.5 if var > 0 else 0.0
    z = (ratio - mean_r) / std if std > 0 else 0.0
    return mean_r, std, z


def init_state_from_balances(st: State):
    """
    Detect what we currently hold (HBAR / DOGE / base asset)
    and set current_asset + current_qty + starting portfolio value.

    We ALWAYS choose the asset with the largest USD value.
    """
    hbar_bal = get_free_balance_mr("HBAR")
    doge_bal = get_free_balance_mr("DOGE")
    base_bal = get_free_balance_mr(BASE_ASSET)

    try:
        _, hbar_price, doge_price = get_prices()
        hbar_val = hbar_bal * hbar_price
        doge_val = doge_bal * doge_price
        base_val = base_bal  # BASE_ASSET ~ 1 USD (USDT/USDC)
    except Exception:
        hbar_val = hbar_bal
        doge_val = doge_bal
        base_val = base_bal

    asset_values = {"HBAR": hbar_val, "DOGE": doge_val, BASE_ASSET: base_val}
    best_asset = max(asset_values, key=asset_values.get)
    best_value = asset_values[best_asset]

    if best_value <= 1e-6:
        st.current_asset = BASE_ASSET
        st.current_qty = base_bal
    else:
        if best_asset == "HBAR":
            st.current_asset = "HBAR"
            st.current_qty = hbar_bal
        elif best_asset == "DOGE":
            st.current_asset = "DOGE"
            st.current_qty = doge_bal
        else:
            st.current_asset = BASE_ASSET
            st.current_qty = base_bal

    st.realized_pnl_usd = base_val + hbar_val + doge_val
    st.unrealized_pnl_usd = 0.0


def get_state(session) -> State:
    st = session.query(State).first()
    if not st:
        st = State(
            current_asset=BASE_ASSET,
            current_qty=0.0,
            last_ratio=0.0,
            last_z=0.0,
            realized_pnl_usd=0.0,
            unrealized_pnl_usd=0.0,
        )
        init_state_from_balances(st)
        session.add(st)
        session.commit()
        session.refresh(st)
    return st


def decide_signal(
    ratio: float, mean_r: float, std_r: float, z: float, state: State
) -> Tuple[bool, bool, str]:
    """
    Return (sell_signal, buy_signal, reason).

    Logic is the same as your original monolithic app:
    - First require enough history.
    - If ratio thresholds enabled → use those.
    - Else fall back to z-score (z_entry).
    - Only act when current_asset is HBAR or DOGE.
    """
    sell_signal = False
    buy_signal = False
    reason = "none"

    if not has_enough_history():
        return False, False, "not_enough_history"

    if bot_config.use_ratio_thresholds:
        reason = "ratio_thresholds"
        if bot_config.sell_ratio_threshold > 0 and ratio >= bot_config.sell_ratio_threshold:
            sell_signal = True
        if bot_config.buy_ratio_threshold > 0 and ratio <= bot_config.buy_ratio_threshold:
            buy_signal = True
    else:
        reason = "z_score"
        if std_r > 0:
            if z > bot_config.z_entry:
                sell_signal = True
            elif z < -bot_config.z_entry:
                buy_signal = True
        else:
            reason = "std_zero"

    # We only trade when we are actually in HBAR or DOGE
    if state.current_asset not in ("HBAR", "DOGE"):
        sell_signal = False
        buy_signal = False

    return sell_signal, buy_signal, reason


# ========== Generic MA/STD helper (also used in tests) ==========


def compute_ma_std_window(prices: List[float], window: int) -> Tuple[float, float]:
    """
    Compute mean/std over the last `window` prices.
    If window >= len(prices), uses all prices.
    Behavior is aligned with the original helper used by both bots.
    """
    if not prices:
        return 0.0, 0.0

    if window <= 0 or window > len(prices):
        window = len(prices)

    subset = prices[-window:]
    mean = sum(subset) / len(subset)
    var = sum((p - mean) ** 2 for p in subset) / len(subset)
    std = var ** 0.5 if var > 0 else 0.0
    return mean, std


# ========== Shared trade helpers (used by bot & manual) ==========


def _compute_quote_from_order(order, qty_base: float, price_base: float) -> float:
    """
    Derive how much quote asset we *actually* received from a MARKET SELL.

    Prefer cummulativeQuoteQty from Binance order response (real filled amount),
    and fall back to qty * price if not available (e.g. in some mocks).
    """
    try:
        cq = order.get("cummulativeQuoteQty")
        if cq is not None:
            val = float(cq)
            if val > 0:
                return val
    except Exception:
        pass
    return qty_base * price_base


def execute_mr_trade(
    direction: str,
    notional_usd: float,
    use_all_balance: bool,
):
    """
    Execute a single HBAR <-> DOGE rotation.

    direction: "HBAR->DOGE" or "DOGE->HBAR"
    notional_usd: how much USD(notional) to trade when use_all_balance=False
    use_all_balance: if True, ignore notional and use the full balance
                     of the *from* asset.

    Returns:
      (from_asset, to_asset, qty_from, qty_to, ratio)
    or None if the trade could not be executed (qty too small, Binance error, etc).
    """
    if direction not in ("HBAR->DOGE", "DOGE->HBAR"):
        raise ValueError(f"Unsupported MR direction: {direction}")

    # Current prices (HBAR & DOGE vs MR quote: USDT on testnet / USDC on mainnet)
    _, hbar_price, doge_price = get_prices()

    if direction == "HBAR->DOGE":
        from_asset = "HBAR"
        to_asset = "DOGE"
        price_from = hbar_price
        price_to = doge_price
    else:
        from_asset = "DOGE"
        to_asset = "HBAR"
        price_from = doge_price
        price_to = hbar_price

    sym_from = mr_symbol(from_asset)
    sym_to = mr_symbol(to_asset)

    # Decide how much of from_asset to sell
    bal_from = get_free_balance_mr(from_asset)
    if use_all_balance:
        qty_from_raw = bal_from
    else:
        qty_from_raw = min(notional_usd / price_from, bal_from)

    qty_from = adjust_quantity(sym_from, qty_from_raw)
    if qty_from <= 0:
        print(f"[MR] execute_mr_trade: qty_from too small ({qty_from_raw}) for {from_asset}")
        return None

    # 1) SELL from_asset → quote
    order_sell = place_market_order_mr(sym_from, "SELL", qty_from)
    if not order_sell:
        print("[MR] execute_mr_trade: sell order failed")
        return None

    # Use actual filled quote, not naive qty*price
    quote_received = _compute_quote_from_order(order_sell, qty_from, price_from)

    # 2) BUY to_asset using the quote we actually got
    qty_to_raw = quote_received / price_to
    qty_to = adjust_quantity(sym_to, qty_to_raw)
    if qty_to <= 0:
        print(f"[MR] execute_mr_trade: qty_to too small ({qty_to_raw}) for {to_asset}")
        return None

    order_buy = place_market_order_mr(sym_to, "BUY", qty_to)
    if not order_buy:
        print("[MR] execute_mr_trade: buy order failed")
        return None

    # Strategy uses HBAR/DOGE ratio as its "price"
    ratio = hbar_price / doge_price
    return from_asset, to_asset, qty_from, qty_to, ratio


# ========== Bot loop ==========


def bot_loop():
    global bot_stop_flag
    session = SessionLocal()
    try:
        while not bot_stop_flag:
            try:
                ts = datetime.utcnow()
                btc, hbar, doge = get_prices()
                ratio = hbar / doge

                with mr_lock:
                    mean_r, std_r, z = compute_stats(ratio)

                    snap = PriceSnapshot(
                        ts=ts,
                        btc=btc,
                        hbar=hbar,
                        doge=doge,
                        ratio=ratio,
                        zscore=z,
                    )
                    session.add(snap)

                    state = get_state(session)
                    state.last_ratio = ratio
                    state.last_z = z

                    if bot_config.enabled:
                        sell_signal, buy_signal, _ = decide_signal(
                            ratio, mean_r, std_r, z, state
                        )

                        # HBAR expensive → HBAR -> DOGE
                        if sell_signal and state.current_asset == "HBAR":
                            res = execute_mr_trade(
                                "HBAR->DOGE",
                                bot_config.trade_notional_usd,
                                bot_config.use_all_balance,
                            )
                            if res:
                                from_asset, to_asset, qty_from, qty_to, trade_ratio = res
                                tr = Trade(
                                    ts=ts,
                                    side="HBAR->DOGE",
                                    from_asset=from_asset,
                                    to_asset=to_asset,
                                    qty_from=qty_from,
                                    qty_to=qty_to,
                                    price=trade_ratio,
                                    fee=0.0,
                                    pnl_usd=0.0,
                                    is_testnet=int(bot_config.use_testnet),
                                )
                                session.add(tr)
                                state.current_asset = to_asset
                                state.current_qty = qty_to

                        # HBAR cheap → DOGE -> HBAR
                        elif buy_signal and state.current_asset == "DOGE":
                            res = execute_mr_trade(
                                "DOGE->HBAR",
                                bot_config.trade_notional_usd,
                                bot_config.use_all_balance,
                            )
                            if res:
                                from_asset, to_asset, qty_from, qty_to, trade_ratio = res
                                tr = Trade(
                                    ts=ts,
                                    side="DOGE->HBAR",
                                    from_asset=from_asset,
                                    to_asset=to_asset,
                                    qty_from=qty_from,
                                    qty_to=qty_to,
                                    price=trade_ratio,
                                    fee=0.0,
                                    pnl_usd=0.0,
                                    is_testnet=int(bot_config.use_testnet),
                                )
                                session.add(tr)
                                state.current_asset = to_asset
                                state.current_qty = qty_to

                    session.commit()

            except Exception as e:
                print(f"Error in MR bot loop: {e}")
                session.rollback()

            time.sleep(bot_config.poll_interval_sec)
    finally:
        session.close()


def start_bot_thread():
    global bot_thread, bot_stop_flag
    if bot_thread and bot_thread.is_alive():
        return
    bot_stop_flag = False
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()


def stop_bot_thread():
    global bot_stop_flag
    bot_stop_flag = True
