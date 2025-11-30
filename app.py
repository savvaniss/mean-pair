import os
import threading
import time
from datetime import datetime
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from binance.client import Client
from binance.exceptions import BinanceAPIException
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime
)
from sqlalchemy.orm import sessionmaker, declarative_base

# =========================
# ENV / CONFIG
# =========================

load_dotenv()

# --- Mean-reversion (MR) credentials ---
MR_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
MR_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET")

MR_MAINNET_API_KEY = os.getenv("BINANCE_MAINNET_API_KEY")
MR_MAINNET_API_SECRET = os.getenv("BINANCE_MAINNET_API_SECRET")

# --- Bollinger credentials (separate account / sub-account) ---
# If not set, they fall back to the MR keys.
BOLL_TESTNET_API_KEY = os.getenv("BINANCE_BOLL_TESTNET_API_KEY", MR_TESTNET_API_KEY)
BOLL_TESTNET_API_SECRET = os.getenv("BINANCE_BOLL_TESTNET_API_SECRET", MR_TESTNET_API_SECRET)

BOLL_MAINNET_API_KEY = os.getenv("BINANCE_BOLL_MAINNET_API_KEY")
BOLL_MAINNET_API_SECRET = os.getenv("BINANCE_BOLL_MAINNET_API_SECRET")

# default env when the app starts: "testnet" or "mainnet"
DEFAULT_ENV = os.getenv("BINANCE_DEFAULT_ENV", "testnet").lower()

# Base asset you conceptually hold when "neutral"
# (used for balances & initial state only, trades use HBAR/DOGE symbols)
BASE_ASSET = os.getenv("BASE_ASSET", "USDT").upper()

AUTO_START = os.getenv("BOT_AUTO_START", "false").lower() == "true"

# Which quote assets to use for MR prices / symbols
TESTNET_QUOTE = os.getenv("BINANCE_TESTNET_QUOTE", "USDT").upper()
MAINNET_QUOTE = os.getenv("BINANCE_MAINNET_QUOTE", "USDT").upper()

if DEFAULT_ENV not in ("testnet", "mainnet"):
    raise RuntimeError("BINANCE_DEFAULT_ENV must be 'testnet' or 'mainnet'")


def create_mr_client(use_testnet: bool) -> Client:
    """Client for mean-reversion bot."""
    if use_testnet:
        if not MR_TESTNET_API_KEY or not MR_TESTNET_API_SECRET:
            raise RuntimeError(
                "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET must be set for MR bot"
            )
        return Client(MR_TESTNET_API_KEY, MR_TESTNET_API_SECRET, testnet=True)
    else:
        if not MR_MAINNET_API_KEY or not MR_MAINNET_API_SECRET:
            raise RuntimeError(
                "BINANCE_MAINNET_API_KEY / BINANCE_MAINNET_API_SECRET must be set for MR bot"
            )
        return Client(MR_MAINNET_API_KEY, MR_MAINNET_API_SECRET)


def create_boll_client(use_testnet: bool) -> Client:
    """
    Client for Bollinger bot (separate sub-account).

    Preference:
      - if Bollinger keys are set -> use them
      - else -> fall back to MR keys (so you can still run with one account)
    """
    if use_testnet:
        key = BOLL_TESTNET_API_KEY or MR_TESTNET_API_KEY
        sec = BOLL_TESTNET_API_SECRET or MR_TESTNET_API_SECRET
        if not key or not sec:
            raise RuntimeError(
                "No testnet keys for Bollinger bot (BINANCE_BOLL_TESTNET_API_KEY or BINANCE_TESTNET_API_KEY)"
            )
        return Client(key, sec, testnet=True)
    else:
        key = BOLL_MAINNET_API_KEY or MR_MAINNET_API_KEY
        sec = BOLL_MAINNET_API_SECRET or MR_MAINNET_API_SECRET
        if not key or not sec:
            raise RuntimeError(
                "No mainnet keys for Bollinger bot (BINANCE_BOLL_MAINNET_API_KEY or BINANCE_MAINNET_API_KEY)"
            )
        return Client(key, sec)


# Global clients, start in default env
USE_TESTNET = DEFAULT_ENV == "testnet"
mr_client = create_mr_client(USE_TESTNET)
boll_client = create_boll_client(USE_TESTNET)


def get_mr_quote() -> str:
    """Quote asset the MR bot should use (e.g. USDT on testnet, USDC on mainnet)."""
    return TESTNET_QUOTE if USE_TESTNET else MAINNET_QUOTE


def mr_symbol(base: str) -> str:
    """Build MR symbol like HBARUSDC / DOGEUSDT based on environment."""
    return f"{base}{get_mr_quote()}"


# =========================
# DATABASE SETUP (SQLite)
# =========================

DATABASE_URL = "sqlite:///./mean_reversion.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# =========================
# DB MODELS
# =========================


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    btc = Column(Float)
    hbar = Column(Float)
    doge = Column(Float)
    ratio = Column(Float)
    zscore = Column(Float)


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    side = Column(String)
    from_asset = Column(String)
    to_asset = Column(String)
    qty_from = Column(Float)
    qty_to = Column(Float)
    price = Column(Float)
    fee = Column(Float)
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)  # 1 testnet, 0 mainnet


class State(Base):
    __tablename__ = "state"
    id = Column(Integer, primary_key=True, index=True)
    current_asset = Column(String)
    current_qty = Column(Float)
    last_ratio = Column(Float)
    last_z = Column(Float)
    realized_pnl_usd = Column(Float)
    unrealized_pnl_usd = Column(Float)


