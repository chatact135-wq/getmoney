import os
import time
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import engine, SessionLocal, Base, TradeJournal, get_db

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")
PAIRS = ["EUR/USD", "GBP/USD"]

# Dynamic memory stores
last_logged_signal = {}
signal_timestamps = {}

# --- Cooldown Tracker ---
last_trade_execution_times = {}
COOLDOWN_MINUTES = 5  

# --- Timezone Settings ---
TIMEZONE_OFFSET = 4  # +4 Hours

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=50&timezone=UTC&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error from TwelveData")}
            
        if "values" not in response:
            return {"api_error": "No price data returned. Check API Key or Credits."}
        
        df = pd.DataFrame(response["values"])
        
        # Timezone Fix (+4 Hours) 
        df["datetime"] = pd.to_datetime(df["datetime"]) + timedelta(hours=TIMEZONE_OFFSET)
        
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
            
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps, last_trade_execution_times
    
    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}

    df = data
    
    current_price = "-"
    if df is not None and len(df) > 0:
        current_price = round(df.iloc[-1]["close"], 5)

    if df is None or len(df) < 30:
        return {"action": "WAIT", "reason": f"Gathering Candles ({len(df) if df is not None else 0}/30)", "entry": current_price, "sl": "-", "tp": "-"}

    try:
        # --- Predictive Indicators (Bollinger Bands & RSI) ---
        bbands = df.ta.bbands(length=20, std=2)
        rsi_series = df.ta.rsi(length=14)
        atr_series = df.ta.atr(length=14)

        if bbands is None or bbands.empty or rsi_series is None or atr_series is None:
