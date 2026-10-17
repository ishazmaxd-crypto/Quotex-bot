import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import time
from datetime import datetime, timedelta, timezone
import threading

# Define UTC+5 timezone offset
PKT_TIMEZONE = timezone(timedelta(hours=5))

# =============================================
# ⚙️ FAIL-SAFE SECRET HANDLER (No More KeyError!)
# =============================================
def get_discord_webhook():
    """Safely retrieves the Discord Webhook URL without crashing."""
    try:
        if "DISCORD_WEBHOOK" in st.secrets:
            return st.secrets["DISCORD_WEBHOOK"].strip()
    except Exception:
        pass
    return ""

DISCORD_WEBHOOK = get_discord_webhook()

PAIRS = {
    "USD/JPY": "JPY=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "GBP/JPY": "GBPJPY=X",
    "EUR/JPY": "EURJPY=X",
    "AUD/USD": "AUDUSD=X",
}

TIMEFRAME = "1m"
SIGNAL_THRESHOLD = 2

# Initialize session state for on-screen logs
if "activity_logs" not in st.session_state:
    st.session_state["activity_logs"] = []

def add_log(message):
    """Adds a timestamped log to the on-screen dashboard."""
    ts = datetime.now(PKT_TIMEZONE).strftime("%H:%M:%S")
    log_entry = f"[{ts}] {message}"
    st.session_state["activity_logs"].insert(0, log_entry)
    # Keep only the last 15 logs
    st.session_state["activity_logs"] = st.session_state["activity_logs"][:15]

# =============================================
# 🖥️ ON-SCREEN DASHBOARD UI
# =============================================
st.set_page_config(page_title="Quotex Bot Dashboard", page_icon="🤖", layout="centered")
st.title("🤖 Quotex Signals Bot — Dashboard")

# 1. Webhook Secret Checker Card
if DISCORD_WEBHOOK and DISCORD_WEBHOOK.startswith("https://discord.com"):
    st.success("✅ Discord Webhook Secret: Connected & Valid")
else:
    st.error("❌ Discord Webhook Secret: MISSING OR INVALID! Please check Streamlit App Settings -> Secrets.")

# 2. Settings Summary
st.info(f"📍 Timezone: UTC+5 | ⏱ Timeframe: {TIMEFRAME} | 🎯 Signal Threshold: {SIGNAL_THRESHOLD}")

# 3. Live Activity Log Box (Method 2)
st.subheader("📋 Live Activity & Error Log")
log_box = st.empty()

def update_ui_logs():
    if st.session_state["activity_logs"]:
        log_box.code("\n".join(st.session_state["activity_logs"]), language="text")
    else:
        log_box.info("Waiting for first scan cycle...")

# =============================================
# 📊 INDICATOR MATH (Pure Pandas)
# =============================================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_bollinger(series, window=20, std_dev=2):
    sma = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return lower, upper

# =============================================
# 🎯 5-PIP LEVEL RETEST LOGIC
# =============================================
def check_5pip_level_retest(df, lookback=30):
    now = df.iloc[-1]
    history = df.iloc[-(lookback + 1):-1]

    close_p = now["close"]
    high_p = now["high"]
    low_p = now["low"]
    is_jpy = close_p > 50

    step = 0.05 if is_jpy else 0.0005
    tolerance = 0.01 if is_jpy else 0.0001
    nearest_level = round(close_p / step) * step

    touching_top = abs(high_p - nearest_level) <= tolerance
    touching_bottom = abs(low_p - nearest_level) <= tolerance

    if not (touching_top or touching_bottom):
        return None, None

    for _, row in history.iterrows():
        if touching_top and abs(row["high"] - nearest_level) <= tolerance and row["close"] < nearest_level:
            return "PUT", nearest_level
        if touching_bottom and abs(row["low"] - nearest_level) <= tolerance and row["close"] > nearest_level:
            return "CALL", nearest_level

    return None, None

