import requests
import os
from datetime import datetime, timezone

# PRODUCTION THRESHOLDS (revert from test)
TRADE_VALUE_THRESHOLD = 100  # $10K+
ACCOUNT_AGE_THRESHOLD_DAYS = 90

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
    # Best endpoint for first tx
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
alerts = []

for trade in trades:
    proxy_wallet = trade.get("proxyWallet")
    if not proxy_wallet:
        continue

    value = float(trade.get("usdcSize") or (float(trade.get("size", 0)) * float(trade.get("price", 0))))

    if value > TRADE_VALUE_THRESHOLD:
        print(f"Large trade ${value:.2f} by {proxy_wallet} - checking age...")
        first_ts = get_first_tx_timestamp(proxy_wallet)
        age_note = ""
        if first_ts is None:
            age_note = " (no history detected - very new/low-activity proxy)"
            age_days = 0
        else:
            age_days = (current_time - first_ts) / 86400
            if age_days >= ACCOUNT_AGE_THRESHOLD_DAYS:
                print(f"Too old ({age_days:.2f} days)")
                continue

        if age_days < ACCOUNT_AGE_THRESHOLD_DAYS or first_ts is None:
            alert_line = f"ALERT: ${value:.2f} trade by {proxy_wallet}{age_note} | Market: {trade.get('title')} | Tx: {trade.get('transactionHash')}"
            alerts.append(alert_line)
            print(f"MATCH: {alert_line}")

if alerts:
    full_alert = "\n".join(alerts)  # Single lines, safe for env/email
    print(f"{len(alerts)} alert(s):\n{full_alert}")
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"ALERTS={full_alert}")
else:
    print("No qualifying trades this run.")

print("Run complete.")
