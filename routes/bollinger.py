from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from binance.exceptions import BinanceAPIException  # kept for runtime, not used in tests

import config
from database import SessionLocal, Trade, BollSnapshot, BollState, BollTrade
from engines import bollinger as eng
from engines.bollinger import (
    get_free_balance_boll,
    place_market_order_boll,
    adjust_quantity_boll,
)

router = APIRouter()

# =========================
# Pydantic models
# =========================


class BollStatusResponse(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    price: float
    ma: float
    upper: float
    lower: float
    position: str
    qty_asset: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    enabled: bool
    quote_balance: float
    use_testnet: bool


class BollHistoryPoint(BaseModel):
    ts: str
    price: float
    ma: float
    upper: float
    lower: float


class BollConfigModel(eng.BollConfig):
    """Thin wrapper so we can use it as response_model."""
    pass


class ManualBollingerSellRequest(BaseModel):
    symbol: str      # e.g. "HBARUSDC", "BTCUSDT"
    qty_base: float  # how much of the base asset to sell (HBAR, BTC, etc.)


class ManualBollingerSellResponse(BaseModel):
    status: str
    symbol: str
    base_asset: str
    quote_asset: str
    qty_sold: float
    quote_received_est: float


# =========================
# Config endpoints
# =========================


@router.get("/boll_config", response_model=BollConfigModel)
def get_boll_config():
    return eng.boll_config


@router.post("/boll_config", response_model=BollConfigModel)
def update_boll_config(cfg: BollConfigModel):
    """
    Update Bollinger configuration.

    - Validates the symbol's quoteAsset is one of USDT/USDC/BTC/BNB.
    - If symbol changes, clears in-memory history and BollState DB.
    - Does NOT change 'enabled' flag (controlled by /boll_start and /boll_stop).
    """
    global eng

    env_changed = cfg.use_testnet != eng.boll_config.use_testnet
    if env_changed:
        config.switch_boll_env(cfg.use_testnet)
        eng.boll_config.use_testnet = cfg.use_testnet

    # validate symbol's quote asset using the (possibly switched) client
    if cfg.symbol:
        info = config.boll_client.get_symbol_info(cfg.symbol)
        quote = info["quoteAsset"]
        allowed_quotes = {"USDT", "USDC", "BTC", "BNB"}
        if quote not in allowed_quotes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Symbol {cfg.symbol} has quoteAsset {quote}, "
                    f"but allowed quotes are: {', '.join(sorted(allowed_quotes))}"
                ),
            )

    old_symbol = eng.boll_config.symbol
    new_symbol = cfg.symbol

    # symbol changed -> reset history and DB state
    if env_changed or (new_symbol and new_symbol != old_symbol):
        with eng.boll_lock:
            eng.boll_ts_history.clear()
            eng.boll_price_history.clear()
            eng.boll_last_trade_ts = 0.0

        session = SessionLocal()
        try:
            session.query(BollState).delete()
            session.commit()
        finally:
            session.close()

        eng.current_boll_symbol = new_symbol or eng.current_boll_symbol

    # preserve current enabled state
    current_enabled = eng.boll_config.enabled
    data = cfg.dict()
    data.pop("enabled", None)

    for field, value in data.items():
        setattr(eng.boll_config, field, value)

    eng.boll_config.enabled = current_enabled
    return eng.boll_config


@router.post("/boll_start")
def boll_start():
    if not eng.boll_config.symbol:
        raise HTTPException(
            status_code=400,
            detail="Set a symbol in Bollinger config first",
        )
    eng.boll_config.enabled = True
    return {"status": "started"}


@router.post("/boll_stop")
def boll_stop():
    eng.boll_config.enabled = False
    return {"status": "stopped"}


# =========================
# Status / balances / history
# =========================


