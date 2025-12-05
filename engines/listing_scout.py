import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from binance.exceptions import BinanceAPIException

import config
from database import SessionLocal, Trade
from engines import listings_service

logger = logging.getLogger(__name__)


@dataclass
class ScoutPosition:
    symbol: str
    qty: float
    entry_price: float
    target_price: float
    listed_at: datetime | None = None


@dataclass
class ListingScoutState:
    enabled: bool = False
    use_testnet: bool = config.DEFAULT_ENV == "testnet"
    last_error: str | None = None
    last_run: datetime | None = None
    watched: Set[str] = field(default_factory=set)
    positions: Dict[str, ScoutPosition] = field(default_factory=dict)
    target_notional_eur: float = float(os.getenv("LISTING_SCOUT_NOTIONAL_EUR", "10"))
    pump_profit_pct: float = float(os.getenv("LISTING_SCOUT_PUMP_PCT", "0.08"))


state = ListingScoutState()
scout_thread: Optional[threading.Thread] = None
stop_event = threading.Event()

POLL_SECONDS = int(os.getenv("LISTING_SCOUT_POLL_SECONDS", "45"))


def _quote_for_env(use_testnet: bool) -> str:
    return "USDT" if use_testnet else "USDC"


def _adjust_quantity(info: dict, qty: float) -> float:
    lot = next((f for f in info.get("filters", []) if f.get("filterType") == "LOT_SIZE"), None)
    if not lot:
        return qty

    step = float(lot.get("stepSize", 0))
    min_qty = float(lot.get("minQty", 0))
    max_qty = float(lot.get("maxQty", 0))

    if step <= 0:
        return qty

    adjusted = math.floor(qty / step) * step
    if adjusted < min_qty:
        return 0.0
    if max_qty > 0:
        adjusted = min(adjusted, max_qty)
    return float(adjusted)


def _resolve_symbol(client, base: str, fallback_pair: str | None, preferred_quote: str) -> Tuple[str | None, dict | None]:
    base = base.upper().replace("/", "")
    candidates: List[str] = [f"{base}{preferred_quote}"]

    if fallback_pair:
        candidates.append(fallback_pair.replace("/", ""))

    alt_quote = "USDT" if preferred_quote != "USDT" else "USDC"
    candidates.append(f"{base}{alt_quote}")

    for symbol in candidates:
        try:
            info = client.get_symbol_info(symbol)
            if info:
                return symbol, info
        except BinanceAPIException:
            continue
        except Exception:
            continue

    return None, None


def _record_trade(symbol: str, side: str, qty: float, price: float, use_testnet: bool) -> None:
    session = SessionLocal()
    base_asset = symbol.replace("USDT", "").replace("USDC", "")
    quote_asset = "USDT" if symbol.endswith("USDT") else "USDC"
    try:
        trade = Trade(
            ts=datetime.utcnow(),
            side=f"{side} {symbol} (listing scout)",
            from_asset=base_asset if side == "SELL" else quote_asset,
            to_asset=quote_asset if side == "SELL" else base_asset,
            qty_from=qty if side == "SELL" else qty * price,
            qty_to=qty * price if side == "SELL" else qty,
            price=price,
            fee=0.0,
            pnl_usd=0.0,
            is_testnet=int(use_testnet),
        )
        session.add(trade)
        session.commit()
    finally:
        session.close()


