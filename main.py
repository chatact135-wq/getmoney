import os
import time
import requests
import pandas as pd
import pandas_ta as ta
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, TradeJournal
from metaapi_cloud_sdk import MetaApi

# --- CONFIGURATION ---
TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

PAIRS = ["EUR/USD", "GBP/USD"]
last_logged_signal = {}
signal_timestamps = {}
active_trend = {}

meta_api_client = None
connection = None

async def get_meta_api():
    global meta_api_client, connection
    if meta_api_client is None:
        meta_api_client = MetaApi(TOKEN)
        account = await meta_api_client.get_account(ACCOUNT_ID)
        # We need a streaming connection for order execution
        connection = account.get_streaming_connection()
        await connection.connect()
        await connection.wait_synchronized()
    return connection

async def execute_trade(action, symbol, entry_price):
    try:
        conn = await get_meta_api()
        # Prevent redundant trades: check if position already exists for this pair
        positions = conn.terminal_state.positions
        if any(p['symbol'] == symbol.replace('/', '') for p in positions):
            return None

        # Risk Management: TP 18 moves, SL 50 moves (1 move = 0.0001)
        move = 0.0001
        if action == "BUY":
            tp = round(entry_price + (18 * move), 5)
            sl = round(entry_price - (50 * move), 5)
            return await conn.create_market_buy_order(symbol.replace('/', ''), 0.1, sl, tp)
        else:
            tp = round(entry_price - (18 * move), 5)
            sl = round(entry_price + (50 * move), 5)
            return await conn.create_market_sell_order(symbol.replace('/', ''), 0.1, sl, tp)
    except Exception as e:
        print(f"Trade execution failed: {e}")

@app.on_event("startup")
async def startup_event():
    await get_meta_api()

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url).json()
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "Unknown API Error")}
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]: df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Server Error: {str(e)}"}

def analyze_strategy(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps, active_trend
    
    current_time = datetime.utcnow() + timedelta(hours=4)
    if current_time.hour == 20 and current_time.minute >= 30:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
    
    df = data
    if df is None or len(df) < 25:
        return {"action": "WAIT", "reason": "Wait: Gathering data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
    
    ema5 = df.ta.ema(length=5).iloc[-2]
    ema13 = df.ta.ema(length=13).iloc[-2]
    rsi = df.ta.rsi(length=14).iloc[-2]
    atr = df.ta.atr(length=14).iloc[-2]
    bb = df.ta.bbands(length=20, std=2)
    lower_band = bb[[c for c in bb.columns if "BBL" in c][0]].iloc[-2]
    upper_band = bb[[c for c in bb.columns if "BBU" in c][0]].iloc[-2]
    
    close = df.iloc[-2]["close"]
    candle_time = df.iloc[-2]["datetime"]
    
    if active_trend.get(pair) == "BUY" and not (ema5 < ema13):
        return {"action": "WAIT", "reason": "Wait: Trend locked (BUY active)", "entry": round(close, 5), "sl": "-", "tp": "-", "timestamp": 0}
    if active_trend.get(pair) == "SELL" and not (ema5 > ema13):
        return {"action": "WAIT", "reason": "Wait: Trend locked (SELL active)", "entry": round(close, 5), "sl": "-", "tp": "-", "timestamp": 0}
    
    if ema5 > ema13 and rsi > 52 and close < upper_band:
        action, active_trend[pair], reason = "BUY", "BUY", "Bullish Breakout"
    elif ema5 < ema13 and rsi < 48 and close > lower_band:
        action, active_trend[pair], reason = "SELL", "SELL", "Bearish Breakout"
    else:
        return {"action": "WAIT", "reason": "No signal", "entry": round(close, 5), "sl": "-", "tp": "-", "timestamp": 0}

    # Execute the automated trade
    asyncio.create_task(execute_trade(action, pair, close))

    signal_id = f"{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps: signal_timestamps[signal_id] = int(time.time())
    
    signal = {"action": action, "entry": round(close, 5), "reason": reason, "timestamp": signal_timestamps[signal_id]}
    
    if last_logged_signal.get(pair) != str(candle_time):
        db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], reason=reason))
        db.commit()
        last_logged_signal[pair] = str(candle_time)
    return signal

@app.get("/api/signals")
async def get_signals(db: Session = Depends(get_db)):
    signals = {}
    for pair in PAIRS:
        df = fetch_market_data(pair)
        signals[pair] = analyze_strategy(df, pair, db)
    return signals
