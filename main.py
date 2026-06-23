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
PAIRS = ["EUR/USD", "GBP/USD"]

# Memory stores
last_logged_signal = {}
signal_timestamps = {}
active_trend = {}  # NEW: Tracks the current active trend to prevent late entries

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error from TwelveData")}
        if "values" not in response:
            return {"api_error": "No price data returned. Check API Key or Credits."}
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps, active_trend
    
    # SAFETY: Time Filter (Auto-shutdown at 8:30 PM UAE Time)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if current_time.hour == 20 and current_time.minute >= 30:
        return {"action": "WAIT", "reason": "Market Danger Zone (Past 8:30 PM)", "entry": "-", "sl": "-", "tp": "-"}

    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}
    
    df = data
    current_price = "-"
    if df is not None and len(df) > 0:
        current_price = round(df.iloc[-1]["close"], 5)
    if df is None or len(df) < 20:
        return {"action": "WAIT", "reason": f"Gathering Candles ({len(df) if df is not None else 0}/20)", "entry": current_price, "sl": "-", "tp": "-"}
    
    ema5 = df.ta.ema(length=5).iloc[-2]
    ema13 = df.ta.ema(length=13).iloc[-2]
    rsi = df.ta.rsi(length=14).iloc[-2]
    atr = df.ta.atr(length=14).iloc[-2]
    candle_time = df.iloc[-2]["datetime"]
    close = df.iloc[-2]["close"]
    
    sl_distance = 1.0 * atr
    tp_distance = 1.5 * atr
    
    action = "WAIT"
    reason = "No clear 5m momentum"
    
    # TREND LOCK LOGIC: Only triggers if the trend has flipped to a new direction
    if ema5 > ema13 and rsi > 55 and active_trend.get(pair) != "BUY":
        action = "BUY"
        active_trend[pair] = "BUY" # LOCK: Prevents repeated buys in the same trend
        reason = "New Bullish Breakout (5m)"
    elif ema5 < ema13 and rsi < 45 and active_trend.get(pair) != "SELL":
        action = "SELL"
        active_trend[pair] = "SELL" # LOCK: Prevents repeated sells in the same trend
        reason = "New Bearish Breakout (5m)"
    
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
            pair=pair, action=signal["action"], entry_price=signal["entry"],
            stop_loss=signal["sl"], take_profit=signal["tp"], reason=signal["reason"]
        )
        db.add(new_trade)
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
