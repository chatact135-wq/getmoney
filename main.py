import os
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

app = FastAPI(title="Quant Signal System API")

# Enable CORS for production environments like Railway
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 📈 YOUR QUANTITATIVE TRADING LOGIC
# ==========================================
def calculate_live_signals():
    """
    Generates the current market signals.
    """
    current_time = datetime.now().strftime("%H:%M:%S")

    xau_data = {
        "pair": "XAU/USD",
        "timeframe": "15M",
        "signal": "BUY",
        "triggered": f"Updated at {current_time}",
        "entry": "2350.25",
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
    """
    Serves the dashboard. 
    Notice we added "template" to the file path below!
    """
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    # Tells Python to look inside the 'template' folder for index.html
    html_path = os.path.join(BASE_DIR, "template", "index.html")
    
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
    """
    Endpoint that the frontend checks every 45 seconds.
    """
    live_data = calculate_live_signals()
    return live_data

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
