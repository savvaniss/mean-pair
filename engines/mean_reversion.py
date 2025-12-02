# engines/mean_reversion.py
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from binance.exceptions import BinanceAPIException
from pydantic import BaseModel

import config
from config import BASE_ASSET, mr_symbol, get_mr_quote
from database import SessionLocal, State, PriceSnapshot, Trade, PairHealth
from engines.common import compute_ma_std_window

# Rolling window (in memory)
ratio_history: List[float] = []
last_ratio_was_outlier: bool = False

# Available pair universe (ordered)
AVAILABLE_PAIRS: List[Tuple[str, str]] = [
    ("HBAR", "DOGE"),
    ("ETH", "BTC"),
    ("ADA", "XRP"),
    ("DOGE", "SHIB"),
    ("SOL", "MATIC"),
    ("LINK", "AVAX"),
]

# Lock & thread
mr_lock = threading.Lock()
bot_thread: Optional[threading.Thread] = None
bot_stop_flag = False
mr_rearm_ready = True
mr_last_signal_sign = 0

MIN_HISTORY_FRACTION = 0.5  # at least 50% of window_size and >=5 points
TRADE_FEE_RATE = 0.001  # 0.1% maker/taker approximation


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
    outlier_sigma: float = 5.0
    max_ratio_jump: float = 0.08
    asset_a: str = "HBAR"
    asset_b: str = "DOGE"
    available_pairs: List[Tuple[str, str]] = AVAILABLE_PAIRS


bot_config = BotConfig()
bot_config.use_testnet = config.USE_TESTNET
bot_config.enabled = config.AUTO_START


def current_pair() -> Tuple[str, str]:
    return bot_config.asset_a, bot_config.asset_b


def set_pair(asset_a: str, asset_b: str) -> None:
    if (asset_a, asset_b) not in bot_config.available_pairs:
        raise ValueError("Pair not available")
    bot_config.asset_a = asset_a
    bot_config.asset_b = asset_b


def required_history_len() -> int:
    return max(5, int(bot_config.window_size * MIN_HISTORY_FRACTION))


def has_enough_history() -> bool:
    return len(ratio_history) >= required_history_len()


