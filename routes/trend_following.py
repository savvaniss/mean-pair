"""FastAPI endpoints for the trend-following EMA bot."""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from database import SessionLocal, TrendSnapshot, TrendState, TrendTrade
from engines import trend_following as eng

router = APIRouter()


class TrendStatusResponse(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    price: float
    fast_ema: float
    slow_ema: float
    atr: float
    position: str
    qty_asset: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    enabled: bool
    quote_balance: float
    use_testnet: bool


class TrendHistoryPoint(BaseModel):
    ts: str
    price: float
    fast_ema: float
    slow_ema: float
    atr: float


class TrendTradeRow(BaseModel):
    ts: str
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    pnl_usd: float
    is_testnet: bool


class TrendConfigModel(eng.TrendConfig):
    pass


@router.get("/trend_config", response_model=TrendConfigModel)
def get_trend_config():
    return eng.trend_config


@router.post("/trend_config", response_model=TrendConfigModel)
def update_trend_config(cfg: TrendConfigModel):
    env_changed = cfg.use_testnet != eng.trend_config.use_testnet
    if env_changed:
        config.switch_boll_env(cfg.use_testnet)
        eng.trend_config.use_testnet = cfg.use_testnet

    if cfg.symbol:
        info = config.boll_client.get_symbol_info(cfg.symbol)
        if not info:
            raise HTTPException(status_code=400, detail=f"Unknown symbol {cfg.symbol}")
        quote = info["quoteAsset"]
        if quote not in {"USDT", "USDC", "BTC", "BNB"}:
            raise HTTPException(status_code=400, detail="Unsupported quote asset")

    old_symbol = eng.trend_config.symbol
    new_symbol = cfg.symbol
    if env_changed or (new_symbol and new_symbol != old_symbol):
        with eng.tf_lock:
            eng.tf_ts_history.clear()
            eng.tf_price_history.clear()
            eng.tf_last_trade_ts = 0.0
        session = SessionLocal()
        try:
            session.query(TrendState).delete()
            session.commit()
        finally:
            session.close()
        eng.current_trend_symbol = new_symbol or eng.current_trend_symbol

    current_enabled = eng.trend_config.enabled
    data = cfg.dict()
    data.pop("enabled", None)
    for field, value in data.items():
        setattr(eng.trend_config, field, value)
    eng.trend_config.enabled = current_enabled
    return eng.trend_config


@router.post("/trend_start")
def trend_start():
    if not eng.trend_config.symbol:
        raise HTTPException(status_code=400, detail="Set a symbol for the trend bot first")
    eng.trend_config.enabled = True
    return {"status": "started"}


@router.post("/trend_stop")
def trend_stop():
    eng.trend_config.enabled = False
    return {"status": "stopped"}


@router.get("/trend_status", response_model=TrendStatusResponse)
def trend_status():
    session = SessionLocal()
    try:
        if not eng.trend_config.symbol:
            return TrendStatusResponse(
                symbol="",
                base_asset="",
                quote_asset="USDC" if not config.BOLL_USE_TESTNET else "USDT",
                price=0.0,
                fast_ema=0.0,
                slow_ema=0.0,
                atr=0.0,
                position="FLAT",
                qty_asset=0.0,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=0.0,
                enabled=eng.trend_config.enabled,
                quote_balance=0.0,
                use_testnet=eng.trend_config.use_testnet,
            )

        symbol = eng.trend_config.symbol
        base_asset, quote_asset = eng.parse_symbol_assets(symbol)
        price = eng.get_symbol_price(symbol)
        quote_balance = eng.get_free_balance(quote_asset)
        fast, slow, atr = eng._compute_signals()

        state = eng.get_trend_state(session)
        state.symbol = symbol
        resp = TrendStatusResponse(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price=price,
            fast_ema=fast,
            slow_ema=slow,
            atr=atr,
            position=state.position,
            qty_asset=state.qty_asset,
            realized_pnl_usd=state.realized_pnl_usd,
            unrealized_pnl_usd=state.unrealized_pnl_usd,
            enabled=eng.trend_config.enabled,
            quote_balance=quote_balance,
            use_testnet=eng.trend_config.use_testnet,
        )
        session.commit()
        return resp
    finally:
        session.close()


@router.get("/trend_history", response_model=List[TrendHistoryPoint])
def trend_history(limit: int = 200):
    session = SessionLocal()
    try:
        rows = (
            session.query(TrendSnapshot)
            .order_by(TrendSnapshot.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            TrendHistoryPoint(
                ts= row.ts.isoformat(),
                price=row.price,
                fast_ema=row.fast_ema,
                slow_ema=row.slow_ema,
                atr=row.atr,
            )
            for row in reversed(rows)
        ]
    finally:
        session.close()


@router.get("/trend_trades", response_model=List[TrendTradeRow])
def trend_trades(limit: int = 100):
    session = SessionLocal()
    try:
        trades = (
            session.query(TrendTrade)
            .order_by(TrendTrade.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            TrendTradeRow(
                ts=t.ts.isoformat(),
                symbol=t.symbol,
                side=t.side,
                qty=t.qty,
                price=t.price,
                notional=t.notional,
                pnl_usd=t.pnl_usd,
                is_testnet=bool(t.is_testnet),
            )
            for t in trades
        ]
    finally:
        session.close()
