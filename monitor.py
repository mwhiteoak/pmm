# monitor.py (fixed DB unpack error)

import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

# Config
NEW_ACCOUNT_VALUE_THRESHOLD = Decimal(os.getenv("NEW_ACCOUNT_THRESHOLD", "10000"))
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "7"))
BIG_TRADE_THRESHOLD = Decimal(os.getenv("BIG_TRADE_THRESHOLD", "20000"))
MAX_OTHER_TRADES = 15
SEEN_TRADE_RETENTION_DAYS = int(os.getenv("SEEN_TRADE_RETENTION_DAYS", "21"))
WALLET_TS_TTL_DAYS = int(os.getenv("WALLET_TS_TTL_DAYS", "14"))

# State directory
STATE_DIR = Path(os.getenv("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

# Sports/low-interest keywords
EXCLUDED_KEYWORDS = [
    "nba", "basketball", "college basketball", "ncaab", "ncaa basketball",
    "soccer", "football", "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "world cup", "euro", "mls", "tennis", "golf", "ufc", "mma",
    "cricket", "rugby", "hockey", "nhl", "baseball", "mlb", "f1", "formula 1",
    "boxing", "wwe", "esports", "darts", "snooker", "cycling", "olympics"
]

# In-memory cache
wallet_age_cache = {}

# Session
session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# DB helpers (fixed unpack)
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
        return None, None  # Fixed: safe return when no row

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

# Rate limiting (same as before)
# ... (keep your existing rate_limited decorator and get_first_trade_timestamp function)

# The rest of the script remains unchanged
# (get_recent_trades, safe_decimal, is_low_interest_market, main loop, email building, etc.)

# At the end:
db_prune(conn, current_time)
conn.commit()
conn.close()
