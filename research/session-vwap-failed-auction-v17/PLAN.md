# V17 Session-VWAP Failed-Auction Reversal — Frozen Plan

Status: PREREGISTERED BEFORE IMPLEMENTATION OR V17 MARKET OUTCOMES  
Branch: `research-session-vwap-failed-auction-v17`  
Parent research code: `217a46b0e752800b70115eb9a63528d809242ce6`  
Source: frozen Binance USD-M 15m catalogue from Actions run `36095439671`, 856 symbols, with the repository's frozen BTC source and official Binance 1m chronology.

## Economic hypothesis

At fixed perpetual-futures inventory-reset windows (00:00, 08:00, 16:00 UTC), a coin that stretches far from the session's causally accumulated quote-volume VWAP on a volume climax and then fails to continue can mean-revert intraday. The trade is taken only after a separate closed confirmation bar moves back through the exhaustion bar midpoint toward the **frozen event-time session VWAP**, then at the next contiguous open.

This is distinct from:
- V10 beta/cross-sectional residual reversion: V17 uses absolute within-session VWAP and fixed clock inventory windows, not cross-sectional residual ranks.
- V8 premium absorption: V17 uses no premium or taker-flow condition.
- V14–V16 market-breadth shock/recovery/continuation: V17 uses no breadth trigger.
- V13 level retest: V17 fades a failed auction around a volume-weighted session fair value rather than following a breakout level.

## Causal signal

All values on decision bar `i` use only bars closed by `i`.

1. Session anchor is the latest of 00:00, 08:00, 16:00 UTC.
2. Session VWAP is cumulative quote volume / base volume proxy using only closed 15m bars since that anchor. If valid volume or contiguous history is missing, exclude.
3. Prior ATR is frozen from data available before the exhaustion bar.
4. Exhaustion must occur during bars 2–24 after the session anchor.
5. For a LONG fade, exhaustion close is below session VWAP by at least `D × ATR`; for SHORT, above it by the same amount.
6. Exhaustion volume multiple is at least `V`; its wick toward the extreme is at least 50% of full range and close location is no worse than the outer quartile.
7. A **later** closed confirmation bar, within four bars, must move toward VWAP, close through the exhaustion midpoint, and have directional body. Same-bar exhaustion/confirmation is forbidden.
8. Entry is the next contiguous 15m open. Favorable catch-up gap greater than 0.5% is excluded; adverse gaps are retained.
9. Structural stop is beyond the exhaustion extreme by 0.25 ATR, with 0.5% minimum and 6% maximum entry risk distance.
10. Frozen target reference is the event-time VWAP; it must never move using future bars.

## Frozen grid

Entry configurations: 16 exactly.

- side: LONG fade after downside exhaustion, SHORT fade after upside exhaustion (2)
- displacement `D`: 2.0 ATR, 3.0 ATR (2)
- volume climax `V`: 1.5×, 2.0× causal rolling median volume (2)
- confirmation strength `C`: close at least 0.5 ATR or 1.0 ATR back from the exhaustion extreme (2)

Total: 2 × 2 × 2 × 2 = 16.

Per entry:
- maximum hold: 16 or 32 bars (4h or 8h)
- exits: frozen event-VWAP target, 1.5R target, 2.5R target

Total DEV policy cells: 16 × 2 × 3 = 96.

No thresholds may be changed after V17 outcomes are observed. Error repairs with unchanged economics are not new discoveries.

## Development and selection

DEV: 2021-09 through 2023 only.  
2024 is an already-seen research gate and is run only after a candidate is frozen.  
2025 through 2026-08 and September are not clean holdouts and must not be used for V17 tuning.

Each of the 96 DEV cells must be rejected unless all frozen gates pass:
- resolved N ≥ 300;
- at least 60 distinct symbols;
- top-symbol share ≤ 30%;
- global net40 mean bp > 0 and net40 R > 0;
- per-year N ≥ 30 and at least 20 active KST dates;
- per-year net40 mean bp > 0, net40 R > 0, and equal-active-date/calendar diagnostic R > 0 for 2021, 2022, 2023.

If multiple cells survive, freeze robust non-isolated candidates before 2024; do not choose an isolated peak.

## Execution and account contract

Use the repository's official Binance 1m chronology:
- pre-entry exits ignored;
- any entry-minute TP/SL touch is SL/loss;
- established same-minute collision is SL/loss;
- only proven earlier 1m exit is honored;
- missing/malformed/mismatched source is excluded, counted, preserved and reviewed.

Identical account comparison:
- starting equity 1;
- 0.5% stop-risk budget per trade;
- 2% aggregate stop-risk budget;
- 30% nominal per symbol;
- 200% gross;
- max six positions;
- no duplicate/opposite same-symbol position;
- KST calendar-day -2% flatten/block;
- 10% peak drawdown halves future risk; 15% flattens and halts the split;
- no profit cap;
- 20bp and 40bp round-trip cost stress, plus frozen adverse stop/forced-exit and funding stress.

Daily +0.7% and +2% hit rates use every intersected KST calendar date, including inactive and post-halt dates.

## Required validation before market execution

- exact 16 configurations and 96 policies;
- causal session boundary/VWAP tests;
- no future volume/price leakage;
- same-bar exhaustion/confirmation forbidden;
- confirmation/entry contiguity and gap tests;
- frozen VWAP target does not move under future perturbation;
- stop floor/cap tests;
- official 1m chronology and account invariants;
- source catalogue/hash verification;
- eight-shard synthetic full pipeline with all 96 cells.

## Failure and preservation rule

All errors, exclusions, parameters, hashes, logs, original minute evidence, 96 cells and account outputs must be preserved on research branches. V17 success requires cost- and risk-constrained account growth evidence; validation, PF, win rate, small positive trade EV, or a one-month result alone is not success.
