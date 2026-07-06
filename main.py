import os
import time
import requests
import pandas as pd
import pandas_ta as ta
import asyncio
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from database import get_db, TradeJournal
from metaapi_cloud_sdk import MetaApi

# Configuration
TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

app = FastAPI()

# Global variables for connection
connection = None

async def get_streaming_connection():
    global connection
    if connection is None:
        api = MetaApi(TOKEN)
        # Retrieve account via correct SDK namespace
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        
        # Deploy if necessary
        if account.state != 'DEPLOYED':
            await account.deploy()
            # Wait for deployment to stabilize
            await asyncio.sleep(10) 
            
        connection = account.get_streaming_connection()
        await connection.connect()
        await connection.wait_synchronized()
    return connection

@app.on_event("startup")
async def startup_event():
    # Initialize connection on startup
    await get_streaming_connection()

async def execute_trade(action, symbol, entry_price):
    try:
        conn = await get_streaming_connection()
        positions = conn.terminal_state.positions
        # Check for existing positions
        if any(p['symbol'] == symbol.replace('/', '') for p in positions):
            return None

        move = 0.0001
        if action == "BUY":
            tp, sl = entry_price + (18 * move), entry_price - (50 * move)
            return await conn.create_market_buy_order(symbol.replace('/', ''), 0.1, sl, tp)
        else:
            tp, sl = entry_price - (18 * move), entry_price + (50 * move)
            return await conn.create_market_sell_order(symbol.replace('/', ''), 0.1, sl, tp)
    except Exception as e:
        print(f"Trade execution failed: {e}")

def analyze_strategy(data, pair: str, db: Session):
    # ... [Your logic for ema/rsi/bb] ...
    # Trigger trade in background
    if action in ["BUY", "SELL"]:
        asyncio.create_task(execute_trade(action, pair, close))
    # ... [Rest of logging] ...
