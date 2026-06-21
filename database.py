import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime

# Railway provides the database URL via environment variable. 
# We default to a local SQLite file for testing on your computer.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading_journal.db")

# Railway's Postgres URL might start with 'postgres://', SQLAlchemy needs 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Check_same_thread is needed for SQLite, but not for Postgres
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String, index=True)
    action = Column(String)  # BUY or SELL
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    reason = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

# Create the table
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
