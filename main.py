import os
import time
import requests
import asyncio
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
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
latest_signals = {}  # NEW: Stores the current market state for the dashboard

# --- Cooldown Tracker ---
last_trade_execution_times = {}
COOLDOWN_MINUTES = 5  

# --- Timezone Settings ---
TIMEZONE_OFFSET = 4  # +4 Hours

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=50&timezone=UTC&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error from TwelveData")}
            
        if "values" not in response:
            return {"api_error": "No price data returned. Check API Key or Credits."}
        
        df = pd.DataFrame(response["values"])
        
        df["datetime"] = pd.to_datetime(df["datetime"]) + timedelta(hours=TIMEZONE_OFFSET)
        
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
            
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps, last_trade_execution_times
    
    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}

    df = data
    
    current_price = "-"
    if df is not None and len(df) > 0:
        current_price = round(df.iloc[-1]["close"], 5)

    if df is None or len(df) < 30:
        return {"action": "WAIT", "reason": f"Gathering Candles ({len(df) if df is not None else 0}/30)", "entry": current_price, "sl": "-", "tp": "-"}

    try:
        # OPTION C: Loosened the fence from 2.0 to 1.5 so it catches trades faster
        bbands = df.ta.bbands(length=20, std=1.5)
        rsi_series = df.ta.rsi(length=14)
        atr_series = df.ta.atr(length=14)

        if bbands is None or bbands.empty or rsi_series is None or atr_series is None:
            return {"action": "WAIT", "reason": "Calculating Indicators", "entry": current_price, "sl": "-", "tp": "-"}

        candle_time = df.iloc[-2]["datetime"]
        open_price = df.iloc[-2]["open"]
        close = df.iloc[-2]["close"]

        lower_band = bbands.iloc[:, 0].iloc[-2]
        upper_band = bbands.iloc[:, 2].iloc[-2]
        
        rsi = rsi_series.iloc[-2]
        atr = atr_series.iloc[-2]

        sl_distance = 1.0 * atr
        tp_distance = 1.5 * atr

        action = "WAIT"
        reason = "No clear reversal setup"
        
        # OPTION C: Loosened RSI triggers (40/60 instead of 35/65)
        # --- BUY THE BOTTOM ---
        if (open_price < lower_band or close < lower_band) and rsi < 40 and close > open_price:
            action = "BUY"
            reason = "Bottom Reversal Bounce (5m)"
            
        # --- SELL THE TOP ---
        elif (open_price > upper_band or close > upper_band) and rsi > 60 and close < open_price:
            action = "SELL"
            reason = "Top Reversal Drop (5m)"

    except Exception as e:
        return {"action": "WAIT", "reason": f"System Error: {str(e)}", "entry": current_price, "sl": "-", "tp": "-"}

    current_time_dt = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    
    if action in ["BUY", "SELL"]:
        if pair in last_trade_execution_times:
            time_since_last = current_time_dt - last_trade_execution_times[pair]
            cooldown_expiry = timedelta(minutes=COOLDOWN_MINUTES)
            
            if time_since_last < cooldown_expiry:
                seconds_left = int((cooldown_expiry - time_since_last).total_seconds())
                action = "WAIT"
                reason = f"Cooldown Active ({seconds_left}s remaining)"

    signal = None

    if action in ["BUY", "SELL"]:
        signal_id = f"{pair}_{str(candle_time)}_{action}"
        
        if signal_id not in signal_timestamps:
            signal_timestamps[signal_id] = int(time.time())
            
        signal = {
            "action": action,
            "entry": round(close, 5),
            "sl": round(close - sl_distance, 5) if action == "BUY" else round(close + sl_distance, 5),
            "tp": round(close + tp_distance, 5) if action == "BUY" else round(close - tp_distance, 5),
            "reason": reason,
            "timestamp": signal_timestamps[signal_id],
            "candle_time": str(candle_time)
        }
    else:
        return {"action": "WAIT", "reason": reason, "entry": current_price, "sl": "-", "tp": "-"}

    if signal and last_logged_signal.get(pair) != str(candle_time):
        new_trade = TradeJournal(
            pair=pair,
            action=signal["action"],
            entry_price=signal["entry"],
            stop_loss=signal["sl"],
            take_profit=signal["tp"],
            reason=signal["reason"],
            timestamp=current_time_dt 
        )
        db.add(new_trade)
        db.commit()
        last_logged_signal[pair] = str(candle_time)
        
        last_trade_execution_times[pair] = current_time_dt

    return signal

# =====================================================================
# NEW: AUTONOMOUS BACKGROUND ENGINE
# This loop runs 24/7 on your server, even if the website is completely closed
# =====================================================================
async def autonomous_bot_loop():
    while True:
        # Create a database session specifically for the background worker
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = fetch_market_data(pair)
                signal = analyze_strategy(df, pair, db)
                latest_signals[pair] = signal
        except Exception as e:
            print(f"Background Engine Error: {e}")
        finally:
            db.close()
            
        # The bot rests for 10 seconds, then checks the market again.
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    # As soon as Railway turns on, fire up the background engine
    asyncio.create_task(autonomous_bot_loop())
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(50).all()
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals():
    # NEW: The dashboard no longer forces the bot to calculate. 
    # It just instantly reads whatever the background engine last saved.
    return latest_signals
