# routes/mean_reversion.py
import asyncio
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel

import config
from config import BASE_ASSET, mr_symbol, switch_mr_env
from database import SessionLocal, PriceSnapshot, Trade, State

# IMPORTANT: use ONLY module-level import so monkeypatch works
import engines.mean_reversion as mr

router = APIRouter()
ws_router = APIRouter()


# ============================================================
# Models
# ============================================================

class StatusResponse(BaseModel):
    btc: float
    asset_a: str
    asset_b: str
    price_a: float
    price_b: float
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
    base_balance: float
    asset_a_balance: float
    asset_b_balance: float


# ============================================================
# STATUS
# ============================================================

def _build_status(session) -> dict:
    btc, price_a, price_b = mr.get_prices()

    with mr.mr_lock:
        ratio = price_a / price_b
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
    asset_a_bal = mr.get_free_balance_mr(mr.bot_config.asset_a)
    asset_b_bal = mr.get_free_balance_mr(mr.bot_config.asset_b)

    if st.current_asset == mr.bot_config.asset_a:
        st.current_qty = asset_a_bal
    elif st.current_asset == mr.bot_config.asset_b:
        st.current_qty = asset_b_bal
    else:
        st.current_asset = BASE_ASSET
        st.current_qty = base_bal

    current_value = base_bal + asset_a_bal * price_a + asset_b_bal * price_b
    starting_value = st.realized_pnl_usd or 0.0
    unrealized_pnl = current_value - starting_value

    st.unrealized_pnl_usd = unrealized_pnl
    session.commit()

    return {
        "btc": btc,
        "asset_a": mr.bot_config.asset_a,
        "asset_b": mr.bot_config.asset_b,
        "price_a": price_a,
        "price_b": price_b,
        "ratio": ratio,
        "zscore": z,
        "mean_ratio": mean_r,
        "std_ratio": std_r,
        "current_asset": st.current_asset,
        "current_qty": st.current_qty,
        "realized_pnl_usd": st.realized_pnl_usd,
        "unrealized_pnl_usd": unrealized_pnl,
        "enabled": mr.bot_config.enabled,
        "use_testnet": mr.bot_config.use_testnet,
        "base_balance": base_bal,
        "asset_a_balance": asset_a_bal,
        "asset_b_balance": asset_b_bal,
    }


@router.get("/status", response_model=StatusResponse)
def get_status():
    session = SessionLocal()
    try:
        data = _build_status(session)
        return StatusResponse(**data)
    finally:
        session.close()


