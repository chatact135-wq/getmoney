import os
import time
import asyncio
import requests
import numpy as np
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

SYSTEM_KEYS = ["breakout", "pullback", "fvg", "adx_rsi", "asian_sweep"]

LATEST_SIGNALS = {
    pair: {
        sys_key: {
            "action": "WAIT", "reason": "Initializing quantitative scan...",
            "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0
        } for sys_key in SYSTEM_KEYS
    } for pair in PAIRS
}

last_logged_signal = {sys_key: {} for sys_key in SYSTEM_KEYS}
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
        
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return f"Fetch Exception: {str(e)}"

# Indicator Helper Functions
def calc_ema(df, span):
    return df['close'].ewm(span=span, adjust=False).mean()

def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calc_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_adx(df, period=14):
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)
    
    atr = calc_atr(df, period)
    plus_di = 100 * (pd.Series(df['plus_dm']).ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (pd.Series(df['minus_dm']).ewm(alpha=1/period, adjust=False).mean() / atr)
    
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx, plus_di, minus_di

# 1. Breakout System
def analyze_breakout(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        period = 55
        df_period = data.iloc[-period-2:-2]
        highest_high = df_period['high'].max()
        lowest_low = df_period['low'].min()
        
        current = data.iloc[-2]
        close = float(current["close"])
        candle_time = current["datetime"]
        
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": round(lowest_low, decimals), "resistance": round(highest_high, decimals), "timestamp": 0}

        action = "WAIT"
        reason = f"Scanning Breakout. Ceiling: ${round(highest_high, decimals)} | Floor: ${round(lowest_low, decimals)}"

        if close > highest_high:
            action = "BUY"
            reason = f"Breakout: 14-Hour Ceiling Broken (${round(highest_high, decimals)})"
        elif close < lowest_low:
            action = "SELL"
            reason = f"Breakout: 14-Hour Floor Broken (${round(lowest_low, decimals)})"

        return process_signal("breakout", pair, action, close, lowest_low if action=="BUY" else highest_high, 
                              close + (highest_high - lowest_low)*1.5 if action=="BUY" else close - (highest_high - lowest_low)*1.5,
                              lowest_low, highest_high, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 2. EMA Pullback System
def analyze_pullback(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['ema_50'] = calc_ema(df, 50)
        df['ema_20'] = calc_ema(df, 20)
        df['atr'] = calc_atr(df, 14)

        current = df.iloc[-2]
        close, open_p, low, high = float(current["close"]), float(current["open"]), float(current["low"]), float(current["high"])
        candle_time = current["datetime"]
        ema_50, ema_20, atr = float(current["ema_50"]), float(current["ema_20"]), float(current["atr"])

        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        action = "WAIT"
        reason = f"Trend: {'BULLISH' if close > ema_50 else 'BEARISH'} (50 EMA: ${round(ema_50, decimals)}) | 20 EMA: ${round(ema_20, decimals)}"

        if close > ema_50 and low <= ema_20 and close > open_p:
            action = "BUY"
            reason = f"Fast Pullback: Bullish bounce off 20 EMA (${round(ema_20, decimals)})"
        elif close < ema_50 and high >= ema_20 and close < open_p:
            action = "SELL"
            reason = f"Fast Pullback: Bearish rejection at 20 EMA (${round(ema_20, decimals)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("pullback", pair, action, close, sl, tp, ema_50, ema_20, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 3. Fair Value Gap (FVG) System
def analyze_fvg(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        
        c1, c2, c3 = df.iloc[-4], df.iloc[-3], df.iloc[-2]
        close = float(c3["close"])
        atr = float(c3["atr"])
        candle_time = c3["datetime"]

        action = "WAIT"
        reason = "Scanning Price Action for Fair Value Imbalances..."
        sl, tp, supp, res = 0.0, 0.0, 0.0, 0.0

        # Bullish FVG: Candle 1 High is lower than Candle 3 Low (Gap in Candle 2)
        if c3["low"] > c1["high"]:
            fvg_size = c3["low"] - c1["high"]
            if fvg_size > (atr * 0.3):  # Significant Gap Filter
                action = "BUY"
                supp = float(c1["high"])
                res = float(c3["low"])
                sl = supp - (atr * 1.0)
                tp = close + (atr * 2.0)
                reason = f"Bullish FVG Identified: Gap between ${round(supp, decimals)} - ${round(res, decimals)}"

        # Bearish FVG: Candle 1 Low is higher than Candle 3 High
        elif c3["high"] < c1["low"]:
            fvg_size = c1["low"] - c3["high"]
            if fvg_size > (atr * 0.3):
                action = "SELL"
                res = float(c1["low"])
                supp = float(c3["high"])
                sl = res + (atr * 1.0)
                tp = close - (atr * 2.0)
                reason = f"Bearish FVG Identified: Gap between ${round(supp, decimals)} - ${round(res, decimals)}"

        return process_signal("fvg", pair, action, close, sl, tp, supp, res, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 4. ADX + RSI Momentum System
def analyze_adx_rsi(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['rsi'] = calc_rsi(df, 14)
        df['adx'], df['p_di'], df['m_di'] = calc_adx(df, 14)
        df['atr'] = calc_atr(df, 14)

        current = df.iloc[-2]
        close = float(current["close"])
        rsi = float(current["rsi"])
        adx = float(current["adx"])
        p_di = float(current["p_di"])
        m_di = float(current["m_di"])
        atr = float(current["atr"])
        candle_time = current["datetime"]

        action = "WAIT"
        reason = f"Adx Trend Power: {round(adx, 1)} ({'STRONG' if adx > 25 else 'WEAK'}) | RSI: {round(rsi, 1)}"

        if adx >= 25 and p_di > m_di and rsi >= 55:
            action = "BUY"
            reason = f"Bullish Momentum: ADX Power ({round(adx,1)}) + RSI Expansion ({round(rsi,1)})"
        elif adx >= 25 and m_di > p_di and rsi <= 45:
            action = "SELL"
            reason = f"Bearish Momentum: ADX Power ({round(adx,1)}) + RSI Breakdown ({round(rsi,1)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("adx_rsi", pair, action, close, sl, tp, "-", "-", reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 5. Asian Session Liquidity Sweep
def analyze_asian_sweep(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        
        # Filter Asian session candles (00:00 to 08:00 UTC)
        asian_df = df[df['datetime'].dt.hour < 8]
        if len(asian_df) < 10:
            return {"action": "WAIT", "reason": "Building Asian Session Range...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        asian_high = float(asian_df.iloc[-32:]['high'].max())
        asian_low = float(asian_df.iloc[-32:]['low'].min())

        current = df.iloc[-2]
        close, open_p, high, low = float(current["close"]), float(current["open"]), float(current["high"]), float(current["low"])
        atr = float(current["atr"])
        candle_time = current["datetime"]

        action = "WAIT"
        reason = f"Asian High: ${round(asian_high, decimals)} | Asian Low: ${round(asian_low, decimals)}"

        # Liquidity Sweep Buy: Low swept Asian Low but candle closed above it
        if low < asian_low and close > asian_low and close > open_p:
            action = "BUY"
            reason = f"Liquidity Sweep: False breakdown below Asian Low (${round(asian_low, decimals)})"
        # Liquidity Sweep Sell: High swept Asian High but candle closed below it
        elif high > asian_high and close < asian_high and close < open_p:
            action = "SELL"
            reason = f"Liquidity Sweep: False breakout above Asian High (${round(asian_high, decimals)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("asian_sweep", pair, action, close, sl, tp, asian_low, asian_high, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# Shared Signal Processing & Database Logging
def process_signal(sys_key, pair, action, entry, sl, tp, support, resistance, reason, candle_time, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2

    signal_id = f"{sys_key}_{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps and action != "WAIT":
        signal_timestamps[signal_id] = int(time.time())

    signal = {
        "action": action,
        "entry": round(entry, decimals) if entry != "-" else "-",
        "sl": round(sl, decimals) if sl != "-" and sl != 0.0 else "-",
        "tp": round(tp, decimals) if tp != "-" and tp != 0.0 else "-",
        "support": round(support, decimals) if support != "-" and support != 0.0 else "-",
        "resistance": round(resistance, decimals) if resistance != "-" and resistance != 0.0 else "-",
        "reason": reason,
        "timestamp": signal_timestamps.get(signal_id, 0) if action != "WAIT" else 0
    }

    if action != "WAIT":
        try:
            if last_logged_signal[sys_key].get(pair) != str(candle_time):
                sys_label_map = {
                    "breakout": "Breakout", "pullback": "Pullback", 
                    "fvg": "Fair Value Gap", "adx_rsi": "ADX Momentum", "asian_sweep": "Asian Sweep"
                }
                db.add(TradeJournal(
                    pair=pair,
                    action=f"{action} ({sys_label_map.get(sys_key, sys_key)})",
                    entry_price=signal["entry"],
                    stop_loss=signal["sl"],
                    take_profit=signal["tp"],
                    reason=reason
                ))
                db.commit()
                last_logged_signal[sys_key][pair] = str(candle_time)
        except Exception:
            db.rollback()

    return signal

async def background_bot_loop():
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                if not isinstance(df, str) and df is not None:
                    LATEST_SIGNALS[pair] = {
                        "breakout": analyze_breakout(df, pair, db),
                        "pullback": analyze_pullback(df, pair, db),
                        "fvg": analyze_fvg(df, pair, db),
                        "adx_rsi": analyze_adx_rsi(df, pair, db),
                        "asian_sweep": analyze_asian_sweep(df, pair, db)
                    }
        except Exception as loop_error:
            print(f"Loop error: {str(loop_error)}")
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
    try: 
        trades = db.query(TradeJournal).order_by(TradeJournal.timestamp.desc()).limit(100).all()
    except Exception: 
        trades = []
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals(): 
    return LATEST_SIGNALS
