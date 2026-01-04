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
