# monitor.py (full updated - with caching, deduplication, rate limiting, and summary)

import requests
import os
import time
from datetime import datetime, timezone
from functools import wraps

# Flexible configuration via environment variables (defaults provided)
NEW_ACCOUNT_VALUE_THRESHOLD = int(os.getenv("NEW_ACCOUNT_THRESHOLD", "100"))
ACCOUNT_AGE_THRESHOLD_DAYS = int(os.getenv("ACCOUNT_AGE_DAYS", "90"))
BIG_TRADE_THRESHOLD = int(os.getenv("BIG_TRADE_THRESHOLD", "20000"))
MAX_OTHER_TRADES = 15

# Sports/low-interest keywords
EXCLUDED_KEYWORDS = [
    "nba", "basketball", "college basketball", "ncaab", "ncaa basketball",
    "soccer", "football", "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "world cup", "euro", "mls", "tennis", "golf", "ufc", "mma",
    "cricket", "rugby", "hockey", "nhl", "baseball", "mlb", "f1", "formula 1",
    "boxing", "wwe", "esports", "darts", "snooker", "cycling", "olympics"
]

# Caching and deduplication
wallet_age_cache = {}
seen_wallets = set()

# Rate limiting for Polymarket activity API (10 calls/sec max recommended)
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
    
    url = "https://data-api.polymarket.com/activity"
    params = {
        "user": wallet,
        "type": "TRADE",
        "limit": 1,
        "offset": 0,
        "sortDirection": "ASC"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            first_ts = int(data[0]["timestamp"])
            wallet_age_cache[wallet] = first_ts
            return first_ts
        else:
            print(f"No Polymarket trade activity found for {wallet}")
            wallet_age_cache[wallet] = None
            return None
    except Exception as e:
        print(f"Polymarket activity API error for {wallet}: {e}")
        wallet_age_cache[wallet] = None
        return None

def get_recent_trades():
    params = {"limit": 500}
    try:
        response = requests.get("https://data-api.polymarket.com/trades", params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return []

def is_low_interest_market(title):
    if not title:
        return False
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in EXCLUDED_KEYWORDS)

print(f"Starting Polymarket monitor... [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]")
trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())

new_account_alerts = []
big_trades_high_value = []
big_trades_other = []

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet or proxy_wallet in seen_wallets:
        continue
    seen_wallets.add(proxy_wallet)

    value = float(trade.get("usdcSize") or (float(trade.get("size", 0)) * float(trade.get("price", 0))))
    market_title = trade.get("title", "")

    is_sports = is_low_interest_market(market_title)

    # --- Big trades >$20K ---
    if value > BIG_TRADE_THRESHOLD:
        first_ts = get_first_trade_timestamp(proxy_wallet)
        age_note = ""
        if first_ts is None:
            age_note = " (no Polymarket history - brand new user)"
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
            age_note = " (no Polymarket trade history - brand new user)"
            age_days = 0
        else:
            age_days = (current_time - first_ts) / 86400

        if age_days < ACCOUNT_AGE_THRESHOLD_DAYS:
            alert_text = (
                f"ALERT: Large new-account trade detected! [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]\n\n"
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

if big_trades_other:
    limited_other = big_trades_other[:MAX_OTHER_TRADES]
    note = f"\n(Showing {len(limited_other)} of {len(big_trades_other)} sports/low-interest trades)" if len(big_trades_other) > MAX_OTHER_TRADES else ""
    email_parts.append("\nOTHER BIG TRADES > $20K (Sports / Low-Interest Markets)" + note + "\n\n" + "\n".join(limited_other))

# Write safely
if email_parts:
    full_alert = "\n\n".join(email_parts)
    print(f"Alerts generated: {len(new_account_alerts)} new-account + {len(big_trades_high_value)} high-signal big + {len(big_trades_other)} other big")
    print(full_alert)

    delimiter = "EOF_POLYMARKET_ALERT"
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS<<{delimiter}\n")
        f.write(full_alert + "\n")
        f.write(f"{delimiter}\n")
else:
    print("No qualifying trades this run.")

# Run summary
print(f"\n=== RUN SUMMARY [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] ===")
print(f"Trades analyzed: {len(trades)}")
print(f"Unique wallets processed: {len(seen_wallets)}")
print(f"Wallet age API calls: {len(wallet_age_cache)}")
print(f"New account alerts: {len(new_account_alerts)}")
print(f"High-signal big trades: {len(big_trades_high_value)}")
print(f"Other big trades: {len(big_trades_other)}")
print("Run complete.")
