# ============================================
# QUOTEX SIGNALS BOT — KOYEB CLOUD VERSION
# ============================================

import pandas as pd
import pandas_ta as ta
import yfinance as yf
import requests
import time
import os
import threading
from datetime import datetime
from flask import Flask

# =============================================
# ⚙️ YOUR SETTINGS
# =============================================
DISCORD_WEBHOOK = "PASTE_YOUR_WEBHOOK_URL_HERE"

PAIRS = {
    "USD/JPY": "JPY=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "GBP/JPY": "GBPJPY=X",
    "EUR/JPY": "EURJPY=X",
    "AUD/USD": "AUDUSD=X",
}

TIMEFRAME = "1m"
SIGNAL_THRESHOLD = 1
# =============================================

# --- FLASK WEB SERVER (keeps Koyeb happy) ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Quotex Bot is running! Check Discord for signals."

# --- BOT FUNCTIONS ---

def get_data(ticker):
    df = yf.download(ticker, period="5d", interval=TIMEFRAME, progress=False)
    if df.empty:
        return df
    df = df.reset_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df.rename(columns={df.columns[0]: "time"})
    return df


def add_indicators(df):
    if len(df) < 30:
        return df
    df["rsi"] = df.ta.rsi(length=14)
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    df = pd.concat([df, macd], axis=1)
    bbands = df.ta.bbands(length=20, std=2)
    df = pd.concat([df, bbands], axis=1)
    stoch = df.ta.stoch(k=14, d=3, smooth_k=3)
    df = pd.concat([df, stoch], axis=1)
    df["ema9"] = df.ta.ema(length=9)
    df["ema21"] = df.ta.ema(length=21)
    return df


def check_signal(df):
    if len(df) < 30:
        return "NONE", 0, ["not enough data"]

    now = df.iloc[-1]
    prev = df.iloc[-2]
    call = 0
    put = 0
    reasons = []

    rsi_val = now.get("rsi")
    if pd.notna(rsi_val):
        if rsi_val < 30:
            call += 1
            reasons.append(f"RSI oversold ({rsi_val:.1f})")
        elif rsi_val > 70:
            put += 1
            reasons.append(f"RSI overbought ({rsi_val:.1f})")

    mc = "MACD_12_26_9"
    ms = "MACDs_12_26_9"
    if mc in df.columns and ms in df.columns:
        if pd.notna(now[mc]) and pd.notna(prev[mc]):
            if prev[mc] < prev[ms] and now[mc] > now[ms]:
                call += 1
                reasons.append("MACD bullish cross")
            elif prev[mc] > prev[ms] and now[mc] < now[ms]:
                put += 1
                reasons.append("MACD bearish cross")

    bl = "BBL_20_2.0"
    bu = "BBU_20_2.0"
    if bl in df.columns and bu in df.columns:
        if pd.notna(now[bl]) and pd.notna(now[bu]):
            if now["close"] <= now[bl]:
                call += 1
                reasons.append("Price at lower Bollinger")
            elif now["close"] >= now[bu]:
                put += 1
                reasons.append("Price at upper Bollinger")

    sk = "STOCHk_14_3_3"
    sd = "STOCHd_14_3_3"
    if sk in df.columns and sd in df.columns:
        if pd.notna(now[sk]) and pd.notna(now[sd]):
            if now[sk] < 20 and now[sk] > now[sd]:
                call += 1
                reasons.append("Stochastic bullish")
            elif now[sk] > 80 and now[sk] < now[sd]:
                put += 1
                reasons.append("Stochastic bearish")

    if pd.notna(now["ema9"]) and pd.notna(prev["ema9"]):
        if prev["ema9"] < prev["ema21"] and now["ema9"] > now["ema21"]:
            call += 1
            reasons.append("EMA golden cross")
        elif prev["ema9"] > prev["ema21"] and now["ema9"] < now["ema21"]:
            put += 1
            reasons.append("EMA death cross")

    if call >= SIGNAL_THRESHOLD:
        return "CALL", call, reasons
    elif put >= SIGNAL_THRESHOLD:
        return "PUT", put, reasons
    else:
        return "NONE", max(call, put), reasons


def send_discord(message, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(
                DISCORD_WEBHOOK,
                json={"content": message},
                timeout=15
            )
            if r.status_code in (200, 204):
                return True
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return False


def wait_for_next_candle():
    now = datetime.now()
    seconds_left = 60 - now.second
    wait_time = seconds_left + 2
    time.sleep(wait_time)


def bot_loop():
    """The main bot loop — runs forever in the background."""
    print("Bot thread started!")
    while True:
        wait_for_next_candle()
        now = datetime.now().strftime("%H:%M:%S")
        signals_found = 0
        signals_sent = 0

        for pair_name, ticker in PAIRS.items():
            try:
                df = get_data(ticker)
                if df.empty or len(df) < 30:
                    continue

                df = add_indicators(df)
                signal, score, reasons = check_signal(df)
                price = df.iloc[-1]["close"]

                if signal != "NONE":
                    signals_found += 1
                    reason_text = "\n".join([f"  - {r}" for r in reasons])
                    emoji = "CALL 🟢" if signal == "CALL" else "PUT 🔴"
                    msg = (
                        f"🚨 **SIGNAL: {emoji}**\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"📊 Pair: {pair_name}\n"
                        f"⏱ Timeframe: {TIMEFRAME}\n"
                        f"💰 Price: {price}\n"
                        f"📈 Strength: {score}/5\n"
                        f"📝 Reasons:\n{reason_text}\n"
                        f"🕐 {now}\n"
                        f"⚡ PLACE TRADE NOW — you have ~25 seconds!"
                    )
                    success = send_discord(msg)
                    if success:
                        signals_sent += 1
                        print(f"[{now}] ✅ {pair_name} → {signal} | Sent!")
                    else:
                        print(f"[{now}] ✅ {pair_name} → {signal} | Discord failed")
                else:
                    print(f"[{now}] ⏳ {pair_name}: no signal | {score}/5")

                time.sleep(1)
            except Exception as e:
                print(f"[{now}] ❌ {pair_name}: {e}")

        print(f"[{now}] --- {signals_found} found, {signals_sent} sent ---")


# =============================================
# START EVERYTHING
# =============================================
if __name__ == "__main__":
    # Start the bot in a background thread
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()

    # Start Flask web server (Koyeb needs this)
    port = int(os.environ.get("PORT", 8000))
    print(f"Web server starting on port {port}")
    app.run(host="0.0.0.0", port=port)
