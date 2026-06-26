import os
import time
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import engine, SessionLocal, Base, TradeJournal, get_db

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")
PAIRS = ["EUR/USD", "GBP/USD"]

# Memory stores
last_logged_signal = {} 
signal_timestamps = {}
active_trend = {} 

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error")}
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]: df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps, active_trend
    
    # 1. TIME FILTER: Stop at 8:30 PM UAE (20:30)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if current_time.hour == 20 and current_time.minute >= 30:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
    
    df = data
    if df is None or len(df) < 25:
        return {"action": "WAIT", "reason": "Wait: Gathering data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
    
    # Indicators
    ema5 = df.ta.ema(length=5).iloc[-2]
    ema13 = df.ta.ema(length=13).iloc[-2]
    rsi = df.ta.rsi(length=14).iloc[-2]
    atr = df.ta.atr(length=14).iloc[-2]
    
    # ROBUST Bollinger Bands
    bb = df.ta.bbands(length=20, std=2)
    bb_cols = bb.columns
    lower_band = bb[[c for c in bb_cols if "BBL" in c][0]].iloc[-2]
    upper_band = bb[[c for c in bb_cols if "BBU" in c][0]].iloc[-2]
    
    close = df.iloc[-2]["close"]
    candle_time = df.