@ws_router.websocket("/ws/mean_reversion")
async def ws_mean_reversion(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            session = SessionLocal()
            try:
                status = _build_status(session)
                snap = (
                    session.query(PriceSnapshot)
                    .order_by(PriceSnapshot.ts.desc())
                    .first()
                )
                snapshot = None
                if snap:
                    snapshot = {
                        "ts": snap.ts.isoformat(),
                        "price_a": snap.price_a,
                        "price_b": snap.price_b,
                        "ratio": snap.ratio,
                    }
                await websocket.send_json({"status": status, "snapshot": snapshot})
            finally:
                session.close()
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return


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
    notional_usd: float | None = None
    from_asset_qty: float | None = None


@router.post("/manual_trade")
def manual_trade(req: ManualTradeRequest):
    asset_a, asset_b = mr.current_pair()
    sell_dir = f"{asset_a}->{asset_b}"
    buy_dir = f"{asset_b}->{asset_a}"

    if req.direction not in (sell_dir, buy_dir):
        raise HTTPException(status_code=400, detail="Invalid direction")

    has_notional = req.notional_usd is not None and req.notional_usd > 0
    has_from_qty = req.from_asset_qty is not None and req.from_asset_qty > 0
    if not (has_notional or has_from_qty):
        raise HTTPException(
            status_code=400,
            detail="Provide a positive notional_usd or from_asset_qty",
        )

    try:
        _, price_a, price_b = mr.get_prices()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if req.direction == sell_dir:
        from_asset = asset_a
        to_asset = asset_b
        price_from = price_a
        price_to = price_b
    else:
        from_asset = asset_b
        to_asset = asset_a
        price_from = price_b
        price_to = price_a

    # Cap the notional to the available balance to avoid needless exchange errors
    from_balance = mr.get_free_balance_mr(from_asset)
    max_notional = from_balance * price_from
    if max_notional <= 0:
        raise HTTPException(status_code=400, detail=f"No balance available for {from_asset}")

    requested_notional = (req.from_asset_qty * price_from) if has_from_qty else req.notional_usd
    notional_to_use = min(requested_notional, max_notional)

    session = SessionLocal()
    try:
        ts = datetime.utcnow()
        state = mr.get_state(session)

        # Manual trades always use the explicit notional; don't use_all_balance
        res = mr.execute_mr_trade(
            direction=req.direction,
            notional_usd=notional_to_use,
            use_all_balance=False,
        )
        if not res:
            raise HTTPException(
                status_code=500,
                detail="Trade failed (quantity too small or Binance error)",
            )

        start_price = res.get("sell_price", price_from)
        end_price = res.get("buy_price", price_to)
        start_value = res["qty_from"] * start_price
        end_value = res["qty_to"] * end_price
        fee_quote = (start_value + end_value) * mr.TRADE_FEE_RATE
        pnl_usd = end_value - start_value - fee_quote

        tr = Trade(
            ts=ts,
            side=f"{res['from_asset']}->{res['to_asset']} (manual)",
            from_asset=res["from_asset"],
            to_asset=res["to_asset"],
            qty_from=res["qty_from"],
            qty_to=res["qty_to"],
            price=res["ratio"],
            fee=fee_quote,
            pnl_usd=pnl_usd,
            is_testnet=int(mr.bot_config.use_testnet),
        )
        session.add(tr)

        state.current_asset = res["to_asset"]
        state.current_qty = res["qty_to"]
        state.realized_pnl_usd += pnl_usd

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
            .filter(
                PriceSnapshot.asset_a == mr.bot_config.asset_a,
                PriceSnapshot.asset_b == mr.bot_config.asset_b,
            )
            .order_by(PriceSnapshot.ts.desc())
            .limit(limit)
            .all()
        )
        rows = list(reversed(rows))
        return [
            {
                "ts": r.ts.isoformat(),
                "price_a": r.price_a,
                "price_b": r.price_b,
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
        btc, price_a, price_b = mr.get_prices()

        with mr.mr_lock:
            ratio = price_a / price_b
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

        asset_a, asset_b = mr.current_pair()
        sell_dir = f"{asset_a}->{asset_b}"
        buy_dir = f"{asset_b}->{asset_a}"

        sym_a = mr_symbol(asset_a)
        sym_b = mr_symbol(asset_b)

        if sell_signal and state.current_asset == asset_a:
            if mr.bot_config.use_all_balance:
                qty_a = mr.get_free_balance_mr(asset_a)
            else:
                qty_a = min(
                    mr.bot_config.trade_notional_usd / price_a,
                    mr.get_free_balance_mr(asset_a)
                )
            qty_a = mr.adjust_quantity(sym_a, qty_a)
            if qty_a > 0:
                quote_received = qty_a * price_a
                qty_b = mr.adjust_quantity(sym_b, quote_received / price_b)
                if qty_b > 0:
                    direction = sell_dir
                    from_asset = asset_a
                    to_asset = asset_b
                    qty_from = qty_a
                    qty_to = qty_b

        elif buy_signal and state.current_asset == asset_b:
            if mr.bot_config.use_all_balance:
                qty_b = mr.get_free_balance_mr(asset_b)
            else:
                qty_b = min(
                    mr.bot_config.trade_notional_usd / price_b,
                    mr.get_free_balance_mr(asset_b)
                )
            qty_b = mr.adjust_quantity(sym_b, qty_b)
            if qty_b > 0:
                quote_received = qty_b * price_b
                qty_a = mr.adjust_quantity(sym_a, quote_received / price_a)
                if qty_a > 0:
                    direction = buy_dir
                    from_asset = asset_b
                    to_asset = asset_a
                    qty_from = qty_b
                    qty_to = qty_a

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
# PAIR HISTORY / HEALTH
# ============================================================


@router.get("/pair_history")
def pair_history():
    session = SessionLocal()
    try:
        return mr.get_pair_history(session, limit=100)
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
        switch_mr_env(cfg.use_testnet)
        mr.bot_config.use_testnet = cfg.use_testnet

        mr.ratio_history.clear()
        s = SessionLocal()
        try:
            s.query(State).delete()
            s.commit()
        finally:
            s.close()

    # Pair changes reset state and history
    pair_changed = (cfg.asset_a, cfg.asset_b) != (mr.bot_config.asset_a, mr.bot_config.asset_b)
    if pair_changed:
        if (cfg.asset_a, cfg.asset_b) not in mr.bot_config.available_pairs:
            raise HTTPException(status_code=400, detail="Pair not available")
        mr.set_pair(cfg.asset_a, cfg.asset_b)

    data = cfg.dict()
    data.pop("enabled", None)
    data.pop("available_pairs", None)

    for field, value in data.items():
        setattr(mr.bot_config, field, value)

    mr.bot_config.enabled = current_enabled

    if pair_changed:
        s = SessionLocal()
        try:
            s.query(State).delete()
            mr.reset_history()
            mr.load_ratio_history(s)
            s.commit()
        finally:
            s.close()
    return mr.bot_config


@router.get("/config_best", response_model=mr.BestConfigResult)
def generate_best_config():
    session = SessionLocal()
    try:
        try:
            return mr.generate_best_config_from_history(session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.post("/start")
def start_bot():
    mr.bot_config.enabled = True
    return {"status": "started"}


@router.post("/stop")
def stop_bot():
    mr.bot_config.enabled = False
    return {"status": "stopped"}
