# Mega-runner precursor research

Goal: identify measurable conditions that appeared **before** large altcoin runs, then compare them with contemporaneous non-runners.

## Dataset
- Primary: Binance Spot USDT OHLCV, default 1h, requested 5 years.
- Fetches as far back as each currently-traded symbol's available listing history within the requested window.
- Labels: maximum forward return within 365 days from each candidate low; thresholds +500%, +1000%, +2000%, +5000%.
- The precursor feature stage must only use information available at each observation timestamp (no future leakage).

## Run
GitHub Actions -> **Mega Runner Dataset** -> Run workflow. Default = 5 years / 1h.

After dataset build: add precursor windows (30d/14d/7d/3d/24h), matched controls, feature significance, walk-forward/OOS, then current-universe scoring.
