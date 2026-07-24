import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

# Use Railway persistent volume directory if available, otherwise local folder
if os.path.exists("/data"):
    DB_PATH = "sqlite:////data/trade_journal.db"
else:
    DB_PATH = "sqlite:///./trade_journal.db"

engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_uae_time():
    return datetime.utcnow() + timedelta(hours=4)

class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String, default="XAU/USD")
    action = Column(String)  # e.g., "BUY (Breakout)" or "SELL (Pullback)"
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    reason = Column(String)
    timestamp = Column(DateTime, default=get_uae_time)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
