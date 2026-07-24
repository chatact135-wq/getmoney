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
LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Initializing Hybrid Confluence Engine...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0} for pair in PAIRS}
last_logged_signal = {}
signal_timestamps = {}

def fetch_market_data(symbol: str):
    # CHANGED: outputsize increased to 250 to accurately calculate the 200 EMA
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=250&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
            return f"API Error: {response.get('message', 'Unknown')}"
        if "values" not in response:
            return "API Error: No data returned."
        
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return f"Fetch Exception: {str(e)}"

def calculate_indicators(df):
    """Calculates EMA, RSI, and ATR using pure Pandas for high-speed server execution."""
    # 1. Exponential Moving Averages (Trend)
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    # 2. Relative Strength Index (Momentum)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss = -delta.where(delta < 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # 3. Average True Range (Volatility/Risk)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=14, adjust=False).mean()
    
    return df

def analyze_hybrid_confluence(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2

    if isinstance(data, str):
        return {"action": "WAIT", "reason": data, "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    # Require at least 200 candles to calculate the 200 EMA properly
    if data is None or len(data) < 200:
        return {"action": "WAIT", "reason": "Wait: Building indicator history (Requires 200+ candles)...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = calculate_indicators(data)
        
        # Current Closed Candle (-2 because -1 is the active, moving candle)
        current = df.iloc[-2]
        # Previous Closed Candle (-3)
        prev = df.iloc[-3]
        
        close = float(current["close"])
        open_price = float(current["open"])
        low = float(current["low"])
        high = float(current["high"])
        candle_time = current["datetime"]

        ema_200 = float(current["ema_200"])
        ema_20 = float(current["ema_20"])
        rsi = float(current["rsi"])
        atr = float(current["atr"])

        # 1. TIME LOCK FILTER (8:30 PM UAE / 16:30 UTC)
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        # 2. HYBRID EXECUTION LOGIC
        action = "WAIT"
        reason = f"Macro Trend: {'BULLISH' if close > ema_200 else 'BEARISH'} | 20 EMA: ${round(ema_20, decimals)} | RSI: {round(rsi, 1)}"

        # BUY SETUP: 
        # - Macro Trend is Bullish (Close > 200 EMA)
        # - Price pulled back to or below the 20 EMA (Low <= 20 EMA)
        # - RSI is recovering from oversold dip (< 45)
        # - Candle closed green (Close > Open)
        if close > ema_200 and low <= ema_20 and prev["rsi"] < 45 and close > open_price:
            action = "BUY"
            reason = f"15M Trend Pullback: Bullish bounce off 20 EMA (${round(ema_20, decimals)})"

        # SELL SETUP: 
        # - Macro Trend is Bearish (Close < 200 EMA)
        # - Price rallied to or above the 20 EMA (High >= 20 EMA)
        # - RSI is rejecting from overbought spike (> 55)
        # - Candle closed red (Close < Open)
        elif close < ema_200 and high >= ema_20 and prev["rsi"] > 55 and close < open_price:
            action = "SELL"
            reason = f"15M Trend Pullback: Bearish rejection at 20 EMA (${round(ema_20, decimals)})"

        # 3. SIGNAL PACKAGING & DYNAMIC ATR RISK LOGGING
        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps and action != "WAIT": 
            signal_timestamps[signal_id] = int(time.time())

        if action == "WAIT":
            return {
                "action": "WAIT", "entry": round(close, decimals), "sl": "-", "tp": "-", 
                "support": round(ema_200, decimals), "resistance": round(ema_20, decimals),
                "reason": reason, "timestamp": 0
            }
        else:
            # DYNAMIC RISK MANAGEMENT using ATR (Average True Range)
            # Stop Loss = 1.5x Current Volatility
            # Take Profit = 2.5x Current Volatility
            sl_distance = atr * 1.5
            tp_distance = atr * 2.5
            
            sl_calc = close - sl_distance if action == "BUY" else close + sl_distance
            tp_calc = close + tp_distance if action == "BUY" else close - tp_distance
            
            signal = {
                "action": action, "entry": round(close, decimals), "sl": round(sl_calc, decimals),
                "tp": round(tp_calc, decimals), "support": round(ema_200, decimals), 
                "resistance": round(ema_20, decimals), "reason": reason, "timestamp": signal_timestamps[signal_id]
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
        return {"action": "WAIT", "reason": f"Hybrid Math Error: {str(calc_error)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

async def background_bot_loop():
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                signal = analyze_hybrid_confluence(df, pair, db)
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
