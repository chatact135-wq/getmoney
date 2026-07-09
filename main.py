import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

app = FastAPI(title="Quant Signal System API")

# Enable CORS (Good practice for production APIs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🧠 YOUR QUANTITATIVE TRADING LOGIC
# ==========================================
def calculate_live_signals():
    """
    THIS is where your actual trading bot logic goes!
    You would connect to your broker/exchange (e.g., Binance, OANDA, MetaTrader, CCXT),
    download the 5M and 15M candles, and run your algorithms.
    """
    
    # Example of dynamically fetching the current time for the "triggered" field
    current_time = datetime.now().strftime("%H:%M:%S")

    # Replace these hardcoded dictionaries with your actual live calculations
    xau_data = {
        "pair": "XAU/USD",
        "timeframe": "15M",
        "signal": "BUY",  # e.g., if rsi < 30: return "BUY"
        "triggered": f"Updated at {current_time}",
        "entry": "2350.25", # e.g., current_close_price
        "reason": "M15 Support Breakout + Volume Spike",
        "take_profit": "2365.00",
        "stop_loss": "2340.00"
    }

    eur_data = {
        "pair": "EUR/USD",
        "timeframe": "5M",
        "signal": "SELL",
        "triggered": f"Updated at {current_time}",
        "entry": "1.08520",
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
    """Serves the frontend dashboard."""
    try:
        with open("index.html", "r") as file:
            return HTMLResponse(content=file.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Error: index.html not found.</h1>", 
            status_code=404
        )

@app.get("/api/signals")
def get_signals():
    """API Endpoint that the HTML dashboard calls every 45 seconds."""
    # Calls your live trading logic and returns it as JSON
    live_data = calculate_live_signals()
    return live_data

# ==========================================
# 🚀 SERVER EXECUTION
# ==========================================
if __name__ == "__main__":
    # This allows you to run the file directly via `python main.py` for local testing
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
