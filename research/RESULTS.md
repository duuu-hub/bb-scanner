# LSOB / ICT validation results

Branch: `research-lsob-ict`

Reference implementation: `maxs231/lsob-backtest` (MIT), vendored unchanged for strategy logic.
Data: stored Bitget USDT-futures 15m OHLCV, resampled to 1h where applicable.
Main window: 2024-08-24 through 2026-08-24.
Costs/config: upstream baseline (maker 0.015%, taker 0.045%, stop slippage 0.05%, RR 2, 2% equity risk, 10x cap, zone stop, 24-bar expiry).

## Reproduction sanity check

Upstream BTC 1h reports 196 trades. On Bitget futures data with the same engine and comparable two-year window, displacement=3 produced 195 trades. That near-identical trade count supports that the strategy implementation was reproduced correctly; performance differs because the market data source/window differs.

## BTC

| TF | Confirm bars | Trades | PF | Expectancy R | Return | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| 1h | 2 | 338 | 0.903 | -0.064 | -44.0% | -51.8% |
| 1h | 3 | 195 | 1.158 | +0.139 | +55.3% | -18.3% |
| 1h | 4 | 100 | 1.437 | +0.296 | +70.8% | -14.2% |
| 15m | 2 | 1137 | 0.774 | -0.170 | -98.7% | -99.0% |
| 15m | 3 | 634 | 0.875 | -0.093 | -77.0% | -82.1% |
| 15m | 4 | 302 | 1.085 | +0.080 | +42.7% | -29.0% |

BTC 1h displacement=3 side split: long PF 1.334 / +0.250R; short PF 0.970 / +0.006R.
BTC 1h displacement=4 side split: long PF 1.544 / +0.364R; short PF 1.306 / +0.207R.

### BTC 1h temporal split

| Confirm | Period | Trades | PF | Expectancy R | Return | Max DD |
|---|---|---:|---:|---:|---:|---:|
| 3 | 2024-08-24..2025-08-23 | 107 | 1.212 | +0.161 | +34.0% | -17.9% |
| 3 | 2025-08-24..2026-08-24 | 88 | 1.113 | +0.111 | +15.9% | -15.6% |
| 4 | 2024-08-24..2025-08-23 | 56 | 1.313 | +0.226 | +25.3% | -14.2% |
| 4 | 2025-08-24..2026-08-24 | 44 | 1.560 | +0.386 | +36.3% | -10.1% |

## ETH

| TF | Confirm bars | Trades | PF | Expectancy R | Return | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| 1h | 2 | 365 | 1.023 | +0.041 | +14.5% | -40.1% |
| 1h | 3 | 222 | 1.078 | +0.074 | +25.8% | -22.9% |
| 1h | 4 | 113 | 0.960 | -0.010 | -6.9% | -32.6% |
| 15m | 2 | 1409 | 0.806 | -0.139 | -98.9% | -99.0% |
| 15m | 3 | 752 | 0.981 | +0.014 | -14.5% | -58.6% |
| 15m | 4 | 356 | 0.815 | -0.089 | -53.6% | -65.7% |

### ETH 1h temporal split, displacement=3

| Period | Trades | PF | Expectancy R | Return | Max DD |
|---|---:|---:|---:|---:|---:|
| 2024-08-24..2025-08-23 | 105 | 1.017 | +0.034 | +2.6% | -22.9% |
| 2025-08-24..2026-08-24 | 117 | 1.130 | +0.110 | +22.7% | -17.9% |

## Current interpretation

1. The upstream confirmation effect reproduced strongly on BTC: 2 bars loses; 3 bars turns positive; 4 bars improves further.
2. The exact best confirmation count is not universal: ETH 1h peaks at 3 in this sample and degrades at 4.
3. Native 15m is not robust under the same bar-count rules. BTC only becomes modestly positive at 4 bars; ETH remains weak.
4. The 1h baseline survives a simple first-half/second-half split on both BTC and ETH at displacement=3.
5. BTC performance is direction-asymmetric in this window: most of the displacement=3 edge comes from longs; shorts are roughly flat.
6. The stored repository currently contains long-term raw 15m OHLCV only for BTCUSDT and ETHUSDT. Broad-alt expansion requires rebuilding/fetching raw candle history before applying LSOB.

## Next research stages

- Build/recover a broad-alt raw 15m dataset, then run the exact same 15m/1h LSOB grid across the universe.
- Add time-normalized 15m settings (e.g. 12 confirmation bars and 96-bar expiry) separately from the same-bar comparison.
- Add FVG-only and OB+FVG-overlap variants only after the LSOB baseline is frozen.
- Split by market regime after the standalone strategy behavior is established.
