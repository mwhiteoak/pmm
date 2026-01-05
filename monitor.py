# monitor.py (Clean: Only $5K+ whales • Legend at bottom • Simpler & clearer)
import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

# Config
WHALE_THRESHOLD = Decimal(os.getenv("WHALE_THRESHOLD", "5000"))  # $5,000+
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "7"))
SEEN_TRADE_RETENTION_DAYS = int(os.getenv("SEEN_TRADE_RETENTION_DAYS", "21"))
WALLET_TS_TTL_DAYS = int(os.getenv("WALLET_TS_TTL_DAYS", "14"))

# State
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

# Session
session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# ====================== DB HELPERS ======================
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def db_init(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_trades (
            trade_key TEXT PRIMARY KEY,
            seen_ts INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallet_first_trade (
            wallet TEXT PRIMARY KEY,
            first_trade_ts INTEGER,
            updated_ts INTEGER NOT NULL
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

def get_first_trade_timestamp(wallet: str, conn):
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
        first_ts = int(data[0]["timestamp"]) if data else None
    except Exception as e:
        print(f"API error fetching first trade for {wallet}: {e}")
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

# ====================== CLEANER LEGEND (at bottom) ======================
EMAIL_LEGEND = """
────────────────────────────────────────────────────────────
POLYMARKET ALERT LEGEND

• WHALE: Any trade worth $5,000 or more in USDC — significant capital deployment.

• Price interpretation:
  - Buying YES at $0.90 → believes ~90% chance event happens
  - Buying NO at $0.10  → believes ~90% chance event does NOT happen

• NEW ACCOUNT flags:
  - (first ever seen) → this wallet has never traded on Polymarket before
  - (X.Xd old)        → first trade was less than 7 days ago
  → Especially noteworthy when paired with large bets

• Ask Grok link: One-click to grok.com with a pre-filled question analyzing the trade
"""

# ====================== MAIN ======================
print(f"Starting Polymarket monitor... [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]")

conn = db_connect()
db_init(conn)

trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())
whale_alerts = []
small_trade_count = 0

print(f"Fetched {len(trades)} recent trades. Processing...\n")

for trade in trades:
    tx_hash = trade.get("transactionHash")
    trade_key = tx_hash or f'{trade.get("timestamp")}:{trade.get("proxyWallet")}:{trade.get("size")}:{trade.get("price")}'
    
    if db_seen_trade(conn, trade_key):
        continue
    
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    value = safe_decimal(trade.get("usdcSize")) or (safe_decimal(trade.get("size")) * safe_decimal(trade.get("price")))
    price = safe_decimal(trade.get("price"))
    side = trade.get("side", "").upper()
    size = trade.get("size")
    market_title = trade.get("title", "Unknown Market")

    trade_ts = int(trade.get("timestamp", current_time))
    db_mark_trade(conn, trade_key, trade_ts)

    tx_line = f" Tx: https://polygonscan.com/tx/{tx_hash}\n" if tx_hash else ""
    explorer_line = f"Explorer: https://polygonscan.com/address/{proxy_wallet}\n"

    grok_query = f"Why might someone make this Polymarket trade? ${value:,.0f} {side} {size} @ ${price} on: {market_title}"
    grok_link = f"https://grok.com/?q={quote_plus(grok_query)}"

    first_ts = get_first_trade_timestamp(proxy_wallet, conn)
    new_flag = ""
    if first_ts is None:
        new_flag = " (NEW ACCOUNT - first ever seen)"
    elif (current_time - first_ts) / 86400 < ACCOUNT_AGE_THRESHOLD_DAYS:
        age_days = (current_time - first_ts) / 86400
        new_flag = f" (NEW ACCOUNT - {age_days:.1f}d old)"

    if value >= WHALE_THRESHOLD:
        alert_text = (
            f"WHALE: ${value:,.0f} bet{new_flag}\n"
            f"Wallet: {proxy_wallet}\n"
            f"Market: {market_title}\n"
            f"Side: {side} {size} @ ${price}\n"
            f"{tx_line}"
            f"{explorer_line}"
            f"Ask Grok: {grok_link}\n"
        )
        whale_alerts.append(alert_text)
        print(f"\n*** WHALE ALERT ***\n{alert_text}")

    else:
        small_trade_count += 1
        print(f"Small trade: ${value:,.0f} | {side} {size} @ ${price} | {market_title}")

# ====================== BUILD EMAIL: Alerts first, legend last ======================
email_lines = []

if whale_alerts:
    email_lines.append("POLYMARKET WHALE ALERTS ($5K+ BETS)\n")
    email_lines.extend(whale_alerts)
    email_lines.append(EMAIL_LEGEND.strip())
    full_alert = "\n".join(email_lines)

    print("\n" + "="*70)
    print("EMAIL WILL BE SENT:")
    print(full_alert)
    print("="*70)

    delimiter = "EOF_POLYMARKET_ALERT"
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS<<{delimiter}\n")
        f.write(full_alert + "\n")
        f.write(f"{delimiter}\n")
else:
    print("\nNo $5K+ whale trades this run — no email sent.")

if small_trade_count > 0:
    print(f"\nLogged {small_trade_count} smaller trades for health check.")

db_prune(conn, current_time)
conn.commit()
conn.close()

print(f"\n=== RUN SUMMARY [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] ===")
print(f"Trades analyzed: {len(trades)}")
print(f"Whale alerts sent: {len(whale_alerts)}")
print(f"Small trades logged: {small_trade_count}")
print("Run complete.")
