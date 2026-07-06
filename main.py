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

from metaapi_cloud_sdk import MetaApi

# Your configuration
TOKEN = 'eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiJlZWVmMTkwZDM2ZDEwODIzZTRkZTZmYmNmM2I1OWU2OCIsImFjY2Vzc1J1bGVzIjpbeyJpZCI6InRyYWRpbmctYWNjb3VudC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsidHJhZGluZy1hY2NvdW50LW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVzdC1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcnBjLWFwaSIsIm1ldGhvZHMiOlsibWV0YWFwaS1hcGk6d3M6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVhbC10aW1lLXN0cmVhbWluZy1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJtZXRhc3RhdHMtYXBpIiwibWV0aG9kcyI6WyJtZXRhc3RhdHMtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6InJpc2stbWFuYWdlbWVudC1hcGkiLCJtZXRob2RzIjpbInJpc2stbWFuYWdlbWVudC1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciIsIndyaXRlciJdLCJyZXNvdXJjZXMiOlsiKjokVVNFUl9JRCQ6KiJdfSx7ImlkIjoiY29weWZhY3RvcnktYXBpIiwibWV0aG9kcyI6WyJjb3B5ZmFjdG9yeS1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciIsIndyaXRlciJdLCJyZXNvdXJjZXMiOlsiKjokVVNFUl9JRCQ6KiJdfSx7ImlkIjoibXQtbWFuYWdlci1hcGkiLCJtZXRob2RzIjpbIm10LW1hbmFnZXItYXBpOnJlc3Q6ZGVhbGluZzoqOioiLCJtdC1tYW5hZ2VyLWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJiaWxsaW5nLWFwaSIsIm1ldGhvZHMiOlsiYmlsbGluZy1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciJdLCJyZXNvdXJjZXMiOlsiKjokVVNFUl9JRCQ6KiJdfV0sImlnbm9yZVJhdGVMaW1pdHMiOmZhbHNlLCJ0b2tlbklkIjoiMjAyMTAyMTMiLCJpbXBlcnNvbmF0ZWQiOmZhbHNlLCJyZWFsVXNlcklkIjoiZWVlZjE5MGQzNmQxMDgyM2U0ZGU2ZmJjZjNiNTllNjgiLCJpYXQiOjE3ODMzMTg4OTgsImV4cCI6MTc5MTA5NDg5OH0.bQXMYKwiqPHmVhshjmo94voBuM6y8q3dTagciZWF-_C104c3th9ZwogUekEoK_pApqLaG1rBUcx0DALpCqzHfgCNSZDow00kAs8BnXoit6Qf0UxFVuTRuufXjH2rX9BqWdugw9pQP99RmrS4VG5Nmruu6MFIbw3Av4zSZd8_2Gbe4Lnjpf5Ab7fceDb0uBasuQz076zb_DY5uSehTKqMe4LIcPn27bxs-_t1Yku07STxLvbQPNqsEjMtKXjXGOQ26yNcPQmlNNxdrXy2P1gUbzWFPiJxanyi9xaVaWGNbpO2gJTV2vspT0AAnKIu40gC68X79q-xgaAx3ziGRv4QnNjFUlHGfFaLYeBugvVKwMy7MrH9nxbfmKTuurmOiHpTREWp_o1Qb_a5Bo3lTKhEEJXjV_XzGK-LFSPU6ZYND41y8RuQkMTYYeTWTXh8Kx3dYTgpG0OljeCnZI18ypyaAVK1LBWzb0QpKdOR1_y24NV5YBWg2vCWGMLxmg_pqJi2SON2SBmyA2-QuZrAP0N73y_mWQzj4QLCwVTuQn2N1m2Nk6yPcqSiPJo3T6ppT9zN14vkDaiyMwZCBMUzvg7LLsemm6R-T8NpjjaTAEjzermbLlswNSv_0PIo3JA8wfwn4sT9jifdR_TpCqo5HcsgrB_0wJ1iils9JRYm65Dsl_Q'
ACCOUNT_ID = 'bd530c2f-488a-4aed-9179-61db80ea64ac'

# Initialize the connection
meta_api = MetaApi(TOKEN)

