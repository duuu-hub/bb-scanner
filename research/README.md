# LSOB / ICT research

This branch isolates ICT-style research from the live/demo LONG3 code.

## Stage 1: reference reproduction
- Upstream: maxs231/lsob-backtest (MIT)
- Data: stored Bitget USDT-futures 15m OHLCV
- BTC window: 2024-08-24 through 2026-08-24
- Timeframes: 1h resampled from 15m, plus native 15m
- Parameters: displacement=3, expiry=24 bars, zone stop, RR=2, 2% risk, 10x cap
- Fees/slippage intentionally match upstream first so the comparison isolates data/timeframe effects.

The upstream engine is vendored without strategy changes in research/vendor/lsob_reference.py.
research/lsob_benchmark.py only adapts the stored candle format, resampling, and result export.

Next stages after the baseline:
1. displacement 2/3/4 robustness
2. ETH
3. broader alt universe where full OHLCV is available
4. FVG and OB+FVG overlap variants
5. regime splits
