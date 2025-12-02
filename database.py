# database.py
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

# =========================
# DATABASE SETUP (SQLite)
# =========================

DATABASE_URL = "sqlite:///./mean_reversion.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# =========================
# DB MODELS
# =========================


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    btc = Column(Float)
    hbar = Column(Float)
    doge = Column(Float)
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
    pnl_usd = Column(Float)
    is_testnet = Column(Integer)


Base.metadata.create_all(bind=engine)
