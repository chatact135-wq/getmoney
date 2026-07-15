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
    """Fetches high-quality 5-minute candles to capture sharp intraday swings."""
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
        return {"action": "WAIT", "reason": "Wait: Building initial market matrix", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    df = data
    close = float(df.iloc[-2]["close"])
    candle_time = df.iloc[-2]["datetime"]

    # 1. TIME SAFETY FILTER (8:30 PM Cutoff)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # 2. CALCULATE EXECUTABLE DYNAMIC INDICATORS
    ema9 = float(df.ta.ema(length=9).iloc[-2])
    ema21 = float(df.ta.ema(length=21).iloc[-2])
    ema50 = float(df.ta.ema(length=50).iloc[-2]) # Replaces the rigid 200 EMA
    
    rsi = float(df.ta.rsi(length=14).iloc[-2])
    atr = float(df.ta.atr(length=14).iloc[-2])

    # 3. BIDIRECTIONAL ALGORITHMIC LOGIC
    action = "WAIT"
    reason = "Wait: Scanning for alignment..."

    # BULLISH SWING WAVE CONDITIONS
    if close > ema50 and ema9 > ema21:
        if 50 < rsi < 68:
            action = "BUY"
            reason = "Intraday Bullish Wave Confirmed"
        elif rsi >= 68:
            reason = "Wait: BUY blocked (Market overbought)"
            
    # BEARISH SWING WAVE CONDITIONS
    elif close < ema50 and ema9 < ema21:
        if 32 < rsi < 50:
            action = "SELL"
            reason = "Intraday Bearish Wave Confirmed"
        elif rsi <= 32:
            reason = "Wait: SELL blocked (Market oversold)"
            
    # DIAGNOSTIC TRANSITION STATES
    else:
        if ema9 > ema21 and close < ema50:
            reason = "Wait: Minor upward correction in a local downtrend"
        elif ema9 < ema21 and close > ema50:
            reason = "Wait: Minor pullback in a local uptrend"
        else:
            reason = "Wait: Market consolidating flat"

    # 4. VOLATILITY PROTECTION (ATR) & SIGNAL PACKAGING
    signal_id = f"{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps: 
        signal_timestamps[signal_id] = int(time.time())

    if action == "WAIT":
        signal = {
            "action": "WAIT", "entry": round(close, decimals),
            "sl": "-", "tp": "-", "reason": reason, "timestamp": 0
        }
    else:
        # Volatility adjusted parameters
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

        # Safe DB Journal Logging
        try:
            if last_logged_signal.get(pair) != str(candle_time):
                db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                    stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                db.commit()
                last_logged_signal[pair] = str(candle_time)
        except:
            db.rollback() 
            
    return signal

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
        signals[pair] = analyze_dynamic_strategy(df, pair, db)
    return signals
