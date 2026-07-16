import os
import time
import asyncio
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

PAIRS = [
    "XAU/USD",
    "EUR/USD",
]

# GLOBAL STATE: Stores the background bot's latest findings
LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Booting up...", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0} for pair in PAIRS}

last_logged_signal = {} 
signal_timestamps = {}

def fetch_market_data(symbol: str):
    """Fetches high-quality 5-minute candles."""
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
            return None
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]: df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except:
        return None

def analyze_dynamic_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps

    decimals = 2 if "XAU" in pair else 5

    if data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Wait: Gathering market data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    df = data
    close = float(df.iloc[-2]["close"])
    candle_time = df.iloc[-2]["datetime"]

    # 1. TIME SAFETY FILTER (8:30 PM Cutoff)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # 2. DYNAMIC INDICATORS
    ema9 = float(df.ta.ema(length=9).iloc[-2])
    ema21 = float(df.ta.ema(length=21).iloc[-2])
    ema50 = float(df.ta.ema(length=50).iloc[-2]) 
    
    rsi = float(df.ta.rsi(length=14).iloc[-2])
    atr = float(df.ta.atr(length=14).iloc[-2])

    # 3. ALGORITHMIC LOGIC
    action = "WAIT"
    reason = "Wait: Scanning for alignment..."

    if close > ema50 and ema9 > ema21:
        if 50 < rsi < 68:
            action = "BUY"
            reason = "Intraday Bullish Wave Confirmed"
        elif rsi >= 68: reason = "Wait: BUY blocked (Market overbought)"
    elif close < ema50 and ema9 < ema21:
        if 32 < rsi < 50:
            action = "SELL"
            reason = "Intraday Bearish Wave Confirmed"
        elif rsi <= 32: reason = "Wait: SELL blocked (Market oversold)"
    else:
        if ema9 > ema21 and close < ema50: reason = "Wait: Minor upward correction"
        elif ema9 < ema21 and close > ema50: reason = "Wait: Minor downward pullback"
        else: reason = "Wait: Market consolidating flat"

    # 4. SIGNAL PACKAGING & TIMESTAMPING
    signal_id = f"{pair}_{str(candle_time)}_{action}"
    
    # Only assign a new timestamp if this is a brand new signal
    if signal_id not in signal_timestamps and action != "WAIT": 
        signal_timestamps[signal_id] = int(time.time())

    if action == "WAIT":
        signal = {
            "action": "WAIT", "entry": round(close, decimals),
            "sl": "-", "tp": "-", "reason": reason, "timestamp": 0
        }
    else:
        sl_calc = close - (1.5 * atr) if action == "BUY" else close + (1.5 * atr)
        tp_calc = close + (2.0 * atr) if action == "BUY" else close - (2.0 * atr)
        
        signal = {
            "action": action, 
            "entry": round(close, decimals),
            "sl": round(sl_calc, decimals),
            "tp": round(tp_calc, decimals),
            "reason": reason, 
            "timestamp": signal_timestamps[signal_id]
        }

        # Safe DB Journal Logging in Background
        try:
            if last_logged_signal.get(pair) != str(candle_time):
                db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                    stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                db.commit()
                last_logged_signal[pair] = str(candle_time)
        except:
            db.rollback() 
            
    return signal

# --- BACKGROUND TASK ENGINE ---
async def background_bot_loop():
    """Runs infinitely in the background, logging signals even when the dashboard is closed."""
    while True:
        db = SessionLocal() # Open a database connection for the background task
        try:
            for pair in PAIRS:
                # Use to_thread to prevent the API request from freezing the web server
                df = await asyncio.to_thread(fetch_market_data, pair)
                if df is not None:
                    signal = analyze_dynamic_strategy(df, pair, db)
                    LATEST_SIGNALS[pair] = signal # Update the global state silently
        except Exception as e:
            print(f"Background Bot Error: {e}")
        finally:
            db.close()
        
        # Wait 60 seconds before scanning the market again
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    """Starts the background bot automatically when Railway boots the server."""
    asyncio.create_task(background_bot_loop())

# --- FASTAPI ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    try:
        trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(50).all()
    except:
        trades = []
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals():
    # Instantly returns the cached signals from the background loop
    return LATEST_SIGNALS
