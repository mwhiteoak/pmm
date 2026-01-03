import requests
import os
from datetime import datetime, timezone

# Test thresholds (revert to original after confirming emails)
TRADE_VALUE_THRESHOLD = 100  # Change to 10000 for production
ACCOUNT_AGE_THRESHOLD_DAYS = 90  # Change to 7 for production

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

    # Primary: Active chains endpoint (efficient for first tx)
    url_active = f"https://deep-index.moralis.io/api/v2.2/wallets/{wallet}/chains"
    params_active = {"chains": "0x89"}  # Polygon hex
    try:
        response = requests.get(url_active, headers=headers, params=params_active)
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

    # Fallback: Transactions endpoint (oldest first)
    url_tx = f"https://deep-index.moralis.io/api/v2/{wallet}/transactions"
    params_tx = {"chain": "polygon", "order": "ASC", "limit": 1}
    try:
        response = requests.get(url_tx, headers=headers, params=params_tx)
        response.raise_for_status()
        data = response.json()
        if data.get("result"):
            ts_str = data["result"][0]["block_timestamp"].replace("Z", "+00:00")
            return int(datetime.fromisoformat(ts_str).timestamp())
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"Wallet {wallet} no tx history (404) - treating as very new?")
            # Optional: return current_time to flag as new (uncomment if desired)
            # return int(datetime.now(timezone.utc).timestamp())
        else:
            print(f"Tx endpoint error {e.response.status_code} for {wallet}")
    except Exception as e:
        print(f"Unexpected error for {wallet}: {e}")
    return None

def format_alert(trade, value, age_days):
    return f"""
ALERT: New/Low-Activity Account Large Trade!

Proxy Wallet: {trade.get('proxyWallet')}
Trade Value: ${value:.2f} USDC
Side: {trade.get('side')} {trade.get('size')} shares @ ${trade.get('price')}
Market: {trade.get('title')}
Account Age: {age_days:.2f} days (or no history - very new!)
Trade Time: {datetime.fromtimestamp(trade.get('timestamp', 0), tz=timezone.utc)}
Tx Hash: {trade.get('transactionHash')}

---
"""

print("Starting Polymarket monitor...")
trades = get_recent_trades()
current_time = int(datetime.now(timezone.utc).timestamp())
alerts = []

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    value = float(trade.get("usdcSize") or (float(trade.get("size", 0)) * float(trade.get("price", 0))))

    if value > TRADE_VALUE_THRESHOLD:
        print(f"Large trade ${value:.2f} by {proxy_wallet} - checking age...")
        first_ts = get_first_tx_timestamp(proxy_wallet)
        if first_ts is None:
            # No history = very new account (proxy just deployed)
            age_days = 0
            alerts.append(format_alert(trade, value, age_days))
            print("Match! No tx history - likely brand new account")
        elif (current_time - first_ts) < ACCOUNT_AGE_THRESHOLD_DAYS * 86400:
            age_days = (current_time - first_ts) / 86400
            alerts.append(format_alert(trade, value, age_days))
            print(f"Match! Age {age_days:.2f} days")
        else:
            age_days = (current_time - first_ts) / 86400
            print(f"Too old ({age_days:.2f} days)")

if alerts:
    full_alert = "".join(alerts)
    print(f"{len(alerts)} alert(s) found!\n{full_alert}")
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS={full_alert}")
else:
    print("No qualifying trades this run.")

print("Run complete.")
