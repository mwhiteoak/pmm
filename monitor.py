# monitor.py (with full explanatory legend in every email)
import requests
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

# Config
BIG_TRADE_THRESHOLD = Decimal(os.getenv("BIG_TRADE_THRESHOLD", "10000"))
SMALL_TRADE_HIGH_ODDS_THRESHOLD = Decimal("0.15")   # ≤15¢ or ≥85¢ = high conviction
SMALL_TRADE_MIN_VALUE = Decimal("50")
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "7"))
SEEN_TRADE_RETENTION_DAYS = int(os.getenv("SEEN_TRADE_RETENTION_DAYS", "21"))
WALLET_TS_TTL_DAYS = int(os.getenv("WALLET_TS_TTL_DAYS", "14"))

# State / DB / Session
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DB_PATH = STATE_DIR / "polymarket_state.sqlite"

session = requests.Session()
session.headers.update({"User-Agent": "PolymarketMonitor/1.0"})

# ====================== DB HELPERS (unchanged) ======================
# [All the same db_connect, db_init, db_seen_trade, db_mark_trade, 
#  db_get_wallet_first_ts, db_set_wallet_first_ts, db_prune, 
#  get_first_trade_timestamp, safe_decimal, get_recent_trades functions 
#  from the previous complete version — they are unchanged here]
# (Copy-paste them exactly as in the last working version)

# ====================== LEGEND ======================
EMAIL_LEGEND = """
POLYMARKET ALERT LEGEND

• WHALE = Trade of $10,000 or more (big money moving)

• Interesting small bet = Trade under $10K but at extreme odds:
  - Price ≤ $0.15 or ≥ $0.85 → implies ≥85% or ≤15% probability
  - High conviction: someone is very confident in their view
  - These can be early signals of informed/insider knowledge

• Implied probability:
  - If buying YES at $0.90 → market thinks ~90% chance of YES
  - If buying NO at $0.10 → equivalent to buying YES at $0.90 (90% confidence in NO)
  - Shown as "(XX% implied - high conviction!)" for flagged small bets

• NEW ACCOUNT flag:
  - (NEW ACCOUNT - first ever seen) = wallet has never traded before on Polymarket
  - (NEW ACCOUNT - X.Xd old) = first trade was less than 7 days ago
  - New accounts making big or highly confident bets = extra noteworthy

• Ask Grok link = one-click to grok.com with pre-filled question about the trade

"""

# ====================== MAIN ======================
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

    if value >= BIG_TRADE_THRESHOLD:
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

        if (value >= SMALL_TRADE_MIN_VALUE and
            (price <= SMALL_TRADE_HIGH_ODDS_THRESHOLD or price >= (Decimal("1") - SMALL_TRADE_HIGH_ODDS_THRESHOLD))):
            
            implied_prob = price if side == "YES" else (Decimal("1") - price)
            implied_pct = implied_prob * 100
            odds_note = f" ({implied_pct:.0f}% implied - high conviction!)"

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

# ====================== BUILD EMAIL WITH LEGEND ======================
email_sections = [EMAIL_LEGEND.strip()]

if whale_alerts:
    email_sections.append("\nWHALE ALERTS ($10K+ BETS)\n")
    email_sections.extend(whale_alerts)

if interesting_small_alerts:
    email_sections.append("\nINTERESTING SMALL BETS (High Conviction < $10K)\n")
    email_sections.append("These are smaller trades at very high/low odds — potential sharp or informed signals!\n")
    email_sections.extend(interesting_small_alerts)

if len(email_sections) > 1:  # More than just the legend
    full_alert = "\n".join(email_sections)
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
    print("\nNo alerts this run — only the legend would be sent, so skipping email.")

if small_trade_count > 0:
    print(f"\nLogged {small_trade_count} small trades for health check.")

db_prune(conn, current_time)
conn.commit()
conn.close()

print(f"\n=== RUN SUMMARY [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] ===")
print(f"Trades analyzed: {len(trades)}")
print(f"Whale alerts: {len(whale_alerts)}")
print(f"Interesting small alerts: {len(interesting_small_alerts)}")
print(f"Total small trades logged: {small_trade_count}")
print("Run complete.")
