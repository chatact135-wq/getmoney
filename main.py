import os
import time
import requests
import pandas as pd
import pandas_ta as ta
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

# Dynamic memory store to prevent logging duplicate signals
last_logged_signal = {}

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error from TwelveData")}
            
        if "values" not in response:
            return {"api_error": "No price data returned. Check API Key or Credits."}
        
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        
        # THE FIX: Only require open, high, low, and close. 
        # Forex does not provide volume data!
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
            
        # Safely convert volume ONLY if it actually exists (like if you switch to Crypto later)
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        # This is where your 'volume' error was being generated
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal
    
    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}

    df = data
    
    current_price = "-"
    if df is not None and len(df) > 0:
        current_price = round(df.iloc[-1]["close"], 5)

    if df is None or len(df) < 30:
        return {"action": "WAIT", "reason": f"Gathering Candles ({len(df) if df is not None else 0}/30)", "entry": current_price, "sl": "-", "tp": "-"}

    ema9_series = df.ta.ema(length=9)
    ema21_series = df.ta.ema(length=21)
    rsi_series = df.ta.rsi(length=14)
    atr_series = df.ta.atr(length=14)
    adx_df = df.ta.adx(length=14)

    candle_time = df.iloc[-2]["datetime"]
    close = df.iloc[-2]["close"]

    ema9 = ema9_series.iloc[-2]
    ema21 = ema21_series.iloc[-2]
    rsi = rsi_series.iloc[-2]
    atr = atr_series.iloc[-2]
    adx = adx_df.iloc[-2, 0]

    if pd.isna(adx) or adx < 18:
        return {"action": "WAIT", "reason": "Market is flat (Low ADX)", "entry": current_price, "sl": "-", "tp": "-"}

    is_bullish = ema9 > ema21
    is_bearish = ema9 < ema21

    sl_distance = 1.5 * atr
    tp_distance = 3.0 * atr

    signal = None

    if is_bullish and rsi < 40:
        signal = {
            "action": "BUY",
            "entry": round(close, 5),
            "sl": round(close - sl_distance, 5),
            "tp": round(close + tp_distance, 5),
            "reason": "Intraday Bullish Trend & RSI Pullback",
            "timestamp": int(time.time()),
            "candle_time": str(candle_time)
        }
    elif is_bearish and rsi > 60:
        signal = {
            "action": "SELL",
            "entry": round(close, 5),
            "sl": round(close + sl_distance, 5),
            "tp": round(close - tp_distance, 5),
            "reason": "Intraday Bearish Trend & RSI Overbought",
            "timestamp": int(time.time()),
            "candle_time": str(candle_time)
        }
    else:
        return {"action": "WAIT", "reason": "No high-probability setup", "entry": current_price, "sl": "-", "tp": "-"}

    if signal and last_logged_signal.get(pair) != str(candle_time):
        new_trade = TradeJournal(
            pair=pair,
            action=signal["action"],
            entry_price=signal["entry"],
            stop_loss=signal["sl"],
            take_profit=signal["tp"],
            reason=signal["reason"]
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
