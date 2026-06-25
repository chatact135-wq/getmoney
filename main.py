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

# Dynamic memory stores
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
    
    # 1. TIME FILTER: Stop at 8:30 PM UAE
    current_time = datetime.utcnow() + timedelta(hours=4)
    if current_time.hour == 20 and current_time.minute >= 30:
        return {"action": "WAIT", "reason": "Market Danger Zone (Past 8:30 PM)", "entry": "-", "sl": "-", "tp": "-"}

    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}
    
    df = data
    if df is None or len(df) < 25:
        return {"action": "WAIT", "reason": "Gathering Data...", "entry": "-", "sl": "-", "tp": "-"}
    
    # Indicators
    ema5 = df.ta.ema(length=5).iloc[-2]
    ema13 = df.ta.ema(length=13).iloc[-2]
    rsi = df.ta.rsi(length=14).iloc[-2]
    atr = df.ta.atr(length=14).iloc[-2]
    
    # Bollinger Bands for Volatility Guard
    bb = df.ta.bbands(length=20, std=2)
    lower_band = bb['BBL_20_2.0'].iloc[-2]
    upper_band = bb['BBU_20_2.0'].iloc[-2]
    
    close = df.iloc[-2]["close"]
    candle_time = df.iloc[-2]["datetime"]
    
    action = "WAIT"
    reason = "No clear momentum"

    # 2. TREND LOCK + BOLLINGER FILTER
    # BUY: Only if EMAs cross, RSI > 55, not already buying, AND price isn't at the ceiling
    if ema5 > ema13 and rsi > 55 and active_trend.get(pair) != "BUY" and close < upper_band:
        action = "BUY"
        active_trend[pair] = "BUY"
        reason = "Bullish Breakout (BB Filtered)"
            
    # SELL: Only if EMAs cross, RSI < 45, not already selling, AND price isn't at the floor
    elif ema5 < ema13 and rsi < 45 and active_trend.get(pair) != "SELL" and close > lower_band:
        action = "SELL"
        active_trend[pair] = "SELL"
        reason = "Bearish Breakout (BB Filtered)"

    if action in ["BUY", "SELL"]:
        signal = {
            "action": action, "entry": round(close, 5),
            "sl": round(close - (1.0*atr) if action == "BUY" else close + (1.0*atr), 5),
            "tp": round(close + (1.5*atr) if action == "BUY" else close - (1.5*atr), 5),
            "reason": reason, "candle_time": str(candle_time)
        }
        
        # Log to DB
        if last_logged_signal.get(pair) != str(candle_time):
            db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
            db.commit()
            last_logged_signal[pair] = str(candle_time)
        return signal
    
    return {"action": "WAIT", "reason": reason, "entry": round(close, 5), "sl": "-", "tp": "-"}

# ... [Keep your existing FastAPI app routes] ...
