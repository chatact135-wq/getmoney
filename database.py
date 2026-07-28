import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    print("DATABASE ALERT: Connected to permanent PostgreSQL database!")
    engine = create_engine(DATABASE_URL)
else:
    print("DATABASE ALERT: No external DB URL found. Using local SQLite.")
    engine = create_engine("sqlite:///./trade_journal.db", connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_uae_time():
    return datetime.utcnow() + timedelta(hours=4)

class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String, default="XAU/USD")
    action = Column(String)  # Includes system name, e.g., "BUY (Fair Value Gap)"
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
