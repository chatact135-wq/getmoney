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
    "XAG/USD",
]

# GLOBAL SERVER STATE
LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Initializing server matrix...", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0} for pair in PAIRS}

last_logged_signal = {} 
signal_timestamps = {}

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
            # EXPOSE API ERROR TO UI
            return f"API Error: {response.get('message', 'Unknown API issue')}"
        
        if "values" not in response:
            return "API Error: No data returned. Check API Key or limit."

        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]: df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return f"Fetch Exception: {str(e)}"

def analyze_quant_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps

    if "XAG" in pair: decimals = 3
    elif "XAU" in pair: decimals = 2
    else: decimals = 5

    # Check if data is an error string sent from fetch_market_data
    if isinstance(data, str):
        return {"action": "WAIT", "reason": data, "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    if data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Wait: Insufficient candles fetched", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    try:
        df = data
        close = float(df.iloc[-2]["close"])
        candle_time = df.iloc[-2]["datetime"]

        # 1. TIME LOCK FILTER
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

        # 2. INDICATORS
        ema50 = float(df.ta.ema(length=50).iloc[-2]) 
        
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        macd_line = float(macd_df.iloc[-2].iloc[0])   
        macd_signal = float(macd_df.iloc[-2].iloc[2]) 
        
        rsi = float(df.ta.rsi(length=14).iloc[-2])
        atr = float(df.ta.atr(length=14).iloc[-2])

        bb_std = 2.5
        bb = df.ta.bbands(length=20, std=bb_std)
        lower_band = float(bb.iloc[-2].iloc[0])
        upper_band = float(bb.iloc[-2].iloc[2])
        
        bandwidth = (upper_band - lower_band) / ema50
        historical_bandwidth_avg = float((bb.iloc[:, 2] - bb.iloc[:, 0]).rolling(window=50).mean().iloc[-2] / ema50)

        # 3. LOGIC
        action = "WAIT"
        reason = "Wait: Tracking structural alignment..."

        if bandwidth < (historical_bandwidth_avg * 0.65):
            reason = "Wait: Squeeze detected (Low Volatility)"
        elif close > ema50 and macd_line > macd_signal:
            if 50 < rsi < 68 and close < upper_band:
                action = "BUY"
                reason = "Institutional Momentum Breakout"
            elif rsi >= 68: reason = "Wait: BUY Overextended"
            elif close >= upper_band: reason = "Wait: Price hitting ceiling"
        elif close < ema50 and macd_line < macd_signal:
            if 32 < rsi < 50 and close > lower_band:
                action = "SELL"
                reason = "Institutional Velocity Distribution"
            elif rsi <= 32: reason = "Wait: SELL Overextended"
            elif close <= lower_band: reason = "Wait: Price hitting floor"
        else:
            if close > ema50 and macd_line < macd_signal: reason = "Wait: Bullish structure slowing"
            elif close < ema50 and macd_line > macd_signal: reason = "Wait: Bearish structure slowing"
            else: reason = "Wait: Market consolidating flat"

        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps and action != "WAIT": 
            signal_timestamps[signal_id] = int(time.time())

        if action == "WAIT":
            return {"action": "WAIT", "entry": round(close, decimals), "sl": "-", "tp": "-", "reason": reason, "timestamp": 0}
        else:
            sl_multiplier = 1.8 if "XAG" in pair else 1.5
            tp_multiplier = 2.5 if "XAG" in pair else 2.0
            sl_calc = close - (sl_multiplier * atr) if action == "BUY" else close + (sl_multiplier * atr)
            tp_calc = close + (tp_multiplier * atr) if action == "BUY" else close - (tp_multiplier * atr)
            
            signal = {
                "action": action, "entry": round(close, decimals), "sl": round(sl_calc, decimals),
                "tp": round(tp_calc, decimals), "reason": reason, "timestamp": signal_timestamps[signal_id]
            }

            try:
                if last_logged_signal.get(pair) != str(candle_time):
                    db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                    db.commit()
                    last_logged_signal[pair] = str(candle_time)
            except:
                db.rollback() 
            return signal

    except Exception as calc_error:
        return {"action": "WAIT", "reason": f"Calculation Error: {str(calc_error)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

async def background_bot_loop():
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                signal = analyze_quant_strategy(df, pair, db)
                LATEST_SIGNALS[pair] = signal
        except Exception as loop_error:
            for pair in PAIRS:
                LATEST_SIGNALS[pair] = {"action": "WAIT", "reason": f"Loop Crash: {str(loop_error)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
        finally:
            db.close()
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_bot_loop())

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request): return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    try: trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(50).all()
    except: trades = []
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals(): return LATEST_SIGNALS