def reset_history():
    global ratio_history
    ratio_history = []


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
    Returns (btc_quote, asset_a_quote, asset_b_quote) for the active pair.
    """
    client = get_mr_client()
    if client is None:
        raise RuntimeError("mr_client is None – configure it, or mock get_prices in tests")

    quote = get_mr_quote()
    tickers = client.get_all_tickers()
    price_map: Dict[str, float] = {t["symbol"]: float(t["price"]) for t in tickers}
    btc = price_map.get(f"BTC{quote}")
    asset_a_price = price_map.get(f"{bot_config.asset_a}{quote}")
    asset_b_price = price_map.get(f"{bot_config.asset_b}{quote}")
    if btc is None or asset_a_price is None or asset_b_price is None:
        raise RuntimeError(
            f"Missing BTC{quote} / {bot_config.asset_a}{quote} / {bot_config.asset_b}{quote} from Binance"
        )
    return btc, asset_a_price, asset_b_price


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


def _min_notional(symbol: str) -> float:
    client = get_mr_client()
    if client is None:
        raise RuntimeError("mr_client is None – configure it, or mock _min_notional in tests")

    info = client.get_symbol_info(symbol)
    min_notional_filter = next(
        (f for f in info["filters"] if f["filterType"] == "MIN_NOTIONAL"), None
    )
    return float(min_notional_filter.get("minNotional", 0.0)) if min_notional_filter else 0.0


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


def load_ratio_history(session):
    """Warm up the in-memory ratio history from recent DB snapshots."""
    global ratio_history
    asset_a, asset_b = current_pair()
    ratio_history = [
        snap.ratio
        for snap in session.query(PriceSnapshot)
        .filter(PriceSnapshot.asset_a == asset_a, PriceSnapshot.asset_b == asset_b)
        .order_by(PriceSnapshot.id.desc())
        .limit(bot_config.window_size)
    ][::-1]


def _filter_outlier(ratio: float) -> Tuple[float, bool]:
    """
    Detect abrupt ratio jumps and clamp them to reduce their impact on stats.

    An outlier is flagged when the move is both far from the rolling mean
    (several standard deviations) *and* represents a large relative jump from
    the previous ratio. When flagged, we limit the move to the chosen sigma
    band to avoid polluting z-score calculations.
    """
    if len(ratio_history) < required_history_len():
        return ratio, False

    prev = ratio_history[-1]
    rel_jump = abs(ratio - prev) / max(prev, 1e-9)
    mean_r, std_r = compute_ma_std_window(
        ratio_history, min(len(ratio_history), bot_config.window_size)
    )

    if std_r == 0:
        return ratio, False

    exceeds_sigma = abs(ratio - mean_r) > bot_config.outlier_sigma * std_r
    exceeds_jump = rel_jump > bot_config.max_ratio_jump
    if exceeds_sigma and exceeds_jump:
        direction = 1 if ratio > mean_r else -1
        capped_ratio = mean_r + direction * bot_config.outlier_sigma * std_r
        return capped_ratio, True

    return ratio, False


def compute_stats(ratio: float) -> Tuple[float, float, float, bool]:
    """
    Update ratio history and compute mean/std/z.
    For len(history) < 5, returns (last_ratio, 0, 0, False) to avoid unstable stats.
    Applies outlier detection to reduce the impact of sudden jumps.
    """
    global ratio_history, last_ratio_was_outlier
    ratio_filtered, is_outlier = _filter_outlier(ratio)
    last_ratio_was_outlier = is_outlier

    ratio_history.append(ratio_filtered)
    if len(ratio_history) > bot_config.window_size:
        ratio_history = ratio_history[-bot_config.window_size :]

    if len(ratio_history) < 5:
        return ratio_filtered, 0.0, 0.0, is_outlier

    mean_r = sum(ratio_history) / len(ratio_history)
    var = sum((r - mean_r) ** 2 for r in ratio_history) / len(ratio_history)
    std = var ** 0.5 if var > 0 else 0.0
    z = (ratio_filtered - mean_r) / std if std > 0 else 0.0
    return mean_r, std, z, is_outlier


def init_state_from_balances(st: State):
    """
    Detect what we currently hold (asset_a / asset_b / base asset)
    and set current_asset + current_qty + starting portfolio value.

    We ALWAYS choose the asset with the largest USD value.
    """
    asset_a, asset_b = current_pair()

    asset_a_bal = get_free_balance_mr(asset_a)
    asset_b_bal = get_free_balance_mr(asset_b)
    base_bal = get_free_balance_mr(BASE_ASSET)

    try:
        _, asset_a_price, asset_b_price = get_prices()
        a_val = asset_a_bal * asset_a_price
        b_val = asset_b_bal * asset_b_price
        base_val = base_bal  # BASE_ASSET ~ 1 USD (USDT/USDC)
    except Exception:
        a_val = asset_a_bal
        b_val = asset_b_bal
        base_val = base_bal

    asset_values = {asset_a: a_val, asset_b: b_val, BASE_ASSET: base_val}
    best_asset = max(asset_values, key=asset_values.get)
    best_value = asset_values[best_asset]

    if best_value <= 1e-6:
        st.current_asset = BASE_ASSET
        st.current_qty = base_bal
    else:
        if best_asset == asset_a:
            st.current_asset = asset_a
            st.current_qty = asset_a_bal
        elif best_asset == asset_b:
            st.current_asset = asset_b
            st.current_qty = asset_b_bal
        else:
            st.current_asset = BASE_ASSET
            st.current_qty = base_bal

    st.realized_pnl_usd = base_val + a_val + b_val
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
    - Only act when current_asset is the active pair.
    """
    sell_signal = False
    buy_signal = False
    reason = "none"

    if not has_enough_history():
        return False, False, "not_enough_history"

    global mr_rearm_ready, mr_last_signal_sign
    if bot_config.use_ratio_thresholds:
        reason = "ratio_thresholds"
        if bot_config.sell_ratio_threshold > 0 and ratio >= bot_config.sell_ratio_threshold:
            sell_signal = True
        if bot_config.buy_ratio_threshold > 0 and ratio <= bot_config.buy_ratio_threshold:
            buy_signal = True
    else:
        reason = "z_score"
        if std_r > 0:
            if abs(z) < bot_config.z_exit:
                mr_rearm_ready = True
                mr_last_signal_sign = 0
            if z > bot_config.z_entry and (mr_rearm_ready or mr_last_signal_sign < 0):
                sell_signal = True
                mr_rearm_ready = False
                mr_last_signal_sign = 1
            elif z < -bot_config.z_entry and (mr_rearm_ready or mr_last_signal_sign > 0):
                buy_signal = True
                mr_rearm_ready = False
                mr_last_signal_sign = -1
        else:
            reason = "std_zero"

    asset_a, asset_b = current_pair()
    if state.current_asset not in (asset_a, asset_b):
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


