from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engines import backtester
from engines import freqtrade_algos as ft


class EquityPointModel(BaseModel):
    ts: datetime
    equity: float


class TradeModel(BaseModel):
    ts: datetime
    action: str
    price: float
    size: float
    pnl: float


class BacktestRequest(BaseModel):
    strategy: str
    symbol: Optional[str] = None
    asset_a: Optional[str] = None
    asset_b: Optional[str] = None
    base_symbol: Optional[str] = None
    alt_symbols: Optional[List[str]] = None
    interval: str = "1h"
    lookback_days: int = 14
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    starting_balance: float = 1000.0
    window_size: int = 70
    num_std: float = 3.0
    z_entry: float = 3.0
    z_exit: float = 0.4
    fast_window: int = 12
    slow_window: int = 26
    atr_window: int = 14
    atr_stop_mult: float = 2.0
    momentum_window: int = 3
    min_beta: float = 1.1
    conversion_symbol: Optional[str] = None
    switch_cooldown: int = 0
    fee_rate: float = 0.001
    position_pct: float = 1.0


class BacktestResponse(BaseModel):
    strategy: str
    start: datetime
    end: datetime
    final_balance: float
    return_pct: float
    win_rate: float
    max_drawdown: float
    trades: List[TradeModel]
    equity_curve: List[EquityPointModel]


router = APIRouter()


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest(req: BacktestRequest):
    strategy = req.strategy.lower()

    if (req.start_date and not req.end_date) or (req.end_date and not req.start_date):
        raise HTTPException(status_code=400, detail="start_date and end_date must both be provided")

    if req.interval not in backtester.SUPPORTED_INTERVALS:
        supported = ", ".join(backtester.SUPPORTED_INTERVALS)
        raise HTTPException(status_code=400, detail=f"interval must be one of: {supported}")

    if req.switch_cooldown < 0:
        raise HTTPException(status_code=400, detail="switch_cooldown must be non-negative")

    try:
        if strategy == "mean_reversion":
            if not req.asset_a or not req.asset_b:
                raise HTTPException(status_code=400, detail="asset_a and asset_b are required")
            result = backtester.backtest_mean_reversion(
                asset_a=req.asset_a.upper(),
                asset_b=req.asset_b.upper(),
                interval=req.interval,
                window=req.window_size,
                z_entry=req.z_entry,
                z_exit=req.z_exit,
                lookback_days=req.lookback_days,
                start=req.start_date,
                end=req.end_date,
                starting_balance=req.starting_balance,
                fee_rate=req.fee_rate,
                position_pct=req.position_pct,
            )
        elif strategy == "bollinger":
            if not req.symbol:
                raise HTTPException(status_code=400, detail="symbol is required for Bollinger")
            result = backtester.backtest_bollinger(
                symbol=req.symbol.upper(),
                interval=req.interval,
                window=req.window_size,
                num_std=req.num_std,
                lookback_days=req.lookback_days,
                start=req.start_date,
                end=req.end_date,
                starting_balance=req.starting_balance,
                fee_rate=req.fee_rate,
                position_pct=req.position_pct,
            )
        elif strategy == "trend_following":
            if not req.symbol:
                raise HTTPException(status_code=400, detail="symbol is required for trend")
            result = backtester.backtest_trend(
                symbol=req.symbol.upper(),
                interval=req.interval,
                fast=req.fast_window,
                slow=req.slow_window,
                atr_window=req.atr_window,
                atr_stop_mult=req.atr_stop_mult,
                lookback_days=req.lookback_days,
                start=req.start_date,
                end=req.end_date,
                starting_balance=req.starting_balance,
                fee_rate=req.fee_rate,
                position_pct=req.position_pct,
            )
        elif strategy in {
            ft.PATTERN_RECOGNITION,
            ft.STRATEGY_001,
            ft.STRATEGY_002,
            ft.STRATEGY_003,
            ft.SUPERTREND,
        }:
            if not req.symbol:
                raise HTTPException(status_code=400, detail="symbol is required for freqtrade")
            result = backtester.backtest_freqtrade(
                strategy=strategy,
                symbol=req.symbol.upper(),
                interval=req.interval,
                lookback_days=req.lookback_days,
                start=req.start_date,
                end=req.end_date,
                starting_balance=req.starting_balance,
                fee_rate=req.fee_rate,
                position_pct=req.position_pct,
            )
        elif strategy == "amplification":
            symbols = req.alt_symbols or [
                "SOLUSDC",
                "ETHUSDC",
                "LINKUSDC",
                "XRPUSDC",
                "DOGEUSDC",
                "HBARUSDC",
                "ARBUSDC",
                "AVAXUSDC",
            ]
            result = backtester.backtest_amplification(
                base_symbol=(req.base_symbol or "BTCUSDC").upper(),
                symbols=[s.upper() for s in symbols],
                interval=req.interval,
                lookback_days=req.lookback_days,
                momentum_window=req.momentum_window,
                min_beta=req.min_beta,
                conversion_symbol=(req.conversion_symbol or "").upper() or None,
                switch_cooldown=req.switch_cooldown,
                starting_balance=req.starting_balance,
                fee_rate=req.fee_rate,
                position_pct=req.position_pct,
                start=req.start_date,
                end=req.end_date,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported strategy {req.strategy}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - surface clean message
        raise HTTPException(status_code=400, detail=str(exc))

    return BacktestResponse(
        strategy=result.strategy,
        start=result.start,
        end=result.end,
        final_balance=result.final_balance,
        return_pct=result.return_pct,
        win_rate=result.win_rate,
        max_drawdown=result.max_drawdown,
        trades=[
            TradeModel(
                ts=t.ts,
                action=t.action,
                price=t.price,
                size=t.size,
                pnl=t.pnl,
            )
            for t in result.trades
        ],
        equity_curve=[EquityPointModel(ts=p.ts, equity=p.equity) for p in result.equity_curve],
    )

