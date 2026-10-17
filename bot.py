import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import time
from datetime import datetime, timedelta, timezone
import threading

# =============================================
# ⚙️ TIMEZONE & SECRETS
# =============================================
PKT_TIMEZONE = timezone(timedelta(hours=5))

def get_discord_webhook():
    try:
        return st.secrets["DISCORD_WEBHOOK"].strip()
    except Exception:
        return ""

DISCORD_WEBHOOK = get_discord_webhook()

PAIRS = {
    "USD/JPY": "JPY=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "AUD/USD": "AUDUSD=X",
}

TIMEFRAME = "1m"
SIGNAL_THRESHOLD = 1

# =============================================
# 📊 INDICATOR MATH (Pure Pandas — No Build Errors!)
# =============================================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(series):
    exp1 = series.ewm(span=12, adjust=False).mean()
    exp2 = series.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

def calc_bollinger(series, window=20, std_dev=2):
    sma = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return lower, upper

# =============================================
# 🤖 BOT LOGIC
# =============================================
st.title("🤖 Quotex Cloud Bot is Running 24/7")

if DISCORD_WEBHOOK:
    st.success("App is active. Signals are sent to Discord.")
else:
    st.error("❌ DISCORD_WEBHOOK is missing in Streamlit Secrets!")

def send_discord(message):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": message}, timeout=10)
    except Exception:
        pass

def get_data(ticker):
    df = yf.download(ticker, period="2d", interval=TIMEFRAME, progress=False)
    if df.empty:
        return df
    df = df.reset_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df.rename(columns={df.columns[0]: "time"})
    return df

def check_pair(name, ticker):
    df = get_data(ticker)
    if df.empty or len(df) < 30:
        return

    close = df["close"]
    df["rsi"] = calc_rsi(close)
    df["macd"], df["macd_sig"] = calc_macd(close)
    df["bb_low"], df["bb_high"] = calc_bollinger(close)
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()

    now = df.iloc[-1]
    prev = df.iloc[-2]

    call = 0
    put = 0
    reasons = []

    # 1. RSI
    if pd.notna(now["rsi"]):
        if now["rsi"] < 30:
            call += 1
            reasons.append(f"RSI oversold ({now['rsi']:.1f})")
        elif now["rsi"] > 70:
            put += 1
            reasons.append(f"RSI overbought ({now['rsi']:.1f})")

    # 2. MACD Crossover
    if pd.notna(now["macd"]) and pd.notna(prev["macd"]):
        if prev["macd"] < prev["macd_sig"] and now["macd"] > now["macd_sig"]:
            call += 1
            reasons.append("MACD bullish cross")
        elif prev["macd"] > prev["macd_sig"] and now["macd"] < now["macd_sig"]:
            put += 1
            reasons.append("MACD bearish cross")

    # 3. Bollinger Bands
    if pd.notna(now["bb_low"]):
        if now["close"] <= now["bb_low"]:
            call += 1
            reasons.append("Price at lower Bollinger")
        elif now["close"] >= now["bb_high"]:
            put += 1
            reasons.append("Price at upper Bollinger")

    # 4. EMA Crossover
    if pd.notna(now["ema9"]) and pd.notna(prev["ema9"]):
        if prev["ema9"] < prev["ema21"] and now["ema9"] > now["ema21"]:
            call += 1
            reasons.append("EMA golden cross")
        elif prev["ema9"] > prev["ema21"] and now["ema9"] < now["ema21"]:
            put += 1
            reasons.append("EMA death cross")

    # Check signal threshold
    ts = datetime.now(PKT_TIMEZONE).strftime("%H:%M:%S")
    if call >= SIGNAL_THRESHOLD:
        reason_str = "\n".join([f"  • {r}" for r in reasons])
        msg = (
            f"🚨 **SIGNAL: CALL 🟢**\n"
            f"📊 Pair: {name}\n"
            f"⏱ Timeframe: {TIMEFRAME}\n"
            f"💰 Price: {now['close']}\n"
            f"📈 Strength: {call}/4\n"
            f"📝 Reasons:\n{reason_str}\n"
            f"🕐 {ts}\n"
            f"⚡ PLACE TRADE NOW — you have ~25 seconds!"
        )
        send_discord(msg)
    elif put >= SIGNAL_THRESHOLD:
        reason_str = "\n".join([f"  • {r}" for r in reasons])
        msg = (
            f"🚨 **SIGNAL: PUT 🔴**\n"
            f"📊 Pair: {name}\n"
            f"⏱ Timeframe: {TIMEFRAME}\n"
            f"💰 Price: {now['close']}\n"
            f"📈 Strength: {put}/4\n"
            f"📝 Reasons:\n{reason_str}\n"
            f"🕐 {ts}\n"
            f"⚡ PLACE TRADE NOW — you have ~25 seconds!"
        )
        send_discord(msg)

def bot_loop():
    while True:
        # 1. Sync to candle boundary (:02s) using UTC+5
        now_dt = datetime.now(PKT_TIMEZONE)
        wait_time = (60 - now_dt.second) + 2
        time.sleep(wait_time)
        
        current_time_pkt = datetime.now(PKT_TIMEZONE)
        current_hour = current_time_pkt.hour  # 0 to 23
        current_day = current_time_pkt.weekday()  # 0=Mon, 5=Sat, 6=Sun

        # 2. WEEKEND CHECK (Saturday & Sunday)
        if current_day >= 5:
            print(f"[{current_time_pkt.strftime('%H:%M:%S')}] 🛑 Weekend detected. Market closed. Skipping...")
            continue

        # 3. NIGHT TIME CHECK (1:00 AM to 6:00 AM local time)
        if 1 <= current_hour < 6:
            print(f"[{current_time_pkt.strftime('%H:%M:%S')}] 🌙 Night pause (1 AM - 6 AM). Skipping live signals...")
            continue

        # 4. RUN MARKET ANALYSIS (Active Hours)
        for name, ticker in PAIRS.items():
            try:
                check_pair(name, ticker)
                time.sleep(1)
            except Exception as e:
                print(f"Error checking {name}: {e}")
                continue

        print(f"[{current_time_pkt.strftime('%H:%M:%S')}] Analysis completed for all pairs.")

# Start background thread
if "bot_thread_started" not in st.session_state:
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()
    st.session_state["bot_thread_started"] = True
