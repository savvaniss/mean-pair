# routes/mean_reversion.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from config import BASE_ASSET, mr_symbol, switch_env
from database import SessionLocal, PriceSnapshot, Trade, State

# IMPORTANT: use ONLY module-level import so monkeypatch works
import engines.mean_reversion as mr

router = APIRouter()


# ============================================================
# Models
# ============================================================

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


# ============================================================
# STATUS
# ============================================================

@router.get("/status", response_model=StatusResponse)
def get_status():
    session = SessionLocal()
    try:
        btc, hbar, doge = mr.get_prices()

        with mr.mr_lock:
            ratio = hbar / doge
            if mr.ratio_history:
                mean_r = sum(mr.ratio_history) / len(mr.ratio_history)
                var = sum((r - mean_r) ** 2 for r in mr.ratio_history) / len(mr.ratio_history)
                std_r = var ** 0.5 if var > 0 else 0.0
                z = (ratio - mean_r) / std_r if std_r > 0 else 0.0
            else:
                mean_r = ratio
                std_r = 0.0
                z = 0.0

        st = mr.get_state(session)

        base_bal = mr.get_free_balance_mr(BASE_ASSET)
        hbar_bal = mr.get_free_balance_mr("HBAR")
        doge_bal = mr.get_free_balance_mr("DOGE")
        usdc_bal = mr.get_free_balance_mr("USDC")

        # Update current_qty based on balances
        if st.current_asset == "HBAR":
            st.current_qty = hbar_bal
        elif st.current_asset == "DOGE":
            st.current_qty = doge_bal
        else:
            st.current_asset = BASE_ASSET
            st.current_qty = base_bal

        current_value = base_bal + hbar_bal * hbar + doge_bal * doge
        starting_value = st.realized_pnl_usd or 0.0
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
            enabled=mr.bot_config.enabled,
            use_testnet=mr.bot_config.use_testnet,
            usdc_balance=usdc_bal,
            hbar_balance=hbar_bal,
            doge_balance=doge_bal,
        )
    finally:
        session.close()


# ============================================================
# SYNC STATE
# ============================================================

@router.post("/sync_state_from_balances")
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

        mr.init_state_from_balances(st)
        session.commit()
        return {
            "status": "ok",
            "current_asset": st.current_asset,
            "current_qty": st.current_qty,
        }
    finally:
        session.close()


# ============================================================
# MANUAL TRADE
# ============================================================

class ManualTradeRequest(BaseModel):
    direction: str
    notional_usd: float


@router.post("/manual_trade")
def manual_trade(req: ManualTradeRequest):
    if req.notional_usd <= 0:
        raise HTTPException(status_code=400, detail="notional_usd must be > 0")

    if req.direction not in ("HBAR->DOGE", "DOGE->HBAR"):
        raise HTTPException(status_code=400, detail="Invalid direction")

    session = SessionLocal()
    try:
        ts = datetime.utcnow()
        state = mr.get_state(session)

        # Manual trades always use the explicit notional; don't use_all_balance
        res = mr.execute_mr_trade(
            direction=req.direction,
            notional_usd=req.notional_usd,
            use_all_balance=False,
        )
        if not res:
            raise HTTPException(
                status_code=500,
                detail="Trade failed (quantity too small or Binance error)",
            )

        from_asset, to_asset, qty_from, qty_to, ratio = res

        tr = Trade(
            ts=ts,
            side=f"{from_asset}->{to_asset} (manual)",
            from_asset=from_asset,
            to_asset=to_asset,
            qty_from=qty_from,
            qty_to=qty_to,
            price=ratio,
            fee=0.0,
            pnl_usd=0.0,
            is_testnet=int(mr.bot_config.use_testnet),
        )
        session.add(tr)

        state.current_asset = to_asset
        state.current_qty = qty_to

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


# ============================================================
# HISTORY + TRADES
# ============================================================

@router.get("/history")
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


@router.get("/trades")
def list_trades(limit: int = 100):
    session = SessionLocal()
    try:
        trades = session.query(Trade).order_by(Trade.ts.desc()).limit(limit).all()
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


# ============================================================
# NEXT SIGNAL
# ============================================================

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


