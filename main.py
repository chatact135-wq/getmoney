import os
import time
import asyncio
import requests
import pandas as pd
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import engine, SessionLocal, Base, TradeJournal, get_db

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")

PAIRS = ["XAU/USD"]
LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Initializing 15M (55-Period) S&R Breakout Engine...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0} for pair in PAIRS}
last_logged_signal = {}
signal_timestamps = {}

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
            return f"API Error: {response.get('message', 'Unknown')}"
        if "values" not in response:
            return "API Error: No data returned."
        
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        
        if "volume" not in df.columns or df["volume"].isnull().all():
            df["volume"] = 1.0
            
        for col in ["open", "high", "low", "close", "volume"]: 
            df[col] = df[col].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return f"Fetch Exception: {str(e)}"

def analyze_sr_breakout(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2

    if isinstance(data, str):
        return {"action": "WAIT", "reason": data, "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    # Require at least 60 candles to safely calculate a 55-period window
    if data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Wait: Building 15M S&R price history (55 periods)...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data
        close = float(df.iloc[-2]["close"])
        candle_time = df.iloc[-2]["datetime"]

        # 1. TIME LOCK FILTER (8:30 PM UAE)
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        # 2. DYNAMIC SUPPORT & RESISTANCE ENGINE (55-Period Window on 15M = ~13.75 Hours)
        df['resistance'] = df['high'].shift(1).rolling(window=55).max()
        df['support'] = df['low'].shift(1).rolling(window=55).min()
        
        resistance = float(df['resistance'].iloc[-2])
        support = float(df['support'].iloc[-2])

        # 3. BREAKOUT EXECUTION LOGIC
        action = "WAIT"
        reason = f"15M Range (55 candles): Support (${round(support, decimals)}) & Resistance (${round(resistance, decimals)})"

        # BUY: 15-minute close strictly above 55-period Resistance ceiling
        if close > resistance:
            action = "BUY"
            reason = f"15M Macro Breakout! Price closed above Resistance (${round(resistance, decimals)})"

        # SELL: 15-minute close strictly below 55-period Support floor
        elif close < support:
            action = "SELL"
            reason = f"15M Macro Breakdown! Price closed below Support (${round(support, decimals)})"

        # 4. SIGNAL PACKAGING & DB LOGGING
        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps and action != "WAIT": 
            signal_timestamps[signal_id] = int(time.time())

        if action == "WAIT":
            return {
                "action": "WAIT", "entry": round(close, decimals), "sl": "-", "tp": "-", 
                "support": round(support, decimals), "resistance": round(resistance, decimals),
                "reason": reason, "timestamp": 0
            }
        else:
            sl_calc = support if action == "BUY" else resistance
            risk_distance = abs(close - sl_calc)
            tp_calc = close + (1.5 * risk_distance) if action == "BUY" else close - (1.5 * risk_distance)
            
            signal = {
                "action": action, "entry": round(close, decimals), "sl": round(sl_calc, decimals),
                "tp": round(tp_calc, decimals), "support": round(support, decimals), 
                "resistance": round(resistance, decimals), "reason": reason, "timestamp": signal_timestamps[signal_id]
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
        return {"action": "WAIT", "reason": f"S&R Math Error: {str(calc_error)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

async def background_bot_loop():
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                signal = analyze_sr_breakout(df, pair, db)
                LATEST_SIGNALS[pair] = signal
        except Exception as loop_error:
            for pair in PAIRS:
                LATEST_SIGNALS[pair] = {"action": "WAIT", "reason": f"Engine Fault: {str(loop_error)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}
        finally:
            db.close()
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