async def connect_to_account():
    # Retrieve the account
    account = await meta_api.get_account(ACCOUNT_ID)
    
    # Deploy/Connect
    if account.connection_status != 'CONNECTED':
        print("Deploying account...")
        await account.deploy()
        
    # Wait for connection
    print("Waiting for API to connect...")
    await account.wait_connected()
    print("Successfully connected to MetaApi!")

# You will call this function at the start of your bot loop

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")
PAIRS = ["EUR/USD", "GBP/USD"]

# Memory stores
last_logged_signal = {} 
signal_timestamps = {}
active_trend = {} 

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
    
    # 1. TIME FILTER: Stop at 8:30 PM UAE (20:30)
    current_time = datetime.utcnow() + timedelta(hours=4)
    if current_time.hour == 20 and current_time.minute >= 30:
        return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM)", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    if isinstance(data, dict) and "api_error" in data:
        return {"action": "WAIT", "reason": data["api_error"], "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
    
    df = data
    if df is None or len(df) < 25:
        return {"action": "WAIT", "reason": "Wait: Gathering data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
    
    # Indicators
    ema5 = df.ta.ema(length=5).iloc[-2]
    ema13 = df.ta.ema(length=13).iloc[-2]
    rsi = df.ta.rsi(length=14).iloc[-2]
    atr = df.ta.atr(length=14).iloc[-2]
    
    # ROBUST Bollinger Bands
    bb = df.ta.bbands(length=20, std=2)
    bb_cols = bb.columns
    lower_band = bb[[c for c in bb_cols if "BBL" in c][0]].iloc[-2]
    upper_band = bb[[c for c in bb_cols if "BBU" in c][0]].iloc[-2]
    
    close = df.iloc[-2]["close"]
    candle_time = df.iloc[-2]["datetime"]
    
    # DIAGNOSTIC LOGIC: Check why it's waiting
    if active_trend.get(pair) == "BUY" and not (ema5 < ema13):
        return {"action": "WAIT", "reason": "Wait: Trend locked (BUY active)", "entry": round(close, 5), "sl": "-", "tp": "-", "timestamp": 0}
    if active_trend.get(pair) == "SELL" and not (ema5 > ema13):
        return {"action": "WAIT", "reason": "Wait: Trend locked (SELL active)", "entry": round(close, 5), "sl": "-", "tp": "-", "timestamp": 0}
    
    # LESS CONSERVATIVE: RSI Thresholds lowered to 52 and 48
    if ema5 > ema13 and rsi > 52 and close < upper_band:
        action = "BUY"
        active_trend[pair] = "BUY"
        reason = "Bullish Breakout"
    # SELL CONDITIONS
    elif ema5 < ema13 and rsi < 48 and close > lower_band:
        action = "SELL"
        active_trend[pair] = "SELL"
        reason = "Bearish Breakout"
    else:
        # Identify specifically why it failed
        if not (rsi > 52 or rsi < 48): reason = "Wait: RSI Neutral"
        elif not (ema5 > ema13 or ema5 < ema13): reason = "Wait: EMAs flat"
        elif close >= upper_band: reason = "Wait: Price hitting ceiling"
        elif close <= lower_band: reason = "Wait: Price hitting floor"
        else: reason = "Wait: No signal"
        
        return {"action": "WAIT", "reason": reason, "entry": round(close, 5), "sl": "-", "tp": "-", "timestamp": 0}

    # If we reached here, action is BUY or SELL
    signal_id = f"{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps: signal_timestamps[signal_id] = int(time.time())
    
    # WIDER SL: Using 1.3 * ATR instead of 1.0 * ATR
    signal = {
        "action": action, "entry": round(close, 5),
        "sl": round(close - (1.3*atr) if action == "BUY" else close + (1.3*atr), 5),
        "tp": round(close + (1.5*atr) if action == "BUY" else close - (1.5*atr), 5),
        "reason": reason, "timestamp": signal_timestamps[signal_id]
    }
    
    # Log to DB
    if last_logged_signal.get(pair) != str(candle_time):
        db.add(TradeJournal(pair=pair, action=action, entry_price=signal["entry"], 
                            stop_loss=signal["sl"], take_profit=signal["tp"], reason=reason))
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
