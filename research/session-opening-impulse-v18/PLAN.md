# V18 Session Opening Impulse Acceptance — Frozen Research Plan

## Status

Preregistered before implementation or any V18 market outcome is observed.

V17 is rejected: 0/96 DEV survivors. Its positive long tail was sparse and regime-dependent, while all short cells had non-positive cost-adjusted R. V18 does not retune V17 exhaustion thresholds. It tests a different mechanism: continuation after a scheduled global-session opening impulse is accepted rather than faded.

## Economic hypothesis

At 00:00, 08:00 and 16:00 UTC, new regional risk flow can reprice crypto across many instruments. A large first-30-minute directional impulse that is broad across the frozen universe, remains on the same side of its new-session VWAP, and survives a later shallow pullback may represent inventory transfer rather than a transient wick. Reacceleration after that pullback should continue for several hours.

This differs from:
- V14–V16, which used rolling market breadth recovery/expansion without a fixed session-opening auction;
- V17, which faded extreme displacement back toward session VWAP;
- V13, which retested pre-existing breakout levels rather than requiring a newly formed session impulse, session VWAP acceptance and cross-sectional opening breadth.

## Causal event sequence

For every 8-hour UTC session beginning at 00:00, 08:00 or 16:00:

1. Build the opening impulse only from the first two fully closed 15m bars.
2. Direction is the sign of close(bar 2) minus session open. The absolute move must exceed the frozen ATR multiple.
3. At bar-2 close, compute causal frozen-universe breadth: fraction of eligible symbols with same-direction first-30m returns above 0.25 ATR. No current/future bar may enter ATR or eligibility.
4. Both opening bars must close on the impulse side of causal session VWAP; the second close must be in the directional outer quartile of its range.
5. During the next four closed bars, require a pullback toward session VWAP without closing through it and without retracing more than the frozen fraction of the opening impulse.
6. A distinct later confirmation bar must close beyond the previous bar's directional high/low and on the impulse side of session VWAP. Pullback and confirmation cannot be the same bar.
7. Enter at the next contiguous 15m open. A favorable gap exceeding 0.5% is excluded; an adverse gap is retained.
8. Stop is beyond the pullback extreme plus 0.25 ATR, with 0.5% floor and 6% cap. Freeze the session VWAP and opening-range extreme at entry.

No step may use data that was not closed before its decision timestamp.

## Frozen entry grid

- side: LONG, SHORT
- first-30m displacement: 1.0 ATR, 1.5 ATR
- same-direction opening breadth: 55%, 65%
- maximum pullback retracement of opening impulse: 38.2%, 61.8%

Total entry configurations: 2 × 2 × 2 × 2 = 16.

For each entry:
- maximum hold: 16 or 32 bars (4h or 8h)
- exit: opening-range projection 1.0× impulse, 1.5R, or 2.5R

Total DEV policy cells: 16 × 2 × 3 = 96.

Thresholds are frozen before V18 outcomes. Technical error recovery with unchanged economics is not a new discovery.

## Development and selection

DEV is 2021-09 through 2023 only. 2024 is already observed and may be used only as a gate after a candidate is frozen. 2025 through 2026-08 and September are not clean holdouts and must not be used for V18 tuning.

A DEV cell is rejected unless all gates pass:
- resolved N ≥ 300
- at least 60 symbols
- top-symbol share ≤ 30%
- global net40 mean bp > 0 and net40 R > 0
- per-year N ≥ 30 and at least 20 active KST dates
- per-year net40 mean bp > 0, net40 R > 0, and equal-active-date/calendar diagnostic R > 0 for 2021, 2022 and 2023

If multiple cells survive, freeze a non-isolated robust region before any 2024 processing.

## Execution and account contract

Use the repository's official Binance 1m chronology:
- pre-entry exits ignored
- any entry-minute TP/SL touch is SL/loss
- an established-position same-minute collision is SL/loss
- only a proven earlier 1m exit is honored
- missing, malformed or mismatched source is excluded, counted, preserved and reviewed

Identical account comparison:
- starting equity 1
- 0.5% stop-risk per trade
- 2% aggregate stop-risk
- 30% nominal per symbol
- 200% gross
- maximum six positions
- no duplicate or opposite same-symbol position
- KST calendar-day -2% flatten/block
- 10% peak drawdown halves future risk; 15% flattens and halts the split
- no profit cap
- 20bp and 40bp round-trip cost stress plus frozen adverse stop/forced-exit and funding stress

Daily +0.7% and +2% hit rates use every intersected KST calendar date, including inactive and post-halt dates.

## Validation required before market execution

- exact 16 entry configurations and 96 policies
- causal fixed-session boundary and VWAP tests
- frozen-universe breadth computed only at the bar-2 close
- no future ATR, price, breadth or universe leakage
- pullback and confirmation on distinct closed bars
- confirmation-to-entry contiguity and favorable/adverse gap tests
- frozen targets and structural stop floor/cap tests
- official 1m chronology and account invariants
- immutable source catalogue/hash verification
- eight-shard synthetic pipeline producing all 96 cells

## Failure and preservation rule

Preserve every error, exclusion, parameter, source hash, log, original minute record, 96-cell table and account output on research branches. V18 is not successful without cost- and risk-constrained account growth evidence. Validation, PF, win rate, per-trade EV, or a short recent interval alone is not success.
