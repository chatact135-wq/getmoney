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

# Exclusive focus on Gold
PAIRS = [
    "XAU/USD"
]

LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Initializing server matrix...", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0} for pair in PAIRS}
last_logged_signal = {} 
signal_timestamps = {}

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
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

def analyze_gold_master_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2

    # Error handling from fetch
    if isinstance(data, str):
        return {"action": "WAIT", "reason": data, "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    if data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Wait: Insufficient candles fetched", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    try:
        df = data
        close = float(df.iloc[-2]["close"])
        candle_time = df.iloc[-2]["datetime"]

        # 1. TIME LOCK FILTER (8:30 PM UAE)
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

        # 2. INDICATORS (Confluence Strategy)
        ema9 = float(df.ta.ema(length=9).iloc[-2])
        ema21 = float(df.ta.ema(length=21).iloc[-2])
        ema50 = float(df.ta.ema(length=50).iloc[-2]) 
        
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        macd_line = float(macd_df.iloc[-2].iloc[0])   
        macd_signal = float(macd_df.iloc[-2].iloc[2]) 
        
        rsi = float(df.ta.rsi(length=14).iloc[-2])
        atr = float(df.ta.atr(length=14).iloc[-2])

        # 3. CONFLUENCE LOGIC
        action = "WAIT"
        reason = "Wait: Tracking structural alignment..."

        # BUY LOGIC: Price > 50 EMA | 9 EMA > 21 EMA | MACD Line > Signal
        if close > ema50 and ema9 > ema21 and macd_line > macd_signal:
            if 50 < rsi < 70:
                action = "BUY"
                reason = "Trend & Momentum Confluence (BUY)"
            elif rsi >= 70: 
                reason = "Wait: Overbought (RSI > 70)"
                
        # SELL LOGIC: Price < 50 EMA | 9 EMA < 21 EMA | MACD Line < Signal
        elif close < ema50 and ema9 < ema21 and macd_line < macd_signal:
            if 30 < rsi < 50:
                action = "SELL"
                reason = "Trend & Momentum Confluence (SELL)"
            elif rsi <= 30: 
                reason = "Wait: Oversold (RSI < 30)"
        
        # Consolidation & Minor Pullbacks
        else:
            if close > ema50 and ema9 < ema21: reason = "Wait: Bullish Pullback Phase"
            elif close < ema50 and ema9 > ema21: reason = "Wait: Bearish Pullback Phase"
            else: reason = "Wait: Awaiting Momentum Cross"

        # 4. SIGNAL PACKAGING & DB LOGGING
        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps and action != "WAIT": 
            signal_timestamps[signal_id] = int(time.time())

        if action == "WAIT":
            return {"action": "WAIT", "entry": round(close, decimals), "sl": "-", "tp": "-", "reason": reason, "timestamp": 0}
        else:
            # Gold Risk Parameters: 1.5x ATR Stop Loss, 2.0x ATR Take Profit
            sl_calc = close - (1.5 * atr) if action == "BUY" else close + (1.5 * atr)
            tp_calc = close + (2.0 * atr) if action == "BUY" else close - (2.0 * atr)
            
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
                signal = analyze_gold_master_strategy(df, pair, db)
                LATEST_SIGNALS[pair] = signal
        except Exception as loop_error:
            for pair in PAIRS:
                LATEST_SIGNALS[pair] = {"action": "WAIT", "reason": f"Loop Crash: {str(loop_error)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
        finally:
            db.close()
            
        # Scan frequency set to 60 seconds
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_bot_loop())

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request): 
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    try: trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(50).all()
    except: trades = []
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals(): 
    return LATEST_SIGNALS