@router.get("/boll_status", response_model=BollStatusResponse)
def boll_status():
    session = SessionLocal()
    try:
        if not eng.boll_config.symbol:
            # no symbol yet → basic empty status
            return BollStatusResponse(
                symbol="",
                base_asset="",
                quote_asset="USDC" if not config.BOLL_USE_TESTNET else "USDT",
                price=0.0,
                ma=0.0,
                upper=0.0,
                lower=0.0,
                position="FLAT",
                qty_asset=0.0,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=0.0,
                enabled=eng.boll_config.enabled,
                quote_balance=0.0,
                use_testnet=eng.boll_config.use_testnet,
            )

        symbol = eng.boll_config.symbol
        base_asset, quote_asset = eng.parse_symbol_assets(symbol)

        # price and quote balance
        price = eng.get_symbol_price_boll(symbol)
        quote_balance = eng.get_free_balance_boll(quote_asset)

        with eng.boll_lock:
            if eng.boll_price_history:
                ma, std = eng.compute_ma_std_window(
                    eng.boll_price_history, max(5, eng.boll_config.window_size)
                )
            else:
                ma = price
                std = 0.0
            upper = ma + eng.boll_config.num_std * std
            lower = ma - eng.boll_config.num_std * std

        state = eng.get_boll_state(session)
        state.symbol = symbol
        if state.position == "LONG" and state.qty_asset > 0 and state.entry_price > 0:
            state.unrealized_pnl_usd = (price - state.entry_price) * state.qty_asset
        else:
            state.unrealized_pnl_usd = 0.0
        session.commit()

        return BollStatusResponse(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price=price,
            ma=ma,
            upper=upper,
            lower=lower,
            position=state.position,
            qty_asset=state.qty_asset,
            realized_pnl_usd=state.realized_pnl_usd,
            unrealized_pnl_usd=state.unrealized_pnl_usd,
            enabled=eng.boll_config.enabled,
            quote_balance=quote_balance,
            use_testnet=eng.boll_config.use_testnet,
        )
    finally:
        session.close()


@router.get("/boll_balances")
def boll_balances():
    """
    Returns all balances for the Bollinger account (boll_client),
    so you can verify USDC / USDT / etc in the sub-account.
    """
    acc = config.boll_client.get_account()
    return [
        {
            "asset": b["asset"],
            "free": float(b["free"]),
            "locked": float(b["locked"]),
        }
        for b in acc["balances"]
        if float(b["free"]) > 0 or float(b["locked"]) > 0
    ]


@router.get("/boll_history", response_model=List[BollHistoryPoint])
def boll_history(symbol: Optional[str] = None, limit: int = 300):
    target_symbol = symbol or eng.boll_config.symbol
    if not target_symbol:
        raise HTTPException(status_code=400, detail="Set a symbol to load history")

    with eng.boll_lock:
        if eng.boll_price_history and eng.boll_ts_history:
            n = min(len(eng.boll_price_history), limit)
            prices = eng.boll_price_history[-n:]
            tss = eng.boll_ts_history[-n:]
        else:
            prices = []
            tss = []

    if prices:
        points: List[BollHistoryPoint] = []
        for i in range(len(prices)):
            sub_prices = prices[max(0, i - eng.boll_config.window_size + 1) : i + 1]
            if not sub_prices:
                ma = prices[i]
                std = 0.0
            else:
                ma, std = eng.compute_ma_std_window(sub_prices, len(sub_prices))
            upper = ma + eng.boll_config.num_std * std
            lower = ma - eng.boll_config.num_std * std
            points.append(
                BollHistoryPoint(
                    ts=tss[i].isoformat(),
                    price=prices[i],
                    ma=ma,
                    upper=upper,
                    lower=lower,
                )
            )
        return points

    session = SessionLocal()
    try:
        rows = (
            session.query(BollSnapshot)
            .filter(BollSnapshot.symbol == target_symbol)
            .order_by(BollSnapshot.ts.desc())
            .limit(limit)
            .all()
        )
        if rows:
            rows = list(reversed(rows))
            return [
                BollHistoryPoint(
                    ts=r.ts.isoformat(),
                    price=r.price,
                    ma=r.ma,
                    upper=r.upper,
                    lower=r.lower,
                )
                for r in rows
            ]

        return []
    finally:
        session.close()


