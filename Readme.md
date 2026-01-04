```markdown
# Polymarket Big Trade Monitor

Real-time monitoring script for **Polymarket** trades on Polygon.  
Alerts on:

- **Large trades** ≥ **$50,000** (from accounts of **any age** — whale detection)
- **Significant trades** ≥ **$10,000** from **new accounts** (age < 90 days or brand new)

Designed to run periodically (e.g. GitHub Actions every 5–15 minutes) and output alerts to GitHub Actions environment variables (for notifications via email, Discord webhook, Telegram, etc.).

## Features

- Fetches the latest 500 trades from Polymarket's public data API
- Calculates trade value in USDC (prefers `usdcSize` when available)
- Determines account age by looking at the wallet's first-ever trade timestamp
- Caches wallet first-seen timestamp for 14 days to reduce API calls
- Deduplicates alerts using seen trade keys (tx hash or fallback composite key)
- Prunes old data automatically
- Outputs formatted alert blocks + summary stats

## Alerts Examples

**New account alert** ($10K+ from young wallet):

```
ALERT: New user $18,450 bet!
Wallet: 0xabc...123 (age: 4.2d)
Market: Will Bitcoin reach $150k by end of 2025?
Side: YES 18450 @ $0.37
Tx: https://polygonscan.com/tx/0x...
Explorer: https://polygonscan.com/address/0xabc...
```

**Whale alert** (any account ≥ $50K):

```
WHALE: $87,200 bet (any age account)
Wallet: 0xdef...789 (age: 412.7d)
Market: Presidential Election Winner 2028
Side: NO 50000 @ $0.42
Tx: https://polygonscan.com/tx/0x...
Explorer: https://polygonscan.com/address/0xdef...
```

## Requirements

- Python 3.8+
- `requests`
- `python-dotenv` (optional — if using `.env`)

## Setup

1. Clone the repo

```bash
git clone https://github.com/yourusername/polymarket-monitor.git
cd polymarket-monitor
```

2. Install dependencies

```bash
pip install requests python-dotenv
```

3. (Recommended) Create `.env` file in project root

```env
# Optional - change thresholds
NEW_ACCOUNT_THRESHOLD=10000
ACCOUNT_AGE_DAYS=90
LARGE_TRADE_THRESHOLD=50000           # any-age whale threshold
BIG_TRADE_THRESHOLD=10000             # legacy / new-account reference

# How long to keep seen trades & wallet age cache
SEEN_TRADE_RETENTION_DAYS=21
WALLET_TS_TTL_DAYS=14

# If using GitHub Actions notifications
GITHUB_TOKEN=ghp_xxxx...
```

4. Run locally

```bash
python monitor.py
```

## GitHub Actions Example (recommended)

```yaml
name: Polymarket Whale Monitor

on:
  schedule:
    - cron: '*/10 * * * *'    # every 10 minutes
  workflow_dispatch:

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests python-dotenv

      - name: Run monitor
        env:
          LARGE_TRADE_THRESHOLD: 50000
          NEW_ACCOUNT_THRESHOLD: 8000     # example: lower threshold for new accounts
          ACCOUNT_AGE_DAYS: 60
        run: python monitor.py

      - name: Send alert (example: Discord webhook)
        if: env.ALERTS != ''
        env:
          DISCORD_WEBHOOK: ${{ secrets.DISCORD_WEBHOOK }}
        run: |
          curl -H "Content-Type: application/json" \
            -d "{\"content\": \"${{ env.ALERTS }}\"}" \
            $DISCORD_WEBHOOK
```

## Customization

| Environment Variable       | Default   | Purpose                                  |
|----------------------------|-----------|------------------------------------------|
| `LARGE_TRADE_THRESHOLD`    | 50000     | Minimum size for **any-age** whale alert |
| `NEW_ACCOUNT_THRESHOLD`    | 10000     | Minimum size for **new account** alert   |
| `ACCOUNT_AGE_DAYS`         | 90        | Max age (days) to consider "new"         |
| `SEEN_TRADE_RETENTION_DAYS`| 21        | How long to remember seen trades         |
| `WALLET_TS_TTL_DAYS`       | 14        | How long to cache wallet first-trade ts  |

## Known Limitations

- Uses public Polymarket data API — rate limits may apply during very high traffic
- Relies on first-trade timestamp to estimate age (may be slightly inaccurate for wallets with off-chain activity)
- Only shows most recent 500 trades per run (should catch almost all large trades)

## Contributing

PRs welcome — especially:

- Better deduplication / trade key logic
- Support for more alert destinations (Telegram, Slack, etc.)
- Adding market resolution status filtering
- Price impact / liquidity estimation

Enjoy spotting the whales! 🐳

Last updated: January 2026```
