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


# ========== Binance helpers ==========


def get_prices() -> Tuple[float, float, float]:
    """
    Mean reversion bot prices – uses MR client and the configured quote asset.
    (e.g. BTCUSDT/HBARUSDT/DOGEUSDT on testnet, BTCUSDC/HBARUSDC/DOGEUSDC on mainnet)
    """
    quote = get_mr_quote()
    tickers = config.mr_client.get_all_tickers()
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
    acc = config.mr_client.get_account()
    for b in acc["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


def adjust_quantity(symbol: str, qty: float) -> float:
    """Clamp qty to Binance LOT_SIZE filter (minQty/stepSize)."""
    info = config.mr_client.get_symbol_info(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot_filter["stepSize"])
    min_qty = float(lot_filter["minQty"])

    steps = int(qty / step_size)
    adj = steps * step_size
    if adj < min_qty:
        return 0.0
    return adj


def place_market_order_mr(symbol: str, side: str, quantity: float):
    try:
        return config.mr_client.create_order(
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
    """Return (sell_signal, buy_signal, reason)."""
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

    if state.current_asset not in ("HBAR", "DOGE"):
        sell_signal = False
        buy_signal = False

    return sell_signal, buy_signal, reason


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
                        hbar_sym = mr_symbol("HBAR")
                        doge_sym = mr_symbol("DOGE")

                        # HBAR expensive → HBAR -> DOGE
                        if sell_signal and state.current_asset == "HBAR":
                            if bot_config.use_all_balance:
                                qty_hbar = get_free_balance_mr("HBAR")
                            else:
                                notional = bot_config.trade_notional_usd
                                qty_hbar = min(
                                    notional / hbar, get_free_balance_mr("HBAR")
                                )
                            qty_hbar = adjust_quantity(hbar_sym, qty_hbar)

                            if qty_hbar > 0:
                                order_sell = place_market_order_mr(
                                    hbar_sym, "SELL", qty_hbar
                                )
                                if order_sell:
                                    quote_received = qty_hbar * hbar
                                    qty_doge = quote_received / doge
                                    qty_doge = adjust_quantity(doge_sym, qty_doge)
                                    if qty_doge > 0:
                                        order_buy = place_market_order_mr(
                                            doge_sym, "BUY", qty_doge
                                        )
                                        if order_buy:
                                            tr = Trade(
                                                ts=ts,
                                                side="HBAR->DOGE",
                                                from_asset="HBAR",
                                                to_asset="DOGE",
                                                qty_from=qty_hbar,
                                                qty_to=qty_doge,
                                                price=ratio,
                                                fee=0.0,
                                                pnl_usd=0.0,
                                                is_testnet=int(bot_config.use_testnet),
                                            )
                                            session.add(tr)
                                            state.current_asset = "DOGE"
                                            state.current_qty = qty_doge

                        # HBAR cheap → DOGE -> HBAR
                        elif buy_signal and state.current_asset == "DOGE":
                            if bot_config.use_all_balance:
                                qty_doge = get_free_balance_mr("DOGE")
                            else:
                                notional = bot_config.trade_notional_usd
                                qty_doge = min(
                                    notional / doge, get_free_balance_mr("DOGE")
                                )
                            qty_doge = adjust_quantity(doge_sym, qty_doge)

                            if qty_doge > 0:
                                order_sell = place_market_order_mr(
                                    doge_sym, "SELL", qty_doge
                                )
                                if order_sell:
                                    quote_received = qty_doge * doge
                                    qty_hbar = quote_received / hbar
                                    qty_hbar = adjust_quantity(hbar_sym, qty_hbar)
                                    if qty_hbar > 0:
                                        order_buy = place_market_order_mr(
                                            hbar_sym, "BUY", qty_hbar
                                        )
                                        if order_buy:
                                            tr = Trade(
                                                ts=ts,
                                                side="DOGE->HBAR",
                                                from_asset="DOGE",
                                                to_asset="HBAR",
                                                qty_from=qty_doge,
                                                qty_to=qty_hbar,
                                                price=ratio,
                                                fee=0.0,
                                                pnl_usd=0.0,
                                                is_testnet=int(bot_config.use_testnet),
                                            )
                                            session.add(tr)
                                            state.current_asset = "HBAR"
                                            state.current_qty = qty_hbar

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
