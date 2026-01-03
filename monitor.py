import requests
import os
from datetime import datetime, timezone

# Configuration
POLYMARKET_TRADES_URL = "https://data-api.polymarket.com/trades"
TRADE_VALUE_THRESHOLD = 100  # $10K in USDC
ACCOUNT_AGE_THRESHOLD_DAYS = 365

# Load Moralis API key
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")

def get_recent_trades():
    params = {"limit": 500}
    try:
        response = requests.get(POLYMARKET_TRADES_URL, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching Polymarket trades: {e}")
        return []

def get_first_tx_timestamp(wallet):
    if not MORALIS_API_KEY:
        print("Missing Moralis API key - check secrets")
        return None
    headers = {"X-API-Key": MORALIS_API_KEY}
    url = f"https://deep-index.moralis.io/api/v2.2/wallets/{wallet}"
    params = {"chain": "polygon", "order": "ASC"}  # Get oldest tx first
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        cursor = data.get("cursor")
        page = data.get("result", [])
        if page:
            oldest_tx = page[0]  # First page, oldest due to ASC order
            ts_str = oldest_tx["block_timestamp"]
            ts_str = ts_str.replace("Z", "+00:00")
            return int(datetime.fromisoformat(ts_str).timestamp())
    except Exception as e:
        print(f"Moralis error for wallet {wallet}: {e}")
    return None

def format_alert(trade, value, age_days):
    return f"""
ALERT: New account trade over $10K!

Proxy Wallet: {trade.get('proxyWallet')}
Trade Value: ${value:.2f} USDC (using usdcSize if available, else size * price)
Side: {trade.get('side', 'Unknown')} 
Size: {trade.get('size', 'N/A')} shares
Price: ${trade.get('price', 'N/A')}
Market: {trade.get('title', 'Unknown')}
Account Age: {age_days:.2f} days
Timestamp: {datetime.fromtimestamp(trade.get('timestamp', 0), tz=timezone.utc)}
Tx Hash: {trade.get('transactionHash')}

---
"""

# Main
print("Starting Polymarket monitor run...")
trades = get_recent_trades()
if not trades:
    print("No trades fetched - check Polymarket API.")
current_time = int(datetime.now(timezone.utc).timestamp())
alerts = []

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    # Prefer usdcSize if available (more accurate value)
    usdc_size = trade.get("usdcSize")
    if usdc_size is not None:
        value = float(usdc_size)
    else:
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        value = size * price

    if value > TRADE_VALUE_THRESHOLD:
        first_ts = get_first_tx_timestamp(proxy_wallet)
        if first_ts:
            age_seconds = current_time - first_ts
            if age_seconds < ACCOUNT_AGE_THRESHOLD_DAYS * 86400:
                age_days = age_seconds / 86400
                alerts.append(format_alert(trade, value, age_days))
        else:
            print(f"Could not get age for wallet {proxy_wallet} - skipping")

if alerts:
    full_alert = "".join(alerts)
    print(f"{len(alerts)} alert(s) found!")
    print(full_alert)  # Visible in Actions logs
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS={full_alert}")
else:
    print("No matching trades found this run.")

print("Monitor run complete.")
