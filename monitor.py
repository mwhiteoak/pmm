import requests
import os
from datetime import datetime, timezone

# Configuration (use low for testing, revert later)
TRADE_VALUE_THRESHOLD = 100  # Test with 100, revert to 10000
ACCOUNT_AGE_THRESHOLD_DAYS = 365  # Test with 30, revert to 7

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
    url = f"https://deep-index.moralis.io/api/v2/{wallet}/transactions"
    params = {"chain": "polygon", "order": "ASC", "limit": 1}  # Oldest first, only 1
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        page = data.get("result", [])
        if page:
            oldest_tx = page[0]
            ts_str = oldest_tx["block_timestamp"].replace("Z", "+00:00")
            return int(datetime.fromisoformat(ts_str).timestamp())
        else:
            print(f"No transactions found for wallet {wallet}")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"Wallet {wallet} not found or no txs on Polygon (404)")
        else:
            print(f"Moralis error {e.response.status_code} for wallet {wallet}")
    except Exception as e:
        print(f"Unexpected Moralis error for wallet {wallet}: {e}")
    return None

def format_alert(trade, value, age_days):
    return f"""
ALERT: Qualifying trade found!

Proxy Wallet: {trade.get('proxyWallet')}
Trade Value: ${value:.2f} USDC
Side: {trade.get('side')} 
Shares: {trade.get('size')} at ${trade.get('price')}
Market: {trade.get('title')}
Account Age: {age_days:.2f} days (first tx timestamp)
Trade Timestamp: {datetime.fromtimestamp(trade.get('timestamp', 0), tz=timezone.utc)}
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

    usdc_size = trade.get("usdcSize")
    if usdc_size is not None:
        value = float(usdc_size)
    else:
        value = float(trade.get("size", 0)) * float(trade.get("price", 0))

    if value > TRADE_VALUE_THRESHOLD:
        print(f"Large trade detected (${value:.2f}) - checking age for {proxy_wallet}")
        first_ts = get_first_tx_timestamp(proxy_wallet)
        if first_ts:
            age_days = (current_time - first_ts) / 86400
            if age_days < ACCOUNT_AGE_THRESHOLD_DAYS:
                alerts.append(format_alert(trade, value, age_days))
                print(f"Match! Age {age_days:.2f} days")
            else:
                print(f"Too old ({age_days:.2f} days)")
        else:
            print(f"Skipped (no age data)")

if alerts:
    full_alert = "".join(alerts)
    print(f"{len(alerts)} alert(s) found!\n{full_alert}")
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS={full_alert}")
else:
    print("No qualifying trades this run.")

print("Run complete.")
