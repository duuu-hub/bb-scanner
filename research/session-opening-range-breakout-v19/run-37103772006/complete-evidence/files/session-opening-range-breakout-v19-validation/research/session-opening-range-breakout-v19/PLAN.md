# V19 Session Opening-Range Balance Breakout — Frozen Research Plan

## Status

Preregistered before implementation or any V19 market outcome is observed.

V18 is rejected: all 96 cells had negative cost-stressed mean return and risk-normalized R; the least-bad cell was -22.2427 bp/trade, R -0.11791 and PF 0.7752. Immediate session-opening breadth impulse continuation is therefore not refined.

V19 tests a distinct economic premise: a quiet, two-bar opening range that is accepted as balanced value can store liquidity; a later volume- and taker-confirmed escape from that frozen range may continue. It does not require an opening impulse, breadth shock, failed auction, generic multi-day channel, or immediate breakout retest.

## Frozen causal signal

For exact UTC sessions anchored at 00:00, 08:00 and 16:00:

1. Bars 0 and 1 are the frozen 30-minute opening range.
2. Prior ATR and prior 96-bar quote-volume median must be known before each event. A symbol requires at least 30 observed days.
3. Opening-range width must be at least 0.25 prior ATR and no more than the registered width cap.
4. During the next registered balance window (bars 2 onward), every close must remain inside the frozen opening high/low.
5. The balance window must include at least one close above and one close below its then-known causal session VWAP. Equality does not count.
6. During the next four closed bars, a distinct breakout bar must:
   - close at least 0.10 prior ATR beyond the frozen opening high/low,
   - close in the directional outer quartile of its own range,
   - meet the registered quote-volume multiple versus the shifted prior median,
   - have taker-buy quote share at least 55% for LONG or at most 45% for SHORT,
   - retain the same exact session and contiguous 15-minute path.
7. Enter at the next contiguous 15-minute open in the same session. A favorable gap exceeding 0.5% is excluded; an adverse gap is retained.
8. Structural stop is 0.25 prior ATR inside the broken opening boundary, with a 0.5% risk floor and 6% cap.
9. The opening-range measured-move target is the broken boundary plus one frozen opening-range width. It must remain favorable from actual entry.

No datum after a decision timestamp may affect that decision.

## Frozen entry grid

- side: LONG, SHORT
- maximum opening-range width: 1.0 ATR, 1.5 ATR
- balance window: 4 bars, 8 bars
- breakout quote-volume multiple: 1.25x, 1.75x

Total entry configurations: 2 × 2 × 2 × 2 = 16.

For each entry:
- maximum hold: 16 or 32 bars (4h or 8h)
- exit: one-opening-range measured move, 1.5R, or 2.5R

Total DEV policy cells: 16 × 2 × 3 = 96.

Thresholds are frozen before V19 outcomes. Error recovery with unchanged economics is not a new discovery.

## Development and selection

DEV is 2021-09 through 2023 only. 2024 is already observed and may be processed only after a DEV candidate is frozen. 2025 through 2026-08 and September are not clean holdouts and may not tune V19.

Reject a DEV cell unless all pass:
- resolved N ≥ 300
- at least 60 symbols
- top-symbol positive-contribution share ≤ 30%
- global net40 mean bp > 0 and net40 R > 0
- each of 2021, 2022 and 2023: N ≥ 30, at least 20 active KST dates, net40 mean bp > 0, net40 R > 0 and equal-date diagnostic R > 0

If multiple cells survive, freeze a non-isolated region before any 2024 processing.

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

Daily +0.7% and +2% rates use all intersected KST calendar dates, including inactive and post-halt dates.

## Validation before market execution

- exact 16 entries and 96 policies
- causal session anchors, shifted ATR and shifted volume tests
- frozen opening range and balance-window path tests
- above-and-below VWAP balance acceptance
- distinct later breakout and four-bar expiry
- mirrored LONG/SHORT, volume and taker-flow gates
- next-open contiguity and favorable/adverse gap tests
- stop floor/cap and frozen measured-move target tests
- future perturbation invariance
- immutable 256/856 source catalogue/hash verification
- official chronology/account invariants
- eight-shard synthetic scan retaining all 96 cells

## Failure and preservation

Preserve every parameter, failure, exclusion, source hash, official minute input, log, 96-cell table and account output on research branches. V19 is not successful without cost- and risk-constrained account growth and later robustness/fresh-forward evidence.
