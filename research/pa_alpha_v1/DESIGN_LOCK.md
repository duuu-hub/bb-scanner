# PA Alpha V1 — Design Lock

Goal: maximize robust trading performance, not Wonyotti imitation.

## Fixed source models
- Reuse AOA Price Action V1 feature family and models derived only from 2019-2021 AOA behavior.
- OHLC price action only. No volume, RSI, Bollinger Bands, EMA, ATR, OI, funding, order book.
- Direction model and flip-hazard model are fit on all available AOA-era records through 2021-12-31, then frozen.

## Development / validation split
- Development window: 2022-01-01 through 2024-12-31.
- Final holdout: 2025-01-01 through latest stored BTCUSDT candle.
- The final holdout must not influence candidate selection.
- 2022-2024 candidate selection uses yearly robustness, not headline total return alone.

## Execution
- BTCUSDT 15m.
- Decision on completed 15m candle; execute at next 15m open.
- Always one directional position, 1.0x notional in V1.
- Flip = close old + open opposite at same next-bar open.
- Cost cases: 0 bp diagnostic, 4 bp round trip base, 8 bp round trip stress.

## Candidate policy family
The AOA flip hazard remains the core. We only tune a very small discrete policy family.

Hazard threshold quantile candidates (measured on 2022 state scores):
- 0.90, 0.95, 0.975, 0.99

Minimum hold:
- 1h, 4h, 12h

Confirmation:
- 1 or 2 consecutive bars.

Continuation veto variants:
1. NONE
2. BASIC: suppress a flip while current direction has positive 4h and 12h return and price is in the favorable half of its 24h range.
3. STRONG: suppress a flip while signed 4h >= +25 bp, signed 12h >= +50 bp, and signed 24h range-location >= +0.20 from midpoint.
4. BREAKOUT: suppress a flip when current direction has a 24h breakout OR signed 4h > 0 and signed 24h > 0 with favorable 24h location.

No averaging, pyramiding, or partial exits in Alpha V1. Those are deferred until the directional edge is established.

## Candidate selection on 2022-2024
Primary score is robust and cost-aware:
- require PF > 1.0 under 4 bp cost;
- require at least 2 of 3 calendar years positive under 4 bp cost;
- rank by median yearly return + 0.5*total CAGR - 0.5*abs(MDD);
- reject strategies with fewer than 30 legs over development window.

Select one single policy before revealing 2025+.

## Final report
For selected policy report:
- 2022-2024 development metrics
- 2025, 2026, and aggregate holdout metrics
- 0/4/8 bp cost sensitivity
- return, CAGR, PF, MDD, win rate, leg count, median hold
- year-by-year returns
- BTC buy-and-hold comparison
