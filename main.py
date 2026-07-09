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

PAIRS = [
    "XAU/USD",
    "EUR/USD",
]

last_logged_signal = {} 
signal_timestamps = {}

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        if "status" in response and response["status"] == "error":
            return None
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]: df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except:
        return None

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps

    decimals = 2 if "XAU" in pair else 5

    if data is None or len(data) < 25:
        return {"action": "WAIT", "reason": "Wait: Gathering data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    df = data
    close = float(df.iloc[-2]["close"])
    candle_time = df.iloc[-2]["datetime"]

    # SIMPLE TIME FILTER (8:30 PM UAE)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # Indicators
    ema5 = float(df.ta.ema(length=5).iloc[-2])
    ema13 = float(df.ta.ema(length=13).iloc[-2])
    rsi = float(df.ta.rsi(length=14).iloc[-2])
    atr = float(df.ta.atr(length=14).iloc[-2])

    bb_std = 2.8 if "XAU" in pair else 2.0
    bb = df.ta.bbands(length=20, std=bb_std)
    
    bb_cols = bb.columns
    lower_band = float(bb[[c for c in bb_cols if "BBL" in c][0]].iloc[-2])
    upper_band = float(bb[[c for c in bb_cols if "BBU" in c][0]].iloc[-2])

    # CONDITIONS
    action = "WAIT"
    if ema5 > ema13 and rsi > 52 and close < upper_band:
        action = "BUY"
        reason = "Bullish Breakout"
    elif ema5 < ema13 and rsi < 48 and close > lower_band:
        action = "SELL"
        reason = "Bearish Breakout"
    else:
        if not (rsi > 52 or rsi < 48): reason = "Wait: RSI Neutral"
        elif not (ema5 > ema13 or ema5 < ema13): reason = "Wait: EMAs flat"
        elif close >= upper_band: reason = "Wait: Price hitting ceiling"
        elif close <= lower_band: reason = "Wait: Price hitting floor"
        else: reason = "Wait: No signal"

    # Signal Calculation
    signal_id = f"{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps: 
        signal_timestamps[signal_id] = int(time.time())

    if action == "WAIT":
        signal = {
            "action": "WAIT", 
            "entry": round(close, decimals),
            "sl": "-",
            "tp": "-",
            "reason": reason, 
            "timestamp": 0
        }
    else:
        sl_calc = close - (1.3*atr) if action == "BUY" else close + (1.3*atr)
        tp_calc = close + (1.5*atr) if action == "BUY" else close - (1.5*atr)
        
        signal = {
            "action": action, 
            "entry": round(close, decimals),
            "sl": round(sl_calc, decimals),
            "tp": round(tp_calc, decimals),
            "reason": reason, 
            "timestamp": signal_timestamps[signal_id]
        }

        # Safe DB Logging
        try:
            if last_logged_signal.get(pair) != str(candle_time):
                db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                    stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                db.commit()
                last_logged_signal[pair] = str(candle_time)
        except:
            db.rollback() 
            
    return signal

# FIXED: Fast API 0.112.0+ TemplateResponse Formatting
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
async def get_signals(db: Session = Depends(get_db)):
    signals = {}
    for pair in PAIRS:
        df = fetch_market_data(pair)
        signals[pair] = analyze_strategy(df, pair, db)
    return signals
