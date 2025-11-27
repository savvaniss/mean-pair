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

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
BINANCE_ENV = os.getenv("BINANCE_ENV", "testnet").lower()
BASE_ASSET = os.getenv("BASE_ASSET", "USDC")
AUTO_START = os.getenv("BOT_AUTO_START", "false").lower() == "true"

if not API_KEY or not API_SECRET:
    raise RuntimeError("BINANCE_API_KEY / BINANCE_API_SECRET not set in .env")

TESTNET = BINANCE_ENV == "testnet"

# =========================
# BINANCE CLIENT
# =========================

if TESTNET:
    client = Client(API_KEY, API_SECRET, testnet=True)
else:
    client = Client(API_KEY, API_SECRET)

# =========================
# DATABASE SETUP (SQLite)
# =========================

DATABASE_URL = "sqlite:///./mean_reversion.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# =========================
# SYMBOL FILTER HELPERS
# =========================

symbol_info_cache = {}


def get_symbol_info_cached(symbol: str):
    if symbol not in symbol_info_cache:
        info = client.get_symbol_info(symbol)
        symbol_info_cache[symbol] = info
    return symbol_info_cache[symbol]


def adjust_quantity(symbol: str, qty: float) -> float:
    """Clamp qty to Binance LOT_SIZE filter (minQty/stepSize)."""
    info = get_symbol_info_cached(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot_filter["stepSize"])
    min_qty = float(lot_filter["minQty"])

    steps = int(qty / step_size)
    adj = steps * step_size
    if adj < min_qty:
        return 0.0
    return adj

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


Base.metadata.create_all(bind=engine)

# =========================
# BOT CONFIG (in memory)
# =========================


class BotConfig(BaseModel):
    enabled: bool = False
    poll_interval_sec: int = 20
    window_size: int = 50
    z_entry: float = 1.5
    z_exit: float = 0.3
    trade_notional_usd: float = 50.0
    use_all_balance: bool = False
    use_testnet: bool = TESTNET
    # explicit ratio thresholds
    use_ratio_thresholds: bool = False
    sell_ratio_threshold: float = 0.0  # ratio >= this → sell HBAR (HBAR->DOGE)
    buy_ratio_threshold: float = 0.0   # ratio <= this → buy HBAR (DOGE->HBAR)


bot_config = BotConfig()
bot_config.enabled = AUTO_START

# Rolling window storage (in memory)
ratio_history: List[float] = []
lock = threading.Lock()
bot_thread: Optional[threading.Thread] = None
bot_stop_flag = False

# Minimum history requirement: at least 30% of window_size (and at least 5 points)
MIN_HISTORY_FRACTION = 0.3


def required_history_len() -> int:
    # at least 5, at least 30% of window
    return max(5, int(bot_config.window_size * MIN_HISTORY_FRACTION))


def has_enough_history() -> bool:
    return len(ratio_history) >= required_history_len()

# =========================
# HELPERS
# =========================


def get_prices():
    tickers = client.get_all_tickers()
    price_map = {t["symbol"]: float(t["price"]) for t in tickers}
    btc = price_map.get("BTCUSDT")
    hbar = price_map.get("HBARUSDT")
    doge = price_map.get("DOGEUSDT")
    if btc is None or hbar is None or doge is None:
        raise RuntimeError("Missing one of BTCUSDT / HBARUSDT / DOGEUSDT from Binance")
    return btc, hbar, doge


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


def get_free_balance(asset: str):
    acc = client.get_account()
    for b in acc["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


def init_state_from_balances(st: State):
    hbar_bal = get_free_balance("HBAR")
    doge_bal = get_free_balance("DOGE")
    base_bal = get_free_balance(BASE_ASSET)

    if hbar_bal > 0 and doge_bal == 0:
        st.current_asset = "HBAR"
        st.current_qty = hbar_bal
    elif doge_bal > 0 and hbar_bal == 0:
        st.current_asset = "DOGE"
        st.current_qty = doge_bal
    else:
        st.current_asset = BASE_ASSET
        st.current_qty = base_bal

    st.realized_pnl_usd = 0.0
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


def place_market_order(symbol: str, side: str, quantity: float):
    try:
        return client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"Binance error: {e}")
        return None

# =========================
# BOT LOOP
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

            with lock:
                try:
                    ts = datetime.utcnow()
                    btc, hbar, doge = get_prices()
                    ratio = hbar / doge
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

                    # HBAR expensive → HBAR -> DOGE
                    if sell_signal and state.current_asset == "HBAR":
                        if bot_config.use_all_balance:
                            qty_hbar = get_free_balance("HBAR")
                        else:
                            notional = bot_config.trade_notional_usd
                            qty_hbar = min(notional / hbar, get_free_balance("HBAR"))
                        qty_hbar = adjust_quantity("HBARUSDT", qty_hbar)

                        if qty_hbar <= 0:
                            print("Qty HBAR too small after LOT_SIZE adjust")
                        else:
                            order_sell = place_market_order("HBARUSDT", "SELL", qty_hbar)
                            if order_sell:
                                usdt_received = qty_hbar * hbar
                                qty_doge = usdt_received / doge
                                qty_doge = adjust_quantity("DOGEUSDT", qty_doge)

                                if qty_doge <= 0:
                                    print("Qty DOGE too small after LOT_SIZE adjust")
                                else:
                                    order_buy = place_market_order("DOGEUSDT", "BUY", qty_doge)
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
                            qty_doge = get_free_balance("DOGE")
                        else:
                            notional = bot_config.trade_notional_usd
                            qty_doge = min(notional / doge, get_free_balance("DOGE"))
                        qty_doge = adjust_quantity("DOGEUSDT", qty_doge)

                        if qty_doge <= 0:
                            print("Qty DOGE too small after LOT_SIZE adjust")
                        else:
                            order_sell = place_market_order("DOGEUSDT", "SELL", qty_doge)
                            if order_sell:
                                usdt_received = qty_doge * doge
                                qty_hbar = usdt_received / hbar
                                qty_hbar = adjust_quantity("HBARUSDT", qty_hbar)

                                if qty_hbar <= 0:
                                    print("Qty HBAR too small after LOT_SIZE adjust")
                                else:
                                    order_buy = place_market_order("HBARUSDT", "BUY", qty_hbar)
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


