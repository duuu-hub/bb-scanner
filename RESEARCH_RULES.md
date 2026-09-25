# Research Audit & Cleanup Standard

This is the shared operating procedure for every ChatGPT conversation/agent working on trading research in this repository.

## Purpose

Prevent separate chats from trusting stale, bugged, empty, look-ahead-biased, or incompatible backtests. Preserve one auditable lineage per strategy and make cross-chat handoff deterministic.

## Status vocabulary

Use these labels consistently:

- IDEA — not validated.
- UNAUDITED — result exists but implementation/data integrity has not been audited.
- AUDIT PASS — code/data/execution semantics passed the checks below.
- NEEDS FIX — a concrete implementation or pipeline defect exists.
- QUARANTINED — historical results may be contaminated; do not use for decisions/comparisons.
- INVALID — confirmed materially wrong result.
- 5Y PASS — audited implementation passed the defined long-history test.
- OOS PASS — audited implementation passed a genuinely held-out period.
- COST PASS — remains viable under the stated fee/slippage stress.
- DEMO — eligible for forward/shadow demo only.
- ARCHIVE — no longer an active candidate.

A workflow conclusion of SUCCESS is never itself an audit status.

## Required evidence before accepting a backtest

Record and verify:

1. Exact branch and commit SHA.
2. Exact strategy direction and frozen entry/exit rules.
3. Data venue/type (Spot or Futures), timeframe, universe, start/end dates, and source.
4. Input sanity: expected files found, universe > 0, rows/candles > 0, timestamps monotonic, duplicates handled.
5. No future leakage/look-ahead. Indicators and higher-timeframe features must use only information known at entry.
6. Signal timestamp and entry timestamp are explicit. If entry is next bar, enforce it mechanically.
7. Entry bar treatment is explicit. Never omit a tradable entry bar from TP/SL path evaluation.
8. TP/SL same-bar collisions are resolved with lower timeframe data where available; unresolved collisions use a documented conservative rule.
9. Holding limits are wall-clock correct, not merely row-count correct when candles can be missing.
10. Higher-timeframe resampling/alignment is deterministic and warnings about ignored origin/anchor behavior are investigated.
11. Fees, spread/slippage assumptions, funding where relevant, and round-trip cost are explicit.
12. Same-symbol overlap, max positions, exposure/seed cap, and event ordering match the intended portfolio rules.
13. MDD definition is stated. Realized-daily approximation must not be mislabeled as full intraday MTM MDD.
14. Results include trade count, PF, return, MDD, and preferably Sharpe plus yearly/OOS breakdown.
15. Concentration is checked by symbol and time cluster. Hundreds of trades in one event window are not treated as hundreds of independent observations.

## Mandatory bug procedure

When any material defect is found:

1. Stop using the affected metrics immediately.
2. Identify the earliest affected implementation/commit when possible.
3. Mark all downstream results from that implementation QUARANTINED or INVALID.
4. Fix the root cause, not the displayed metric.
5. Add a sanity assertion where practical so an empty/partial run cannot report success unnoticed.
6. Rerun the corrected implementation on the same canonical data.
7. Compare pre-fix vs post-fix metrics and document the delta.
8. Only the corrected lineage can advance to OOS/cost/portfolio/demo evaluation.

Examples of material defects: future data leakage, wrong direction, omitted entry bar, TP-first ambiguity bias, wrong resample alignment, empty universe, partial data mistaken for full data, row-count horizon error, broken slot/exposure chronology.

## Robustness sequence

For candidates that survive code/data audit:

AUDIT PASS -> canonical long-history replay -> temporal/year breakdown -> OOS/walk-forward -> cost/slippage stress -> parameter-neighborhood check -> concentration check -> portfolio interaction -> DEMO/shadow.

Do not optimize using the final OOS period and then call that same period OOS.

## Portfolio rules

Never combine headline returns from separate strategy reports. Re-simulate the combined portfolio chronologically using one initial capital base, exact overlaps, exposure caps, slot limits, entry/exit ordering, costs, and a consistent equity/MDD calculation.

## Cross-chat handoff

Before continuing prior research, a new chat/agent must:
1. Read AGENTS.md and this file.
2. Inspect the relevant branch head and latest workflow/artifact rather than relying on conversation recollection alone.
3. Separate workflow/pipeline failures from strategy failures.
4. Reuse the latest valid implementation; do not recreate completed work.
5. Update the shared master ledger when a strategy changes audit state.

## Current cleanup scope

Current priority families:
1. BTC/ETH Core
2. LONG3/L2
3. Continuation SHORT
4. SMC/ICT/LSOB
5. Rank5
6. ASL1
7. Ichimoku/PSAR
8. AOA lineage
9. Remaining BB/L3/exhaustion research as secondary/archive candidates

Killzone is excluded from this cleanup/audit scope unless explicitly re-enabled by the user.
