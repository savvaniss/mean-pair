from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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


def _execute_backtest(req: BacktestRequest):
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
            return backtester.backtest_mean_reversion(
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
        if strategy == "bollinger":
            if not req.symbol:
                raise HTTPException(status_code=400, detail="symbol is required for Bollinger")
            return backtester.backtest_bollinger(
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
        if strategy == "trend_following":
            if not req.symbol:
                raise HTTPException(status_code=400, detail="symbol is required for trend")
            return backtester.backtest_trend(
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
        if strategy in {
            ft.PATTERN_RECOGNITION,
            ft.STRATEGY_001,
            ft.STRATEGY_002,
            ft.STRATEGY_003,
            ft.SUPERTREND,
        }:
            if not req.symbol:
                raise HTTPException(status_code=400, detail="symbol is required for freqtrade")
            return backtester.backtest_freqtrade(
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
        if strategy == "amplification":
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
            return backtester.backtest_amplification(
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
        raise HTTPException(status_code=400, detail=f"Unsupported strategy {req.strategy}")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - surface clean message
        raise HTTPException(status_code=400, detail=str(exc))


def _to_response(result):
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


class BacktestGrid(BaseModel):
    window_sizes: Optional[List[int]] = None
    num_std_widths: Optional[List[float]] = None
    z_entries: Optional[List[float]] = None
    z_exits: Optional[List[float]] = None
    fast_windows: Optional[List[int]] = None
    slow_windows: Optional[List[int]] = None
    atr_stop_mults: Optional[List[float]] = None
    momentum_windows: Optional[List[int]] = None
    min_betas: Optional[List[float]] = None
    switch_cooldowns: Optional[List[int]] = None


class BatchBacktestRequest(BacktestRequest):
    months: int = 24
    grid: BacktestGrid = Field(default_factory=BacktestGrid)


class BatchRunResult(BaseModel):
    config_label: str
    params: dict[str, float | int | str]
    start: datetime
    end: datetime
    final_balance: float
    return_pct: float
    win_rate: float
    max_drawdown: float


class BatchBacktestResponse(BaseModel):
    strategy: str
    months: int
    results: List[BatchRunResult]


MAX_GRID_RUNS = 120


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest(req: BacktestRequest):
    result = _execute_backtest(req)
    return _to_response(result)


def _add_months(anchor: datetime, delta: int) -> datetime:
    month = anchor.month - 1 + delta
    year = anchor.year + month // 12
    month = month % 12 + 1
    day = min(
        anchor.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return anchor.replace(year=year, month=month, day=day)


def _monthly_windows(months: int) -> List[tuple[datetime, datetime]]:
    start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    windows: List[tuple[datetime, datetime]] = []
    for offset in range(months, 0, -1):
        start = _add_months(start_of_month, -offset)
        end = _add_months(start_of_month, -(offset - 1)) - timedelta(seconds=1)
        windows.append((start, end))
    return windows


def _build_config_variants(
    req: BatchBacktestRequest,
) -> List[tuple[BacktestRequest, dict[str, float | int | str]]]:
    def values_or_default(values: Optional[List], fallback):
        return values or [fallback]

    strategy = req.strategy.lower()
    grid = req.grid or BacktestGrid()
    configs: List[tuple[BacktestRequest, dict[str, float | int | str]]] = []

    if strategy == "mean_reversion":
        for window in values_or_default(grid.window_sizes, req.window_size):
            for z_entry in values_or_default(grid.z_entries, req.z_entry):
                for z_exit in values_or_default(grid.z_exits, req.z_exit):
                    params = {"window_size": window, "z_entry": z_entry, "z_exit": z_exit}
                    configs.append((req.copy(update=params), params))
    elif strategy == "bollinger":
        for window in values_or_default(grid.window_sizes, req.window_size):
            for num_std in values_or_default(grid.num_std_widths, req.num_std):
                params = {"window_size": window, "num_std": num_std}
                configs.append((req.copy(update=params), params))
    elif strategy == "trend_following":
        for fast in values_or_default(grid.fast_windows, req.fast_window):
            for slow in values_or_default(grid.slow_windows, req.slow_window):
                for atr_stop in values_or_default(grid.atr_stop_mults, req.atr_stop_mult):
                    params = {"fast_window": fast, "slow_window": slow, "atr_stop_mult": atr_stop}
                    configs.append((req.copy(update=params), params))
    elif strategy == "amplification":
        for momentum in values_or_default(grid.momentum_windows, req.momentum_window):
            for beta in values_or_default(grid.min_betas, req.min_beta):
                for cooldown in values_or_default(grid.switch_cooldowns, req.switch_cooldown):
                    params = {
                        "momentum_window": momentum,
                        "min_beta": beta,
                        "switch_cooldown": cooldown,
                    }
                    configs.append((req.copy(update=params), params))
    else:
        configs.append((req, {}))

    return configs


def _config_label(params: dict[str, float | int | str]) -> str:
    if not params:
        return "default"
    return ", ".join(f"{k}={v}" for k, v in params.items())


@router.post("/backtest/grid", response_model=BatchBacktestResponse)
def run_backtest_grid(req: BatchBacktestRequest):
    if req.months <= 0:
        raise HTTPException(status_code=400, detail="months must be positive")

    windows = _monthly_windows(req.months)
    configs = _build_config_variants(req)
    total_runs = len(windows) * len(configs)

    if total_runs > MAX_GRID_RUNS:
        raise HTTPException(
            status_code=400,
            detail=f"Grid too large ({total_runs} runs). Please reduce the number of parameter combinations or months (max {MAX_GRID_RUNS}).",
        )
    results: List[BatchRunResult] = []

    for config, params in configs:
        for start, end in windows:
            lookback_days = max((end - start).days, 1)
            config_for_range = config.copy(
                update={"start_date": start, "end_date": end, "lookback_days": lookback_days}
            )
            result = _execute_backtest(config_for_range)
            results.append(
                BatchRunResult(
                    config_label=_config_label(params),
                    params=params or {},
                    start=start,
                    end=end,
                    final_balance=result.final_balance,
                    return_pct=result.return_pct,
                    win_rate=result.win_rate,
                    max_drawdown=result.max_drawdown,
                )
            )

    return BatchBacktestResponse(strategy=req.strategy, months=req.months, results=results)

