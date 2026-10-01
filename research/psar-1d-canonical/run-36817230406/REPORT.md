# PSAR 1D SHORT canonical validation — 2026-10-01

Run: https://github.com/duuu-hub/bb-scanner/actions/runs/36817230406
Artifact: 11141947083 (psar-1d-canonical-reconcile-36817230406)
Code: c663461feed381f31434347b037f578e3c0f2818
Branch: research-rank5-binance-15m-5y
Raw source Run: 36095439671; historical study Run: 36691537039.

## Verdict

The reflip exit consistently improves the fixed exit, but the frozen seven-day SHORT strategy is not stable enough to confirm as a standalone operational strategy. It loses in 2023 and the observed portion of 2026. No HOLDOUT-based duration or market filter was introduced.

## Exact cohort reconciliation

All 14,936 historical Age=3 BEAR observations were reproduced from raw data. There are zero symbol/timestamp mismatches and zero D0 or ret8 mismatches.

The 6,094 -> 6,288 HOLDOUT increase consists of exactly 194 added filtered signals with 12-23 remaining daily bars. The old study required 24 future bars; the later horizon/reflip study required only 12. These are source/segment tails, not a shard overlap or lookahead in PSAR. Of the 194, 191 occur in August 2026; one each occurs in March 2025, March 2026 and June 2026. TRAIN adds three tail/short-segment signals and loses two signals through the slightly moved tercile boundary, for net +1.

The canonical engine uses a common eight-day outcome-availability cohort for all maximum holds 4/5/6/7/8, requires complete contiguous 96-bar UTC days, resets across raw data gaps, and retains the existing 100-day burn-in/event semantics. It excludes LOW only, with no unrequested upper D0 cap. The prior finite TRAIN upper edge silently excluded 15 HOLDOUT observations above the historical TRAIN maximum. The eight-day common cohort adds 51 HOLDOUT signals before that upper-cap repair; the final HOLDOUT is 6,354. TRAIN contains 4,326 filtered signals. Purging TRAIN labels that cross 2025-01-01 is enforced; zero such rows exist in this dataset.

## Frozen rule

1D projected OPEN PSAR, SHORT, age i-flip_index = 3; flip OPEN is age zero, so entry occurs three calendar days after the flip. This preserves the previous code's zero-based Age=3 and does not silently shift it to the third OPEN counted inclusively.

D0 = abs(previous confirmed close - projected PSAR) / prior confirmed ATR14. ATR is the existing study's 14-bar simple mean of true range. TRAIN q1/3 is 2.0542569527384407. Enter only D0 > that threshold. Exit at the first daily OPEN whose projected PSAR side is BULL, or maximum hold OPEN. The TRAIN-only 40bp PF selection chooses seven days. The chosen rule is persisted before HOLDOUT outcome evaluation.

| Max days | TRAIN reflip PF (40bp) | HOLDOUT reflip PF (40bp) |
|---:|---:|---:|
|4|1.0354|1.3002|
|5|1.1068|1.2898|
|6|1.3235|1.2204|
|7|1.4304|1.1770|
|8|1.3927|1.1660|

Seven-day HOLDOUT: n=6,354, PF=1.1770, win=56.012%, mean net return=+0.83995%, average hold=6.3975 days, early exit=18.0831%. Fixed seven-day PF=1.0810 and mean=+0.43339%. At 80bp PF=1.0893; at 100bp PF=1.0478. These costs are flat round-trip deductions, not historical funding or liquidation modeling.

## Annual and rolling results

Frozen seven-day rule, 40bp; years assign trades by entry timestamp. 2026 is incomplete. Last eligible entry is 2026-08-23; raw source coverage is 2021-09-01 through 2026-08-31. The first valid signal after burn-in is 2021-12-14.

| Year | Signals | PF | Win % | Mean net % |
|---:|---:|---:|---:|---:|
|2021|5|0.3587|60.00|-10.5679|
|2022|1,058|1.7974|58.60|+2.6001|
|2023|1,248|0.8018|46.79|-0.8087|
|2024|2,015|1.6423|54.09|+3.0440|
|2025|3,184|1.5155|61.78|+2.2747|
|2026|3,170|0.8816|50.22|-0.6012|

Expanding TRAIN annual replay, selection frozen before each test year: 2023 max7/PF0.7831; 2024 max6/PF1.4148; 2025 max7/PF1.5332; 2026 max7/PF0.8833. Rolling two-year replay gives the same failure years: 2023 PF0.7831; 2024 PF1.4101; 2025 PF1.5288; 2026 PF0.8819. These are retrospective replays on already observed data, not pristine OOS.

BTC regime diagnosis uses only the previous confirmed BTC close versus previous confirmed 200-day SMA. HOLDOUT seven-day PF is 1.2039 above and 1.1600 below. No regime restriction was applied. TRAIN regime rows are descriptive entry-year groups; selection remains the purged TRAIN cohort in frozen_train_choice.json.

A paired equal-weight entry-day block bootstrap (14-calendar-day blocks, 1,000 resamples; 502 observed entry days) gives reflip-minus-fixed improvement +0.62394 percentage points per entry day, 95% interval [+0.08542,+1.21466]. This supports the exit component, not stable profitability of the whole strategy.

## Integrity and practical limits

Thirty named distinct invariants passed ten complete rounds using different randomized fixtures. All 750 usable daily source segments underwent real-data causal/prefix checks each round. Every complete daily OHLC aggregation was compared with its 96 raw 15m bars during collection. Input: 856 files, 968 raw contiguous segments, 112 gaps, 594,774 complete days. The 1D study includes BNX as did its historical source; the 1H study's BNX exclusion was not silently imported.

Worst resolved seven-day signal return at 40bp is -210.774% of entry notional (EVAAUSDT, 2026-07-01 -> 2026-07-08), followed by -210.324% (BAKEUSDT, 2025-09-08 -> 2025-09-11). A short notional return can be below -100%; these are raw OPEN-to-OPEN signal outcomes, not an account drawdown calculation. Intraday margin, liquidation, realistic executable portfolio limits and historical funding have not been modeled. Do not convert raw signal n/mean to account profit, assert an account MDD, or promote the strategy to live trading from this report.

The research scope is completed: row-count discrepancy explained, canonical recomputation finished, TRAIN selection frozen, annual and rolling diagnostics completed, and distinct 30-by-10 audit passed. Account portfolio/margin evaluation would be a separate next study if the candidate is retained.
