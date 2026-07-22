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

PAIRS = ["XAU/USD"]
LATEST_SIGNALS = {pair: {"action": "WAIT", "reason": "Initializing Institutional SMC Core...", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0} for pair in PAIRS}
last_logged_signal = {}
signal_timestamps = {}

def fetch_market_data(symbol: str):
    """Fetches full market data including volume for Profile rendering."""
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
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

def analyze_smc_volume_profile(data, pair: str, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2

    if isinstance(data, str):
        return {"action": "WAIT", "reason": data, "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    # Require at least 120 candles to build a statistically valid Volume Profile
    if data is None or len(data) < 120:
        return {"action": "WAIT", "reason": "Wait: Gathering Volume Profile Data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

    try:
        df = data
        close = float(df.iloc[-2]["close"])
        candle_time = df.iloc[-2]["datetime"]

        # 1. TIME LOCK FILTER (8:30 PM UAE)
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "timestamp": 0}

        # 2. MACRO TREND & RISK ENGINE
        atr = float(df.ta.atr(length=14).iloc[-2])
        ema50 = float(df.ta.ema(length=50).iloc[-2])

        # 3. VOLUME PROFILE ENGINE (Mapping the Institutional Footprints)
        vp_df = df.iloc[-101:-1].copy()
        # Round Gold prices to the nearest 0.50 to build density bins
        vp_df['price_bin'] = (vp_df['close'] * 2).round() / 2  
        volume_profile = vp_df.groupby('price_bin')['volume'].sum()
        
        if volume_profile.empty:
            return {"action": "WAIT", "reason": "Wait: Insufficient Volume Data", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
            
        # Isolate the top 5 High Volume Nodes (HVNs) where whales accumulated
        hvns = volume_profile.nlargest(5).index.tolist() 

        # 4. SMART MONEY CONCEPTS (SMC) - Fair Value Gap (FVG) Detector
        recent_df = df.iloc[-15:-1].copy()
        bullish_fvg = None
        bearish_fvg = None
        
        for i in range(2, len(recent_df)):
            # Bullish FVG Calculation: True gap between low of current and high of (i-2)
            if recent_df.iloc[i]['low'] > recent_df.iloc[i-2]['high']:
                bullish_fvg = (recent_df.iloc[i-2]['high'] + recent_df.iloc[i]['low']) / 2
                
            # Bearish FVG Calculation: True gap between high of current and low of (i-2)
            if recent_df.iloc[i]['high'] < recent_df.iloc[i-2]['low']:
                bearish_fvg = (recent_df.iloc[i-2]['low'] + recent_df.iloc[i]['high']) / 2

        # 5. EXECUTION MATRIX (SMC & Volume Confluence)
        action = "WAIT"
        reason = "Wait: Hunting FVG & Liquidity Zones..."
        
        # Proximity tolerance for a successful "Tap" into the zone
        tolerance = 1.0  

        # BUY RULES: Uptrend + Bullish FVG Backed by Heavy Volume
        if close > ema50 and bullish_fvg:
            for hvn in hvns:
                # FVG must physically overlap an institutional High Volume Node
                if abs(bullish_fvg - hvn) <= 2.5: 
                    # Current price must tap precisely into the FVG zone
                    if abs(close - bullish_fvg) <= tolerance: 
                        action = "BUY"
                        reason = "SMC Sniper: Bullish FVG + High Volume Node Tap"
                        break

        # SELL RULES: Downtrend + Bearish FVG Backed by Heavy Volume
        elif close < ema50 and bearish_fvg:
            for hvn in hvns:
                if abs(bearish_fvg - hvn) <= 2.5:
                    if abs(close - bearish_fvg) <= tolerance:
                        action = "SELL"
                        reason = "SMC Sniper: Bearish FVG + High Volume Node Tap"
                        break

        # 6. ASYNCHRONOUS JOURNALING
        signal_id = f"{pair}_{str(candle_time)}_{action}"
        if signal_id not in signal_timestamps and action != "WAIT": 
            signal_timestamps[signal_id] = int(time.time())

        if action == "WAIT":
            return {"action": "WAIT", "entry": round(close, decimals), "sl": "-", "tp": "-", "reason": reason, "timestamp": 0}
        else:
            # Tight Institutional Stops (1.0 ATR) & Asymmetrical Targets (2.5 ATR)
            sl_calc = close - (1.0 * atr) if action == "BUY" else close + (1.0 * atr)
            tp_calc = close + (2.5 * atr) if action == "BUY" else close - (2.5 * atr)
            
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
        return {"action": "WAIT", "reason": f"SMC Math Error: {str(calc_error)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}

async def background_bot_loop():
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                signal = analyze_smc_volume_profile(df, pair, db)
                LATEST_SIGNALS[pair] = signal
        except Exception as loop_error:
            for pair in PAIRS:
                LATEST_SIGNALS[pair] = {"action": "WAIT", "reason": f"Engine Fault: {str(loop_error)}", "entry": "-", "sl": "-", "tp": "-", "timestamp": 0}
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
