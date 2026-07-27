import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

# Grab the permanent database connection URL from Railway
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # SQLAlchemy requires 'postgresql://' instead of 'postgres://'
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    print("DATABASE ALERT: Successfully connected to permanent database!")
    engine = create_engine(DATABASE_URL)
else:
    print("DATABASE ALERT: No external DB found. Using temporary local file.")
    # Fallback to local SQLite if the URL is missing
    engine = create_engine("sqlite:///./trade_journal.db", connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_uae_time():
    # Sync timestamps strictly to UAE local time (UTC+4)
    return datetime.utcnow() + timedelta(hours=4)

class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String, default="XAU/USD")
    action = Column(String) 
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    reason = Column(String)
    timestamp = Column(DateTime, default=get_uae_time)

# Create the tables in the database
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
