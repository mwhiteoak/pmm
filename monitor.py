# monitor.py (updated: $10K+ whales emailed + optional "Interesting Small Bets" section for high-odds trades)
import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

# Config
BIG_TRADE_THRESHOLD = Decimal(os.getenv("BIG_TRADE_THRESHOLD", "10000"))          # $10K+
SMALL_TRADE_HIGH_ODDS_THRESHOLD = Decimal("0.15")                                 # Flag small trades <15¢ or >85¢ (i.e., <15% or >85% implied prob)
SMALL_TRADE_MIN_VALUE = Decimal("50")                                             # Only consider small trades ≥$50 for "interesting" flag (avoids noise)
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "7"))
SEEN_TRADE_RETENTION_DAYS = int(os.getenv("SEEN_TRADE_RETENTION_DAYS", "21"))
WALLET_TS_TTL_DAYS = int(os.getenv("WALLET_TS_TTL_DAYS", "14"))

# State / DB / Session setup unchanged...
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# All DB helpers unchanged (db_connect, db_init, db_seen_trade, db_mark_trade, db_get_wallet_first_ts,
# db_set_wallet_first_ts, db_prune, get_first_trade_timestamp, safe_decimal, get_recent_trades)
# ... [paste the unchanged functions from previous version here]

print(f"Starting Polymarket monitor... [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]")

conn = db_connect()
db_init(conn)

trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())
whale_alerts = []
interesting_small_alerts = []
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
    side = trade.get("side").upper()  # YES or NO
    size = trade.get("size")
    market_title = trade.get("title", "Unknown Market")

    trade_ts = int(trade.get("timestamp", current_time))
    db_mark_trade(conn, trade_key, trade_ts)

    # Common lines
    tx_line = f" Tx: https://polygonscan.com/tx/{tx_hash}\n" if tx_hash else ""
    explorer_line = f"Explorer: https://polygonscan.com/address/{proxy_wallet}\n"

    # Grok query link - easy click to paste into Grok.com
    grok_query = f"Why might someone make this Polymarket trade? ${value:,.0f} {side} {size} @ ${price} on: {market_title}"
    grok_link = f"https://grok.com/?q={quote_plus(grok_query)}"

    # Check new account flag (for both whale and interesting small)
    first_ts = get_first_trade_timestamp(proxy_wallet, conn)
    new_flag = ""
    if first_ts is None:
        new_flag = " (NEW ACCOUNT - first ever seen)"
    elif (current_time - first_ts) / 86400 < ACCOUNT_AGE_THRESHOLD_DAYS:
        age_days = (current_time - first_ts) / 86400
        new_flag = f" (NEW ACCOUNT - {age_days:.1f}d old)"

    if value >= BIG_TRADE_THRESHOLD:
        # === WHALE ALERT ===
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
        # === SMALL TRADE LOGGING & INTERESTING FLAGGING ===
        small_trade_count += 1
        print(f"Small trade: ${value:,.0f} | {side} {size} @ ${price} | {market_title}")

        # Flag as "interesting" if high odds (very confident bet) + decent size
        if (value >= SMALL_TRADE_MIN_VALUE and
            (price <= SMALL_TRADE_HIGH_ODDS_THRESHOLD or price >= (Decimal("1") - SMALL_TRADE_HIGH_ODDS_THRESHOLD))):
            
            implied_prob = price if side == "YES" else (Decimal("1") - price)
            implied_pct = implied_prob * 100
            odds_note = f" ({implied_pct:.0f}% implied probability - strong conviction!)"

            small_alert = (
                f"Interesting small bet: ${value:,.0f}{new_flag}{odds_note}\n"
                f"Wallet: {proxy_wallet}\n"
                f"Market: {market_title}\n"
                f"Side: {side} {size} @ ${price}\n"
                f"{tx_line}"
                f"{explorer_line}"
                f"Ask Grok: {grok_link}\n"
            )
            interesting_small_alerts.append(small_alert)

# Build email content
email_sections = []

if whale_alerts:
    email_sections.append("POLYMARKET WHALE ALERTS ($10K+ BETS)\n")
    email_sections.extend(whale_alerts)

if interesting_small_alerts:
    email_sections.append("\nINTERESTING SMALL BETS (High Conviction < $10K)\n")
    email_sections.append("These are smaller trades but at very high/low odds — potential early signals!\n")
    email_sections.extend(interesting_small_alerts)

if email_sections:
    full_alert = "\n".join(email_sections)
    print("\n" + "="*60)
    print("EMAIL WILL BE SENT:")
    print(full_alert)
    print("="*60)

    delimiter = "EOF_POLYMARKET_ALERT"
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS<<{delimiter}\n")
        f.write(full_alert + "\n")
        f.write(f"{delimiter}\n")
else:
    print("\nNo whale trades or interesting small bets this run — quiet period.")

if small_trade_count > 0:
    print(f"\nLogged {small_trade_count} small trades (< $10K) for health check.")

db_prune(conn, current_time)
conn.commit()
conn.close()

print(f"\n=== RUN SUMMARY [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] ===")
print(f"Trades analyzed: {len(trades)}")
print(f"Whale alerts: {len(whale_alerts)}")
print(f"Interesting small alerts: {len(interesting_small_alerts)}")
print(f"Total small trades seen: {small_trade_count}")
print("Run complete.")
