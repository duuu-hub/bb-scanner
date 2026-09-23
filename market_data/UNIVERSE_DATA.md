# Bitget market-data architecture

## Why this exists

The live scanner fetches recent candles to calculate Bollinger Bands, but those
responses are transient. They are not a historical OHLCV database.

The original long-term collector also only stored BTCUSDT and ETHUSDT and
rewrote monthly CSV files on every sync. That is fine for two symbols but scales
poorly to hundreds of markets because Git history grows from repeated rewrites.

## Universe store v2

`market_data/universe_collector.py` creates a separate research-grade archive:

- Market: Bitget USDT perpetual futures
- Base interval: 15 minutes only
- Default universe: active, normal **crypto** USDT perpetual contracts
- RWA/stock/ETF/FX perpetuals: excluded by default with Bitget's `isRwa` flag
- Optional scope: `--scope all` or `--scope rwa`
- Partition: one immutable gzip CSV per completed UTC day
- Manifest: one JSON file per day with the exact symbol universe and row counts
- Higher timeframes: derived from 15m with `market_data/universe_store.py`
- API pacing: below Bitget's documented 20 requests/second IP limit
- Failed symbol request: the whole daily partition is rejected instead of silently
  committing a partial dataset

Paths:

```
market_data_store/bitget/universe_15m/
  YYYY/MM/YYYY-MM-DD.csv.gz
  manifests/YYYY/MM/YYYY-MM-DD.json
```

A daily partition contains all selected symbols. Once written successfully it is
not rewritten during normal sync. This avoids touching hundreds of monthly files
every day and keeps Git history much cleaner.

## Daily collection

The production workflow is intended to run shortly after 00:00 UTC and collect
the previous completed UTC day. That gives exactly 96 expected 15m candles for a
contract that traded for the whole day.

```bash
python market_data/universe_collector.py --mode sync
```

Smoke-test a few symbols:

```bash
python market_data/universe_collector.py \
  --mode sync \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --out-root universe_test_output
```

## Backfill

Backfill is manual because a broad universe over hundreds of days is intentionally
a large job:

```bash
python market_data/universe_collector.py --mode backfill --days 120
```

Important: a historical backfill uses the contracts that are active when the
backfill is run. That is not a point-in-time historical universe, so research
using those dates must account for survivorship bias. Daily manifests preserve
the exact selection basis.

## Loading for research

```python
from market_data.universe_store import load_symbol, resample_ohlcv

btc = load_symbol("BTCUSDT", "2026-01-01", "2026-06-01")
btc_1h = resample_ohlcv(btc, "1h")
```

The existing BTC/ETH monthly store remains untouched for long-history research.
Universe v2 is the scalable forward/broad-market archive.


## Verified scale (2026-09-23)

A full smoke test against the live contract list found:

- 802 active USDT perpetual contracts total
- 466 crypto contracts (`isRwa != YES`)
- 336 RWA / stock / ETF / FX style contracts (`isRwa == YES`)
- Crypto-only daily partition: 44,736 rows = 466 × 96 completed 15m candles
- Compressed daily file: about 0.94 MiB
- 8-worker collection runtime: about 45 seconds for the complete crypto universe

At that observed compression ratio, one year of crypto-only daily partitions is
roughly 0.34 GiB before Git object overhead. Because each completed-day gzip file
is immutable, normal daily collection does not repeatedly rewrite old market data.
