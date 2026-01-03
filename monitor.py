import requests
import os
from datetime import datetime

# Configuration
POLYMARKET_TRADES_URL = "https://data-api.polymarket.com/trades"
TRADE_VALUE_THRESHOLD = 100  # $10K in USDC
ACCOUNT_AGE_THRESHOLD_DAYS = 30

# Load Moralis API key from secrets
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")

def get_recent_trades():
    params = {"limit": 500}
    response = requests.get(POLYMARKET_TRADES_URL, params=params)
    if response.status_code == 200:
        return response.json()
    print(f"Error fetching trades: {response.status_code} {response.text}")
    return []

def get_first_tx_timestamp(wallet):
    if not MORALIS_API_KEY:
        print("Missing Moralis API key")
        return None
    headers = {"X-API-Key": MORALIS_API_KEY}
    url = f"https://deep-index.moralis.io/api/v2.2/wallets/{wallet}/chains"
    params = {"chains": "0x89"}  # Polygon
    response = requests.get(url, headers=headers, params=params)
    |
    if response.status_code == 200:
        data = response.json()
        for chain in data.get("active_chains", []):
            if chain.get("chain_id") == "0x89":
                first_tx = chain.get("first_transaction", {}).get("block_timestamp")
                if first_tx:
                    # Handle ISO format with Z
                    ts_str = first_tx.replace("Z", "+00:00")
                    return int(datetime.fromisoformat(ts_str).timestamp())
    else:
        print(f"Moralis error for wallet {wallet}: {response.status_code}")
    return None

def format_alert(trade, value, age_days):
    return f"""
ALERT: New account trade over $10K!

Proxy Wallet: {trade.get('proxyWallet')}
Trade Value: ${value:.2f} USDC
Side: {trade.get('side')} {trade.get('size')} shares at ${trade.get('price')}
Market: {trade.get('title')}
Account Age: {age_days:.2f} days
Timestamp: {datetime.fromtimestamp(trade['timestamp'])}
Tx Hash: {trade.get('transactionHash')}

---
"""

# Main execution
trades = get_recent_trades()
current_time = int(datetime.now().timestamp())
alerts = []

for trade in trades:
    size = float(trade.get("size", 0))
    price = float(trade.get("price", 0))
    value = size * price
    proxy_wallet = trade.get("proxyWallet")

    if value > TRADE_VALUE_THRESHOLD and proxy_wallet:
        first_ts = get_first_tx_timestamp(proxy_wallet)
        if first_ts and (current_time - first_ts) < ACCOUNT_AGE_THRESHOLD_DAYS * 86400:
            age_days = (current_time - first_ts) / 86400
            alerts.append(format_alert(trade, value, age_days))

# Output alerts for email step
if alerts:
    full_alert = "".join(alerts)
    print(f"{len(alerts)} alert(s) found!")
    print(full_alert)
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS={full_alert}")
else:
    print("No matching trades found.")
