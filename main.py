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

def fetch_tf_data(symbol: str, interval: str, outputsize: int = 250):
    """Helper to fetch data for specific timeframes safely."""
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVEDATA_API_KEY}"
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

def analyze_mtf_strategy(pair: str, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2 if "XAU" in pair else 5

    # 1. FETCH MULTI-TIMEFRAME DATA
    df_5m = fetch_tf_data(pair, "5min", outputsize=100)
    df_1h = fetch_tf_data(pair, "1h", outputsize=250) # Need 250 for 1H 200 EMA

    if df_5m is None or df_1h is None or len(df_1h) < 210 or len(df_5m) < 30:
        return {"action": "WAIT", "reason": "Wait: Syncing Timeframes...", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    # Current execution parameters (5-Minute chart)
    close_5m = float(df_5m.iloc[-2]["close"])
    candle_time_5m = df_5m.iloc[-2]["datetime"]

    # Macro parameters (1-Hour chart)
    close_1h = float(df_1h.iloc[-2]["close"])
    ema200_1h = float(df_1h.ta.ema(length=200).iloc[-2])

    # 2. UAE TIME FILTER (8:30 PM Cutoff)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": round(close_5m, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # 3. CALCULATE 5-MINUTE INDICATORS FOR FREQUENCY
    ema5 = float(df_5m.ta.ema(length=5).iloc[-2])
    ema13 = float(df_5m.ta.ema(length=13).iloc[-2])
    rsi = float(df_5m.ta.rsi(length=14).iloc[-2])
    atr = float(df_5m.ta.atr(length=14).iloc[-2])

    bb_std = 2.8 if "XAU" in pair else 2.0
    bb = df_5m.ta.bbands(length=20, std=bb_std)
    bb_cols = bb.columns
    lower_band = float(bb[[c for c in bb_cols if "BBL" in c][0]].iloc[-2])
    upper_band = float(bb[[c for c in bb_cols if "BBU" in c][0]].iloc[-2])

    # 4. CONDITIONAL LOGIC (1H Trend Filter + 5M Execution triggers)
    action = "WAIT"
    
    if close_1h > ema200_1h: # Macro Uptrend
        if ema5 > ema13 and rsi > 52 and close_5m < upper_band:
            action = "BUY"
            reason = "5M Breakout Aligned with 1H Macro Uptrend"
        else:
            if ema5 < ema13: reason = "Wait: Blocked counter-trend 5M SELL"
            elif not (rsi > 52): reason = "Wait: 5M RSI Neutral"
            elif close_5m >= upper_band: reason = "Wait: 5M Price Overextended"
            else: reason = "Wait: Scanning 1H Bullish Market..."
            
    else: # Macro Downtrend
        if ema5 < ema13 and rsi < 48 and close_5m > lower_band:
            action = "SELL"
            reason = "5M Breakout Aligned with 1H Macro Downtrend"
        else:
            if ema5 > ema13: reason = "Wait: Blocked counter-trend 5M BUY"
            elif not (rsi < 48): reason = "Wait: 5M RSI Neutral"
            elif close_5m <= lower_band: reason = "Wait: 5M Price Overextended"
            else: reason = "Wait: Scanning 1H Bearish Market..."

    # Signal Output & Journaling Calculation
    signal_id = f"{pair}_{str(candle_time_5m)}_{action}"
    if signal_id not in signal_timestamps: 
        signal_timestamps[signal_id] = int(time.time())

    if action == "WAIT":
        signal = {
            "action": "WAIT", "entry": round(close_5m, decimals),
            "sl": "-", "tp": "-", "reason": reason, "timestamp": 0
        }
    else:
        sl_calc = close_5m - (1.3 * atr) if action == "BUY" else close_5m + (1.3 * atr)
        tp_calc = close_5m + (1.5 * atr) if action == "BUY" else close_5m - (1.5 * atr)
        
        signal = {
            "action": action, 
            "entry": round(close_5m, decimals),
            "sl": round(sl_calc, decimals),
            "tp": round(tp_calc, decimals),
            "reason": reason, 
            "timestamp": signal_timestamps[signal_id]
        }

        try:
            if last_logged_signal.get(pair) != str(candle_time_5m):
                db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                    stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                db.commit()
                last_logged_signal[pair] = str(candle_time_5m)
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
        signals[pair] = analyze_mtf_strategy(pair, db)
    return signals