# --- Bollinger bot state / trades ---


class BollState(Base):
    __tablename__ = "boll_state"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)          # e.g. "HBARUSDC"
    position = Column(String)        # "FLAT" or "LONG"
    qty_asset = Column(Float)
    entry_price = Column(Float)
    realized_pnl_usd = Column(Float)
    unrealized_pnl_usd = Column(Float)


class BollTrade(Base):
    __tablename__ = "boll_trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    symbol = Column(String)
    side = Column(String)  # "BUY" or "SELL"
    qty = Column(Float)
    price = Column(Float)
    notional = Column(Float)
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)


Base.metadata.create_all(bind=engine)

# =========================
# BOT CONFIG (mean reversion)
# =========================


class BotConfig(BaseModel):
    enabled: bool = False
    poll_interval_sec: int = 20
    window_size: int = 100
    z_entry: float = 3
    z_exit: float = 0.4
    trade_notional_usd: float = 50.0
    use_all_balance: bool = True
    use_testnet: bool = False  # will be overridden below
    # explicit ratio thresholds
    use_ratio_thresholds: bool = False
    sell_ratio_threshold: float = 0.0  # ratio >= this → sell HBAR (HBAR->DOGE)
    buy_ratio_threshold: float = 0.0   # ratio <= this → buy HBAR (DOGE->HBAR)


bot_config = BotConfig()
bot_config.use_testnet = USE_TESTNET
bot_config.enabled = AUTO_START

# Rolling window storage (in memory)
ratio_history: List[float] = []

# =========================
# BOT CONFIG (Bollinger)
# =========================


class BollConfig(BaseModel):
    enabled: bool = False
    symbol: str = "BNBUSDC"               # e.g. "HBARUSDC" or HBARBTC
    poll_interval_sec: int = 20
    window_size: int = 70          # lookback for MA/std
    num_std: float = 3.0           # Bollinger band width
    max_position_usd: float = 50.0 # max position size in quote *units*
    use_all_balance: bool = True   # if true, can use all quote up to max_position_usd
    stop_loss_pct: float = 0.15    # 15% hard stop-loss on open long
    take_profit_pct: float = 0.15  # 15% take profit
    cooldown_sec: int = 80         # min seconds between trades


boll_config = BollConfig()

# Bollinger in-memory history
boll_ts_history: List[datetime] = []
boll_price_history: List[float] = []
BOLL_MAX_HISTORY = 500
boll_last_trade_ts: float = 0.0

# track last symbol we populated history for
current_boll_symbol: str = ""

# =========================
# GLOBAL LOCKS / THREADS
# =========================

# Separate locks for MR and Bollinger so they don't block each other
mr_lock = threading.Lock()
boll_lock = threading.Lock()

bot_thread: Optional[threading.Thread] = None
bot_stop_flag = False

boll_thread: Optional[threading.Thread] = None
boll_stop_flag = False

# Minimum history requirement: at least 50% of window_size (and at least 5 points)
MIN_HISTORY_FRACTION = 0.5


def required_history_len() -> int:
    # at least 5, at least some fraction of window
    return max(5, int(bot_config.window_size * MIN_HISTORY_FRACTION))


def has_enough_history() -> bool:
    return len(ratio_history) >= required_history_len()


# ---- Bollinger specific helpers ----

def boll_required_history_len() -> int:
    return max(5, int(boll_config.window_size * MIN_HISTORY_FRACTION))


def boll_has_enough_history() -> bool:
    return len(boll_price_history) >= boll_required_history_len()

# =========================
# LOW-LEVEL HELPERS (MR vs Bollinger)
# =========================


def adjust_quantity(symbol: str, qty: float, *, for_boll: bool = False) -> float:
    """
    Clamp qty to Binance LOT_SIZE filter (minQty/stepSize).
    If for_boll=True we use the Bollinger client, otherwise the MR client.
    """
    client = boll_client if for_boll else mr_client
    info = client.get_symbol_info(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot_filter["stepSize"])
    min_qty = float(lot_filter["minQty"])

    steps = int(qty / step_size)
    adj = steps * step_size
    if adj < min_qty:
        return 0.0
    return adj


def parse_symbol_assets(symbol: str, *, for_boll: bool = False):
    """Return (baseAsset, quoteAsset) from exchange info."""
    client = boll_client if for_boll else mr_client
    info = client.get_symbol_info(symbol)
    return info["baseAsset"], info["quoteAsset"]


# ----- MR helpers -----


def get_prices():
    """
    Mean reversion bot prices – uses MR client and the configured quote asset.
    (e.g. BTCUSDT/HBARUSDT/DOGEUSDT on testnet, BTCUSDC/HBARUSDC/DOGEUSDC on mainnet)
    """
    quote = get_mr_quote()
    tickers = mr_client.get_all_tickers()
    price_map = {t["symbol"]: float(t["price"]) for t in tickers}
    btc = price_map.get(f"BTC{quote}")
    hbar = price_map.get(f"HBAR{quote}")
    doge = price_map.get(f"DOGE{quote}")
    if btc is None or hbar is None or doge is None:
        raise RuntimeError(f"Missing one of BTC{quote} / HBAR{quote} / DOGE{quote} from Binance")
    return btc, hbar, doge


