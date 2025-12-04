"""Cross-sectional momentum engine using relative strength rankings."""

import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from binance.exceptions import BinanceAPIException
from pydantic import BaseModel

import config
from database import SessionLocal, RSSnapshot, RSState, RSTrade
from engines.common import clamp_to_step

RS_MAX_HISTORY = 600

rs_price_history: Dict[str, List[float]] = {}
rs_lock = threading.Lock()
rs_thread: Optional[threading.Thread] = None
rs_stop_flag = False
active_spreads: List[Dict[str, object]] = []
last_rebalance_ts: Optional[datetime] = None


class RSConfig(BaseModel):
    enabled: bool = False
    poll_interval_sec: int = 20
    lookback_window: int = 30
    rebalance_interval_sec: int = 300
    top_n: int = 2
    bottom_n: int = 2
    min_rs_gap: float = 0.5
    max_notional_usd: float = 50.0
    use_all_balance: bool = True
    symbols: List[str] = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "ADAUSDC", "XRPUSDC"]
    use_testnet: bool = config.BOLL_USE_TESTNET


rs_config = RSConfig()


def get_rs_client():
    return config.boll_client


def parse_symbol_assets(symbol: str) -> Tuple[str, str]:
    info = get_rs_client().get_symbol_info(symbol)
    return info["baseAsset"], info["quoteAsset"]


def get_free_balance(asset: str) -> float:
    client = get_rs_client()
    if client is None:
        return 0.0
    acc = client.get_account()
    for bal in acc["balances"]:
        if bal["asset"] == asset:
            return float(bal["free"])
    return 0.0


def adjust_quantity(symbol: str, qty: float) -> float:
    client = get_rs_client()
    if client is None:
        return 0.0
    info = client.get_symbol_info(symbol)
    lot_filter = next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")
    return clamp_to_step(qty, lot_filter["stepSize"], lot_filter["minQty"])


def place_market_order(symbol: str, side: str, quantity: float):
    client = get_rs_client()
    if client is None:
        raise RuntimeError("rs_client is None – configure it, or mock in tests")
    try:
        return client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
    except BinanceAPIException as e:
        print(f"[RS] Binance error: {e}")
        return None


def compute_relative_strength(prices: List[float], window: int) -> float:
    if len(prices) < 2:
        return 0.0

    window = max(2, min(window, len(prices)))
    returns = []
    for i in range(len(prices) - window, len(prices) - 1):
        prev, curr = prices[i], prices[i + 1]
        if prev == 0:
            continue
        returns.append((curr - prev) / prev)

    if not returns:
        return 0.0

    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = var ** 0.5 if var > 0 else 0.0
    return mean / std if std > 0 else 0.0


def _record_state(session) -> RSState:
    state = session.query(RSState).first()
    if not state:
        state = RSState(
            last_rebalance=last_rebalance_ts,
            open_spreads=len(active_spreads),
            quote_asset=_infer_quote_asset(rs_config.symbols),
        )
        session.add(state)
        session.commit()
        session.refresh(state)
    state.last_rebalance = last_rebalance_ts
    state.open_spreads = len(active_spreads)
    state.quote_asset = _infer_quote_asset(rs_config.symbols)
    session.commit()
    return state


def _infer_quote_asset(symbols: List[str]) -> str:
    for sym in symbols:
        for quote in ("USDT", "USDC", "BUSD", "BTC", "BNB"):
            if sym.endswith(quote):
                return quote
    return config.BASE_ASSET


def _update_history(symbol: str, price: float, lookback: int) -> float:
    with rs_lock:
        history = rs_price_history.setdefault(symbol, [])
        history.append(price)
        if len(history) > RS_MAX_HISTORY:
            del history[0 : len(history) - RS_MAX_HISTORY]
        rs_value = compute_relative_strength(history, lookback)
    return rs_value


def rank_universe() -> List[Tuple[str, float, float]]:
    ranked: List[Tuple[str, float, float]] = []
    with rs_lock:
        items = list(rs_price_history.items())
    for symbol, prices in items:
        if not prices:
            continue
        rs_val = compute_relative_strength(prices, rs_config.lookback_window)
        ranked.append((symbol, rs_val, prices[-1]))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def _build_spreads(ranked: List[Tuple[str, float, float]]) -> List[Dict[str, object]]:
    if len(ranked) < rs_config.top_n + rs_config.bottom_n:
        return []
    longs = ranked[: rs_config.top_n]
    shorts = ranked[-rs_config.bottom_n :]
    spreads: List[Dict[str, object]] = []
    for long_row, short_row in zip(longs, reversed(shorts)):
        rs_gap = long_row[1] - short_row[1]
        if rs_gap < rs_config.min_rs_gap:
            continue
        spreads.append(
            {
                "long": long_row[0],
                "short": short_row[0],
                "rs_gap": rs_gap,
                "notional_usd": rs_config.max_notional_usd,
            }
        )
    return spreads


def rs_loop():
    global active_spreads, last_rebalance_ts

    session = SessionLocal()
    try:
        while not rs_stop_flag:
            if not rs_config.enabled:
                time.sleep(1)
                continue

            client = get_rs_client()
            if client is None:
                time.sleep(1)
                continue

            ts = datetime.utcnow()
            tickers = client.get_all_tickers()
            price_map = {t["symbol"]: float(t["price"]) for t in tickers}

            for symbol in rs_config.symbols:
                price = price_map.get(symbol)
                if price is None:
                    continue
                rs_val = _update_history(symbol, price, rs_config.lookback_window)
                session.add(
                    RSSnapshot(
                        ts=ts,
                        symbol=symbol,
                        price=price,
                        rs=rs_val,
                    )
                )

            ranked = rank_universe()
            should_rebalance = False
            if last_rebalance_ts is None:
                should_rebalance = True
            else:
                delta = (datetime.utcnow() - last_rebalance_ts).total_seconds()
                should_rebalance = delta >= rs_config.rebalance_interval_sec

            if should_rebalance and ranked:
                spreads = _build_spreads(ranked)
                if spreads:
                    active_spreads = spreads
                    last_rebalance_ts = datetime.utcnow()
                    for spread in spreads:
                        session.add(
                            RSTrade(
                                ts=last_rebalance_ts,
                                long_symbol=spread["long"],
                                short_symbol=spread["short"],
                                rs_gap=spread["rs_gap"],
                                notional=spread["notional_usd"],
                            )
                        )

            _record_state(session)
            session.commit()

            time.sleep(rs_config.poll_interval_sec)
    finally:
        session.close()


def start_rs_thread():
    global rs_thread, rs_stop_flag
    if rs_thread and rs_thread.is_alive():
        return
    rs_stop_flag = False
    rs_thread = threading.Thread(target=rs_loop, daemon=True)
    rs_thread.start()


def stop_rs_thread():
    global rs_stop_flag
    rs_stop_flag = True
    if rs_thread:
        rs_thread.join(timeout=1.0)