@router.get("/boll_config_best", response_model=BollConfigModel)
def boll_config_best(symbol: Optional[str] = None):
    session = SessionLocal()
    try:
        try:
            return eng.generate_best_boll_config_from_history(session, symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.get("/boll_trades")
def boll_trades(limit: int = 100):
    session = SessionLocal()
    try:
        trades = (
            session.query(BollTrade)
            .order_by(BollTrade.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ts": t.ts.isoformat(),
                "symbol": t.symbol,
                "side": t.side,
                "qty": t.qty,
                "price": t.price,
                "notional": t.notional,
                "fee": t.fee or 0.0,
                "pnl_usd": t.pnl_usd,
                "is_testnet": bool(t.is_testnet),
            }
            for t in trades
        ]
    finally:
        session.close()


# =========================
# Manual sell
# =========================


@router.post("/bollinger_manual_sell", response_model=ManualBollingerSellResponse)
def bollinger_manual_sell(req: ManualBollingerSellRequest):
    """
    Manually sell <qty_base> of the base asset of <symbol> for its quote
    (e.g. sell 10 HBAR in HBARUSDC, or 0.01 BTC in BTCUSDT).

    IMPORTANT for tests:
      tests monkeypatch:
        - routes.bollinger.get_free_balance_boll
        - routes.bollinger.place_market_order_boll

      So we MUST call those helpers from this module's namespace,
      not via eng.get_free_balance_boll / eng.place_market_order_boll.
    """
    if req.qty_base <= 0:
        raise HTTPException(status_code=400, detail="qty_base must be > 0")

    try:
        # derive base/quote assets from the symbol string
        base_asset, quote_asset = eng.parse_symbol_assets(req.symbol)

        # use helper that tests monkeypatch
        free_base = get_free_balance_boll(base_asset)
        if free_base <= 0:
            raise HTTPException(
                status_code=400, detail=f"No free balance for {base_asset}"
            )

        # never sell more than we have; cap at requested qty_base
        qty_requested = min(req.qty_base, free_base)

        # LOT_SIZE adjustment via helper (calls boll_client.get_symbol_info)
        qty_adj = adjust_quantity_boll(req.symbol, qty_requested)
        if qty_adj <= 0:
            raise HTTPException(
                status_code=400,
                detail="Quantity too small after Binance LOT_SIZE filter",
            )

        # estimate quote received using current price
        price = eng.get_symbol_price_boll(req.symbol)
        quote_est = qty_adj * price

        # use helper that tests monkeypatch
        order = place_market_order_boll(req.symbol, "SELL", qty_adj)
        if not order:
            raise HTTPException(status_code=500, detail="Sell order failed")

        # optional: record in generic Trade table for history
        session = SessionLocal()
        try:
            ts = datetime.utcnow()
            tr = Trade(
                ts=ts,
                side=f"{base_asset}->{quote_asset} (manual SC)",
                from_asset=base_asset,
                to_asset=quote_asset,
                qty_from=qty_adj,
                qty_to=quote_est,
                price=price,
                fee=0.0,
                pnl_usd=0.0,
                is_testnet=int(config.BOLL_USE_TESTNET),
            )
            session.add(tr)
            session.commit()
        finally:
            session.close()

        return ManualBollingerSellResponse(
            status="ok",
            symbol=req.symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            qty_sold=qty_adj,
            quote_received_est=quote_est,
        )

    except HTTPException:
        # propagate intentional errors
        raise
    except BinanceAPIException as e:
        # explicit Binance error path (for real runtime)
        raise HTTPException(status_code=400, detail=f"Binance error: {e.message}")
    except Exception as e:
        # catch-all for anything unexpected
        raise HTTPException(status_code=500, detail=str(e))


# =========================
# Symbols listing
# =========================


@router.get("/symbols")
def list_symbols():
    """
    Flat symbol list for the Bollinger account.

    Returns a flat list of symbols that have one of the allowed quote assets.
    Uses the Bollinger client so you see what that account can trade.
    """
    info = config.boll_client.get_exchange_info()
    allowed_quotes = {"USDT", "USDC", "BTC", "BNB"}
    out = []
    for s in info["symbols"]:
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") not in allowed_quotes:
            continue
        out.append(
            {
                "symbol": s["symbol"],
                "baseAsset": s["baseAsset"],
                "quoteAsset": s["quoteAsset"],
            }
        )
    return out


@router.get("/symbols_grouped")
def list_symbols_grouped():
    """
    Returns symbols grouped by quote asset for UI:

      {
        "USDT": [ {symbol, baseAsset, quoteAsset}, ... ],
        "USDC": [ ... ],
        "BTC":  [ ... ],
        "BNB":  [ ... ]
      }
    """
    info = config.boll_client.get_exchange_info()
    allowed_quotes = ["USDT", "USDC", "BTC", "BNB"]

    grouped = {q: [] for q in allowed_quotes}
    for s in info["symbols"]:
        if s.get("status") != "TRADING":
            continue
        qa = s.get("quoteAsset")
        if qa not in grouped:
            continue
        grouped[qa].append(
            {
                "symbol": s["symbol"],
                "baseAsset": s["baseAsset"],
                "quoteAsset": qa,
            }
        )

    return grouped
