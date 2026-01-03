# monitor.py (full updated - uses Polymarket activity API for accurate first-trade age)

import requests
import os
from datetime import datetime, timezone

# THRESHOLDS
NEW_ACCOUNT_VALUE_THRESHOLD = 1000      # $10K+ for new-account alerts
ACCOUNT_AGE_THRESHOLD_DAYS = 60           # <7 days old
BIG_TRADE_THRESHOLD = 20000              # $20K+ to list regardless of age
MAX_OTHER_TRADES = 15                    # Limit sports/low-interest to avoid long emails

# Sports/low-interest keywords
EXCLUDED_KEYWORDS = [
    "nba", "basketball", "college basketball", "ncaab", "ncaa basketball",
    "soccer", "football", "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "world cup", "euro", "mls", "tennis", "golf", "ufc", "mma",
    "cricket", "rugby", "hockey", "nhl", "baseball", "mlb", "f1", "formula 1",
    "boxing", "wwe", "esports", "darts", "snooker", "cycling", "olympics"
]

def get_recent_trades():
    params = {"limit": 500}
    try:
        response = requests.get("https://data-api.polymarket.com/trades", params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return []

def get_first_trade_timestamp(wallet):
    # Polymarket activity API - get the earliest trade (sortDirection=ASC, limit=1)
    url = f"https://data-api.polymarket.com/activity"
    params = {
        "user": wallet,
        "type": "TRADE",
        "limit": 1,
        "offset": 0,
        "sortDirection": "ASC"  # Earliest first
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            first_activity = data[0]
            return int(first_activity["timestamp"])
        else:
            print(f"No Polymarket activity found for {wallet}")
            return None
    except Exception as e:
        print(f"Polymarket activity API error for {wallet}: {e}")
        return None

def is_low_interest_market(title):
    if not title:
        return False
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in EXCLUDED_KEYWORDS)

print("Starting Polymarket monitor...")
trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())

new_account_alerts = []
big_trades_high_value = []
big_trades_other = []

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    value = float(trade.get("usdcSize") or (float(trade.get("size", 0)) * float(trade.get("price", 0))))
    market_title = trade.get("title", "")

    is_sports = is_low_interest_market(market_title)

    # --- Big trades >$20K ---
    if value > BIG_TRADE_THRESHOLD:
        age_note = ""
        first_ts = get_first_trade_timestamp(proxy_wallet)
        if first_ts is None:
            age_note = " (no Polymarket history - very new user)"
        else:
            age_days = (current_time - first_ts) / 86400
            age_note = f" (Polymarket age: {age_days:.1f} days)"

        big_trade_text = (
            f"• ${value:.2f} | Wallet: {proxy_wallet}{age_note}\n"
            f"  Market: {market_title}\n"
            f"  Side: {trade.get('side')} {trade.get('size')} shares @ ${trade.get('price')}\n"
            f"  Tx: https://polygonscan.com/tx/{trade.get('transactionHash')}\n"
        )
        if not is_sports:
            big_trades_high_value.append(big_trade_text)
        else:
            big_trades_other.append(big_trade_text)

    # --- New-account large trades >$10K ---
    if value > NEW_ACCOUNT_VALUE_THRESHOLD:
        print(f"Large trade detected: ${value:.2f} by {proxy_wallet} - checking Polymarket age...")
        first_ts = get_first_trade_timestamp(proxy_wallet)
        age_note = ""
        age_days = None

        if first_ts is None:
            age_note = " (no Polymarket trade history - likely brand new user)"
            age_days = 0
        else:
            age_days = (current_time - first_ts) / 86400

        if age_days < ACCOUNT_AGE_THRESHOLD_DAYS:
            alert_text = (
                f"ALERT: Large new-account trade detected!\n\n"
                f"Proxy Wallet: {proxy_wallet}{age_note}\n"
                f"Trade Value: ${value:.2f} USDC\n"
                f"Side: {trade.get('side')} {trade.get('size')} shares @ ${trade.get('price')}\n"
                f"Market: {market_title}\n"
                f"Trade Time: {datetime.fromtimestamp(trade.get('timestamp', 0), tz=timezone.utc)}\n"
                f"Transaction: https://polygonscan.com/tx/{trade.get('transactionHash')}\n"
                f"Proxy Wallet Explorer: https://polygonscan.com/address/{proxy_wallet}\n"
            )
            new_account_alerts.append(alert_text)
            print(f"MATCH FOUND: New-account large trade ${value:.2f} (Polymarket age: {age_days:.1f} days)")

# Build email body
email_parts = []

high_signal_count = len(new_account_alerts) + len(big_trades_high_value)
if high_signal_count > 0:
    email_parts.append(
        f"THINGS TO CHECK - {high_signal_count} High-Signal Activity "
        "(Politics, Crypto, Finance, Elections, News - sports filtered out)\n"
    )

if new_account_alerts:
    email_parts.append("NEW ACCOUNT LARGE TRADES (> $10K from accounts <7 days old or no Polymarket history)\n")
    email_parts.extend(new_account_alerts)

if big_trades_high_value:
    email_parts.append("HIGH-SIGNAL TRADES > $20K\n\n" + "\n".join(big_trades_high_value))

# Limit other trades
if big_trades_other:
    limited_other = big_trades_other[:MAX_OTHER_TRADES]
    note = f"\n(Showing {len(limited_other)} of {len(big_trades_other)} sports/low-interest trades)" if len(big_trades_other) > MAX_OTHER_TRADES else ""
    email_parts.append("\nOTHER BIG TRADES > $20K (Sports / Low-Interest Markets)" + note + "\n\n" + "\n".join(limited_other))

# Write safely
if email_parts:
    full_alert = "\n\n".join(email_parts)
    print(f"Alerts generated (limited other trades to {MAX_OTHER_TRADES})")
    print(full_alert)

    delimiter = "EOF_POLYMARKET_ALERT"
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS<<{delimiter}\n")
        f.write(full_alert + "\n")
        f.write(f"{delimiter}\n")
else:
    print("No qualifying trades this run.")

print("Run complete.")
