# Long-term BTC/ETH market data

This directory contains the maintenance code for long-term Bitget USDT-futures market candles used by the strategy research project.

## Scope

- Exchange: Bitget
- Market: USDT futures
- Symbols: BTCUSDT, ETHUSDT
- Interval: 15 minutes
- Source endpoint: `/api/v2/mix/market/history-candles`
- Stored fields: timestamp, UTC datetime, OHLC, base volume, quote volume

## Maintenance behavior

`collector.py` is designed to handle four jobs automatically:

1. Initial historical backfill as far back as Bitget returns data.
2. Incremental updates after the newest stored completed candle.
3. Integrity checks for timestamp alignment, OHLC invariants, duplicate timestamps, and internal 15-minute gaps.
4. Automatic recovery of missed intervals by re-querying the missing ranges.

Data is stored as monthly CSV files under:

`market_data_store/bitget/15m/<SYMBOL>/YYYY-MM.csv`

The GitHub Actions workflow runs once per day. If a scheduled run is missed or fails, the next successful run starts from the newest stored candle and repairs internal gaps before committing data.

No API key or secret is required because the collector uses Bitget public market-data endpoints.

## Manual use

Update and repair both symbols:

```bash
python market_data/collector.py --mode sync
```

Force a historical rebuild/backfill:

```bash
python market_data/collector.py --mode backfill
```

Run only one symbol:

```bash
python market_data/collector.py --mode sync --symbol BTCUSDT
```
