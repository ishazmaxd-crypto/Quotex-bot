import streamlit as st
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import requests
import time
from datetime import datetime
import threading

# --- SETTINGS ---
DISCORD_WEBHOOK = "PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE"
PAIRS = {
    "USD/JPY": "JPY=X", "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X",
    "GBP/JPY": "GBPJPY=X", "EUR/JPY": "EURJPY=X", "AUD/USD": "AUDUSD=X",
}
TIMEFRAME = "1m"
SIGNAL_THRESHOLD = 1

st.title("🤖 Quotex Bot is Active")
st.write("Monitoring markets 24/7...")

def send_discord(message):
    try: requests.post(DISCORD_WEBHOOK, json={"content": message}, timeout=10)
    except: pass

def bot_loop():
    while True:
        # Sync to the start of the minute
        now = datetime.now()
        time.sleep(62 - now.second) 
        
        ts = datetime.now().strftime("%H:%M:%S")
        for name, ticker in PAIRS.items():
            try:
                df = yf.download(ticker, period="2d", interval=TIMEFRAME, progress=False)
                df.columns = [str(c).lower() for c in df.columns]
                df["rsi"] = ta.rsi(df["close"], length=14)
                
                last = df.iloc[-1]
                if last["rsi"] < 30:
                    send_discord(f"🟢 **CALL**: {name} | RSI: {last['rsi']:.1f} | Time: {ts}")
                elif last["rsi"] > 70:
                    send_discord(f"🔴 **PUT**: {name} | RSI: {last['rsi']:.1f} | Time: {ts}")
            except: continue

if 'started' not in st.session_state:
    threading.Thread(target=bot_loop, daemon=True).start()
    st.session_state['started'] = True
