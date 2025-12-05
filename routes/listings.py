from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

import config
from engines import listing_scout, listings_service
from routes.trading import _adjust_quantity, _client_for_account

router = APIRouter()


class ListingOut(BaseModel):
    symbol: str
    name: str
    pair: str
    network: str | None
    listed_at: datetime
    source: str
    url: str
    exchange_type: str


class BinanceBuyRequest(BaseModel):
    symbol: str
    notional: float = 10.0
    account: str = "mr"
    use_testnet: bool = config.DEFAULT_ENV == "testnet"


class BinanceBuyResponse(BaseModel):
    status: str
    symbol: str
    qty_executed: float
    price_used: float
    notional: float
    account: str
    quote_asset: str
    is_testnet: bool


class ScoutStartRequest(BaseModel):
    use_testnet: bool = config.DEFAULT_ENV == "testnet"


class ScoutConfig(BaseModel):
    target_notional_eur: float = Field(10.0, gt=0)
    pump_profit_pct: float = Field(0.08, gt=0)


@router.get("/listings", response_class=HTMLResponse)
def listings_page():
    return RedirectResponse(url="/?tab=listings")


@router.get("/api/listings/latest", response_model=List[ListingOut])
def listings_latest(
    exchange_type: Optional[str] = Query(None, description="cex or dex"),
    exchange: Optional[str] = Query(None, description="exchange name"),
    network: Optional[str] = Query(None, description="chain/network"),
    minutes: int = Query(60, ge=5, le=1440, description="time window in minutes"),
    search: Optional[str] = Query(None, description="search term"),
    sort: str = Query("listed_at_desc", description="listed_at_desc or listed_at_asc"),
):
    events = listings_service.get_recent_listings(
        exchange_type=exchange_type,
        exchange=exchange,
        network=network,
        minutes=minutes,
        search=search,
        sort=sort,
    )
    return [
        ListingOut(
            symbol=e.symbol,
            name=e.name,
            pair=e.pair,
            network=e.network,
            listed_at=e.listed_at,
            source=e.source,
            url=e.url,
            exchange_type=e.exchange_type,
        )
        for e in events
    ]


@router.get("/api/listings/health")
def listings_health():
    return listings_service.get_health()


@router.post("/api/listings/binance/buy", response_model=BinanceBuyResponse)
def binance_quick_buy(req: BinanceBuyRequest):
    if req.notional <= 0:
        raise HTTPException(status_code=400, detail="notional must be > 0")

    client = _client_for_account(req.account, req.use_testnet)
    if not client:
        raise HTTPException(status_code=503, detail="Binance client unavailable")

    symbol_base = req.symbol.upper().replace("/", "")
    preferred_quote = "USDT" if req.use_testnet else "USDC"
    trade_symbol = f"{symbol_base}{preferred_quote}"
    alt_symbol = f"{symbol_base}{'USDC' if preferred_quote == 'USDT' else 'USDT'}"

    try:
        info = client.get_symbol_info(trade_symbol)
        if not info and alt_symbol != trade_symbol:
            trade_symbol = alt_symbol
            info = client.get_symbol_info(trade_symbol)
        if not info:
            raise HTTPException(status_code=404, detail=f"Symbol {trade_symbol} not found")

        ticker = client.get_symbol_ticker(symbol=trade_symbol)
        price = float(ticker["price"])
        qty = req.notional / price
        qty_adj = _adjust_quantity(info, qty)
        if qty_adj <= 0:
            raise HTTPException(status_code=400, detail="Quantity too small after LOT_SIZE adjustment")

        order = client.order_market(symbol=trade_symbol, side="BUY", quantity=qty_adj)
        if not order:
            raise HTTPException(status_code=500, detail="Order failed")

        fills = order.get("fills") or []
        fill_price = float(fills[0].get("price", price)) if fills else price

        return BinanceBuyResponse(
            status="ok",
            symbol=trade_symbol,
            qty_executed=qty_adj,
            price_used=fill_price,
            notional=qty_adj * fill_price,
            account=req.account,
            quote_asset=trade_symbol[len(symbol_base) :],
            is_testnet=req.use_testnet,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - network
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/listings/binance/scout/status")
def binance_scout_status():
    return listing_scout.get_status()


@router.post("/api/listings/binance/scout/start")
def binance_scout_start(req: ScoutStartRequest):
    listing_scout.start_scout(req.use_testnet)
    return {"status": "started", "use_testnet": req.use_testnet}


@router.post("/api/listings/binance/scout/stop")
def binance_scout_stop():
    listing_scout.stop_scout()
    return {"status": "stopped"}


@router.get("/api/listings/binance/scout/config", response_model=ScoutConfig)
def binance_scout_config_get():
    return listing_scout.get_config()


@router.post("/api/listings/binance/scout/config", response_model=ScoutConfig)
def binance_scout_config_set(req: ScoutConfig):
    if req.target_notional_eur <= 0 or req.pump_profit_pct <= 0:
        raise HTTPException(status_code=400, detail="config values must be positive")
    return listing_scout.update_config(req.target_notional_eur, req.pump_profit_pct)
