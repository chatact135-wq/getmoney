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

# Configured for precious metals trading portfolio
PAIRS = [
    "XAU/USD",
    # "EUR/USD",  # Commented out per structural updates
    "XAG/USD",
]

# GLOBAL SERVER STATE
LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Initializing server matrix...", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0} for pair in PAIRS}

last_logged_signal = {} 
signal_timestamps = {}

def fetch_market_data(symbol: str):
    """Fetches clean 5-minute intervals optimizing candle lookbacks for structural indicators."""
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

def analyze_quant_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps

    # Precision pricing calibration for Precious Metals
    if "XAG" in pair:
        decimals = 3
    elif "XAU" in pair:
        decimals = 2
    else:
        decimals = 5

    if data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Wait: Synchronizing quantitative matrix", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    df = data
    close = float(df.iloc[-2]["close"])
    candle_time = df.iloc[-2]["datetime"]

    # 1. TIME LOCK FILTER (8:30 PM Cutoff)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

    # 2. ADVANCED QUANTITATIVE INDICATORS
    # Intraday Structural Trend Line
    ema50 = float(df.ta.ema(length=50).iloc[-2]) 
    
    # Momentum Convergence / Divergence Engine
    macd_df = df.ta.macd(fast=12, slow=26, signal=9)
    macd_line = float(macd_df.iloc[-2].iloc[0])   # MACD Line
    macd_signal = float(macd_df.iloc[-2].iloc[2]) # Signal Line
    
    # Relative Strength & Multi-Period Volatility
    rsi = float(df.ta.rsi(length=14).iloc[-2])
    atr = float(df.ta.atr(length=14).iloc[-2])

    # Volatility Bandwidth & Squeeze Analytics
    bb_std = 2.5 if "XAU" in pair or "XAG" in pair else 2.0
    bb = df.ta.bbands(length=20, std=bb_std)
    lower_band = float(bb.iloc[-2].iloc[0])
    upper_band = float(bb.iloc[-2].iloc[2])
    
    # Bandwidth calculation to detect low-volatility traps
    bandwidth = (upper_band - lower_band) / ema50
    historical_bandwidth_avg = float((bb.iloc[:, 2] - bb.iloc[:, 0]).rolling(window=50).mean().iloc[-2] / ema50)

    # 3. ALGORITHMIC EXECUTION MATRIX
    action = "WAIT"
    reason = "Wait: Tracking structural alignment..."

    # Volatility Squeeze Trap Safeguard
    if bandwidth < (historical_bandwidth_avg * 0.65):
        reason = "Wait: Squeeze detected (Insufficient volume/volatility)"
    
    # BULLISH CONFLUENCE BREAKOUT ENGINE
    elif close > ema50 and macd_line > macd_signal:
        if 50 < rsi < 68 and close < upper_band:
            action = "BUY"
            reason = "Institutional Momentum Breakout Confirmed"
        elif rsi >= 68: 
            reason = "Wait: BUY Overextended (Exhaustion Risk)"
        elif close >= upper_band:
            reason = "Wait: Price hitting structural overhead ceiling"
            
    # BEARISH CONFLUENCE BREAKOUT ENGINE
    elif close < ema50 and macd_line < macd_signal:
        if 32 < rsi < 50 and close > lower_band:
            action = "SELL"
            reason = "Institutional Velocity Distribution Confirmed"
        elif rsi <= 32: 
            reason = "Wait: SELL Overextended (Floor Bounce Risk)"
        elif close <= lower_band:
            reason = "Wait: Price hitting structural support floor"
            
    # TRANSITION/CONSOLIDATION PHASES
    else:
        if close > ema50 and macd_line < macd_signal:
            reason = "Wait: Bullish structure losing momentum acceleration"
        elif close < ema50 and macd_line > macd_signal:
            reason = "Wait: Bearish structure losing velocity decay"
        else:
            reason = "Wait: Market consolidating flat"

    # 4. VOLATILITY ENGINE PACKAGING & STORAGE
    signal_id = f"{pair}_{str(candle_time)}_{action}"
    
    if signal_id not in signal_timestamps and action != "WAIT": 
        signal_timestamps[signal_id] = int(time.time())

    if action == "WAIT":
        signal = {
            "action": "WAIT", "entry": round(close, decimals),
            "sl": "-", "tp": "-", "reason": reason, "timestamp": 0
        }
    else:
        # Metals require precision ATR spacing to protect capital from stop hunts
        sl_multiplier = 1.8 if "XAG" in pair else 1.5
        tp_multiplier = 2.5 if "XAG" in pair else 2.0
        
        sl_calc = close - (sl_multiplier * atr) if action == "BUY" else close + (sl_multiplier * atr)
        tp_calc = close + (tp_multiplier * atr) if action == "BUY" else close - (tp_multiplier * atr)
        
        signal = {
            "action": action, 
            "entry": round(close, decimals),
            "sl": round(sl_calc, decimals),
            "tp": round(tp_calc, decimals),
            "reason": reason, 
            "timestamp": signal_timestamps[signal_id]
        }

        # Safe DB Asynchronous Mirroring
        try:
            if last_logged_signal.get(pair) != str(candle_time):
                db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                    stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                db.commit()
                last_logged_signal[pair] = str(candle_time)
        except:
            db.rollback() 
            
    return signal

# --- ASYNC BACKGROUND SEED ENGINE ---
async def background_bot_loop():
    """Independent infinite async thread to scan precious metals portfolio continuously."""
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                if df is not None:
                    signal = analyze_quant_strategy(df, pair, db)
                    LATEST_SIGNALS[pair] = signal
        except Exception as e:
            print(f"Quant Loop Operational Alert: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    """Launches the independent algorithmic daemon upon server execution."""
    asyncio.create_task(background_bot_loop())

# --- ENDPOINTS ---
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
    return LATEST_SIGNALS
