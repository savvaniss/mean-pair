"""Lightweight historical backtester for the bundled algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import os
import requests
from engines.common import compute_ma_std_window
from engines import freqtrade_algos as ft


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class TradeResult:
    ts: datetime
    action: str
    price: float
    size: float
    pnl: float


@dataclass
class EquityPoint:
    ts: datetime
    equity: float


@dataclass
class BacktestResult:
    strategy: str
    start: datetime
    end: datetime
    trades: List[TradeResult]
    equity_curve: List[EquityPoint]
    final_balance: float
    return_pct: float
    win_rate: float
    max_drawdown: float


BINANCE_INTERVAL_MS = {
    "20s": 20_000,
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

SUPPORTED_INTERVALS = tuple(BINANCE_INTERVAL_MS.keys())


def _mr_quote() -> str:
    env = os.getenv("BINANCE_DEFAULT_ENV", "testnet").lower()
    testnet_quote = os.getenv("BINANCE_TESTNET_QUOTE", "USDT").upper()
    mainnet_quote = os.getenv("BINANCE_MAINNET_QUOTE", "USDC").upper()
    return testnet_quote if env != "mainnet" else mainnet_quote


def _expand_to_20s(candles: List[Candle], start: datetime, end: datetime) -> List[Candle]:
    expanded: List[Candle] = []
    for candle in candles:
        for offset in (0, 20, 40):
            ts = candle.ts + timedelta(seconds=offset)
            if ts < start or ts > end:
                continue
            expanded.append(
                Candle(
                    ts=ts,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                )
            )
    return expanded


def _fetch_binance_public_klines(
    symbol: str, interval: str, start: datetime, end: datetime
) -> List[Candle]:
    interval_ms = BINANCE_INTERVAL_MS.get(interval)
    if not interval_ms:
        raise ValueError(f"Unsupported interval {interval}")

    request_interval = interval
    request_interval_ms = interval_ms
    if interval == "20s":
        request_interval = "1m"
        request_interval_ms = BINANCE_INTERVAL_MS["1m"]

    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    candles: List[Candle] = []

    while start_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": request_interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break

        for row in data:
            ts_ms, open_, high, low, close_, *_ = row
            candles.append(
                Candle(
                    ts=datetime.utcfromtimestamp(ts_ms / 1000),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close_),
                )
            )

        last_open_time = data[-1][0]
        start_ms = last_open_time + request_interval_ms
        if start_ms <= last_open_time:
            break

    if interval == "20s":
        return _expand_to_20s(candles, start, end)
    return candles


def _fetch_yahoo_klines(
    symbol: str, interval: str, start: datetime, end: datetime
) -> List[Candle]:
    import yfinance as yf

    interval_map = {
        "20s": "1m",
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "4h": "1h",
        "1d": "1d",
    }
    yf_interval = interval_map.get(interval, "1h")
    df = yf.download(
        tickers=symbol,
        start=start,
        end=end,
        interval=yf_interval,
        progress=False,
        auto_adjust=False,
    )

    candles: List[Candle] = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime()
        candles.append(
            Candle(
                ts=ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
            )
        )
    if interval == "20s":
        return _expand_to_20s(candles, start, end)
    return candles


def _fetch_klines(symbol: str, interval: str, start: datetime, end: datetime) -> List[Candle]:
    try:
        candles = _fetch_binance_public_klines(symbol, interval, start, end)
        if candles:
            return candles
    except Exception:
        # fall back to Yahoo Finance for symbols not on Binance
        pass

    candles = _fetch_yahoo_klines(symbol, interval, start, end)
    if not candles:
        raise RuntimeError("No historical data available from Binance or Yahoo Finance")
    return candles


def _resolve_range(
    lookback_days: int, start: Optional[datetime] = None, end: Optional[datetime] = None
) -> Tuple[datetime, datetime]:
    resolved_end = end or datetime.utcnow()
    resolved_start = start or resolved_end - timedelta(days=lookback_days)
    if resolved_start >= resolved_end:
        raise ValueError("start must be before end")
    return resolved_start, resolved_end


def _compute_drawdown(equity_curve: Iterable[EquityPoint]) -> float:
    peak = 0.0
    max_dd = 0.0
    for point in equity_curve:
        peak = max(peak, point.equity)
        if peak > 0:
            dd = (peak - point.equity) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _build_equity_curve(
    cash: float, qty: float, prices: List[Candle], start_idx: int = 0
) -> List[EquityPoint]:
    curve: List[EquityPoint] = []
    for idx in range(start_idx, len(prices)):
        price = prices[idx].close
        equity = cash + qty * price
        curve.append(EquityPoint(ts=prices[idx].ts, equity=equity))
    return curve


def _align_prices(base: List[Candle], alt: List[Candle]) -> List[Tuple[datetime, float]]:
    """Return a list of (ts, price) pairs for timestamps present in both series."""

    alt_by_ts = {c.ts: c.close for c in alt}
    return [(c.ts, alt_by_ts[c.ts]) for c in base if c.ts in alt_by_ts]


def backtest_bollinger(
    symbol: str,
    interval: str,
    window: int,
    num_std: float,
    lookback_days: int,
    starting_balance: float,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> BacktestResult:
    start, end = _resolve_range(lookback_days, start, end)
    candles = _fetch_klines(symbol, interval, start, end)
    prices = [c.close for c in candles]

    cash = starting_balance
    qty = 0.0
    trades: List[TradeResult] = []
    equity: List[EquityPoint] = []

    for idx, price in enumerate(prices):
        mean, std = compute_ma_std_window(prices[: idx + 1], window)
        upper = mean + num_std * std
        lower = mean - num_std * std
        equity.append(EquityPoint(ts=candles[idx].ts, equity=cash + qty * price))

        if idx < window // 2:
            continue

        if qty == 0 and price <= lower:
            qty = cash / price
            trades.append(
                TradeResult(ts=candles[idx].ts, action="BUY", price=price, size=qty, pnl=0.0)
            )
            cash = 0.0
        elif qty > 0 and price >= upper:
            cash = qty * price
            pnl = cash - starting_balance
            trades.append(
                TradeResult(ts=candles[idx].ts, action="SELL", price=price, size=qty, pnl=pnl)
            )
            qty = 0.0

    final_balance = cash + qty * prices[-1] if prices else starting_balance
    ret = (final_balance - starting_balance) / starting_balance if starting_balance else 0.0
    wins = [t for t in trades if t.pnl > 0]
    equity_curve = equity
    max_dd = _compute_drawdown(equity_curve)

    return BacktestResult(
        strategy="bollinger",
        start=start,
        end=end,
        trades=trades,
        equity_curve=equity_curve,
        final_balance=final_balance,
        return_pct=ret,
        win_rate=len(wins) / len(trades) if trades else 0.0,
        max_drawdown=max_dd,
    )


def backtest_trend(
    symbol: str,
    interval: str,
    fast: int,
    slow: int,
    atr_window: int,
    atr_stop_mult: float,
    lookback_days: int,
    starting_balance: float,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> BacktestResult:
    start, end = _resolve_range(lookback_days, start, end)
    candles = _fetch_klines(symbol, interval, start, end)
    prices = [c.close for c in candles]

    cash = starting_balance
    qty = 0.0
    entry_price = 0.0
    trades: List[TradeResult] = []
    equity: List[EquityPoint] = []

    def ema(series: List[float], window: int) -> float:
        if not series:
            return 0.0
        window = max(1, min(window, len(series)))
        k = 2 / (window + 1)
        val = series[-window]
        for price_ in series[-window + 1 :]:
            val = price_ * k + val * (1 - k)
        return val

    for idx, price in enumerate(prices):
        fast_ema = ema(prices[: idx + 1], fast)
        slow_ema = ema(prices[: idx + 1], slow)
        atr_slice = prices[max(0, idx - atr_window + 1) : idx + 1]
        atr = sum(abs(atr_slice[i] - atr_slice[i - 1]) for i in range(1, len(atr_slice))) / max(
            1, len(atr_slice) - 1
        )
        equity.append(EquityPoint(ts=candles[idx].ts, equity=cash + qty * price))

        if qty == 0 and fast_ema > slow_ema:
            qty = cash / price
            entry_price = price
            trades.append(
                TradeResult(ts=candles[idx].ts, action="BUY", price=price, size=qty, pnl=0.0)
            )
            cash = 0.0
        elif qty > 0:
            stop_price = entry_price - atr_stop_mult * atr
            if fast_ema < slow_ema or (atr > 0 and price <= stop_price):
                cash = qty * price
                pnl = cash - starting_balance
                trades.append(
                    TradeResult(
                        ts=candles[idx].ts, action="SELL", price=price, size=qty, pnl=pnl
                    )
                )
                qty = 0.0

    final_balance = cash + qty * prices[-1] if prices else starting_balance
    ret = (final_balance - starting_balance) / starting_balance if starting_balance else 0.0
    wins = [t for t in trades if t.pnl > 0]
    equity_curve = equity
    max_dd = _compute_drawdown(equity_curve)

    return BacktestResult(
        strategy="trend_following",
        start=start,
        end=end,
        trades=trades,
        equity_curve=equity_curve,
        final_balance=final_balance,
        return_pct=ret,
        win_rate=len(wins) / len(trades) if trades else 0.0,
        max_drawdown=max_dd,
    )


def backtest_mean_reversion(
    asset_a: str,
    asset_b: str,
    interval: str,
    window: int,
    z_entry: float,
    z_exit: float,
    lookback_days: int,
    starting_balance: float,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> BacktestResult:
    quote = _mr_quote()
    symbol_a = f"{asset_a}{quote}"
    symbol_b = f"{asset_b}{quote}"
    start, end = _resolve_range(lookback_days, start, end)
    a_candles = _fetch_klines(symbol_a, interval, start, end)
    b_candles = _fetch_klines(symbol_b, interval, start, end)
    length = min(len(a_candles), len(b_candles))
    a_prices = [c.close for c in a_candles[:length]]
    b_prices = [c.close for c in b_candles[:length]]

    cash = starting_balance
    qty_a = 0.0
    qty_b = 0.0
    trades: List[TradeResult] = []
    equity: List[EquityPoint] = []
    last_side: Optional[str] = None

    for idx in range(length):
        price_a = a_prices[idx]
        price_b = b_prices[idx]
        ratio = price_a / price_b if price_b else 0.0
        mean, std = compute_ma_std_window([a / b for a, b in zip(a_prices[: idx + 1], b_prices[: idx + 1])], window)
        z = (ratio - mean) / std if std else 0.0
        equity.append(
            EquityPoint(
                ts=a_candles[idx].ts,
                equity=cash + qty_a * price_a + qty_b * price_b,
            )
        )

        if idx < window // 2:
            continue

        if qty_a == 0 and qty_b == 0:
            if z <= -abs(z_entry):
                qty_a = cash / price_a
                trades.append(
                    TradeResult(
                        ts=a_candles[idx].ts,
                        action="BUY_A",
                        price=price_a,
                        size=qty_a,
                        pnl=0.0,
                    )
                )
                cash = 0.0
                last_side = "A"
            elif z >= abs(z_entry):
                qty_b = cash / price_b
                trades.append(
                    TradeResult(
                        ts=a_candles[idx].ts,
                        action="BUY_B",
                        price=price_b,
                        size=qty_b,
                        pnl=0.0,
                    )
                )
                cash = 0.0
                last_side = "B"
        elif last_side == "A" and abs(z) <= z_exit:
            cash = qty_a * price_a
            pnl = cash - starting_balance
            trades.append(
                TradeResult(
                    ts=a_candles[idx].ts,
                    action="EXIT_A",
                    price=price_a,
                    size=qty_a,
                    pnl=pnl,
                )
            )
            qty_a = 0.0
            last_side = None
        elif last_side == "B" and abs(z) <= z_exit:
            cash = qty_b * price_b
            pnl = cash - starting_balance
            trades.append(
                TradeResult(
                    ts=a_candles[idx].ts,
                    action="EXIT_B",
                    price=price_b,
                    size=qty_b,
                    pnl=pnl,
                )
            )
            qty_b = 0.0
            last_side = None

    final_balance = cash + qty_a * a_prices[-1] + qty_b * b_prices[-1] if length else starting_balance
    ret = (final_balance - starting_balance) / starting_balance if starting_balance else 0.0
    wins = [t for t in trades if t.pnl > 0]
    equity_curve = equity
    max_dd = _compute_drawdown(equity_curve)

    return BacktestResult(
        strategy="mean_reversion",
        start=start,
        end=end,
        trades=trades,
        equity_curve=equity_curve,
        final_balance=final_balance,
        return_pct=ret,
        win_rate=len(wins) / len(trades) if trades else 0.0,
        max_drawdown=max_dd,
    )


def backtest_freqtrade(
    strategy: str,
    symbol: str,
    interval: str,
    lookback_days: int,
    starting_balance: float,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> BacktestResult:
    start, end = _resolve_range(lookback_days, start, end)
    candles = _fetch_klines(symbol, interval, start, end)
    prices = [c.close for c in candles]

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.high for c in candles]

    cash = starting_balance
    qty = 0.0
    trades: List[TradeResult] = []
    equity: List[EquityPoint] = []

    def pattern_signal(idx: int) -> Tuple[bool, bool]:
        # Simplified: buy on RSI oversold, sell on overbought
        rsi = ft._rsi(prices[: idx + 1], period=14)
        return rsi < 30, rsi > 70

    def strategy001_signal(idx: int) -> Tuple[bool, bool]:
        fast = ft._ema(prices[: idx + 1], 9)
        slow = ft._ema(prices[: idx + 1], 21)
        return fast > slow, fast < slow

    def strategy002_signal(idx: int) -> Tuple[bool, bool]:
        mean, upper, lower = ft._bollinger(prices[: idx + 1], window=20, stds=2)
        return prices[idx] < lower, prices[idx] > upper

    def strategy003_signal(idx: int) -> Tuple[bool, bool]:
        k, d = ft._stochastic(highs[: idx + 1], lows[: idx + 1], prices[: idx + 1])
        return k < 20 and d < 20, k > 80 and d > 80

    def supertrend_signal(idx: int) -> Tuple[bool, bool]:
        mfi = ft._mfi(highs[: idx + 1], lows[: idx + 1], prices[: idx + 1], volumes[: idx + 1])
        return mfi < 35, mfi > 65

    signal_map = {
        ft.PATTERN_RECOGNITION: pattern_signal,
        ft.STRATEGY_001: strategy001_signal,
        ft.STRATEGY_002: strategy002_signal,
        ft.STRATEGY_003: strategy003_signal,
        ft.SUPERTREND: supertrend_signal,
    }

    signal_fn = signal_map.get(strategy)
    if not signal_fn:
        raise ValueError(f"Unsupported strategy {strategy}")

    for idx, price in enumerate(prices):
        buy, sell = signal_fn(idx)
        equity.append(EquityPoint(ts=candles[idx].ts, equity=cash + qty * price))

        if qty == 0 and buy:
            qty = cash / price
            trades.append(
                TradeResult(ts=candles[idx].ts, action="BUY", price=price, size=qty, pnl=0.0)
            )
            cash = 0.0
        elif qty > 0 and sell:
            cash = qty * price
            pnl = cash - starting_balance
            trades.append(
                TradeResult(ts=candles[idx].ts, action="SELL", price=price, size=qty, pnl=pnl)
            )
            qty = 0.0

    final_balance = cash + qty * prices[-1] if prices else starting_balance
    ret = (final_balance - starting_balance) / starting_balance if starting_balance else 0.0
    wins = [t for t in trades if t.pnl > 0]
    equity_curve = equity
    max_dd = _compute_drawdown(equity_curve)

    return BacktestResult(
        strategy=strategy,
        start=start,
        end=end,
        trades=trades,
        equity_curve=equity_curve,
        final_balance=final_balance,
        return_pct=ret,
        win_rate=len(wins) / len(trades) if trades else 0.0,
        max_drawdown=max_dd,
    )


def backtest_amplification(
    base_symbol: str,
    symbols: List[str],
    interval: str,
    lookback_days: int,
    momentum_window: int,
    min_beta: float,
    conversion_symbol: Optional[str],
    switch_cooldown: int,
    starting_balance: float,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> BacktestResult:
    """Rotate into the highest-beta altcoin when BTC momentum is positive."""

    from engines import amplification  # local import to avoid circular dependency

    start, end = _resolve_range(lookback_days, start, end)
    base_candles = _fetch_klines(base_symbol, interval, start, end)
    if not base_candles:
        raise ValueError("No base price data available for requested window")

    base_prices = [c.close for c in base_candles]
    base_ts = [c.ts for c in base_candles]

    aligned_alts: Dict[str, List[Tuple[datetime, float]]] = {}
    stats = []
    for sym in symbols:
        alt_candles = _fetch_klines(sym, interval, start, end)
        if not alt_candles:
            continue
        aligned = _align_prices(base_candles, alt_candles)
        if len(aligned) < momentum_window + 1:
            continue
        alt_prices = [p for _, p in aligned]
        stat = amplification.compute_stat(base_prices[-len(alt_prices) :], alt_prices)
        stat.symbol = sym
        stats.append(stat)
        aligned_alts[sym] = aligned

    stats.sort(key=lambda s: s.beta, reverse=True)
    candidates = [s for s in stats if s.beta >= min_beta and s.correlation > 0]
    if not candidates:
        raise ValueError("No altcoins met amplification thresholds for backtest")

    cash = starting_balance
    position_sym: Optional[str] = None
    qty = 0.0
    trades: List[TradeResult] = []
    equity: List[EquityPoint] = []

    alt_price_maps = {sym: dict(aligned) for sym, aligned in aligned_alts.items()}
    last_seen_prices: Dict[str, Optional[float]] = {sym: None for sym in aligned_alts}

    cooldown = 0

    for idx in range(len(base_prices)):
        ts = base_ts[idx]
        price_base = base_prices[idx]
        holding_price = None
        for sym, price_map in alt_price_maps.items():
            price_at_ts = price_map.get(ts)
            if price_at_ts is not None:
                last_seen_prices[sym] = price_at_ts
        if position_sym:
            holding_price = last_seen_prices.get(position_sym)
        equity.append(EquityPoint(ts=ts, equity=cash + (qty * holding_price if holding_price else 0.0)))

        if idx < momentum_window:
            continue

        if cooldown > 0:
            cooldown -= 1
            continue

        momentum = (price_base - base_prices[idx - momentum_window]) / base_prices[
            idx - momentum_window
        ]

        top_candidate = None
        preferred = None
        if conversion_symbol:
            preferred = next((c for c in candidates if c.symbol == conversion_symbol), None)
            if not preferred:
                preferred = next((c for c in stats if c.symbol == conversion_symbol), None)
        ordered_candidates = []
        if preferred:
            ordered_candidates.append(preferred)
        for cand in candidates:
            if preferred and cand.symbol == preferred.symbol:
                continue
            ordered_candidates.append(cand)
        for cand in ordered_candidates:
            if ts in alt_price_maps.get(cand.symbol, {}):
                top_candidate = cand
                break

        if momentum > 0 and top_candidate and position_sym != top_candidate.symbol:
            if position_sym and holding_price:
                cash = qty * holding_price
                trades.append(
                    TradeResult(ts=ts, action=f"EXIT_{position_sym}", price=holding_price, size=qty, pnl=cash - starting_balance)
                )
                qty = 0.0
                position_sym = None
            alt_price = alt_price_maps[top_candidate.symbol].get(ts)
            if alt_price is None:
                continue
            if cash > 0:
                qty = cash / alt_price
                trades.append(
                    TradeResult(ts=ts, action=f"BUY_{top_candidate.symbol}", price=alt_price, size=qty, pnl=0.0)
                )
                position_sym = top_candidate.symbol
                cash = 0.0
                cooldown = switch_cooldown
        elif momentum < 0 and position_sym and holding_price:
            cash = qty * holding_price
            trades.append(
                TradeResult(ts=ts, action=f"EXIT_{position_sym}", price=holding_price, size=qty, pnl=cash - starting_balance)
            )
            qty = 0.0
            position_sym = None
            cooldown = switch_cooldown

    if position_sym:
        last_ts = base_ts[-1]
        last_price = alt_price_maps[position_sym].get(last_ts)
        if last_price is None:
            last_price = last_seen_prices.get(position_sym)
        if last_price:
            cash = qty * last_price
            trades.append(
                TradeResult(
                    ts=last_ts,
                    action=f"EXIT_{position_sym}",
                    price=last_price,
                    size=qty,
                    pnl=cash - starting_balance,
                )
            )
            qty = 0.0
            position_sym = None

    final_balance = cash
    equity_curve = equity if equity else [EquityPoint(ts=start, equity=starting_balance)]
    ret = (final_balance - starting_balance) / starting_balance if starting_balance else 0.0
    wins = [t for t in trades if t.pnl > 0]
    max_dd = _compute_drawdown(equity_curve)

    return BacktestResult(
        strategy="amplification",
        start=start,
        end=end,
        trades=trades,
        equity_curve=equity_curve,
        final_balance=final_balance,
        return_pct=ret,
        win_rate=len(wins) / len(trades) if trades else 0.0,
        max_drawdown=max_dd,
    )