def evaluate_pair_health(history: List[PriceSnapshot]) -> Tuple[bool, float]:
    ratios = [h.ratio for h in history if h.ratio is not None]
    if len(ratios) < 5:
        return False, 0.0

    _, std = compute_ma_std_window(ratios, min(len(ratios), bot_config.window_size))
    # Consider pair "good" if it shows movement (std) but not extreme noise
    is_good = std > 0.0001
    return is_good, std


def get_pair_history(session, limit: int = 50):
    asset_a, asset_b = current_pair()
    snaps = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.asset_a == asset_a, PriceSnapshot.asset_b == asset_b)
        .order_by(PriceSnapshot.ts.desc())
        .limit(limit)
        .all()
    )
    health, std = evaluate_pair_health(snaps)
    health_row = PairHealth(
        ts=datetime.utcnow(),
        asset_a=asset_a,
        asset_b=asset_b,
        std=std,
        is_good=int(health),
        sample_count=len(snaps),
    )
    session.add(health_row)
    session.commit()
    session.refresh(health_row)
    history = [
        {
            "ts": s.ts.isoformat() if s.ts else None,
            "price_a": s.price_a,
            "price_b": s.price_b,
            "ratio": s.ratio,
            "zscore": s.zscore,
        }
        for s in snaps
    ][::-1]
    health_history = [
        {
            "ts": h.ts.isoformat() if h.ts else None,
            "std": h.std,
            "is_good": bool(h.is_good),
            "samples": h.sample_count,
        }
        for h in (
            session.query(PairHealth)
            .filter(PairHealth.asset_a == asset_a, PairHealth.asset_b == asset_b)
            .order_by(PairHealth.ts.desc())
            .limit(20)
            .all()
        )
    ][::-1]

    return {
        "pair": f"{asset_a}/{asset_b}",
        "std": std,
        "is_good_pair": health,
        "history": history,
        "health_history": health_history,
    }