# =============================================
# 🤖 MARKET ANALYSIS ENGINE
# =============================================
def send_discord(message):
    webhook_url = get_discord_webhook()
    if not webhook_url:
        add_log("⚠️ Discord alert skipped — Webhook URL is missing!")
        return False

    try:
        r = requests.post(webhook_url, json={"content": message}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        add_log(f"❌ Discord send error: {e}")
        return False

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

def analyze_market(df):
    now = df.iloc[-1]
    prev = df.iloc[-2]

    open_p, close_p = now["open"], now["close"]
    high_p, low_p = now["high"], now["low"]

    body_size = abs(close_p - open_p)
    upper_wick = high_p - max(open_p, close_p)
    lower_wick = min(open_p, close_p) - low_p

    call_score, put_score = 0, 0
    reasons = []

    # 1. Level Retest
    direction, level = check_5pip_level_retest(df, lookback=30)
    if direction == "CALL":
        call_score += 2
        reasons.append(f"🎯 Level Retest: Touched {level:.5f} (Proven Support)")
    elif direction == "PUT":
        put_score += 2
        reasons.append(f"🎯 Level Retest: Touched {level:.5f} (Proven Resistance)")

    # 2. Pinbar Wicks
    if lower_wick >= (2 * body_size) and lower_wick > upper_wick:
        call_score += 1
        reasons.append("🕯️ Price Action: Lower Wick Rejection")
    elif upper_wick >= (2 * body_size) and upper_wick > lower_wick:
        put_score += 1
        reasons.append("🕯️ Price Action: Upper Wick Rejection")

    # 3. Engulfing
    if prev["close"] < prev["open"] and close_p > open_p and close_p > prev["open"]:
        call_score += 1
        reasons.append("🔥 Price Action: Bullish Engulfing")
    elif prev["close"] > prev["open"] and close_p < open_p and close_p < prev["open"]:
        put_score += 1
        reasons.append("🔥 Price Action: Bearish Engulfing")

    # 4. Indicators
    close_series = df["close"]
    rsi = calc_rsi(close_series).iloc[-1]
    bb_low, bb_high = calc_bollinger(close_series)

    if pd.notna(rsi):
        if rsi < 30:
            call_score += 1
            reasons.append(f"📊 Indicator: RSI Oversold ({rsi:.1f})")
        elif rsi > 70:
            put_score += 1
            reasons.append(f"📊 Indicator: RSI Overbought ({rsi:.1f})")

    if pd.notna(bb_low.iloc[-1]):
        if close_p <= bb_low.iloc[-1]:
            call_score += 1
            reasons.append("📊 Indicator: Lower Bollinger Touch")
        elif close_p >= bb_high.iloc[-1]:
            put_score += 1
            reasons.append("📊 Indicator: Upper Bollinger Touch")

    return call_score, put_score, reasons

def check_pair(name, ticker):
    df = get_data(ticker)
    if df.empty or len(df) < 35:
        add_log(f"⚠️ {name}: Fetching data failed or empty.")
        return

    call_score, put_score, reasons = analyze_market(df)
    now_price = df.iloc[-1]["close"]
    ts = datetime.now(PKT_TIMEZONE).strftime("%H:%M:%S")

    if call_score >= SIGNAL_THRESHOLD:
        reason_str = "\n".join([f"  • {r}" for r in reasons])
        msg = f"🚨 **SIGNAL: CALL 🟢**\n📊 Pair: {name}\n⏱ Timeframe: {TIMEFRAME}\n💰 Price: {now_price}\n🎯 Score: {call_score}\n📝 Reasons:\n{reason_str}\n🕐 {ts}\n⚡ PLACE TRADE NOW!"
        if send_discord(msg):
            add_log(f"🟢 CALL SIGNAL SENT: {name} @ {now_price}")

    elif put_score >= SIGNAL_THRESHOLD:
        reason_str = "\n".join([f"  • {r}" for r in reasons])
        msg = f"🚨 **SIGNAL: PUT 🔴**\n📊 Pair: {name}\n⏱ Timeframe: {TIMEFRAME}\n💰 Price: {now_price}\n🎯 Score: {put_score}\n📝 Reasons:\n{reason_str}\n🕐 {ts}\n⚡ PLACE TRADE NOW!"
        if send_discord(msg):
            add_log(f"🔴 PUT SIGNAL SENT: {name} @ {now_price}")

def bot_loop():
    """Background thread for market scanning."""
    while True:
        now_dt = datetime.now(PKT_TIMEZONE)
        wait_time = (60 - now_dt.second) + 2
        time.sleep(wait_time)

        current_time_pkt = datetime.now(PKT_TIMEZONE)
        current_hour = current_time_pkt.hour
        current_day = current_time_pkt.weekday()

        # Weekend Check
        if current_day >= 5:
            add_log("🛑 Weekend detected. Market closed. Paused.")
            continue

        # Night Check (1 AM - 6 AM)
        if 1 <= current_hour < 6:
            add_log("🌙 Night pause (1 AM - 6 AM). Paused.")
            continue

        # Active Scanning
        add_log("🔍 Scanning all Forex pairs...")
        for name, ticker in PAIRS.items():
            try:
                check_pair(name, ticker)
                time.sleep(1)
            except Exception as e:
                add_log(f"❌ Error checking {name}: {e}")
                continue

# Start Background Thread
if "bot_thread_started" not in st.session_state:
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()
    st.session_state["bot_thread_started"] = True

# Initial UI Render
update_ui_logs()