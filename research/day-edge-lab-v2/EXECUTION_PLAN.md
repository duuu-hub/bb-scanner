# Canonical replay plan, frozen before account outcomes

The preregistered 144-cell event scout, actual run 36858492497, selected:
- SHOCK_FADE_L1_T6_M-1: long after a closed 15m return <=-6%, max hold 12h.
- SHOCK_FADE_L4_T6_M-1: long after a closed 1h return <=-6%, max hold 24h.

These are raw dependent events, not profitable-account findings. PLAN.md's
development/gate/comparison/recent-check roles and limitations remain binding.
Preserve every scout cell and every rejected account variant.

## Frozen variants

Enter following 15m OPEN. Simultaneous candidates rank by absolute signal
lookback return divided by closed 15m ATR fraction. Keep all causal intents;
portfolio constraints decide executions. No outcome-conditioned suppression.

Initial stop distance = max(2, 3 or 4 times closed Wilder ATR14 on 15m bars,
1% of actual entry). Reject stop distance >8%. Set levels at actual fill from
already-known ATR. Long SL = entry-distance; optional TP = entry+2*distance.

For each multiple:
1. TIME: initial stop or exact selected-seed 12h/24h OPEN timeout.
2. TP2: initial stop, fixed 2R TP or that same timeout.
3. TRAIL: initial stop; after one complete holding hour, after each closed 15m
   bar update long stop to max(old stop, close-multiple*newly closed ATR).
   This stop activates next bar. No next-bar extrema enter its calculation.

Compare 18 single-seed and nine union variants, 27 total. Union combines both
entry masks. On same-coin/same-entry ties the 15m-shock seed and 12h deadline
wins, independently of future outcomes. No same-symbol extra position.

Official Binance 1m resolves entry-bar touches and parent TP/SL collisions.
Repository canonical chronology applies unchanged. Entry-minute TP/SL/TP-only
=> conservative LOSS; established same-minute both => LOSS. Respect earlier
proven 1m exit. DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH explicitly excluded/count.
Never infer ambiguous chronology from parent OHLC. Established single-level
touches conservatively release slots at parent close unless OPEN crossed it.
Actual SL gaps fill at the worse OPEN. Timeouts use exact scheduled OPEN;
only at an exact split endpoint may preceding CLOSE substitute a missing next
OPEN. Missing holding paths excluded. Each shard checkpoints partial work.

## Account/cost rules

Retain 0.5% equity/trade risk, 2% aggregate reserved stop budget, six positions,
30% coin notional, 200% gross; Korea midnight net equity -2% flatten/block;
peak -10% halve future risk, -15% flatten/halt. No +2% profit ceiling.
All budgets/triggers can be exceeded by gaps/slip. Equity/guards observed each
15m, so intrabar equity drawdown can be deeper than reported 15m MDD.

20/40bp round trip at unchanged price, charged on actual entry/exit notional,
additional 10bp adverse SL/forced slip and 2bp/day funding stress on actual
hold. Not historical actual funding/order-book impact. Reserve funding using
each trade's intended max hold; preserve V1's old default 12h budget.
Every holding budget remains <=7 days.

DEV and calendar-2024 GATE restart capital. Show guarded and diagnostic at
both costs; diagnostics disable daily/DD guards only. Report executable N,
net return/CAGR, PF/win/EV/loss streak, utilization and concurrency, full Korea
calendar-day mean and +0.7%/+2% frequencies including inactive/halted days.

## Frozen selection rule

Provisional research survivor: positive guarded account return in both periods
and both costs; no 15% halt; MDD<15%; >=80 trades per row; 20bp CAGR>=20% and
PF>=1.15 in each period; 40bp PF>=1.05; positive full-calendar-year net returns
in 2022/2023/2024 at 20bp. Rank by smaller DEV/GATE 20bp CAGR; prefer lower
stressed MDD for ties. Show neighbours; no isolated-peak promotion.
Any chronology exclusions require explicit data-coverage review before advancing.

This is a material-growth research threshold, not achievement of the user's
higher daily goal. Separately flag whether minimum DEV/GATE calendar daily
mean reaches 0.7%. A lower positive result cannot be relabeled goal completion.

Freeze one winner before 2025-2026 already-observed comparative replay,
15m entry-delay, stronger execution/funding stress, time-block uncertainty and
reserved September recent check. That month may have been viewed in other
chats and is too short for independent confirmation. Forward paper execution
after a final freeze is ultimately necessary. If no survivor, preserve all
failed evidence and declare/execute a new hypothesis batch. Do not refit this
batch to the already-observed comparison years.

Local workspace connection was interrupted during implementation. Continue
via isolated GitHub Actions runners; validate all code/tests before backtests.
Research never touches main, live configuration or watcher state.
