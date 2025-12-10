"""FastAPI endpoints for the cross-sectional relative strength bot."""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from database import SessionLocal, RSSnapshot, RSState, RSTrade
from engines import relative_strength as eng

router = APIRouter()


class RSScore(BaseModel):
    symbol: str
    price: float
    rs: float


class RSSpread(BaseModel):
    long: str
    short: str
    rs_gap: float
    notional_usd: float


class RSStatusResponse(BaseModel):
    enabled: bool
    use_testnet: bool
    quote_asset: str
    quote_balance: float
    top_symbols: List[RSScore]
    bottom_symbols: List[RSScore]
    active_spreads: List[RSSpread]
    last_rebalance: str | None
    lookback_window: int
    rebalance_interval_sec: int


class RSHistoryPoint(BaseModel):
    ts: str
    symbol: str
    price: float
    rs: float


class RSTradeRow(BaseModel):
    ts: str
    long_symbol: str
    short_symbol: str
    rs_gap: float
    notional: float
    is_testnet: bool


class RSConfigModel(eng.RSConfig):
    pass


@router.get("/rs_config", response_model=RSConfigModel)
def get_rs_config():
    return eng.rs_config


@router.post("/rs_config", response_model=RSConfigModel)
def update_rs_config(cfg: RSConfigModel):
    env_changed = cfg.use_testnet != eng.rs_config.use_testnet
    if env_changed:
        config.switch_boll_env(cfg.use_testnet)
        eng.rs_config.use_testnet = cfg.use_testnet

    if not cfg.symbols:
        raise HTTPException(status_code=400, detail="Provide at least one symbol")

    symbols_changed = set(cfg.symbols) != set(eng.rs_config.symbols)
    if symbols_changed:
        with eng.rs_lock:
            eng.rs_price_history.clear()
        session = SessionLocal()
        try:
            session.query(RSSnapshot).delete()
            session.commit()
        finally:
            session.close()

    current_enabled = eng.rs_config.enabled
    data = cfg.dict()
    data.pop("enabled", None)
    for field, value in data.items():
        setattr(eng.rs_config, field, value)
    eng.rs_config.enabled = current_enabled
    return eng.rs_config


@router.post("/rs_start")
def rs_start():
    if not eng.rs_config.symbols:
        raise HTTPException(status_code=400, detail="Configure at least one symbol first")
    eng.rs_config.enabled = True
    return {"status": "started"}


@router.post("/rs_stop")
def rs_stop():
    eng.rs_config.enabled = False
    return {"status": "stopped"}


@router.get("/rs_status", response_model=RSStatusResponse)
def rs_status():
    session = SessionLocal()
    try:
        ranked = eng.rank_universe()
        top = [RSScore(symbol=s, rs=rs, price=p) for s, rs, p in ranked[: eng.rs_config.top_n]]
        bottom_candidates = ranked[-eng.rs_config.bottom_n :]
        bottom_sorted = sorted(bottom_candidates, key=lambda x: x[1])
        bottom = [RSScore(symbol=s, rs=rs, price=p) for s, rs, p in bottom_sorted]

        quote_asset = eng._infer_quote_asset(eng.rs_config.symbols)
        quote_balance = eng.get_free_balance(quote_asset) if config.boll_client else 0.0

        state = session.query(RSState).first()
        last_rebalance = None
        if state and state.last_rebalance:
            last_rebalance = state.last_rebalance.isoformat()

        spreads = [
            RSSpread(
                long=sp["long"],
                short=sp["short"],
                rs_gap=sp["rs_gap"],
                notional_usd=sp["notional_usd"],
            )
            for sp in eng.active_spreads
        ]

        return RSStatusResponse(
            enabled=eng.rs_config.enabled,
            use_testnet=eng.rs_config.use_testnet,
            quote_asset=quote_asset,
            quote_balance=quote_balance,
            top_symbols=top,
            bottom_symbols=bottom,
            active_spreads=spreads,
            last_rebalance=last_rebalance,
            lookback_window=eng.rs_config.lookback_window,
            rebalance_interval_sec=eng.rs_config.rebalance_interval_sec,
        )
    finally:
        session.close()


@router.get("/rs_history", response_model=List[RSHistoryPoint])
def rs_history(limit: int = 200):
    session = SessionLocal()
    try:
        rows = (
            session.query(RSSnapshot)
            .order_by(RSSnapshot.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            RSHistoryPoint(
                ts=row.ts.isoformat(),
                symbol=row.symbol,
                price=row.price,
                rs=row.rs,
            )
            for row in reversed(rows)
        ]
    finally:
        session.close()


@router.get("/rs_trades", response_model=List[RSTradeRow])
def rs_trades(limit: int = 100):
    session = SessionLocal()
    try:
        trades = (
            session.query(RSTrade)
            .order_by(RSTrade.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            RSTradeRow(
                ts=row.ts.isoformat(),
                long_symbol=row.long_symbol,
                short_symbol=row.short_symbol,
                rs_gap=row.rs_gap,
                notional=row.notional,
                is_testnet=bool(row.is_testnet),
            )
            for row in trades
        ]
    finally:
        session.close()