@router.get("/next_signal", response_model=NextSignalResponse)
def next_signal():
    session = SessionLocal()
    try:
        btc, hbar, doge = mr.get_prices()

        with mr.mr_lock:
            ratio = hbar / doge
            if mr.ratio_history:
                mean_r = sum(mr.ratio_history) / len(mr.ratio_history)
                var = sum((r - mean_r) ** 2 for r in mr.ratio_history) / len(mr.ratio_history)
                std_r = var ** 0.5 if var > 0 else 0.0
                z = (ratio - mean_r) / std_r if std_r > 0 else 0.0
            else:
                mean_r = ratio
                std_r = 0.0
                z = 0.0

        state = mr.get_state(session)
        sell_signal, buy_signal, reason = mr.decide_signal(ratio, mean_r, std_r, z, state)

        upper_band = mean_r + mr.bot_config.z_entry * std_r if std_r > 0 else mean_r
        lower_band = mean_r - mr.bot_config.z_entry * std_r if std_r > 0 else mean_r

        direction = "NONE"
        from_asset = ""
        to_asset = ""
        qty_from = 0.0
        qty_to = 0.0

        hbar_sym = mr_symbol("HBAR")
        doge_sym = mr_symbol("DOGE")

        if sell_signal and state.current_asset == "HBAR":
            if mr.bot_config.use_all_balance:
                qty_hbar = mr.get_free_balance_mr("HBAR")
            else:
                qty_hbar = min(
                    mr.bot_config.trade_notional_usd / hbar,
                    mr.get_free_balance_mr("HBAR")
                )
            qty_hbar = mr.adjust_quantity(hbar_sym, qty_hbar)
            if qty_hbar > 0:
                quote_received = qty_hbar * hbar
                qty_doge = mr.adjust_quantity(doge_sym, quote_received / doge)
                if qty_doge > 0:
                    direction = "HBAR->DOGE"
                    from_asset = "HBAR"
                    to_asset = "DOGE"
                    qty_from = qty_hbar
                    qty_to = qty_doge

        elif buy_signal and state.current_asset == "DOGE":
            if mr.bot_config.use_all_balance:
                qty_doge = mr.get_free_balance_mr("DOGE")
            else:
                qty_doge = min(
                    mr.bot_config.trade_notional_usd / doge,
                    mr.get_free_balance_mr("DOGE")
                )
            qty_doge = mr.adjust_quantity(doge_sym, qty_doge)
            if qty_doge > 0:
                quote_received = qty_doge * doge
                qty_hbar = mr.adjust_quantity(hbar_sym, quote_received / hbar)
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
            sell_threshold=mr.bot_config.sell_ratio_threshold
            if mr.bot_config.use_ratio_thresholds else 0.0,
            buy_threshold=mr.bot_config.buy_ratio_threshold
            if mr.bot_config.use_ratio_thresholds else 0.0,
            from_asset=from_asset,
            to_asset=to_asset,
            qty_from=qty_from,
            qty_to=qty_to,
        )
    finally:
        session.close()


# ============================================================
# CONFIG ENDPOINTS
# ============================================================

@router.get("/config", response_model=type(mr.bot_config))
def get_config():
    return mr.bot_config


@router.post("/config", response_model=type(mr.bot_config))
def update_config(cfg: type(mr.bot_config)):
    current_enabled = mr.bot_config.enabled

    if cfg.use_testnet != mr.bot_config.use_testnet:
        switch_env(cfg.use_testnet)
        mr.bot_config.use_testnet = cfg.use_testnet

        mr.ratio_history.clear()
        s = SessionLocal()
        try:
            s.query(State).delete()
            s.commit()
        finally:
            s.close()

    data = cfg.dict()
    data.pop("enabled", None)

    for field, value in data.items():
        setattr(mr.bot_config, field, value)

    mr.bot_config.enabled = current_enabled
    return mr.bot_config


@router.post("/start")
def start_bot():
    mr.bot_config.enabled = True
    return {"status": "started"}


@router.post("/stop")
def stop_bot():
    mr.bot_config.enabled = False
    return {"status": "stopped"}
