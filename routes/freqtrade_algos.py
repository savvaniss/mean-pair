"""Endpoints to manage the PatternRecognition and Strategy001 adapters."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database import AlgoSnapshot, AlgoTrade
from engines import freqtrade_algos as eng

router = APIRouter()


class AlgoConfigModel(BaseModel):
    enabled: bool = False
    symbol: str
    timeframe: str
    poll_interval_sec: int
    max_position_usd: float
    use_testnet: bool
    buy_threshold: Optional[int] = None


class AlgoStatusResponse(BaseModel):
    strategy: str
    symbol: str
    price: float
    last_signal: float
    position: str
    qty_asset: float
    entry_price: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    enabled: bool
    use_testnet: bool
    timeframe: str


class AlgoHistoryPoint(BaseModel):
    ts: str
    price: float
    indicator_a: float
    indicator_b: float
    indicator_c: float
    indicator_d: float


class AlgoTradeRow(BaseModel):
    ts: str
    strategy: str
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    pnl_usd: float
    is_testnet: int


@router.get("/ft_configs", response_model=dict)
def get_configs():
    return {k: v.dict() for k, v in eng.algo_configs.items()}


@router.post("/ft_config/{strategy}", response_model=AlgoConfigModel)
def update_config(strategy: str, cfg: AlgoConfigModel):
    data = cfg.dict()
    data.pop("enabled", None)
    try:
        updated = eng.update_config(strategy, data)
    except ValueError as exc:  # pragma: no cover - thin wrapper
        raise HTTPException(status_code=400, detail=str(exc))
    return AlgoConfigModel(**updated.dict())


@router.post("/ft_start/{strategy}")
def start(strategy: str):
    if strategy not in eng.algo_configs:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    eng.set_enabled(strategy, True)
    return {"status": f"{strategy} started"}


@router.post("/ft_stop/{strategy}")
def stop(strategy: str):
    if strategy not in eng.algo_configs:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    eng.set_enabled(strategy, False)
    return {"status": f"{strategy} stopped"}


@router.get("/ft_status", response_model=List[AlgoStatusResponse])
def status():
    return [AlgoStatusResponse(**row) for row in eng.get_status()]


@router.get("/ft_history", response_model=List[AlgoHistoryPoint])
def history(strategy: str = Query(..., description="Strategy key"), limit: int = 200):
    if strategy not in eng.algo_configs:
        raise HTTPException(status_code=404, detail="Unknown strategy")
    rows: List[AlgoSnapshot] = eng.get_history(strategy, limit=limit)
    return [
        AlgoHistoryPoint(
            ts=row.ts.isoformat(),
            price=row.price,
            indicator_a=row.indicator_a or 0.0,
            indicator_b=row.indicator_b or 0.0,
            indicator_c=row.indicator_c or 0.0,
            indicator_d=row.indicator_d or 0.0,
        )
        for row in rows
    ]


@router.get("/ft_trades", response_model=List[AlgoTradeRow])
def trades(strategy: Optional[str] = Query(None), limit: int = 200):
    rows: List[AlgoTrade] = eng.get_trades(strategy, limit=limit)
    return [
        AlgoTradeRow(
            ts=row.ts.isoformat(),
            strategy=row.strategy,
            symbol=row.symbol,
            side=row.side,
            qty=row.qty,
            price=row.price,
            notional=row.notional,
            pnl_usd=row.pnl_usd,
            is_testnet=row.is_testnet,
        )
        for row in rows
    ]
