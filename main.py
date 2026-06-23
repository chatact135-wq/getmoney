import os
import time
import requests
import asyncio
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
latest_signals = {}  # Stores current signal state for instant dashboard loading

# --- Timezone Settings ---
TIMEZONE_OFFSET = 4  # +4 Hours

def fetch_market_data(symbol: str):
    # Set to 15min as per your previous successful setup
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=50&timezone=UTC&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error")}
        if "values" not in response:
            return {"api_error": "No price data returned."}
        
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"]) + timedelta(hours=TIMEZONE_OFFSET)
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps
    
    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}

    df = data
    if df is None or len(df) < 20:
        return {"action": "WAIT", "reason": "Gathering Candles", "entry": "-", "sl": "-", "tp": "-"}

    ema5 = df.ta.ema(length=5).iloc[-2]
    ema13 = df.ta.ema(length=13).iloc[-2]
    rsi = df.ta.rsi(length=14).iloc[-2]
    atr = df.ta.atr(length=14).iloc[-2]
    
    close = df.iloc[-2]["close"]
    candle_time = df.iloc[-2]["datetime"]
    
    sl_distance = 1.0 * atr
    tp_distance = 1.5 * atr

    action = "WAIT"
    reason = "No clear momentum"
    
    if ema5 > ema13 and rsi > 55:
        action = "BUY"
        reason = "Bullish Breakout (15m)"
    elif ema5 < ema13 and rsi < 45:
        action = "SELL"
        reason = "Bearish Breakout (15m)"

    if action != "WAIT":
        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps:
            signal_timestamps[signal_id] = int(time.time())
            
            # Save to Database
            new_trade = TradeJournal(
                pair=pair, action=action, entry_price=round(close, 5),
                stop_loss=round(close - sl_distance, 5) if action == "BUY" else round(close + sl_distance, 5),
                take_profit=round(close + tp_distance, 5) if action == "BUY" else round(close - tp_distance, 5),
                reason=reason, timestamp=datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
            )
            db.add(new_trade)
            db.commit()
            
        return {"action": action, "entry": round(close, 5), "reason": reason, "timestamp": signal_timestamps[signal_id]}
    
    return {"action": "WAIT", "reason": reason}

# --- 24/7 BACKGROUND ENGINE ---
async def autonomous_bot_loop():
    while True:
        db = SessionLocal()
        for pair in PAIRS:
            df = fetch_market_data(pair)
            latest_signals[pair] = analyze_strategy(df, pair, db)
        db.close()
        await asyncio.sleep(60) # Checks every minute

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(autonomous_bot_loop())

@app.get("/api/signals")
async def get_signals():
    return latest_signals

@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
