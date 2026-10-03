# V20 Session Opening-Range Liquidity Sweep Reversal — Frozen Research Plan

## Status

Preregistered before implementation or any V20 market outcome is observed.

V19 is rejected after the actual Actions run `37103772006`: 96 cells were executed and none had positive risk-normalized DEV expectancy. Only one sparse cell had positive mean return/PF (47 outcomes, 39 symbols, +10.3986 bp, R -0.03152, PF 1.1257); it was negative in 2021 and 2023. The later opening-range continuation family is retired rather than locally tuned.

V20 tests a distinct, falsifiable economic premise: after a quiet opening range has been accepted as two-sided value, a volume/taker-aggressive wick through a frozen range boundary that closes decisively back inside can be a liquidity sweep rather than price discovery. The trade is against the sweep toward previously accepted value. This is not V19 with nearby thresholds: V19 required a close outside and followed it; V20 requires an outside wick plus a close back inside and reverses it. It is also narrower than V17's generic session-VWAP failed auction because the liquidity pool, rejection boundary and targets are all fixed by the first two session bars.

If V20 fails its frozen gates, retire this opening-range family; do not reverse or locally tune it again from V20 outcomes.

## Frozen causal signal

For exact UTC sessions anchored at 00:00, 08:00 and 16:00:

1. Bars 0 and 1 freeze the 30-minute opening high, low, midpoint and width.
2. Prior ATR and prior 96-bar quote-volume median must be known before each event. A symbol requires at least 30 observed days.
3. Opening-range width must be at least 0.25 prior ATR and no more than the registered width cap.
4. During the next registered acceptance window (bars 2 onward), every close must remain inside the frozen opening range.
5. The acceptance window must include at least one close strictly above and one strictly below its then-known causal session VWAP.
6. During the next four closed bars, the first qualifying sweep bar must:
   - for an upper sweep, trade at least 0.10 prior ATR above the frozen opening high, then close at least 0.05 prior ATR back below that high;
   - for a lower sweep, trade at least 0.10 prior ATR below the frozen opening low, then close at least 0.05 prior ATR back above that low;
   - close inside the frozen opening range;
   - meet the registered quote-volume multiple versus the shifted prior median;
   - show aggressor participation in the swept direction: taker-buy quote share at least 55% for an upper sweep and at most 45% for a lower sweep;
   - retain the same exact session and contiguous 15-minute path.
7. Enter reversal at the next contiguous 15-minute open in the same session: SHORT after an upper sweep, LONG after a lower sweep.
8. A favorable next-open gap exceeding 0.5% versus the sweep close is excluded; an adverse gap is retained.
9. Structural stop is 0.10 prior ATR beyond the sweep bar extreme, with a 0.5% risk floor and 6% cap.
10. Frozen structural targets are the opening-range midpoint and the opposite opening boundary. A structural target must remain favorable from actual entry or that parameterized outcome is excluded.

No datum after a decision timestamp may affect that decision. The sweep bar cannot also be an accepted V19 breakout because its close must be back inside the frozen range.

## Frozen entry grid

- reversal side: LONG after lower sweep, SHORT after upper sweep
- maximum opening-range width: 1.0 ATR, 1.5 ATR
- acceptance window: 4 bars, 8 bars
- sweep quote-volume multiple: 1.25x, 1.75x

Total entry configurations: 2 × 2 × 2 × 2 = 16.

For each entry:
- maximum hold: 16 or 32 bars (4h or 8h)
- exit: opening-range midpoint, opposite opening boundary, or 2.0R

Total DEV policy cells: 16 × 2 × 3 = 96.

Thresholds and all tie-breaking rules are frozen before V20 outcomes. Error recovery with unchanged economics is not a new discovery.

## Development and selection

DEV is 2021-09 through 2023 only. 2024 is already observed and may be processed only after a DEV candidate is frozen. 2025 through 2026-08 and September are not clean holdouts and may not tune V20.

Reject a DEV cell unless all pass:
- resolved N ≥ 300
- at least 60 symbols
- top-symbol positive-contribution share ≤ 30%
- global net40 mean bp > 0 and net40 R > 0
- each of 2021, 2022 and 2023: N ≥ 30, at least 20 active KST dates, net40 mean bp > 0, net40 R > 0 and equal-date diagnostic R > 0

If multiple cells survive, freeze a non-isolated region before any 2024 processing. A sparse positive average, PF-only result, one-year result or boundary spike is not a candidate.

## Execution and account contract

Use the shared official Binance 1-minute chronology:
- ignore exits before entry
- entry-minute TP/SL ambiguity is loss
- established-position same-minute collision is loss
- honor only a proven earlier 1-minute exit
- exclude, count and preserve missing/malformed/mismatched data

Identical account comparison:
- starting equity 1
- 0.5% stop-risk per trade; 2% aggregate
- 30% nominal per symbol; 200% gross
- maximum six positions; no duplicate/opposite same symbol
- KST calendar-day -2% flatten/block
- 10% peak drawdown halves future risk; 15% flattens and halts
- no profit cap
- frozen 20bp and 40bp cost/funding/forced-exit stress

Daily +0.7% and +2% rates use all intersected KST calendar dates, including inactive and post-halt dates. These are research targets, not execution-price or return guarantees.

## Validation before market execution

- exact 16 entries and 96 policies
- causal session anchors, shifted ATR and shifted volume
- frozen opening range and contiguous acceptance path
- strict above-and-below causal-VWAP acceptance
- outside wick, decisive inside close and four-bar expiry
- mirrored upper/SHORT and lower/LONG sweep rules
- volume and swept-direction taker-flow gates
- next-open contiguity and favorable/adverse gap handling
- stop beyond actual sweep extreme with floor/cap
- frozen midpoint/opposite-boundary targets and unfavorable-target exclusion
- mutual exclusion from V19 outside-close breakouts
- future perturbation invariance
- immutable source catalogue/hash verification
- official chronology/account invariants
- eight-shard synthetic scan retaining all 96 cells

## Failure and preservation

Preserve every parameter, failure, exclusion, source hash, official minute input, log, 96-cell table and account output on research branches. V20 is not successful without cost- and risk-constrained account growth and later robustness/fresh-forward evidence.
