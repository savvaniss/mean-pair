"""Amplification strategy utilities.

This module looks for altcoins whose returns are magnified compared to BTC. It
computes a simple beta/correlation snapshot over the last N days, surfaces the
best candidates, and provides a lightweight switching backtest that rotates
into the highest-beta alt when BTC momentum is positive and moves back to cash
when BTC momentum turns negative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, field_validator

from engines import backtester


@dataclass
class AmplificationStat:
    symbol: str
    beta: float
    correlation: float
    up_capture: float
    down_capture: float
    sample_size: int


class AmplificationConfig(BaseModel):
    base_symbol: str = "BTCUSDT"
    symbols: List[str] = ["ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
    lookback_days: int = 60
    interval: str = "1d"
    min_beta: float = 1.1
    min_correlation: float = 0.2
    suggest_top_n: int = 3
    momentum_window: int = 3

    @field_validator("interval")
    def validate_interval(cls, v: str) -> str:  # noqa: D417 - short validator
        if v not in backtester.SUPPORTED_INTERVALS:
            raise ValueError(f"interval must be one of {backtester.SUPPORTED_INTERVALS}")
        return v

    @field_validator("symbols")
    def validate_symbols(cls, v: Iterable[str]) -> List[str]:  # noqa: D417
        cleaned = [s.upper() for s in v if s]
        if not cleaned:
            raise ValueError("At least one altcoin symbol is required")
        return cleaned


config = AmplificationConfig()


def set_config(data: Dict) -> AmplificationConfig:
    """Replace the in-memory config with the supplied payload."""

    global config
    config = AmplificationConfig(**{**config.model_dump(), **data})
    return config


def get_config() -> AmplificationConfig:
    return config


def _percent_returns(series: List[float]) -> List[float]:
    returns: List[float] = []
    for i in range(1, len(series)):
        prev = series[i - 1]
        curr = series[i]
        if prev == 0:
            continue
        returns.append((curr - prev) / prev)
    return returns


def _covariance(x: List[float], y: List[float]) -> float:
    n = min(len(x), len(y))
    if n == 0:
        return 0.0
    x = x[-n:]
    y = y[-n:]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    return sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n


def _variance(x: List[float]) -> float:
    if not x:
        return 0.0
    mean_x = sum(x) / len(x)
    return sum((xi - mean_x) ** 2 for xi in x) / len(x)


def _correlation(x: List[float], y: List[float]) -> float:
    cov = _covariance(x, y)
    var_x = _variance(x)
    var_y = _variance(y)
    denom = (var_x * var_y) ** 0.5
    return cov / denom if denom > 0 else 0.0


def compute_stat(base_closes: List[float], alt_closes: List[float]) -> AmplificationStat:
    base_ret = _percent_returns(base_closes)
    alt_ret = _percent_returns(alt_closes)
    sample = min(len(base_ret), len(alt_ret))
    if sample == 0:
        return AmplificationStat(
            symbol="",
            beta=0.0,
            correlation=0.0,
            up_capture=0.0,
            down_capture=0.0,
            sample_size=0,
        )

    base_ret = base_ret[-sample:]
    alt_ret = alt_ret[-sample:]

    cov = _covariance(base_ret, alt_ret)
    var_base = _variance(base_ret)
    beta = cov / var_base if var_base > 0 else 0.0
    corr = _correlation(base_ret, alt_ret)

    up_base = [b for b in base_ret if b > 0]
    down_base = [b for b in base_ret if b < 0]
    up_capture = (
        (sum(alt_ret[i] for i, b in enumerate(base_ret) if b > 0) / len(up_base))
        / (sum(up_base) / len(up_base))
        if up_base
        else 0.0
    )
    down_capture = (
        (sum(alt_ret[i] for i, b in enumerate(base_ret) if b < 0) / len(down_base))
        / (sum(down_base) / len(down_base))
        if down_base
        else 0.0
    )

    return AmplificationStat(
        symbol="",
        beta=beta,
        correlation=corr,
        up_capture=up_capture,
        down_capture=down_capture,
        sample_size=sample,
    )


def _intersect_closes(
    base: List[backtester.Candle], alt: List[backtester.Candle]
) -> List[float]:
    alt_by_ts = {c.ts: c.close for c in alt}
    aligned: List[float] = []
    for candle in base:
        if candle.ts in alt_by_ts:
            aligned.append(alt_by_ts[candle.ts])
    return aligned


def load_history(symbol: str) -> List[float]:
    end = datetime.utcnow()
    start = end - timedelta(days=config.lookback_days)
    candles = backtester._fetch_klines(symbol, config.interval, start, end)
    return [c.close for c in candles]


def summarize_amplification() -> Dict:
    now = datetime.utcnow()
    start = now - timedelta(days=config.lookback_days)
    base_candles = backtester._fetch_klines(config.base_symbol, config.interval, start, now)
    base_closes = [c.close for c in base_candles]
    if not base_closes:
        raise RuntimeError("No price data available for base symbol")

    rows: List[AmplificationStat] = []
    for sym in config.symbols:
        alt_candles = backtester._fetch_klines(sym, config.interval, start, now)
        alt_closes = _intersect_closes(base_candles, alt_candles)
        if len(alt_closes) < 5:
            continue
        stat = compute_stat(base_closes[-len(alt_closes) :], alt_closes)
        stat.symbol = sym
        rows.append(stat)

    rows.sort(key=lambda r: r.beta, reverse=True)
    suggestions = [
        r.symbol
        for r in rows
        if r.beta >= config.min_beta and r.correlation >= config.min_correlation
    ][: config.suggest_top_n]

    return {
        "base": config.base_symbol,
        "interval": config.interval,
        "lookback_days": config.lookback_days,
        "generated_at": datetime.utcnow(),
        "stats": [
            {
                "symbol": r.symbol,
                "beta": r.beta,
                "correlation": r.correlation,
                "up_capture": r.up_capture,
                "down_capture": r.down_capture,
                "sample_size": r.sample_size,
            }
            for r in rows
        ],
        "suggestions": suggestions,
    }
