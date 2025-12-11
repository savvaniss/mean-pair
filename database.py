# database.py
import os

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# =========================
# DATABASE SETUP
# =========================

# Supports SQLite and PostgreSQL via SQLAlchemy URLs.
# Example:
#   postgres: postgresql+psycopg2://user:pass@host:5432/dbname
#
# In docker-compose, the `DATABASE_URL` is injected and points at the bundled
# Postgres service (`db`). For local development, you can override it to
# SQLite (e.g. `sqlite:///./mean_reversion.db`).
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://meanpair:meanpair@db:5432/meanpair"
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_fee_column_if_missing(conn, table_name: str):
    inspector = inspect(conn)
    if not inspector.has_table(table_name):
        return

    has_fee = any(col["name"] == "fee" for col in inspector.get_columns(table_name))
    if has_fee:
        return

    col_type = "REAL" if engine.dialect.name == "sqlite" else "DOUBLE PRECISION"
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN fee {col_type} DEFAULT 0"))


def ensure_fee_columns():
    """Ensure fee columns exist on boll_trades and trend_trades for existing DBs."""
    with engine.begin() as conn:
        _add_fee_column_if_missing(conn, "boll_trades")
        _add_fee_column_if_missing(conn, "trend_trades")


# =========================
# DB MODELS
# =========================


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    asset_a = Column(String)
    asset_b = Column(String)
    price_a = Column(Float)
    price_b = Column(Float)
    ratio = Column(Float)
    zscore = Column(Float)


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    side = Column(String)
    from_asset = Column(String)
    to_asset = Column(String)
    qty_from = Column(Float)
    qty_to = Column(Float)
    price = Column(Float)
    fee = Column(Float)
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)  # 1 testnet, 0 mainnet


class MRTradeStat(Base):
    __tablename__ = "mr_trade_stats"
    id = Column(Integer, primary_key=True, index=True)
    entry_ts = Column(DateTime, index=True)
    exit_ts = Column(DateTime, index=True)
    entry_z = Column(Float)
    exit_z = Column(Float)
    entry_ratio = Column(Float)
    exit_ratio = Column(Float)
    holding_secs = Column(Float)
    pnl_usd = Column(Float)
    z_entry_bucket = Column(String)


class State(Base):
    __tablename__ = "state"
    id = Column(Integer, primary_key=True, index=True)
    current_asset = Column(String)
    current_qty = Column(Float)
    last_ratio = Column(Float)
    last_z = Column(Float)
    realized_pnl_usd = Column(Float)
    unrealized_pnl_usd = Column(Float)


class BollState(Base):
    __tablename__ = "boll_state"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)  # e.g. "HBARUSDC"
    position = Column(String)  # "FLAT" or "LONG"
    qty_asset = Column(Float)
    entry_price = Column(Float)
    realized_pnl_usd = Column(Float)
    unrealized_pnl_usd = Column(Float)


class BollTrade(Base):
    __tablename__ = "boll_trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    symbol = Column(String)
    side = Column(String)  # "BUY" or "SELL"
    qty = Column(Float)
    price = Column(Float)
    notional = Column(Float)
    fee = Column(Float)
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)


class BollSnapshot(Base):
    __tablename__ = "boll_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    symbol = Column(String)
    price = Column(Float)
    ma = Column(Float)
    upper = Column(Float)
    lower = Column(Float)
    std = Column(Float)


class TrendState(Base):
    __tablename__ = "trend_state"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)
    position = Column(String)  # "FLAT" or "LONG"
    qty_asset = Column(Float)
    entry_price = Column(Float)
    realized_pnl_usd = Column(Float)
    unrealized_pnl_usd = Column(Float)


class TrendTrade(Base):
    __tablename__ = "trend_trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    symbol = Column(String)
    side = Column(String)  # "BUY" or "SELL"
    qty = Column(Float)
    price = Column(Float)
    notional = Column(Float)
    fee = Column(Float)
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)


class TrendSnapshot(Base):
    __tablename__ = "trend_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    symbol = Column(String)
    price = Column(Float)
    fast_ema = Column(Float)
    slow_ema = Column(Float)
    atr = Column(Float)


class RSState(Base):
    __tablename__ = "rs_state"
    id = Column(Integer, primary_key=True, index=True)
    last_rebalance = Column(DateTime, index=True)
    open_spreads = Column(Integer)
    quote_asset = Column(String)


class RSTrade(Base):
    __tablename__ = "rs_trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    long_symbol = Column(String)
    short_symbol = Column(String)
    rs_gap = Column(Float)
    notional = Column(Float)


class RSSnapshot(Base):
    __tablename__ = "rs_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    symbol = Column(String)
    price = Column(Float)
    rs = Column(Float)


class AlgoSnapshot(Base):
    __tablename__ = "algo_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    strategy = Column(String, index=True)
    symbol = Column(String, index=True)
    price = Column(Float)
    indicator_a = Column(Float)
    indicator_b = Column(Float)
    indicator_c = Column(Float)
    indicator_d = Column(Float)


class AlgoTrade(Base):
    __tablename__ = "algo_trades"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    strategy = Column(String, index=True)
    symbol = Column(String, index=True)
    side = Column(String)  # "BUY" or "SELL"
    qty = Column(Float)
    price = Column(Float)
    notional = Column(Float)
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)


class PairHealth(Base):
    __tablename__ = "pair_health"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    asset_a = Column(String)
    asset_b = Column(String)
    std = Column(Float)
    is_good = Column(Integer)  # 1 healthy movement, 0 too flat/noisy
    sample_count = Column(Integer)


class ListingEvent(Base):
    __tablename__ = "listing_events"
    id = Column(Integer, primary_key=True, index=True)
    listed_at = Column(DateTime, index=True)
    fetched_at = Column(DateTime, index=True)
    symbol = Column(String, index=True)
    name = Column(String)
    pair = Column(String, index=True)
    network = Column(String, index=True)
    exchange_type = Column(String, index=True)  # CEX/DEX
    source = Column(String, index=True)  # exchange name
    url = Column(String)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    created_at = Column(DateTime)


Base.metadata.create_all(bind=engine)