app = FastAPI(title="HBAR-DOGE Mean Reversion Bot")
app.mount("/static", StaticFiles(directory="static"), name="static")
start_bot_thread()

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
    usdc_balance: float
    hbar_balance: float
    doge_balance: float


@app.get("/status", response_model=StatusResponse)
def get_status():
    session = SessionLocal()
    try:
        btc, hbar, doge = get_prices()

        with lock:
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

        usdc_bal = get_free_balance("USDC")
        hbar_bal = get_free_balance("HBAR")
        doge_bal = get_free_balance("DOGE")

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
            realized_pnl_usd=st.realized_pnl_usd,
            unrealized_pnl_usd=st.unrealized_pnl_usd,
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

        if req.direction == "HBAR->DOGE":
            qty_hbar = min(req.notional_usd / hbar, get_free_balance("HBAR"))
            qty_hbar = adjust_quantity("HBARUSDT", qty_hbar)
            if qty_hbar <= 0:
                raise HTTPException(status_code=400, detail="Notional too small or no HBAR")

            order_sell = place_market_order("HBARUSDT", "SELL", qty_hbar)
            if not order_sell:
                raise HTTPException(status_code=500, detail="HBAR sell failed")

            usdt_received = qty_hbar * hbar
            qty_doge = usdt_received / doge
            qty_doge = adjust_quantity("DOGEUSDT", qty_doge)
            if qty_doge <= 0:
                raise HTTPException(status_code=400, detail="Converted DOGE qty too small")

            order_buy = place_market_order("DOGEUSDT", "BUY", qty_doge)
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
            qty_doge = min(req.notional_usd / doge, get_free_balance("DOGE"))
            qty_doge = adjust_quantity("DOGEUSDT", qty_doge)
            if qty_doge <= 0:
                raise HTTPException(status_code=400, detail="Notional too small or no DOGE")

            order_sell = place_market_order("DOGEUSDT", "SELL", qty_doge)
            if not order_sell:
                raise HTTPException(status_code=500, detail="DOGE sell failed")

            usdt_received = qty_doge * doge
            qty_hbar = usdt_received / hbar
            qty_hbar = adjust_quantity("HBARUSDT", qty_hbar)
            if qty_hbar <= 0:
                raise HTTPException(status_code=400, detail="Converted HBAR qty too small")

            order_buy = place_market_order("HBARUSDT", "BUY", qty_hbar)
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
    """Preview what trade the bot *would* take now, without executing it."""
    session = SessionLocal()
    try:
        btc, hbar, doge = get_prices()

        with lock:
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

        if sell_signal and state.current_asset == "HBAR":
            if bot_config.use_all_balance:
                qty_hbar = get_free_balance("HBAR")
            else:
                notional = bot_config.trade_notional_usd
                qty_hbar = min(notional / hbar, get_free_balance("HBAR"))
            qty_hbar = adjust_quantity("HBARUSDT", qty_hbar)

            if qty_hbar > 0:
                usdt_received = qty_hbar * hbar
                qty_doge = usdt_received / doge
                qty_doge = adjust_quantity("DOGEUSDT", qty_doge)
                if qty_doge > 0:
                    direction = "HBAR->DOGE"
                    from_asset = "HBAR"
                    to_asset = "DOGE"
                    qty_from = qty_hbar
                    qty_to = qty_doge

        elif buy_signal and state.current_asset == "DOGE":
            if bot_config.use_all_balance:
                qty_doge = get_free_balance("DOGE")
            else:
                notional = bot_config.trade_notional_usd
                qty_doge = min(notional / doge, get_free_balance("DOGE"))
            qty_doge = adjust_quantity("DOGEUSDT", qty_doge)

            if qty_doge > 0:
                usdt_received = qty_doge * doge
                qty_hbar = usdt_received / hbar
                qty_hbar = adjust_quantity("HBARUSDT", qty_hbar)
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
            sell_threshold=bot_config.sell_ratio_threshold if bot_config.use_ratio_thresholds else 0.0,
            buy_threshold=bot_config.buy_ratio_threshold if bot_config.use_ratio_thresholds else 0.0,
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
    global client, TESTNET

    # 🔹 Preserve current enabled state
    current_enabled = bot_config.enabled

    # 🔹 Switch between testnet/mainnet if needed
    if cfg.use_testnet != bot_config.use_testnet:
        TESTNET = cfg.use_testnet
        if TESTNET:
            client.__init__(API_KEY, API_SECRET, testnet=True)
        else:
            client.__init__(API_KEY, API_SECRET)

    # 🔹 Apply all fields EXCEPT 'enabled'
    for field, value in cfg.dict().items():
        if field == "enabled":
            continue
        setattr(bot_config, field, value)

    # 🔹 Restore whatever the bot was doing (RUNNING / STOPPED)
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


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("static/index.html")
