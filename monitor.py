# monitor.py (fixed indentation + full real-time WebSocket version)

import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
import websocket  # pip install websocket-client (add to workflow if needed)

# Config
NEW_ACCOUNT_VALUE_THRESHOLD = Decimal("10000")
ACCOUNT_AGE_THRESHOLD_DAYS = 7
BIG_TRADE_THRESHOLD = Decimal("10000")
MAX_OTHER_TRADES = 15
SEEN_TRADE_RETENTION_DAYS = 21
WALLET_TS_TTL_DAYS = 14

# State
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

# In-memory cache
wallet_age_cache = {}

# Session
session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# DB helpers (same as before)
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def db_init(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_trades (
            trade_key TEXT PRIMARY KEY,
            seen_ts   INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallet_first_trade (
            wallet         TEXT PRIMARY KEY,
            first_trade_ts INTEGER,
            updated_ts     INTEGER NOT NULL
        )
    """)
    conn.commit()

def db_seen_trade(conn, trade_key: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_trades WHERE trade_key = ?", (trade_key,)).fetchone()
    return row is not None

def db_mark_trade(conn, trade_key: str, seen_ts: int):
    conn.execute(
        "INSERT OR REPLACE INTO seen_trades(trade_key, seen_ts) VALUES(?, ?)",
        (trade_key, seen_ts)
    )

def db_get_wallet_first_ts(conn, wallet: str):
    row = conn.execute(
        "SELECT first_trade_ts, updated_ts FROM wallet_first_trade WHERE wallet = ?",
        (wallet,)
    ).fetchone()
    if row:
        return row[0], row[1]
    else:
        return None, None

def db_set_wallet_first_ts(conn, wallet: str, first_ts, updated_ts: int):
    conn.execute(
        "INSERT OR REPLACE INTO wallet_first_trade(wallet, first_trade_ts, updated_ts) VALUES(?, ?, ?)",
        (wallet, first_ts, updated_ts)
    )

def db_prune(conn, now_ts: int):
    cutoff_seen = now_ts - SEEN_TRADE_RETENTION_DAYS * 86400
    cutoff_wallet = now_ts - WALLET_TS_TTL_DAYS * 86400
    conn.execute("DELETE FROM seen_trades WHERE seen_ts < ?", (cutoff_seen,))
    conn.execute("DELETE FROM wallet_first_trade WHERE updated_ts < ?", (cutoff_wallet,))
    conn.commit()

# Rate limiting
def rate_limited(max_calls=10, period=1):
    calls = []
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time()
            calls[:] = [c for c in calls if c > now - period]
            if len(calls) >= max_calls:
                sleep_time = period - (now - calls[0])
                print(f"Rate limit hit - sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
            calls.append(time.time())
            return func(*args, **kwargs)
        return wrapper
    return decorator

@rate_limited(max_calls=10, period=1)
def get_first_trade_timestamp(wallet):
    if wallet in wallet_age_cache:
        return wallet_age_cache[wallet]

    first_ts, updated_ts = db_get_wallet_first_ts(conn, wallet)
    if updated_ts is not None and (time.time() - updated_ts) < WALLET_TS_TTL_DAYS * 86400:
        wallet_age_cache[wallet] = first_ts
        return first_ts

    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": wallet,
        "type": "TRADE",
        "limit": 1,
        "offset": 0,
        "sortDirection": "ASC"
    }
    try:
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            first_ts = int(data[0]["timestamp"])
        else:
            first_ts = None
    except Exception as e:
        print(f"Polymarket activity API error for {wallet}: {e}")
        first_ts = None

    db_set_wallet_first_ts(conn, wallet, first_ts, int(time.time()))
    wallet_age_cache[wallet] = first_ts
    return first_ts

def safe_decimal(val):
    try:
        return Decimal(str(val)) if val not in (None, "", "None") else Decimal("0")
    except (InvalidOperation, TypeError):
        return Decimal("0")

# WebSocket handlers
def on_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("topic") != "activity" or data.get("type") != "trades":
            return

        current_time = int(time.time())
        alerts = []

        for payload in data.get("payload", []):
            trade = payload.get("trade", {})
            tx_hash = trade.get("transactionHash")
            trade_key = tx_hash or f'{trade.get("timestamp")}:{trade.get("proxyWallet")}'

            if db_seen_trade(conn, trade_key):
                continue

            proxy_wallet = trade.get("proxyWallet")
            if not proxy_wallet:
                continue

            value = safe_decimal(trade.get("usdcSize")) or (safe_decimal(trade.get("size")) * safe_decimal(trade.get("price")))

            market_title = trade.get("title", "Unknown")

            first_ts = get_first_trade_timestamp(proxy_wallet)
            age_days = 0 if first_ts is None else (current_time - first_ts#endif) / 86400
            age_note = " (brand new)" if first_ts is None else f" (age: {age_days:.1f}d)"
            is_new = (first_ts is None) or (age_days < ACCOUNT_AGE_THRESHOLD_DAYS)

            tx_line = f"  Tx: https://polygonscan.com/tx/{tx_hash}\n" if tx_hash else ""

            alert_text = None
            if value > NEW_ACCOUNT_VALUE_THRESHOLD and is_new:
                alert_text = (
                    f"ALERT: New user ${value:,.0f} bet!\n"
                    f"Wallet: {proxy_wallet}{age_note}\n"
                    f"Market: {market_title}\n"
                    f"Side: {trade.get('side')} {trade.get('size')} @ ${trade.get('price')}\n"
                    f"{tx_line}"
                    f"Explorer: https://polygonscan.com/address/{proxy_wallet}\n"
                )
            elif value > BIG_TRADE_THRESHOLD:
                alert_text = (
                    f"WHALE: ${value:,.0f} bet\n"
                    f"Wallet: {proxy_wallet}{age_note}\n"
                    f"Market: {market_title}\n"
                    f"Side: {trade.get('side')} {trade.get('size')} @ ${trade.get('price')}\n"
                    f"{tx_line}"
                )

            if alert_text:
                alerts.append(alert_text)
                print(f"\n{alert_text}")

            trade_ts = int(trade.get("timestamp", current_time))
            db_mark_trade(conn, trade_key, trade_ts)

        if alerts:
            full_alert = "REAL-TIME WHALE ALERTS\n\n" + "\n".join(alerts)
            delimiter = "EOF_POLYMARKET_ALERT"
            with open(os.environ["GITHUB_ENV"], "a") as f:
                f.write(f"ALERTS<<{delimiter}\n")
                f.write(full_alert + "\n")
                f.write(f"{delimiter}\n")

    except Exception as e:
        print(f"Error processing message: {e}")

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed - reconnecting in 5s...")
    time.sleep(5)
    start_websocket()

def on_open(ws):
    print("WebSocket connected - subscribing to trades...")
    subscribe_msg = json.dumps({
        "subscriptions": [
            {"topic": "activity", "type": "trades"}
        ]
    })
    ws.send(subscribe_msg)

def start_websocket():
    ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws"  # Official CLOB WebSocket
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

print(f"Starting real-time Polymarket whale monitor... [{datetime.now(timezone.utc)}]")

# DB setup
conn = db_connect()
db_init(conn)

start_websocket()