def _buy_listing(client, listing) -> None:
    preferred_quote = _quote_for_env(state.use_testnet)
    trade_symbol, info = _resolve_symbol(client, listing.symbol, listing.pair, preferred_quote)
    if not trade_symbol or not info:
        logger.info("No tradeable symbol for listing %s", listing.symbol)
        return

    ticker = client.get_symbol_ticker(symbol=trade_symbol)
    price = float(ticker["price"])
    qty = state.target_notional_eur / price
    qty_adj = _adjust_quantity(info, qty)
    if qty_adj <= 0:
        logger.info("Qty too small for %s (computed=%s)", trade_symbol, qty)
        return

    order = client.order_market(symbol=trade_symbol, side="BUY", quantity=qty_adj)
    if not order:
        logger.warning("Buy order failed for %s", trade_symbol)
        return

    fills = order.get("fills") or []
    fill_price = float(fills[0].get("price", price)) if fills else price
    target_price = fill_price * (1 + state.pump_profit_pct)
    state.positions[trade_symbol] = ScoutPosition(
        symbol=trade_symbol,
        qty=qty_adj,
        entry_price=fill_price,
        target_price=target_price,
        listed_at=listing.listed_at,
    )
    _record_trade(trade_symbol, "BUY", qty_adj, fill_price, state.use_testnet)
    logger.info("Bought %s qty %.6f at %.4f (target %.4f)", trade_symbol, qty_adj, fill_price, target_price)


def _maybe_exit(client) -> None:
    to_remove: List[str] = []
    for symbol, pos in state.positions.items():
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            if price >= pos.target_price:
                info = client.get_symbol_info(symbol)
                qty_adj = _adjust_quantity(info or {}, pos.qty)
                if qty_adj <= 0:
                    continue
                order = client.order_market(symbol=symbol, side="SELL", quantity=qty_adj)
                if order:
                    fills = order.get("fills") or []
                    fill_price = float(fills[0].get("price", price)) if fills else price
                    _record_trade(symbol, "SELL", qty_adj, fill_price, state.use_testnet)
                    to_remove.append(symbol)
        except Exception:
            logger.exception("Failed to evaluate exit for %s", symbol)
            continue

    for symbol in to_remove:
        state.positions.pop(symbol, None)


def _loop() -> None:
    while not stop_event.wait(POLL_SECONDS):
        try:
            client = config.create_mr_client(state.use_testnet)
            listings = listings_service.get_recent_listings(
                exchange_type="cex", exchange="Binance", minutes=180
            )
            state.last_run = datetime.utcnow()
            if not client:
                state.last_error = "Binance client unavailable"
                continue

            for listing in listings:
                if listing.symbol in state.watched:
                    continue
                state.watched.add(listing.symbol)
                _buy_listing(client, listing)

            _maybe_exit(client)
            state.last_error = None
        except Exception as exc:  # pragma: no cover - logged only
            logger.exception("Listing scout loop failed: %s", exc)
            state.last_error = str(exc)


def start_scout(use_testnet: bool | None = None) -> None:
    global scout_thread
    if use_testnet is not None:
        state.use_testnet = use_testnet

    if scout_thread and scout_thread.is_alive():
        state.enabled = True
        return

    stop_event.clear()
    scout_thread = threading.Thread(target=_loop, name="listing-scout", daemon=True)
    scout_thread.start()
    state.enabled = True
    logger.info("Listing scout started (testnet=%s)", state.use_testnet)


def stop_scout() -> None:
    global scout_thread
    state.enabled = False
    stop_event.set()
    if scout_thread and scout_thread.is_alive():
        scout_thread.join(timeout=2)
    scout_thread = None
    logger.info("Listing scout stopped")


def get_status() -> dict:
    open_positions = [
        {
            "symbol": p.symbol,
            "qty": p.qty,
            "entry_price": p.entry_price,
            "target_price": p.target_price,
            "listed_at": p.listed_at,
        }
        for p in state.positions.values()
    ]
    return {
        "enabled": state.enabled,
        "use_testnet": state.use_testnet,
        "last_error": state.last_error,
        "last_run": state.last_run,
        "watched": sorted(state.watched),
        "positions": open_positions,
        "target_notional_eur": state.target_notional_eur,
        "pump_profit_pct": state.pump_profit_pct,
    }


def get_config() -> dict:
    return {
        "target_notional_eur": state.target_notional_eur,
        "pump_profit_pct": state.pump_profit_pct,
    }


def update_config(target_notional_eur: float, pump_profit_pct: float) -> dict:
    state.target_notional_eur = target_notional_eur
    state.pump_profit_pct = pump_profit_pct
    return get_config()

