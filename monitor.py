# monitor.py (UPDATED Jan 2026: +Telegram alerts, +market category filter, +position delta for conviction)
import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# Config
NEW_ACCOUNT_VALUE_THRESHOLD = Decimal(os.getenv("NEW_ACCOUNT_THRESHOLD", "10000"))      # >$10K new accounts
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "90"))
LARGE_TRADE_THRESHOLD = Decimal(os.getenv("LARGE_TRADE_THRESHOLD", "50000"))            # ≥$50K any account
MIN_DELTA_THRESHOLD = Decimal(os.getenv("MIN_DELTA_THRESHOLD", "10000"))                 # NEW: minimum position increase to alert
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "90"))
INTERESTED_KEYWORDS = os.getenv("INTERESTED_KEYWORDS", "president,election,fed,politics,macro,trump,harris").lower().split(",")
SEEN_TRADE_RETENTION_DAYS = int(os.getenv("SEEN_TRADE_RETENTION_DAYS", "21"))
WALLET_TS_TTL_DAYS = int(os.getenv("WALLET_TS_TTL_DAYS", "14"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")                                    # NEW: for push alerts
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# State
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

# Session
session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# NEW: Telegram send function
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")

# DB helpers (unchanged except passing conn to get_first_trade_timestamp)
# ... [keep all db_* functions as before]

def get_first_trade_timestamp(wallet, conn):
    # unchanged, but now takes conn as param
    # ... [same as previous version]

def safe_decimal(val):
    try:
        return Decimal(str(val)) if val not in (None, "", "None") else Decimal("0")
    except (InvalidOperation, TypeError):
        return Decimal("0")

def get_recent_trades():
    params = {"limit": 500}
    try:
        response = session.get("https://data-api.polymarket.com/trades", params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return []

# NEW: Get current net position size for a wallet in a specific outcome (asset)
def get_current_position(wallet: str, asset: str):
    url = "https://data-api.polymarket.com/positions"
    params = {"user": wallet, "asset": asset}
    try:
        resp = session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data and len(data) > 0:
            return safe_decimal(data[0].get("size", "0"))
    except Exception as e:
        print(f"Position API error for {wallet}/{asset}: {e}")
    return Decimal("0")

print(f"Starting Polymarket monitor... [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]")

conn = db_connect()
db_init(conn)
trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())
alerts = []

for trade in trades:
    tx_hash = trade.get("transactionHash")
    trade_key = tx_hash or f'{trade.get("timestamp")}:{trade.get("proxyWallet")}:{trade.get("size")}:{trade.get("price")}'
    if db_seen_trade(conn, trade_key):
        continue

    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    # Basic trade info
    usdc_size = safe_decimal(trade.get("usdcSize"))
    value = usdc_size or (safe_decimal(trade.get("size")) * safe_decimal(trade.get("price")))
    market_title = trade.get("title", "Unknown Market")
    market_slug = trade.get("slug", "").lower()

    # 1. Market category filter (quick win - skip noise like sports/pop culture)
    if not any(keyword in market_slug or keyword in market_title.lower() for keyword in INTERESTED_KEYWORDS):
        db_mark_trade(conn, trade_key, int(trade.get("timestamp", current_time)))
        continue

    asset = trade.get("asset")  # unique outcome token ID
    side = trade.get("side")    # BUY or SELL
    trade_size = safe_decimal(trade.get("size"))

    # Calculate position delta
    current_pos = get_current_position(proxy_wallet, asset)
    delta = trade_size if side == "BUY" else -trade_size
    new_pos = current_pos + delta
    position_increase = delta if side == "BUY" else -trade_size  # positive for adds to long

    # Age calculation
    first_ts = get_first_trade_timestamp(proxy_wallet, conn)
    age_days = 0 if first_ts is None else (current_time - first_ts) / 86400
    age_note = " (brand new)" if first_ts is None else f" (age: {age_days:.1f}d)"
    is_new = (first_ts is None) or (age_days < ACCOUNT_AGE_THRESHOLD_DAYS)

    tx_line = f" Tx: https://polygonscan.com/tx/{tx_hash}\n" if tx_hash else ""
    market_link = f"https://polymarket.com/event/{trade.get('eventSlug') or trade.get('slug')}"

    alert_text = None

    # Primary alert logic - now requires meaningful position increase
    if abs(position_increase) >= MIN_DELTA_THRESHOLD:
        if value >= LARGE_TRADE_THRESHOLD:
            alert_text = (
                f"🐳 WHALE ${value:,.0f} (Δ +${abs(position_increase):,.0f})\n"
                f"Wallet: {proxy_wallet}{age_note}\n"
                f"Market: {market_title}\n"
                f"Side: {side} {trade.get('size')} @ ${trade.get('price')}\n"
                f"New pos: ~{new_pos:,.0f} shares\n"
                f"{tx_line}"
                f"Link: {market_link}\n"
                f"Explorer: https://polygonscan.com/address/{proxy_wallet}"
            )
        elif value > NEW_ACCOUNT_VALUE_THRESHOLD and is_new:
            alert_text = (
                f"⚠️ NEW USER ${value:,.0f} (Δ +${abs(position_increase):,.0f})\n"
                f"Wallet: {proxy_wallet}{age_note}\n"
                f"Market: {market_title}\n"
                f"Side: {side} {trade.get('size')} @ ${trade.get('price')}\n"
                f"New pos: ~{new_pos:,.0f} shares\n"
                f"{tx_line}"
                f"Link: {market_link}\n"
                f"Explorer: https://polygonscan.com/address/{proxy_wallet}"
            )

    if alert_text:
        alerts.append(alert_text)
        print(f"\n{alert_text}")
        send_telegram(alert_text)  # Instant push notification

    trade_ts = int(trade.get("timestamp", current_time))
    db_mark_trade(conn, trade_key, trade_ts)

# Legacy GitHub output (kept for CI compatibility)
if alerts:
    full_alert = "LARGE / NEW ACCOUNT TRADES\n\n" + "\n\n".join(alerts)
    print(full_alert)
    if "GITHUB_ENV" in os.environ:
        delimiter = "EOF_POLYMARKET_ALERT"
        with open(os.environ["GITHUB_ENV"], "a") as f:
            f.write(f"ALERTS<<{delimiter}\n")
            f.write(full_alert + "\n")
            f.write(f"{delimiter}\n")
else:
    print("No qualifying trades this run.")

db_prune(conn, current_time)
conn.commit()
conn.close()

print(f"\n=== RUN SUMMARY [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] ===")
print(f"Trades analyzed: {len(trades)}")
print(f"New alerts: {len(alerts)}")
print("Run complete.")