def get_free_balance_mr(asset: str) -> float:
    acc = mr_client.get_account()
    for b in acc["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


def place_market_order_mr(symbol: str, side: str, quantity: float):
    try:
        return mr_client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"MR Binance error: {e}")
        return None


# ----- Bollinger helpers -----


def get_symbol_price_boll(symbol: str) -> float:
    ticker = boll_client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def get_free_balance_boll(asset: str) -> float:
    acc = boll_client.get_account()
    for b in acc["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


def place_market_order_boll(symbol: str, side: str, quantity: float):
    try:
        return boll_client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"Bollinger Binance error: {e}")
        return None

# =========================
# COMMON / MR HELPERS
# =========================


def compute_stats(ratio: float):
    global ratio_history
    ratio_history.append(ratio)
    if len(ratio_history) > bot_config.window_size:
        ratio_history = ratio_history[-bot_config.window_size:]

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

    Now we ALWAYS choose the asset with the largest USD value
    (unless all balances are basically zero).
    """
    # live balances
    hbar_bal = get_free_balance_mr("HBAR")
    doge_bal = get_free_balance_mr("DOGE")
    base_bal = get_free_balance_mr(BASE_ASSET)

    # approximate values in "USD" terms
    try:
        _btc, hbar_price, doge_price = get_prices()
        hbar_val = hbar_bal * hbar_price
        doge_val = doge_bal * doge_price
        base_val = base_bal  # BASE_ASSET is USDT/USDC → ~1 USD
    except Exception:
        # fallback if price fetch fails
        hbar_val = hbar_bal
        doge_val = doge_bal
        base_val = base_bal

    asset_values = {
        "HBAR": hbar_val,
        "DOGE": doge_val,
        BASE_ASSET: base_val,
    }

    best_asset = max(asset_values, key=asset_values.get)
    best_value = asset_values[best_asset]

    # if everything is basically zero → treat as flat in base
    if best_value <= 1e-6:
        st.current_asset = BASE_ASSET
        st.current_qty = base_bal
    else:
        # choose the truly largest asset
        if best_asset == "HBAR":
            st.current_asset = "HBAR"
            st.current_qty = hbar_bal
        elif best_asset == "DOGE":
            st.current_asset = "DOGE"
            st.current_qty = doge_bal
        else:
            st.current_asset = BASE_ASSET
            st.current_qty = base_bal

    # store starting portfolio value (for unrealized PnL calc)
    st.realized_pnl_usd = base_val + hbar_val + doge_val
    st.unrealized_pnl_usd = 0.0


def get_state(session):
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

# =========================
# BOT LOOP (mean reversion)
# =========================


def decide_signal(ratio: float, mean_r: float, std_r: float, z: float, state: State):
    """Return (sell_signal, buy_signal, reason)."""
    sell_signal = False
    buy_signal = False
    reason = "none"

    # Safety: don't trade if we don't have enough history yet
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
            # std=0 → no movement, do nothing
            reason = "std_zero"

    # only meaningful if we can actually trade from current asset
    if state.current_asset not in ("HBAR", "DOGE"):
        sell_signal = False
        buy_signal = False

    return sell_signal, buy_signal, reason


def bot_loop():
    global bot_stop_flag
    session = SessionLocal()
    try:
        while not bot_stop_flag:
            if not bot_config.enabled:
                time.sleep(1)
                continue

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
                            qty_hbar = min(notional / hbar, get_free_balance_mr("HBAR"))
                        qty_hbar = adjust_quantity(hbar_sym, qty_hbar)

                        if qty_hbar <= 0:
                            print("Qty HBAR too small after LOT_SIZE adjust")
                        else:
                            order_sell = place_market_order_mr(hbar_sym, "SELL", qty_hbar)
                            if order_sell:
                                quote_received = qty_hbar * hbar
                                qty_doge = quote_received / doge
                                qty_doge = adjust_quantity(doge_sym, qty_doge)

                                if qty_doge <= 0:
                                    print("Qty DOGE too small after LOT_SIZE adjust")
                                else:
                                    order_buy = place_market_order_mr(doge_sym, "BUY", qty_doge)
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
                            qty_doge = min(notional / doge, get_free_balance_mr("DOGE"))
                        qty_doge = adjust_quantity(doge_sym, qty_doge)

                        if qty_doge <= 0:
                            print("Qty DOGE too small after LOT_SIZE adjust")
                        else:
                            order_sell = place_market_order_mr(doge_sym, "SELL", qty_doge)
                            if order_sell:
                                quote_received = qty_doge * doge
                                qty_hbar = quote_received / hbar
                                qty_hbar = adjust_quantity(hbar_sym, qty_hbar)

                                if qty_hbar <= 0:
                                    print("Qty HBAR too small after LOT_SIZE adjust")
                                else:
                                    order_buy = place_market_order_mr(hbar_sym, "BUY", qty_hbar)
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
                print(f"Error in bot loop: {e}")
                session.rollback()

            time.sleep(bot_config.poll_interval_sec)
    finally:
        session.close()

# =========================
# BOLLINGER LOOP
# =========================


def compute_ma_std_window(prices: List[float], window: int):
    if not prices:
        return 0.0, 0.0
    w = prices[-window:] if len(prices) > window else prices
    mean_p = sum(w) / len(w)
    var = sum((p - mean_p) ** 2 for p in w) / len(w)
    std = var ** 0.5 if var > 0 else 0.0
    return mean_p, std


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

                # network call outside lock
                price = get_symbol_price_boll(symbol)
                base_asset, quote_asset = parse_symbol_assets(symbol, for_boll=True)

                # ---- update shared history under lock, compute bands & history_ok ----
                with boll_lock:
                    boll_ts_history.append(ts)
                    boll_price_history.append(price)
                    if len(boll_price_history) > BOLL_MAX_HISTORY:
                        del boll_price_history[0:len(boll_price_history) - BOLL_MAX_HISTORY]
                        del boll_ts_history[0:len(boll_ts_history) - BOLL_MAX_HISTORY]

                    history_ok = boll_has_enough_history()
                    ma, std = compute_ma_std_window(
                        boll_price_history, max(5, boll_config.window_size)
                    )
                    upper = ma + boll_config.num_std * std
                    lower = ma - boll_config.num_std * std
                # ---- end lock section ----

                state = get_boll_state(session)
                state.symbol = symbol

                # update unrealized PnL
                if state.position == "LONG" and state.qty_asset > 0 and state.entry_price > 0:
                    state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset
                else:
                    state.unrealized_pnl_usd = 0.0

                # Require enough history before trading
                if not history_ok:
                    session.commit()
                    time.sleep(boll_config.poll_interval_sec)
                    continue

                now_ts = time.time()
                if now_ts - boll_last_trade_ts < boll_config.cooldown_sec:
                    # Still in cooldown
                    session.commit()
                    time.sleep(boll_config.poll_interval_sec)
                    continue

                # Risk controls: stop-loss / take-profit
                action = None  # "BUY" or "SELL" or None

                if state.position == "LONG" and state.qty_asset > 0:
                    if boll_config.stop_loss_pct > 0 and price <= state.entry_price * (1 - boll_config.stop_loss_pct):
                        action = "SELL"  # hard stop loss
                    elif boll_config.take_profit_pct > 0 and price >= state.entry_price * (1 + boll_config.take_profit_pct):
                        action = "SELL"  # take profit
                    elif price > upper:
                        # overbought → sell high
                        action = "SELL"
                else:
                    # flat – look for buy low
                    if price < lower:
                        action = "BUY"

                if action == "BUY":
                    # buy base_asset using quote_asset
                    quote_bal = get_free_balance_boll(quote_asset)
                    if quote_bal <= 0:
                        print(f"No {quote_asset} balance for Bollinger buy")
                    else:
                        notional = min(quote_bal, boll_config.max_position_usd)
                        if notional <= 0:
                            print("Bollinger: notional too small")
                        else:
                            qty = notional / price
                            qty = adjust_quantity(symbol, qty, for_boll=True)
                            if qty <= 0:
                                print("Bollinger: qty too small after LOT_SIZE")
                            else:
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
                                        is_testnet=int(bot_config.use_testnet),
                                    )
                                    session.add(tr)
                                    boll_last_trade_ts = now_ts

                elif action == "SELL" and state.position == "LONG" and state.qty_asset > 0:
                    # sell asset back to quote
                    qty = min(state.qty_asset, get_free_balance_boll(base_asset))
                    qty = adjust_quantity(symbol, qty, for_boll=True)
                    if qty <= 0:
                        print("Bollinger: qty too small to sell")
                    else:
                        order = place_market_order_boll(symbol, "SELL", qty)
                        if order:
                            notional_filled = qty * price
                            pnl = (price - state.entry_price) * qty
                            state.realized_pnl_usd += pnl
                            state.qty_asset -= qty
                            if state.qty_asset < 1e-12:
                                state.qty_asset = 0.0
                                state.position = "FLAT"
                                state.entry_price = 0.0
                            state.unrealized_pnl_usd = 0.0
                            tr = BollTrade(
                                ts=ts,
                                symbol=symbol,
                                side="SELL",
                                qty=qty,
                                price=price,
                                notional=notional_filled,
                                pnl_usd=pnl,
                                is_testnet=int(bot_config.use_testnet),
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

# =========================
# THREAD / APP
# =========================


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


app = FastAPI(title="HBAR-DOGE Mean Reversion Bot")
app.mount("/static", StaticFiles(directory="static"), name="static")
start_bot_thread()
start_boll_thread()

# =========================
# API MODELS / ENDPOINTS
# =========================


class StatusResponse(BaseModel):
    btc: float
    hbar: float
    doge: float
    ratio: float
    zscore: float
    mean_ratio: float
    std_ratio: float
    current_asset: str
    current_qty: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    enabled: bool
    use_testnet: bool
    usdc_balance: float      # shown as quote balance in UI
    hbar_balance: float
    doge_balance: float


@app.get("/status", response_model=StatusResponse)
def get_status():
    session = SessionLocal()
    try:
        btc, hbar, doge = get_prices()

        with mr_lock:
            ratio = hbar / doge
            if len(ratio_history) > 0:
                mean_r = sum(ratio_history) / len(ratio_history)
                var = sum((r - mean_r) ** 2 for r in ratio_history) / len(ratio_history)
                std_r = var ** 0.5 if var > 0 else 0.0
                z = (ratio - mean_r) / std_r if std_r > 0 else 0.0
            else:
                mean_r = ratio
                std_r = 0.0
                z = 0.0

        st = get_state(session)

        # --- live balances (MR account) ---
        base_bal = get_free_balance_mr(BASE_ASSET)
        hbar_bal = get_free_balance_mr("HBAR")
        doge_bal = get_free_balance_mr("DOGE")
        usdc_bal = get_free_balance_mr("USDC")  # for display only

        # make sure current_qty is *always* consistent with the chosen asset
        if st.current_asset == "HBAR":
            st.current_qty = hbar_bal
        elif st.current_asset == "DOGE":
            st.current_qty = doge_bal
        else:
            st.current_asset = BASE_ASSET  # safety
            st.current_qty = base_bal

        # approximate portfolio value
        current_value = base_bal + hbar_bal * hbar + doge_bal * doge
        starting_value = st.realized_pnl_usd if st.realized_pnl_usd is not None else 0.0
        unrealized_pnl = current_value - starting_value

        st.unrealized_pnl_usd = unrealized_pnl
        session.commit()

        return StatusResponse(
            btc=btc,
            hbar=hbar,
            doge=doge,
            ratio=ratio,
            zscore=z,
            mean_ratio=mean_r,
            std_ratio=std_r,
            current_asset=st.current_asset,
            current_qty=st.current_qty,
            realized_pnl_usd=0.0,
            unrealized_pnl_usd=unrealized_pnl,
            enabled=bot_config.enabled,
            use_testnet=bot_config.use_testnet,
            usdc_balance=usdc_bal,
            hbar_balance=hbar_bal,
            doge_balance=doge_bal,
        )
    finally:
        session.close()


@app.post("/sync_state_from_balances")
def sync_state_from_balances():
    session = SessionLocal()
    try:
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
            session.add(st)

        init_state_from_balances(st)
        session.commit()
        return {
            "status": "ok",
            "current_asset": st.current_asset,
            "current_qty": st.current_qty,
        }
    finally:
        session.close()


class ManualTradeRequest(BaseModel):
    direction: str
    notional_usd: float


class ManualBollingerSellRequest(BaseModel):
    symbol: str      # e.g. "HBARUSDC", "BTCUSDT"
    qty_base: float  # how much of the base asset to sell (HBAR, BTC, etc.)


class ManualBollingerSellResponse(BaseModel):
    status: str
    symbol: str
    base_asset: str
    quote_asset: str
    qty_sold: float
    quote_received_est: float


@app.post("/manual_trade")
def manual_trade(req: ManualTradeRequest):
    if req.notional_usd <= 0:
        raise HTTPException(status_code=400, detail="notional_usd must be > 0")

    session = SessionLocal()
    try:
        ts = datetime.utcnow()
        btc, hbar, doge = get_prices()
        ratio = hbar / doge
        state = get_state(session)

        hbar_sym = mr_symbol("HBAR")
        doge_sym = mr_symbol("DOGE")

        if req.direction == "HBAR->DOGE":
            qty_hbar = min(req.notional_usd / hbar, get_free_balance_mr("HBAR"))
            qty_hbar = adjust_quantity(hbar_sym, qty_hbar)
            if qty_hbar <= 0:
                raise HTTPException(status_code=400, detail="Notional too small or no HBAR")

            order_sell = place_market_order_mr(hbar_sym, "SELL", qty_hbar)
            if not order_sell:
                raise HTTPException(status_code=500, detail="HBAR sell failed")

            quote_received = qty_hbar * hbar
            qty_doge = quote_received / doge
            qty_doge = adjust_quantity(doge_sym, qty_doge)
            if qty_doge <= 0:
                raise HTTPException(status_code=400, detail="Converted DOGE qty too small")

            order_buy = place_market_order_mr(doge_sym, "BUY", qty_doge)
            if not order_buy:
                raise HTTPException(status_code=500, detail="DOGE buy failed")

            tr = Trade(
                ts=ts,
                side="HBAR->DOGE (manual)",
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

        elif req.direction == "DOGE->HBAR":
            qty_doge = min(req.notional_usd / doge, get_free_balance_mr("DOGE"))
            qty_doge = adjust_quantity(doge_sym, qty_doge)
            if qty_doge <= 0:
                raise HTTPException(status_code=400, detail="Notional too small or no DOGE")

            order_sell = place_market_order_mr(doge_sym, "SELL", qty_doge)
            if not order_sell:
                raise HTTPException(status_code=500, detail="DOGE sell failed")

            quote_received = qty_doge * doge
            qty_hbar = quote_received / hbar
            qty_hbar = adjust_quantity(hbar_sym, qty_hbar)
            if qty_hbar <= 0:
                raise HTTPException(status_code=400, detail="Converted HBAR qty too small")

            order_buy = place_market_order_mr(hbar_sym, "BUY", qty_hbar)
            if not order_buy:
                raise HTTPException(status_code=500, detail="HBAR buy failed")

            tr = Trade(
                ts=ts,
                side="DOGE->HBAR (manual)",
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

        else:
            raise HTTPException(status_code=400, detail="Invalid direction")

        session.commit()
        return {"status": "ok"}
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/history")
def get_history(limit: int = 300):
    session = SessionLocal()
    try:
        rows = (
            session.query(PriceSnapshot)
            .order_by(PriceSnapshot.ts.desc())
            .limit(limit)
            .all()
        )
        rows = list(reversed(rows))
        return [
            {
                "ts": r.ts.isoformat(),
                "btc": r.btc,
                "hbar": r.hbar,
                "doge": r.doge,
                "ratio": r.ratio,
                "zscore": r.zscore,
            }
            for r in rows
        ]
    finally:
        session.close()


@app.get("/trades")
def list_trades(limit: int = 100):
    session = SessionLocal()
    try:
        trades = (
            session.query(Trade)
            .order_by(Trade.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ts": t.ts.isoformat(),
                "side": t.side,
                "from_asset": t.from_asset,
                "to_asset": t.to_asset,
                "qty_from": t.qty_from,
                "qty_to": t.qty_to,
                "price": t.price,
                "fee": t.fee,
                "pnl_usd": t.pnl_usd,
                "is_testnet": bool(t.is_testnet),
            }
            for t in trades
        ]
    finally:
        session.close()


class NextSignalResponse(BaseModel):
    direction: str
    reason: str
    ratio: float
    zscore: float
    mean_ratio: float
    std_ratio: float
    upper_band: float
    lower_band: float
    sell_threshold: float
    buy_threshold: float
    from_asset: str
    to_asset: str
    qty_from: float
    qty_to: float


@app.get("/next_signal", response_model=NextSignalResponse)
def next_signal():
    """Preview what trade the mean-reversion bot *would* take now, without executing it."""
    session = SessionLocal()
    try:
        btc, hbar, doge = get_prices()

        with mr_lock:
            ratio = hbar / doge
            if len(ratio_history) > 0:
                mean_r = sum(ratio_history) / len(ratio_history)
                var = sum((r - mean_r) ** 2 for r in ratio_history) / len(ratio_history)
                std_r = var ** 0.5 if var > 0 else 0.0
                z = (ratio - mean_r) / std_r if std_r > 0 else 0.0
            else:
                mean_r = ratio
                std_r = 0.0
                z = 0.0

        state = get_state(session)

        sell_signal, buy_signal, reason = decide_signal(
            ratio, mean_r, std_r, z, state
        )

        upper_band = mean_r + bot_config.z_entry * std_r if std_r > 0 else mean_r
        lower_band = mean_r - bot_config.z_entry * std_r if std_r > 0 else mean_r

        direction = "NONE"
        from_asset = ""
        to_asset = ""
        qty_from = 0.0
        qty_to = 0.0

        hbar_sym = mr_symbol("HBAR")
        doge_sym = mr_symbol("DOGE")

        if sell_signal and state.current_asset == "HBAR":
            if bot_config.use_all_balance:
                qty_hbar = get_free_balance_mr("HBAR")
            else:
                notional = bot_config.trade_notional_usd
                qty_hbar = min(notional / hbar, get_free_balance_mr("HBAR"))
            qty_hbar = adjust_quantity(hbar_sym, qty_hbar)

            if qty_hbar > 0:
                quote_received = qty_hbar * hbar
                qty_doge = quote_received / doge
                qty_doge = adjust_quantity(doge_sym, qty_doge)
                if qty_doge > 0:
                    direction = "HBAR->DOGE"
                    from_asset = "HBAR"
                    to_asset = "DOGE"
                    qty_from = qty_hbar
                    qty_to = qty_doge

        elif buy_signal and state.current_asset == "DOGE":
            if bot_config.use_all_balance:
                qty_doge = get_free_balance_mr("DOGE")
            else:
                notional = bot_config.trade_notional_usd
                qty_doge = min(notional / doge, get_free_balance_mr("DOGE"))
            qty_doge = adjust_quantity(doge_sym, qty_doge)

            if qty_doge > 0:
                quote_received = qty_doge * doge
                qty_hbar = quote_received / hbar
                qty_hbar = adjust_quantity(hbar_sym, qty_hbar)
                if qty_hbar > 0:
                    direction = "DOGE->HBAR"
                    from_asset = "DOGE"
                    to_asset = "HBAR"
                    qty_from = qty_doge
                    qty_to = qty_hbar

        return NextSignalResponse(
            direction=direction,
            reason=reason,
            ratio=ratio,
            zscore=z,
            mean_ratio=mean_r,
            std_ratio=std_r,
            upper_band=upper_band,
            lower_band=lower_band,
            sell_threshold=bot_config.sell_ratio_threshold
            if bot_config.use_ratio_thresholds
            else 0.0,
            buy_threshold=bot_config.buy_ratio_threshold
            if bot_config.use_ratio_thresholds
            else 0.0,
            from_asset=from_asset,
            to_asset=to_asset,
            qty_from=qty_from,
            qty_to=qty_to,
        )
    finally:
        session.close()


@app.get("/config", response_model=BotConfig)
def get_config():
    return bot_config


@app.post("/config", response_model=BotConfig)
def update_config(cfg: BotConfig):
    global mr_client, boll_client, USE_TESTNET

    # Preserve current enabled state so saving config doesn't stop the bot
    current_enabled = bot_config.enabled

    # Switch between testnet/mainnet if needed
    if cfg.use_testnet != bot_config.use_testnet:
        USE_TESTNET = cfg.use_testnet
        mr_client = create_mr_client(USE_TESTNET)
        boll_client = create_boll_client(USE_TESTNET)

        # reset MR rolling history and state when env changes
        ratio_history.clear()
        s = SessionLocal()
        try:
            s.query(State).delete()
            s.commit()
        finally:
            s.close()

    # Apply all fields EXCEPT 'enabled' (controlled by /start and /stop)
    for field, value in cfg.dict().items():
        if field == "enabled":
            continue
        setattr(bot_config, field, value)

    # Restore previous running state
    bot_config.enabled = current_enabled

    return bot_config


@app.post("/start")
def start_bot():
    bot_config.enabled = True
    return {"status": "started"}


@app.post("/stop")
def stop_bot():
    bot_config.enabled = False
    return {"status": "stopped"}

# =========================
# BOLLINGER API
# =========================


class BollStatusResponse(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    price: float
    ma: float
    upper: float
    lower: float
    position: str
    qty_asset: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    enabled: bool
    quote_balance: float   # <-- add this


class BollHistoryPoint(BaseModel):
    ts: str
    price: float
    ma: float
    upper: float
    lower: float


class BollConfigModel(BollConfig):
    pass


@app.get("/boll_config", response_model=BollConfigModel)
def get_boll_config():
    return boll_config


@app.post("/boll_config", response_model=BollConfigModel)
def update_boll_config(cfg: BollConfigModel):
    global boll_config, boll_ts_history, boll_price_history, boll_last_trade_ts, current_boll_symbol

    # sanity: if symbol provided, validate quote asset is in an allowed set
    if cfg.symbol:
        info = boll_client.get_symbol_info(cfg.symbol)
        quote = info["quoteAsset"]
        allowed_quotes = {"USDT", "USDC", "BTC", "BNB"}
        if quote not in allowed_quotes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Symbol {cfg.symbol} has quoteAsset {quote}, "
                    f"but allowed quotes are: {', '.join(sorted(allowed_quotes))}"
                ),
            )

    # --- detect symbol change and reset history/state ---
    old_symbol = boll_config.symbol
    new_symbol = cfg.symbol

    if new_symbol and new_symbol != old_symbol:
        # wipe in-memory history so chart starts clean
        with boll_lock:
            boll_ts_history.clear()
            boll_price_history.clear()
            boll_last_trade_ts = 0.0
        # reset Bollinger DB state (so qty / PnL are per-symbol)
        session = SessionLocal()
        try:
            session.query(BollState).delete()
            session.commit()
        finally:
            session.close()
        current_boll_symbol = new_symbol

    # don't overwrite enabled flag here – controlled by /boll_start /boll_stop
    current_enabled = boll_config.enabled
    data = cfg.dict()
    data.pop("enabled", None)

    for field, value in data.items():
        setattr(boll_config, field, value)

    boll_config.enabled = current_enabled
    return boll_config


@app.post("/boll_start")
def boll_start():
    if not boll_config.symbol:
        raise HTTPException(status_code=400, detail="Set a symbol in Bollinger config first")
    boll_config.enabled = True
    return {"status": "started"}


@app.post("/boll_stop")
def boll_stop():
    boll_config.enabled = False
    return {"status": "stopped"}


@app.post("/bollinger_manual_sell", response_model=ManualBollingerSellResponse)
def bollinger_manual_sell(req: ManualBollingerSellRequest):
    """
    Manually sell <qty_base> of the base asset of <symbol> for its quote
    (e.g. sell 10 HBAR in HBARUSDC, or 0.01 BTC in BTCUSDT).
    Uses the Bollinger account (boll_client).
    """
    if req.qty_base <= 0:
        raise HTTPException(status_code=400, detail="qty_base must be > 0")

    try:
        info = boll_client.get_symbol_info(req.symbol)
        if not info:
            raise HTTPException(status_code=400, detail=f"Unknown symbol: {req.symbol}")

        base_asset = info["baseAsset"]
        quote_asset = info["quoteAsset"]

        # Check available balance of the base asset (Bollinger account)
        free_base = get_free_balance_boll(base_asset)
        if free_base <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"No free balance for {base_asset}"
            )

        qty_requested = min(req.qty_base, free_base)

        # Clamp to LOT_SIZE
        qty_adj = adjust_quantity(req.symbol, qty_requested, for_boll=True)
        if qty_adj <= 0:
            raise HTTPException(
                status_code=400,
                detail="Quantity too small after Binance LOT_SIZE filter"
            )

        # Get current price for estimate
        ticker = boll_client.get_symbol_ticker(symbol=req.symbol)
        price = float(ticker["price"])

        # Check MIN_NOTIONAL / NOTIONAL filter to avoid APIError -1013
        min_notional = 0.0
        for f in info["filters"]:
            if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = float(f.get("minNotional", f.get("notional", "0")))
                break

        quote_est = qty_adj * price
        if min_notional > 0 and quote_est < min_notional:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Notional {quote_est:.4f} {quote_asset} is below "
                    f"Binance MIN_NOTIONAL {min_notional:.4f} {quote_asset}. "
                    f"Increase quantity or price."
                ),
            )

        # Place market SELL using Bollinger credentials
        order = place_market_order_boll(req.symbol, "SELL", qty_adj)
        if not order:
            raise HTTPException(status_code=500, detail="Sell order failed")

        # Record into Trade table (for history)
        session = SessionLocal()
        try:
            ts = datetime.utcnow()
            tr = Trade(
                ts=ts,
                side=f"{base_asset}->{quote_asset} (manual SC)",
                from_asset=base_asset,
                to_asset=quote_asset,
                qty_from=qty_adj,
                qty_to=quote_est,   # approximate
                price=price,
                fee=0.0,
                pnl_usd=0.0,
                is_testnet=int(bot_config.use_testnet),
            )
            session.add(tr)
            session.commit()
        finally:
            session.close()

        return ManualBollingerSellResponse(
            status="ok",
            symbol=req.symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            qty_sold=qty_adj,
            quote_received_est=quote_est,
        )

    except BinanceAPIException as e:
        raise HTTPException(status_code=400, detail=f"Binance error: {e.message}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/boll_status", response_model=BollStatusResponse)
def boll_status():
    session = SessionLocal()
    try:
        if not boll_config.symbol:
            # no symbol yet → basic empty status
            return BollStatusResponse(
                symbol="",
                base_asset="",
                quote_asset="USDC" if not bot_config.use_testnet else "USDT",
                price=0.0,
                ma=0.0,
                upper=0.0,
                lower=0.0,
                position="FLAT",
                qty_asset=0.0,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=0.0,
                enabled=boll_config.enabled,
                quote_balance=0.0,
            )

        symbol = boll_config.symbol
        base_asset, quote_asset = parse_symbol_assets(symbol, for_boll=True)

        # price fetch outside lock
        price = get_symbol_price_boll(symbol)
        quote_balance = get_free_balance_boll(quote_asset)

        with boll_lock:
            if boll_price_history:
                ma, std = compute_ma_std_window(
                    boll_price_history, max(5, boll_config.window_size)
                )
            else:
                ma = price
                std = 0.0
            upper = ma + boll_config.num_std * std
            lower = ma - boll_config.num_std * std

        state = get_boll_state(session)
        state.symbol = symbol
        if state.position == "LONG" and state.qty_asset > 0 and state.entry_price > 0:
            state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset
        else:
            state.unrealized_pnl_usd = 0.0
        session.commit()

        return BollStatusResponse(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price=price,
            ma=ma,
            upper=upper,
            lower=lower,
            position=state.position,
            qty_asset=state.qty_asset,
            realized_pnl_usd=state.realized_pnl_usd,
            unrealized_pnl_usd=state.unrealized_pnl_usd,
            enabled=boll_config.enabled,
            quote_balance=quote_balance,  # <-- here
        )
    finally:
        session.close()


@app.get("/boll_balances")
def boll_balances():
    """
    Returns all balances for the Bollinger account (boll_client),
    so you can verify USDC / USDT / etc in the sub-account.
    """
    acc = boll_client.get_account()
    # Only show non-zero free balances to keep it readable
    return [
        {"asset": b["asset"], "free": float(b["free"]), "locked": float(b["locked"])}
        for b in acc["balances"]
        if float(b["free"]) > 0 or float(b["locked"]) > 0
    ]


@app.get("/boll_history", response_model=List[BollHistoryPoint])
def boll_history(limit: int = 300):
    with boll_lock:
        n = min(len(boll_price_history), limit)
        prices = boll_price_history[-n:]
        tss = boll_ts_history[-n:]

    if not prices:
        return []

    points: List[BollHistoryPoint] = []
    for i in range(len(prices)):
        sub_prices = prices[max(0, i - boll_config.window_size + 1):i + 1]
        if not sub_prices:
            ma = prices[i]
            std = 0.0
        else:
            ma, std = compute_ma_std_window(sub_prices, len(sub_prices))
        upper = ma + boll_config.num_std * std
        lower = ma - boll_config.num_std * std
        points.append(
            BollHistoryPoint(
                ts=tss[i].isoformat(),
                price=prices[i],
                ma=ma,
                upper=upper,
                lower=lower,
            )
        )
    return points


@app.get("/boll_trades")
def boll_trades(limit: int = 100):
    session = SessionLocal()
    try:
        trades = (
            session.query(BollTrade)
            .order_by(BollTrade.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ts": t.ts.isoformat(),
                "symbol": t.symbol,
                "side": t.side,
                "qty": t.qty,
                "price": t.price,
                "notional": t.notional,
                "pnl_usd": t.pnl_usd,
                "is_testnet": bool(t.is_testnet),
            }
            for t in trades
        ]
    finally:
        session.close()


@app.get("/symbols")
def list_symbols():
    """
    OLD/FLAT VERSION – kept for backwards compatibility.
    Returns a flat list of symbols that have one of the allowed quote assets.

    Uses the Bollinger client (same env) so you see what that account can trade.
    """
    info = boll_client.get_exchange_info()
    allowed_quotes = {"USDT", "USDC", "BTC", "BNB"}
    out = []
    for s in info["symbols"]:
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") not in allowed_quotes:
            continue
        out.append(
            {
                "symbol": s["symbol"],
                "baseAsset": s["baseAsset"],
                "quoteAsset": s["quoteAsset"],
            }
        )
    return out


@app.get("/symbols_grouped")
def list_symbols_grouped():
    """
    NEW: returns symbols grouped by quote asset so the UI can show
    categories like:
      - USDT pairs
      - USDC pairs
      - BTC pairs
      - BNB pairs
    Example response:
    {
      "USDT": [ {symbol, baseAsset, quoteAsset}, ... ],
      "USDC": [ ... ],
      "BTC":  [ ... ],
      "BNB":  [ ... ]
    }

    Also uses the Bollinger client.
    """
    info = boll_client.get_exchange_info()
    allowed_quotes = ["USDT", "USDC", "BTC", "BNB"]

    grouped = {q: [] for q in allowed_quotes}
    for s in info["symbols"]:
        if s.get("status") != "TRADING":
            continue
        qa = s.get("quoteAsset")
        if qa not in grouped:
            continue
        grouped[qa].append(
            {
                "symbol": s["symbol"],
                "baseAsset": s["baseAsset"],
                "quoteAsset": qa,
            }
        )

    return grouped


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("static/index.html")
