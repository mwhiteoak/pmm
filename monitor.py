# monitor.py (1000X better - real-time WebSocket for instant whale alerts)

import websocket
import json
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# Config
NEW_ACCOUNT_VALUE_THRESHOLD = Decimal("10000")
ACCOUNT_AGE_THRESHOLD_DAYS = 7
BIG_TRADE_THRESHOLD = Decimal("10000")  # Captures more whales
MAX_OTHER_TRADES = 15
SEEN_TRADE_RETENTION_DAYS = 21
WALLET_TS_TTL_DAYS = 14

# State
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

# DB (same as before - persistence across restarts)
# ... (keep all DB helpers from previous version: connect, init, seen_trade, mark_trade, wallet_first_trade, prune, etc.)

conn = sqlite3.connect(DB_PATH)
db_init(conn)

wallet_age_cache = {}

def safe_decimal(val):
    try:
        return Decimal(str(val)) if val not in (None, "", "None") else Decimal("0")
    except (InvalidOperation, TypeError):
        return Decimal("0")

def get_first_trade_timestamp(wallet):
    # Same as before - cached + Polymarket activity API for first trade
    # ... (keep your existing function)

def on_message(ws, message):
    data = json.loads(message)
    if data.get("topic") != "activity" or data.get("type") != "trades":
        return

    for trade_payload in data.get("payload", []):
        trade = trade_payload.get("trade", {})
        tx_hash = trade.get("transactionHash")
        trade_key = tx_hash or f'{trade.get("timestamp")}:{trade.get("proxyWallet")}'

        if db_seen_trade(conn, trade_key):
            continue

        proxy_wallet = trade.get("proxyWallet")
        if not proxy_wallet:
            continue

        value = safe_decimal(trade.get("usdcSize") or (safe_decimal(trade.get("size")) * safe_decimal(trade.get("price"))))

        market_title = trade.get("title", "Unknown Market")

        first_ts = get_first_trade_timestamp(proxy_wallet)
        age_days = 0 if first_ts is None else (time.time() - first_ts) / 86400
        age_note = " (brand new)" if first_ts is None else f" (age: {age_days:.1f}d)"
        is_new = (first_ts is None) or (age_days < ACCOUNT_AGE_THRESHOLD_DAYS)

        alert_lines = []
        if value > NEW_ACCOUNT_VALUE_THRESHOLD and is_new:
            alert_lines.append(f"WHALE ALERT: New user ${value:,.0f} bet!")
        if value > BIG_TRADE_THRESHOLD:
            alert_lines.append(f"WHALE: ${value:,.0f} big bet")

        if alert_lines:
            full_alert = (
                f"{' | '.join(alert_lines)}\n"
                f"Wallet: {proxy_wallet}{age_note}\n"
                f"Market: {market_title}\n"
                f"Side: {trade.get('side')} {trade.get('size')} @ ${trade.get('price')}\n"
                f"Tx: https://polygonscan.com/tx/{tx_hash}\n"
                f"Wallet Explorer: https://polygonscan.com/address/{proxy_wallet}\n"
            )
            print(f"\n{full_alert}\n")
            # Send email (your existing GITHUB_ENV method or direct SMTP)

        db_mark_trade(conn, trade_key, int(trade.get("timestamp", time.time())))

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed - reconnecting...")
    time.sleep(5)
    start_websocket()

def on_open(ws):
    print("WebSocket connected - subscribing to trades...")
    subscribe_msg = {
        "subscriptions": [
            {"topic": "activity", "type": "trades"}
        ]
    }
    ws.send(json.dumps(subscribe_msg))

def start_websocket():
    ws = websocket.WebSocketApp(
        "wss://real-time-data.polymarket.com",  # Official RTDS endpoint from docs
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

print(f"Starting real-time Polymarket whale monitor... [{datetime.now(timezone.utc)}]")
start_websocket()

# Keep DB cleanup on graceful exit if needed
