from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from engines import listings_service

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


@router.get("/listings", response_class=HTMLResponse)
def listings_page():
    return FileResponse("templates/listings.html")


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
