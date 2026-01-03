# monitor.py (full updated script - adds market link)

import requests
import os
from datetime import datetime, timezone

# THRESHOLDS
NEW_ACCOUNT_VALUE_THRESHOLD = 100      # $10K+ for new-account alerts
ACCOUNT_AGE_THRESHOLD_DAYS = 90           # <7 days old
BIG_TRADE_THRESHOLD = 20000              # $20K+ to list regardless of age

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

print("Starting Polymarket monitor...")
trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())

new_account_alerts = []   # >$10K + new account
big_trades = []           # >$20K any account

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    value = float(trade.get("usdcSize") or (float(trade.get("size", 0)) * float(trade.get("price", 0))))

    market_slug = trade.get("slug") or trade.get("eventSlug") or "unknown"
    market_link = f"https://polymarket.com/event/{market_slug}"

    # --- Big trades >$20K ---
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
            f"  Market: {trade.get('title')} ({market_link})\n"
            f"  Side: {trade.get('side')} {trade.get('size')} shares @ ${trade.get('price')}\n"
            f"  Tx: https://polygonscan.com/tx/{trade.get('transactionHash')}\n"
        )
        big_trades.append(big_trade_text)

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
                f"Market: {trade.get('title')}\n"
                f"Market Link: {market_link}\n"
                f"Trade Time: {datetime.fromtimestamp(trade.get('timestamp', 0), tz=timezone.utc)}\n"
                f"Transaction: https://polygonscan.com/tx/{trade.get('transactionHash')}\n"
                f"Proxy Wallet Explorer: https://polygonscan.com/address/{proxy_wallet}\n"
            )
            new_account_alerts.append(alert_text)
            print(f"MATCH FOUND: New-account large trade ${value:.2f}")

# Build email body
email_parts = []

if new_account_alerts:
    email_parts.append("NEW ACCOUNT LARGE TRADES (> $10K from accounts <7 days old or no history)\n")
    email_parts.extend(new_account_alerts)

if big_trades:
    big_section = "EXTRA: ALL TRADES > $20K (any account age)\n\n" + "\n".join(big_trades)
    email_parts.append(big_section)

# Write to GITHUB_ENV safely
if email_parts:
    full_alert = "\n\n".join(email_parts)
    print(f"Alerts generated ({len(new_account_alerts)} new-account + {len(big_trades)} big trades)")
    print(full_alert)

    delimiter = "EOF_POLYMARKET_ALERT"
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS<<{delimiter}\n")
        f.write(full_alert + "\n")
        f.write(f"{delimiter}\n")
else:
    print("No qualifying trades this run.")

print("Run complete.")
