# V25 Daily Channel Breakout — Preregistered Plan

Preregistered before V25 implementation and before reading any V25 market outcome. V24 is retired: no survivor among 96 cells, with all cell samples below 300. No V24 numerical outcome is used to choose V25 thresholds.

## Economic hypothesis and scope

Sustained multi-day information repricing can continue after a fully closed daily price leaves its preceding multi-day range. A 2–7 day holding window and a daily volatility budget can make the fixed 20/40 bp execution charge a smaller fraction of the intended move than in short-horizon 15m patterns. This is a new time-scale hypothesis, not a rescue of the V24 entry grid and not evidence that the user's daily target is attainable.

Signals use complete UTC daily candles reconstructed from the immutable Binance USD-M 15m source run 36095439671. Execution and account marking retain the existing 15m engine, including official Binance 1m chronology authority. Never change main or live state.

DEV 2021-09 through 2023, 2024 gate, and later historical periods have already been observed across research. None is a pristine holdout. Selection is frozen from DEV before any V25 gate processing. Future paper execution is still required.

## Exact daily construction and causality

- A daily candle is valid only when all 96 expected 15m timestamps from UTC 00:00 through 23:45 exist exactly once, with finite positive valid OHLC. No interpolation, partial-day candle, or forward fill.
- Its signal becomes known at the UTC next-day boundary. Enter only at the next contiguous 15m OPEN.
- All daily rolling statistics restart after an incomplete or missing day.
- At a completed day d, the 5-day or 20-day upper/lower channel is the maximum high / minimum low of exactly those preceding completed days, excluding d.
- ATR20 is the arithmetic mean of daily true ranges in the 20 completed days preceding d, excluding d. True range uses each previous daily close; a gap invalidates the chain.
- A breakout onset is the first daily close strictly above the prior upper channel for long, or strictly below the lower channel for short. If the preceding completed day was already beyond its own same-horizon channel, no new onset is generated.
- Universe at decision time: at least 30 observed source days and at least USD20m quote turnover over the preceding closed 24h (including the just completed day). Do not select symbols using future listing history.
- This study does not impose 15m shock, volume-climax, taker-flow, session VWAP, path-efficiency, or pullback/reclaim filters.

## Frozen 16 entry/risk configurations

Cartesian product:

1. Direction: long / short.
2. Prior daily channel: 5 / 20 days.
3. BTC regime: ANY / ALIGN20. ALIGN20 requires the exactly timestamp-matched completed BTC daily 20-day return in the trade direction to be positive. No missing-day interpolation; unavailable regime is excluded.
4. Frozen stop distance: 1.5 / 2.5 times prior ATR20.

At the next open, stop is actual entry minus/plus the selected daily ATR distance. This strategy has no structural stop promised before entry. All opening gaps are retained at the actual next open; no favorable catch-up filter. Reject nonpositive levels or a stop distance exceeding 25% of actual entry. Cooldown is fixed at two UTC days between accepted seeds per symbol/configuration, independent of trade outcomes.

Rank simultaneous accepted signals by directional breakout distance divided by ATR20. Ties follow the shared deterministic symbol/key ordering.

## Frozen 96 policy cells

Each entry/risk configuration is crossed with:

- Maximum hold: 48h / 168h (192 / 672 15m bars).
- Fixed take profit: 1R / 2R / 4R from actual entry and actual initial stop distance.

16 × 2 × 3 = 96 cells. No trailing implementation, dynamic stop movement, or partial exit. A TP/SL collision inside a parent bar must use official Binance 1m evidence; same entry-minute touch or established same-minute collision resolves conservatively as LOSS. DATA_GAP, ENTRY_MISMATCH, EXIT_MISMATCH are excluded and counted. All positions end at the split boundary at the canonical forced-exit price; a result must never cross into the next split.

## Costs, selection, and account contract

Keep the same engine's 20/40 bp notional cost scenarios, 10 bp adverse stop/forced-exit slip, and 2 bp/day funding stress. Actual historical funding and market impact remain unreconstructed limitations; no cheaper fee assumption is used to rescue V25.

The unchanged strict DEV gates require >=300 outcomes, >=60 symbols, <=30% concentration in positive symbol contribution, positive net40 mean and mean R, and for each 2021/2022/2023: >=30 outcomes, >=20 active KST dates, positive net40 mean, positive mean R, and positive equal-date mean R. Preserve all cells and choose deterministically at most six policies, one exit per entry key and at most three per direction, by worst yearly equal-date R.

Account replay happens only if DEV survives, under identical capital and constraints: equity 1, 0.5% risk/trade, 2% aggregate reserved risk, 30% nominal per symbol, 200% gross, max six positions, no same-symbol duplicate, KST -2% daily flatten/block, DD10% half risk, DD15% halt/flatten. Report total return, CAGR, MTM MDD, executable N, PF, win rate, losing streak, concurrency, capital use, and all-calendar-day +0.7%/+1%/+2% attainment. Correlated daily entries are not independent account trades.

A passing policy is only an exploratory candidate: freeze it before neighboring settings, stronger costs/funding/slippage, 1–3 minute entry delays, time-cluster analysis, seen historical gates and new forward data. No retrospective threshold changes.

## Required validation and preservation

Prove complete-day aggregation; no partial candle, rolling restart at gaps, prior-only channel/ATR, UTC signal availability, exact next-open entry, long/short symmetry, breakout onset, BTC alignment/missing history, fixed cooldown independent of exits, actual entry risk, nonpositive-level and risk-cap exclusions, future perturbation, both hold durations/three TP distances, original chronology and account invariants.

Run the existing full research regression suite plus new V25 tests, and a synthetic eight-shard pipeline preserving all 96 cells. Synthetic output is validation, never profitability evidence.

Preserve plan commit, code commit, frozen BTC/source hashes, every policy/exclusion/raw ledger, actual Actions logs, and complete evidence on research branches. A GitHub Actions success means computation completed, not strategy success. Record actual terminal state in the central continuation files; do not keep an ended run marked RUNNING.
