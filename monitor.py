# monitor.py (full stable polling version with $10K big trade threshold)

import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# Config - $10K threshold for more whale/copy-trade alerts
NEW_ACCOUNT_VALUE_THRESHOLD = Decimal(os.getenv("NEW_ACCOUNT_THRESHOLD", "10000"))
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "7"))
BIG_TRADE_THRESHOLD = Decimal(os.getenv("BIG_TRADE_THRESHOLD", "10000"))  # $10K+ for big bets
MAX_OTHER_TRADES = 15
SEEN_TRADE_RETENTION_DAYS = int(os.getenv("SEEN_TRADE_RETENTION_DAYS", "21"))
WALLET_TS_TTL_DAYS = int(os.getenv("WALLET_TS_TTL_DAYS", "14"))

# State
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

# Session
session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# DB helpers
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

def get_first_trade_timestamp(wallet):
    first_ts, updated_ts = db_get_wallet_first_ts(conn, wallet)
    if updated_ts is not None and (time.time() - updated_ts) < WALLET_TS_TTL_DAYS * 86400:
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
    return first_ts

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

    value = safe_decimal(trade.get("usdcSize")) or (safe_decimal(trade.get("size")) * safe_decimal(trade.get("price")))

    market_title = trade.get("title", "Unknown Market")

    first_ts = get_first_trade_timestamp(proxy_wallet)
    age_days = 0 if first_ts is None else (current_time - first_ts) / 86400
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

# Build email
if alerts:
    full_alert = "LARGE / NEW ACCOUNT TRADES\n\n" + "\n".join(alerts)
    print(full_alert)
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
