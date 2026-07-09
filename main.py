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

# The order here determines how they appear on the dashboard.
PAIRS = [
    "XAU/USD",
    "EUR/USD",
    # "GBP/USD"  <-- Commented out for now
]

# Memory stores
last_logged_signal = {} 
signal_timestamps = {}
active_trend = {} 

def fetch_market_data(symbol: str):
    # UPDATED: interval is now 15min
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
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

    # DYNAMIC BOLLINGER BANDS: 2.8 for Gold to prevent early ceiling hits, 2.0 for Forex
    bb_std = 2.8 if "XAU" in pair else 2.0
    bb = df.ta.bbands(length=20, std=bb_std)
    
    bb_cols = bb.columns
    lower_band = bb[[c for c in bb_cols if "BBL" in c][0]].iloc[-2]
    upper_band = bb[[c for c in bb_cols if "BBU" in c][0]].iloc[-2]

    close = df.iloc[-2]["close"]
    candle_time = df.iloc[-2]["datetime"]

    # DYNAMIC DECIMALS: 2 for Gold, 5 for Forex
    decimals = 2 if "XAU" in pair else 5

    # DIAGNOSTIC LOGIC
    if active_trend.get(pair) == "BUY" and not (ema5 < ema13):
        return {"action": "WAIT", "reason": "Wait: Trend locked (BUY active)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}
    if active_trend.get(pair) == "SELL" and not (ema5 > ema13):
        return {"action": "WAIT", "reason": "Wait: Trend locked (SELL active)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # CONDITIONS
    if ema5 > ema13 and rsi > 52 and close < upper_band:
        action = "BUY"
        active_trend[pair] = "BUY"
        reason = "Bullish Breakout"
    elif ema5 < ema13 and rsi < 48 and close > lower_band:
        action = "SELL"
        active_trend[pair] = "SELL"
        reason = "Bearish Breakout"
    else:
        # Identify specifically why it failed
        if not (rsi > 52 or rsi < 48): reason = "Wait: RSI Neutral"
        elif not (ema5 > ema13 or ema5 < ema13): reason = "Wait: EMAs flat"
        elif close >= upper_band: reason = "Wait: Price hitting ceiling"
        elif close <= lower_band: reason = "Wait: Price hitting floor"
        else: reason = "Wait: No signal"
        return {"action": "WAIT", "reason": reason, "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # Signal Calculation
    signal_id = f"{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps: signal_timestamps[signal_id] = int(time.time())

    signal = {
        "action": action, 
        "entry": round(close, decimals),
        "sl": round(close - (1.3*atr) if action == "BUY" else close + (1.3*atr), decimals),
        "tp": round(close + (1.5*atr) if action == "BUY" else close - (1.5*atr), decimals),
        "reason": reason, 
        "timestamp": signal_timestamps[signal_id]
    }

    # Log to DB
    if last_logged_signal.get(pair) != str(candle_time):
        db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                            stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
        db.commit()
        last_logged_signal[pair] = str(candle_time)
    return signal

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(50).all()
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals(db: Session = Depends(get_db)):
    signals = {}
    for pair in PAIRS:
        df = fetch_market_data(pair)
        signals[pair] = analyze_strategy(df, pair, db)
    return signals
