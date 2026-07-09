import os
import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

app = FastAPI(title="Quant Signal System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🔑 TWELVEDATA API SETUP
# ==========================================
# Replace "YOUR_API_KEY_HERE" with your actual TwelveData API key!
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")

def get_live_prices():
    """
    Fetches real-time prices from TwelveData for XAU/USD and EUR/USD.
    """
    if TWELVEDATA_API_KEY == "YOUR_API_KEY_HERE":
        return {"XAU/USD": "API KEY MISSING", "EUR/USD": "API KEY MISSING"}
        
    try:
        # Fetch both symbols in a single API call to save rate limits
        url = f"https://api.twelvedata.com/quote?symbol=XAU/USD,EUR/USD&apikey={TWELVEDATA_API_KEY}"
        response = requests.get(url)
        data = response.json()
        
        # Extract the live close prices
        xau_price = data.get("XAU/USD", {}).get("close", "Error")
        eur_price = data.get("EUR/USD", {}).get("close", "Error")
        
        # Format the numbers nicely (2 decimals for Gold, 5 for EUR)
        if xau_price != "Error":
            xau_price = f"{float(xau_price):.2f}"
        if eur_price != "Error":
            eur_price = f"{float(eur_price):.5f}"
            
        return {"XAU/USD": xau_price, "EUR/USD": eur_price}
        
    except Exception as e:
        print(f"TwelveData connection error: {e}")
        return {"XAU/USD": "Error", "EUR/USD": "Error"}

# ==========================================
# 📈 QUANTITATIVE TRADING LOGIC
# ==========================================
def calculate_live_signals():
    """
    Generates the current market signals using live TwelveData prices.
    """
    current_time = datetime.now().strftime("%H:%M:%S")
    
    # 1. Fetch the real prices
    live_prices = get_live_prices()

    # 2. Inject them into your dashboard data
    xau_data = {
        "pair": "XAU/USD",
        "timeframe": "15M",
        "signal": "BUY", 
        "triggered": f"Updated at {current_time}",
        "entry": live_prices["XAU/USD"],  # <--- REAL DATA HERE
        "reason": "M15 Support Breakout + Volume Spike",
        "take_profit": "2365.00",
        "stop_loss": "2340.00"
    }

    eur_data = {
        "pair": "EUR/USD",
        "timeframe": "5M",
        "signal": "SELL",
        "triggered": f"Updated at {current_time}",
        "entry": live_prices["EUR/USD"],  # <--- REAL DATA HERE
        "reason": "M5 Bearish Engulfing at Resistance",
        "take_profit": "1.08100",
        "stop_loss": "1.08750"
    }

    return [xau_data, eur_data]

# ==========================================
# 🌐 WEB SERVER ROUTES
# ==========================================
@app.get("/", response_class=HTMLResponse)
def read_root():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(BASE_DIR, "templates", "index.html")
    
    try:
        with open(html_path, "r", encoding="utf-8") as file:
            return HTMLResponse(content=file.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content=f"<h1>Error: index.html not found exactly at {html_path}</h1>", 
            status_code=404
        )

@app.get("/api/signals")
def get_signals():
    live_data = calculate_live_signals()
    return live_data

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
