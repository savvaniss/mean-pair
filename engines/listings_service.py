import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import and_, desc, or_

import config
from database import ListingEvent, SessionLocal
from engines.binance_listings import BinanceListingsCollector
from engines.cex_listings import CexListingsCollector
from engines.dex_listings import DexListingsCollector
from engines.listings_common import Listing

logger = logging.getLogger(__name__)

scheduler_thread: Optional[threading.Thread] = None
stop_event = threading.Event()
collector_health: Dict[str, dict] = defaultdict(dict)
collector_backoff_until: Dict[str, datetime] = {}
collectors = [
    BinanceListingsCollector(),
    CexListingsCollector(),
    DexListingsCollector(),
]


def _store_listings(listings: List[Listing]) -> None:
    if not listings:
        return

    session = SessionLocal()
    try:
        for item in listings:
            existing = (
                session.query(ListingEvent)
                .filter(
                    and_(
                        ListingEvent.symbol == item.symbol,
                        ListingEvent.source == item.source,
                        ListingEvent.pair == item.pair,
                    )
                )
                .first()
            )
            if existing:
                existing.listed_at = item.listed_at
                existing.network = item.network
                existing.url = item.url
                existing.name = item.name
                existing.exchange_type = item.exchange_type
                existing.fetched_at = datetime.utcnow()
                continue

            session.add(
                ListingEvent(
                    listed_at=item.listed_at,
                    fetched_at=datetime.utcnow(),
                    symbol=item.symbol,
                    name=item.name,
                    pair=item.pair,
                    network=item.network,
                    exchange_type=item.exchange_type,
                    source=item.source,
                    url=item.url,
                )
            )
        session.commit()
    finally:
        session.close()


def run_collector(collector) -> None:
    name = getattr(collector, "name", collector.__class__.__name__)
    now = datetime.utcnow()
    cooldown = collector_backoff_until.get(name)
    if cooldown and cooldown > now:
        logger.info("Skipping %s poll due to backoff until %s", name, cooldown)
        return

    try:
        listings = collector.fetch()
        _store_listings(listings)
        collector_health[name] = {
            "last_run": now,
            "last_error": None,
            "count": len(listings),
        }
        collector_backoff_until.pop(name, None)
    except Exception as exc:  # pragma: no cover - logged in tests
        logger.exception("Listing collector %s failed: %s", name, exc)
        backoff_minutes = 5
        collector_backoff_until[name] = now + timedelta(minutes=backoff_minutes)
        collector_health[name] = {
            "last_run": now,
            "last_error": str(exc),
            "count": 0,
        }


def start_scheduler() -> None:
    global scheduler_thread
    if config.LISTINGS_DISABLE_SCHEDULER:
        logger.info("Listings scheduler disabled via env")
        return
    if scheduler_thread:
        return

    def _loop():
        warm_start()
        while not stop_event.wait(config.LISTINGS_REFRESH_SECONDS):
            for collector in collectors:
                run_collector(collector)

    stop_event.clear()
    scheduler_thread = threading.Thread(target=_loop, name="listings-scheduler", daemon=True)
    scheduler_thread.start()
    logger.info("Listing collectors scheduled (interval=%ss)", config.LISTINGS_REFRESH_SECONDS)


def shutdown_scheduler() -> None:
    global scheduler_thread
    stop_event.set()
    if scheduler_thread and scheduler_thread.is_alive():
        scheduler_thread.join(timeout=2)
    scheduler_thread = None
    for collector in collectors:
        client = getattr(collector, "client", None)
        if client and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                logger.debug("Failed to close client for %s", getattr(collector, "name", "collector"))


def get_recent_listings(
    exchange_type: Optional[str] = None,
    exchange: Optional[str] = None,
    network: Optional[str] = None,
    minutes: int = config.LISTINGS_RETENTION_MINUTES,
    search: Optional[str] = None,
    sort: str = "listed_at_desc",
    limit: int = 200,
) -> List[ListingEvent]:
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        query = session.query(ListingEvent).filter(ListingEvent.listed_at >= cutoff)

        if exchange_type:
            query = query.filter(ListingEvent.exchange_type.ilike(exchange_type))
        if exchange:
            query = query.filter(ListingEvent.source.ilike(f"%{exchange}%"))
        if network:
            query = query.filter(ListingEvent.network.ilike(f"%{network}%"))
        if search:
            like_term = f"%{search}%"
            query = query.filter(
                or_(
                    ListingEvent.symbol.ilike(like_term),
                    ListingEvent.name.ilike(like_term),
                    ListingEvent.pair.ilike(like_term),
                )
            )
        query = query.order_by(desc(ListingEvent.listed_at) if sort.endswith("desc") else ListingEvent.listed_at)
        return query.limit(limit).all()
    finally:
        session.close()


def get_health() -> Dict[str, dict]:
    return collector_health


def warm_start():
    """Run a single collection pass synchronously to seed cache."""
    for collector in collectors:
        run_collector(collector)