def generate_best_config_from_history(session, lookback: int = 300) -> BotConfig:
    """
    Propose config parameters derived from historical ratios for the active pair.

    This uses the recent ratio distribution to pick conservative z-entry/exit
    values, while also suggesting ratio thresholds that are two standard
    deviations away from the rolling mean.
    """
    asset_a, asset_b = current_pair()
    snaps = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.asset_a == asset_a, PriceSnapshot.asset_b == asset_b)
        .order_by(PriceSnapshot.ts.desc())
        .limit(lookback)
        .all()
    )

    ratios = [s.ratio for s in snaps if s.ratio is not None]
    if not ratios:
        raise ValueError("Not enough history to suggest config")

    mean_r, std_r = compute_ma_std_window(ratios, min(len(ratios), bot_config.window_size))
    window_guess = min(max(20, len(ratios) // 2), 500)

    if std_r > 0:
        zscores = sorted(abs((r - mean_r) / std_r) for r in ratios)
        idx = max(0, int(len(zscores) * 0.85) - 1)
        z_entry = max(1.0, min(4.0, zscores[idx]))
    else:
        z_entry = bot_config.z_entry

    z_exit = max(0.2, round(z_entry / 3, 2))
    ratio_span = 2.0
    sell_threshold = mean_r + std_r * ratio_span if std_r > 0 else 0.0
    buy_threshold = mean_r - std_r * ratio_span if std_r > 0 else 0.0

    cfg = BotConfig(**bot_config.dict())
    cfg.window_size = window_guess
    cfg.z_entry = round(z_entry, 2)
    cfg.z_exit = round(z_exit, 2)
    cfg.sell_ratio_threshold = round(sell_threshold, 6)
    cfg.buy_ratio_threshold = round(buy_threshold, 6)
    cfg.use_ratio_thresholds = std_r > 0

    return cfg


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
    Execute a single pair rotation.

    direction: f"{asset_a}->{asset_b}" or the reverse
    notional_usd: how much USD(notional) to trade when use_all_balance=False
    use_all_balance: if True, ignore notional and use the full balance
                     of the *from* asset.

    Returns:
      (from_asset, to_asset, qty_from, qty_to, ratio)
    or None if the trade could not be executed (qty too small, Binance error, etc).
    """
    asset_a, asset_b = current_pair()
    sell_dir = f"{asset_a}->{asset_b}"
    buy_dir = f"{asset_b}->{asset_a}"

    if direction not in (sell_dir, buy_dir):
        raise ValueError(f"Unsupported MR direction: {direction}")

    # Current prices vs MR quote: USDT on testnet / USDC on mainnet
    _, price_a, price_b = get_prices()

    if direction == sell_dir:
        from_asset = asset_a
        to_asset = asset_b
        price_from = price_a
        price_to = price_b
    else:
        from_asset = asset_b
        to_asset = asset_a
        price_from = price_b
        price_to = price_a

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

    # Strategy uses pair ratio as its "price"
    ratio = price_from / price_to
    return from_asset, to_asset, qty_from, qty_to, ratio


# ========== Bot loop ==========


def bot_loop():
    global bot_stop_flag
    session = SessionLocal()
    load_ratio_history(session)
    try:
        while not bot_stop_flag:
            try:
                ts = datetime.utcnow()
                btc, price_a, price_b = get_prices()
                ratio = price_a / price_b

                with mr_lock:
                    mean_r, std_r, z, is_outlier = compute_stats(ratio)

                    snap = PriceSnapshot(
                        ts=ts,
                        asset_a=bot_config.asset_a,
                        asset_b=bot_config.asset_b,
                        price_a=price_a,
                        price_b=price_b,
                        ratio=ratio,
                        zscore=z,
                    )
                    session.add(snap)

                    state = get_state(session)
                    state.last_ratio = ratio
                    state.last_z = z

                    if bot_config.enabled and not is_outlier:
                        sell_signal, buy_signal, _ = decide_signal(
                            ratio, mean_r, std_r, z, state
                        )

                        sell_dir = f"{bot_config.asset_a}->{bot_config.asset_b}"
                        buy_dir = f"{bot_config.asset_b}->{bot_config.asset_a}"

                        if sell_signal and state.current_asset == bot_config.asset_a:
                            res = execute_mr_trade(
                                sell_dir,
                                bot_config.trade_notional_usd,
                                bot_config.use_all_balance,
                            )
                            if res:
                                from_asset, to_asset, qty_from, qty_to, trade_ratio = res
                                start_price = price_a if from_asset == bot_config.asset_a else price_b
                                end_price = price_b if to_asset == bot_config.asset_b else price_a
                                start_value = qty_from * start_price
                                end_value = qty_to * end_price
                                fee_quote = (start_value + end_value) * TRADE_FEE_RATE
                                pnl_usd = end_value - start_value - fee_quote
                                tr = Trade(
                                    ts=ts,
                                    side=sell_dir,
                                    from_asset=from_asset,
                                    to_asset=to_asset,
                                    qty_from=qty_from,
                                    qty_to=qty_to,
                                    price=trade_ratio,
                                    fee=fee_quote,
                                    pnl_usd=pnl_usd,
                                    is_testnet=int(bot_config.use_testnet),
                                )
                                session.add(tr)
                                state.current_asset = to_asset
                                state.current_qty = qty_to
                                state.realized_pnl_usd += pnl_usd

                        elif buy_signal and state.current_asset == bot_config.asset_b:
                            res = execute_mr_trade(
                                buy_dir,
                                bot_config.trade_notional_usd,
                                bot_config.use_all_balance,
                            )
                            if res:
                                from_asset, to_asset, qty_from, qty_to, trade_ratio = res
                                start_price = price_b if from_asset == bot_config.asset_b else price_a
                                end_price = price_a if to_asset == bot_config.asset_a else price_b
                                start_value = qty_from * start_price
                                end_value = qty_to * end_price
                                fee_quote = (start_value + end_value) * TRADE_FEE_RATE
                                pnl_usd = end_value - start_value - fee_quote
                                tr = Trade(
                                    ts=ts,
                                    side=buy_dir,
                                    from_asset=from_asset,
                                    to_asset=to_asset,
                                    qty_from=qty_from,
                                    qty_to=qty_to,
                                    price=trade_ratio,
                                    fee=fee_quote,
                                    pnl_usd=pnl_usd,
                                    is_testnet=int(bot_config.use_testnet),
                                )
                                session.add(tr)
                                state.current_asset = to_asset
                                state.current_qty = qty_to
                                state.realized_pnl_usd += pnl_usd

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
    global bot_stop_flag, bot_thread
    bot_stop_flag = True
    if bot_thread:
        bot_thread.join(timeout=5)
        bot_thread = None
