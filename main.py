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
    # Upgraded to fetch 250 candles so the 200 EMA has enough data to calculate properly
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=250&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        
        # If TwelveData rejects the request, grab their exact error message
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error from TwelveData")}
            
        if "values" not in response:
            return {"api_error": "No price data returned. Check API Key or Credits."}
        
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal
    
    # 1. Did TwelveData block us? Send the exact error to the dashboard UI
    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-"}

    df = data
    
    # 2. ALWAYS grab the most recent price to show on the screen (Even if market is closed)
    current_price = "-"
    if df is not None and len(df) > 0:
        current_price = round(df.iloc[-1]["close"], 5)

    # 3. Check if we have enough data to run the math
    if df is None or len(df) < 200:
        return {"action": "WAIT", "reason": f"Gathering Candles ({len(df) if df is not None else 0}/200)", "entry": current_price, "sl": "-", "tp": "-"}

    # Indicators
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    
    adx_df = df.ta.adx(length=14)
    df["ADX_14"] = adx_df["ADX_14"]

    row = df.iloc[-2] # Target last closed candle
    candle_time = row["datetime"]
    
    close = row["close"]
    ema50 = row["EMA_50"]
    ema200 = row["EMA_200"]
    rsi = row["RSI_14"]
    adx = row["ADX_14"]
    atr = row["ATR_14"]

    # Sideways market filter
    if adx < 22:
        return {"action": "WAIT", "reason": "Market is flat (Low ADX)", "entry": current_price, "sl": "-", "tp": "-"}

    is_bullish = ema50 > ema200
    is_bearish = ema50 < ema200

    sl_distance = 1.5 * atr
    tp_distance = 3.0 * atr

    signal = None

    if is_bullish and rsi < 35:
        signal = {
            "action": "BUY",
            "entry": round(close, 5),
            "sl": round(close - sl_distance, 5),
            "tp": round(close + tp_distance, 5),
            "reason": "Bullish Trend & Oversold RSI Pullback",
            "timestamp": int(time.time()),
            "candle_time": str(candle_time)
        }
    elif is_bearish and rsi > 65:
        signal = {
            "action": "SELL",
            "entry": round(close, 5),
            "sl": round(close + sl_distance, 5),
            "tp": round(close - tp_distance, 5),
            "reason": "Bearish Trend & Overbought RSI Pullback",
            "timestamp": int(time.time()),
            "candle_time": str(candle_time)
        }
    else:
        return {"action": "WAIT", "reason": "No high-probability setup", "entry": current_price, "sl": "-", "tp": "-"}

    # Database Logging - Check if this is a new signal
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
