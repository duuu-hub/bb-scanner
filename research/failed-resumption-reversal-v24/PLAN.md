# V24 Failed Resumption Reversal — Frozen Research Plan

## Status

Preregistered after the completed V23 DEV rejection and before V24 implementation or V24 market execution.

V23 and every period through 2026-09 are already observed research material. DEV 2021-09 through 2023, the 2024 gate, 2025 through 2026-08 comparisons, and September 2026 are **not clean holdouts** and must not be described as such. V24 selection is exploratory/preregistered on the reused DEV interval; any selected policy must be frozen before robustness, stressed execution, recent data, and future-arriving data checks.

## Economic hypothesis

V23 showed that buying or selling the first high-volume resumption after a smooth path and shallow pause is uniformly negative after costs. V24 does not tune that continuation grid. It tests a different event and direction:

> A smooth directional move followed by a shallow pause attracts breakout/resumption orders. If the attempted resumption is rejected on a distinct later bar that closes back through the pause anchor, especially with opposite taker imbalance, trapped continuation inventory should unwind over the following 2–4 hours.

The trade is opposite the preceding path. It requires an attempted resumption and then a separate failure bar; it never enters merely because V23's original continuation signal fired.

## Frozen causal chronology

For each symbol on official Binance USD-M 15-minute bars:

1. Formation window: 8 or 16 fully closed bars ending before the pause.
2. Formation must have absolute net move at least 1.5 ATR, path efficiency at least 0.55 or 0.70, at least 62.5% closes in the formation direction, and no single bar contributes more than 55% of absolute path.
3. Pause bar: distinct next closed bar, retracing 5–35% of the formation move, remaining on the formation side of its midpoint, and volume no more than 85% of the formation median.
4. Attempt bar: distinct next closed bar, makes a new formation-direction close beyond the pause bar, has volume at least 1.25× the formation median, and satisfies the matching-side taker share at least 55%.
5. Failure bar: distinct next closed bar. It must not use the attempt bar's future information.
6. Entry becomes live at the next 15-minute open after the failure bar. No same-bar entry and no future H/L/C.
7. Direction is opposite the formation/attempt direction.
8. Cooldown: 16 bars per symbol after an accepted seed.

## Frozen entry grid: 16 configurations

Cartesian product:

- formation side: LONG-path failure / SHORT-path failure (2)
- formation window: 8 / 16 bars (2)
- path efficiency: 0.55 / 0.70 (2)
- failure confirmation (2):
  - `REENTRY`: failure close crosses back through the pause close and inside the formation terminal close
  - `OPPOSITE_FLOW55`: all REENTRY conditions plus opposite-direction taker share at least 55%

All formation, pause, attempt, and failure thresholds above are fixed. No threshold is chosen from V24 results.

## Stop and gap rules

At entry:

- For a short after failed upward resumption, structural stop is above max(attempt high, failure high) + 0.10 ATR.
- For a long after failed downward resumption, structural stop is below min(attempt low, failure low) - 0.10 ATR.
- Stop distance floor: 0.60 ATR.
- Stop distance cap: 2.50 ATR. Seeds exceeding the cap are excluded.
- A next-open gap through the stop is retained and filled at the adverse open under the canonical engine.
- Entry/TP/SL collisions use official Binance 1-minute data and the repository's conservative chronology contract. Missing/mismatched minute data are explicitly excluded and counted.

## Frozen policy grid: 96 cells

Each of 16 entries is crossed with:

- maximum hold: 8 / 16 bars
- exit:
  - fixed 1.0R
  - fixed 1.5R
  - trailing exit: after four fully closed bars, 2.0 ATR from the favorable extreme

Thus 16 × 2 × 3 = 96 policy cells.

## Costs and DEV selection gates

Apply the repository's canonical 40 bp round-trip stress and all funding/slippage rules already used by the shared engine.

A DEV policy survives only if all are true:

- at least 300 resolved parameterized outcomes
- at least 60 symbols
- no symbol exceeds 30% of outcomes
- cost-stressed mean return > 0 bp
- cost-stressed mean R > 0
- for each of 2021, 2022, 2023:
  - at least 30 outcomes
  - at least 20 active KST dates
  - mean cost-stressed R > 0
  - mean cost-stressed bp > 0
  - equal-date mean R > 0

Selection is deterministic and frozen before the 2024 gate.

## Account comparison if and only if DEV survives

Use identical starting capital, universe, costs and:

- risk per trade 0.5% equity
- aggregate open risk 2%
- nominal per symbol 30%
- gross nominal exposure 200%
- at most 6 open positions
- no same-symbol duplicate
- KST calendar-day -2% stop and flatten
- peak drawdown 10% halves risk; 15% stops and flattens
- calendar-day target rate includes all KST calendar days, rest days, and post-stop days

Report total return, CAGR where meaningful, MDD, PF, win rate, executable trades, expectancy, losing streak, concurrency, exposure utilization, and fractions of all KST calendar days at +0.7%, +1%, and +2%.

## Anti-overfitting and promotion rule

- V23 outcomes motivate only this qualitative failure-event hypothesis; they do not set any V24 threshold.
- No V24 parameter changes after DEV is read.
- Zero survivors retires V24 and requires a new preregistered economic hypothesis.
- A survivor is only a candidate. Freeze it first, then test neighboring settings, stronger costs/slippage, 1–3 minute entry delay, time-cluster dependence, the already observed 2024 and 2025–2026-08 gates, recent data, and future-arriving data.
- September 2026 alone is insufficient independent evidence.
- Do not call any result a success unless whole-account growth, drawdown, executable frequency, and calendar-day target attainment are jointly meaningful.

## Reproducibility

Source: immutable official Binance-derived dataset from Actions run `36095439671`, with file inventory and SHA256 contract inherited from V23. Preserve code commit, plan commit, input hashes, all exclusions, every policy cell, raw candidate ledgers, actual Actions logs, artifacts, and branch paths.
