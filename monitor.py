# monitor.py (full updated script - with sports filtering and "Things to Check" section)

import requests
import os
from datetime import datetime, timezone

# THRESHOLDS
NEW_ACCOUNT_VALUE_THRESHOLD = 100      # $10K+ for new-account alerts
ACCOUNT_AGE_THRESHOLD_DAYS = 90           # <7 days old
BIG_TRADE_THRESHOLD = 20000              # $20K+ to list regardless of age

# Sports/low-interest markets to de-prioritize (still shown, but not flagged as high-value)
EXCLUDED_KEYWORDS = [
    "nba", "basketball", "college basketball", "ncaab", "ncaa basketball",
    "soccer", "football", "premier league", "la liga", "serie a", "bundesliga",
    "champions league", "world cup", "euro", "mls", "tennis", "golf", "ufc", "mma",
    "cricket", "rugby", "hockey", "nhl", "baseball", "mlb", "f1", "formula 1",
    "boxing", "wwe", "esports", "darts", "snooker", "cycling", "olympics"
]

MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")

def get_recent_trades():
    params = {"limit": 500}
    try:
        response = requests.get("https://data-api.polymarket.com/trades", params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return []

def get_first_tx_timestamp(wallet):
    if not MORALIS_API_KEY:
        print("Missing Moralis API key")
        return None
    headers = {"X-API-Key": MORALIS_API_KEY}
    url = f"https://deep-index.moralis.io/api/v2.2/wallets/{wallet}/chains"
    params = {"chains": "0x89"}  # Polygon
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            for chain in data.get("active_chains", []):
                if chain.get("chain_id") == "0x89":
                    first_tx = chain.get("first_transaction", {}).get("block_timestamp")
                    if first_tx:
                        ts_str = first_tx.replace("Z", "+00:00")
                        return int(datetime.fromisoformat(ts_str).timestamp())
    except Exception as e:
        print(f"Active chains error for {wallet}: {e}")
    return None

def is_low_interest_market(title):
    if not title:
        return False
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in EXCLUDED_KEYWORDS)

print("Starting Polymarket monitor...")
trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())

new_account_alerts = []      # >$10K + new account
big_trades_high_value = []   # >$20K in high-signal markets (politics, crypto, etc.)
big_trades_other = []        # >$20K in sports/low-interest markets

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    value = float(trade.get("usdcSize") or (float(trade.get("size", 0)) * float(trade.get("price", 0))))
    market_title = trade.get("title", "")

    is_sports = is_low_interest_market(market_title)

    # --- Big trades >$20K (split by interest) ---
    if value > BIG_TRADE_THRESHOLD:
        age_note = ""
        first_ts = get_first_tx_timestamp(proxy_wallet)
        if first_ts is None:
            age_note = " (no history - very new/low-activity)"
        else:
            age_days = (current_time - first_ts) / 86400
            age_note = f" (account age: {age_days:.1f} days)"

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
        print(f"Large trade detected: ${value:.2f} by {proxy_wallet} - checking age...")
        first_ts = get_first_tx_timestamp(proxy_wallet)
        age_note = ""
        age_days = None

        if first_ts is None:
            age_note = " (no transaction history detected - likely very new/low-activity proxy wallet)"
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
            print(f"MATCH FOUND: New-account large trade ${value:.2f}")

# Build email body
email_parts = []

# Top section: Things to Check (high-signal only)
high_signal_count = len(new_account_alerts) + len(big_trades_high_value)
if high_signal_count > 0:
    email_parts.append(
        f"THINGS TO CHECK - {high_signal_count} High-Signal Activity "
        "(Politics, Crypto, Finance, Elections, News - sports filtered out)\n"
    )

if new_account_alerts:
    email_parts.append("NEW ACCOUNT LARGE TRADES (> $10K from accounts <7 days old or no history)\n")
    email_parts.extend(new_account_alerts)

if big_trades_high_value:
    email_parts.append("HIGH-SIGNAL TRADES > $20K\n\n" + "\n".join(big_trades_high_value))

# Lower section: Other big trades (sports/low-interest)
if big_trades_other:
    email_parts.append("\nOTHER BIG TRADES > $20K (Sports / Low-Interest Markets)\n\n" + "\n".join(big_trades_other))

# Write to GITHUB_ENV safely
if email_parts:
    full_alert = "\n\n".join(email_parts)
    print(f"Alerts generated: {len(new_account_alerts)} new-account + "
          f"{len(big_trades_high_value)} high-signal big + {len(big_trades_other)} other big")
    print(full_alert)

    delimiter = "EOF_POLYMARKET_ALERT"
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS<<{delimiter}\n")
        f.write(full_alert + "\n")
        f.write(f"{delimiter}\n")
else:
    print("No qualifying trades this run.")

print("Run complete.")
