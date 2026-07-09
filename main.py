import os
import time
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Safely import DB so the app doesn't crash if database.py is missing
try:
    from sqlalchemy.orm import Session
    from database import engine, SessionLocal, Base, TradeJournal, get_db
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    SessionLocal = None
    TradeJournal = None
    def get_db():
        yield None

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")

PAIRS = [
    "XAU/USD",
    "EUR/USD",
]

last_logged_signal = {} 
signal_timestamps = {}
active_trend = {} 

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=50&apikey={TWELVEDATA_API_KEY}"
    try:
        # TIMEOUT ADDED: Prevents the server from freezing if TwelveData is slow!
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
            return {"api_error": response.get("message", "TwelveData API Error")}
        if "values" not in response:
            return {"api_error": "No 'values' found (Check API Key limits)"}
            
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]: df[col] = df[col].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return {"api_error": f"Fetch Error: {str(e)}"}

def analyze_strategy(data, pair: str, db):
    try:
        global last_logged_signal, signal_timestamps, active_trend

        # 1. FIXED TIME FILTER: Pauses strictly anytime after 8:30 PM
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

        if isinstance(data, dict) and "api_error" in data:
            return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

        df = data
        if df is None or len(df) < 25:
            return {"action": "WAIT", "reason": "Wait: Gathering data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

        # Indicators
        ema5 = float(df.ta.ema(length=5).iloc[-2])
        ema13 = float(df.ta.ema(length=13).iloc[-2])
        rsi = float(df.ta.rsi(length=14).iloc[-2])
        atr = float(df.ta.atr(length=14).iloc[-2])

        bb_std = 2.8 if "XAU" in pair else 2.0
        bb = df.ta.bbands(length=20, std=bb_std)
        
        bb_cols = bb.columns
        lower_band = float(bb[[c for c in bb_cols if "BBL" in c][0]].iloc[-2])
        upper_band = float(bb[[c for c in bb_cols if "BBU" in c][0]].iloc[-2])

        close = float(df.iloc[-2]["close"])
        candle_time = df.iloc[-2]["datetime"]
        decimals = 2 if "XAU" in pair else 5

        # 2. RESTORED TREND LOCK
        if active_trend.get(pair) == "BUY" and not (ema5 < ema13):
            return {"action": "WAIT", "reason": "Wait: Trend locked (BUY active)", "entry": float(round(close, decimals)), "sl": "-", "tp": "-", "timestamp": 0}
        if active_trend.get(pair) == "SELL" and not (ema5 > ema13):
            return {"action": "WAIT", "reason": "Wait: Trend locked (SELL active)", "entry": float(round(close, decimals)), "sl": "-", "tp": "-", "timestamp": 0}

        # CONDITIONS
        if ema5 > ema13 and rsi > 52 and close < upper_band:
            action = "BUY"
            active_trend[pair] = "BUY"
            reason = "Bullish Breakout"
        elif ema5 < ema13 and rsi < 48 and close > lower_band:
            action = "SELL"
            active_trend[pair] = "SELL"
            reason = "Bearish Breakout"
        else:
            if not (rsi > 52 or rsi < 48): reason = "Wait: RSI Neutral"
            elif not (ema5 > ema13 or ema5 < ema13): reason = "Wait: EMAs flat"
            elif close >= upper_band: reason = "Wait: Price hitting ceiling"
            elif close <= lower_band: reason = "Wait: Price hitting floor"
            else: reason = "Wait: No signal"
            return {"action": "WAIT", "reason": reason, "entry": float(round(close, decimals)), "sl": "-", "tp": "-", "timestamp": 0}

        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps: signal_timestamps[signal_id] = int(time.time())

        # FORCED PYTHON FLOATS (Prevents JSON Serialization Crash)
        sl_calc = close - (1.3*atr) if action == "BUY" else close + (1.3*atr)
        tp_calc = close + (1.5*atr) if action == "BUY" else close - (1.5*atr)

        signal = {
            "action": action, 
            "entry": float(round(close, decimals)),
            "sl": float(round(sl_calc, decimals)),
            "tp": float(round(tp_calc, decimals)),
            "reason": str(reason), 
            "timestamp": int(signal_timestamps[signal_id])
        }

        # 3. SAFE DATABASE LOGGING
        if db and DB_AVAILABLE:
            try:
                if last_logged_signal.get(pair) != str(candle_time):
                    db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                                        stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
                    db.commit()
                    last_logged_signal[pair] = str(candle_time)
            except Exception:
                pass # Fail silently so UI doesn't break
            
        return signal
        
    except Exception as e:
        return {"action": "WAIT", "reason": f"CRASH: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
def journal_page(request: Request, db = Depends(get_db)):
    trades = []
    if DB_AVAILABLE and db:
        try:
            trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(50).all()
        except:
            pass
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

# REMOVED ASYNC: This prevents the entire server from freezing!
@app.get("/api/signals")
def get_signals():
    db_session = None
    if DB_AVAILABLE:
        try:
            db_session = SessionLocal()
        except Exception:
            pass 

    try:
        signals = {}
        for pair in PAIRS:
            try:
                df = fetch_market_data(pair)
                signals[pair] = analyze_strategy(df, pair, db_session)
            except Exception as strat_err:
                signals[pair] = {"action": "WAIT", "reason": f"STRAT CRASH: {str(strat_err)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
        return signals
    except Exception as master_err:
        return {"SYSTEM_ERROR": {"action": "WAIT", "reason": f"API CRASH: {str(master_err)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}}
    finally:
        if db_session:
            try: db_session.close()
            except: pass
