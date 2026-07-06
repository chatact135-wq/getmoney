import os
import time
import requests
import pandas as pd
import pandas_ta as ta
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import engine, SessionLocal, Base, TradeJournal, get_db
from metaapi_cloud_sdk import MetaApi

# --- CONFIGURATION (Reading from Environment Variables) ---
TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

meta_api_client = None

async def get_meta_api():
    global meta_api_client
    if meta_api_client is None:
        meta_api_client = MetaApi(TOKEN)
    return meta_api_client

@app.on_event("startup")
async def startup_event():
    try:
        api = await get_meta_api()
        account = await api.get_account(ACCOUNT_ID)
        if account.connection_status != 'CONNECTED':
            print("Deploying account to MetaApi...")
            await account.deploy()
        print("Waiting for MetaApi connection...")
        await account.wait_connected()
        print("Successfully connected to MetaApi!")
    except Exception as e:
        print(f"Failed to connect to MetaApi: {e}")

# ... [Keep your fetch_market_data, analyze_strategy, and routes exactly as they were] ...
