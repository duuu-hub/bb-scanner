# V27 Daily Breakdown — Intraday Capacity and Latency Study

## Preregistration and hypothesis
V25 remains rejected at its frozen gates; V26 remains exploratory with no qualified candidate. V26's seven-day shorts concentrate returns in a few crash dates, and daily ATR risk sizing uses little capital. This study isolates whether the same full eight-key short family retains executable account value when held for at most 24 hours, allowed only one or two KST entries per day, and entered after a fixed delay. It is a horizon/capacity/latency diagnostic, not a novel signal discovery or rescue of V25. Register this plan before implementation or V27 market outcomes.

## Frozen family and data
All eight V25 short entry keys: side -1; prior daily Donchian width 5/20; BTC regime ANY/ALIGN20; prior daily ATR20 stop multiple 1.5/2.5. Keep complete-UTC-day aggregation, preceding-only channel and ATR, price onset, 30-day decision-time history, USD20m closed-24h turnover, two-day outcome-independent cooldown, nominal next exact UTC 00:00 open and score. No new entry filter, union, optimized risk or best-cell selection.

Use immutable official Binance USD-M 15m run 36095439671 and BTC context run 36858492497, the V25 frozen context SHA256 dab527bef32197364070e1d1cd17b5f9260cbf17f099e9698070ce5843c87faa, eight original shards / 856 processed files. Verify each source byte against the full frozen inventory. DEV = 2021-09-01 to 2024-01-01 UTC; GATE = 2024-01-01 to 2025-01-01 UTC. Independently scan both periods on all decision-time eligible symbols, including new 2024 listings. Physically truncate feature/outcome inputs before 2025. Both periods have already been observed; neither is pristine OOS. Do not open 2025/2026 outcomes or the September recent check.

## Entry delay and exit contract
For each key, delay is exactly 0 or 1 completed 15m bar (0/15 minutes). This is a coarse adverse execution stress, not a calibrated estimate of a 1–3-minute order delay. No interpolated prices or subminute claims.
- Freeze SL at the nominal next-open entry plus the original daily ATR distance BEFORE looking at the delayed price. Do not recompute/relocate it from a delayed fill.
- At delayed time, use that exact authoritative 15m OPEN. Require a contiguous path from nominal to delayed open.
- If the delayed open is at/beyond the frozen stop, or the original frozen stop was touched during the waiting interval, cancel the order causally and record DELAY_STOP_INVALIDATED. Such a cancelled order never existed; count it separately and do not call it a losing/winning execution. Its future outcome cannot affect cancellation.
- If delayed entry yields invalid/nonpositive levels or actual stop risk exceeds the unchanged 25% cap, explicitly record the reason.
- Rank concurrent intents using only the original closed-day score, then symbol. Delayed price/extrema do not alter the score or create new signal eligibility.
- Fixed TP = actual fill minus twice the actual distance to the frozen SL. Never anchor TP to a designed fill. Nonpositive targets are excluded/countable.
- Maximum hold = 96 x 15m = 24 hours from ACTUAL fill. Exact scheduled next OPEN timeout, or original split boundary without carry.
- Use unchanged shared canonical.resolve with max_hold_bars 96 and official Binance 1m chronology for parent-bar ambiguity. Pre-entry touches are ignored; entry-minute ambiguity and established-position same-minute collision conservatively LOSS; DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH explicit exclusions. Missing paths are never interpolated.
Exactly 8 keys x 2 delays = 16 independent outcome policies; exits are fixed, not swept.

## Account matrix and invariant rules
Replay each of the 16 policies independently, each DEV/GATE split, each 20/40bp roundtrip notional cost, each guarded/unguarded diagnostic, each KST daily entry cap UNLIMITED/1/2.
Exactly 16 x 2 x 2 x 2 x 3 = 384 account scenarios. UNLIMITED is a matched 24h control, not a deployment option; unguarded is risk diagnosis only.
The cap counts ACCEPTED account executions, not raw signals or rejected intents. Rejected slot/same-symbol/risk/guard attempts consume no quota. Reset at KST midnight, even while positions remain open. Causal timestamp/score/symbol ordering is unchanged.

Use the shared relative_pullback_portfolio.simulate, with one OPTIONAL daily-cap argument defaulting to None; all previous default behavior must remain equivalent. Equity 1, risk 0.5% per entry, 2% aggregate reserved stop risk, 30% symbol notional, 200% gross, max 6 positions, no same-symbol additions; fees charged on actual entry and exit notional, stop/forced slip 10bp, funding stress 2bp/day using actual hold. Same KST -2% flatten/block, DD10% halve new risk, DD15% flatten/halt. Report every intersected KST date including inactive and post-halt days, mark partial boundary days. Shared chronology contract, source hashes and all older tests remain mandatory.

## Reports and predeclared material screens
Preserve all 384 summaries, executable trade logs, 15m MTM curves, full daily calendars, yearly/quarterly returns, independent outcomes, all exclusions, cancelled-delay counts, quota/slot/risk rejections, source/minute hashes and original logs. Report net return, CAGR, MDD, PF, win rate, executable N, EV, max loss streak, concurrency/exposure/utilization/idle dates and +0.7/+1/+2% day frequencies. Never multiply raw event N by EV to imply account returns.

For every policy/split: show trade-weighted and equal-entry-date independent R, top-five positive entry-date share, arithmetic removal of the single/five best positive entry dates. For each 40bp guarded account: identical concentration tests on original realized net account P&L, without reallocating/replaying deleted capital.

A further-audit flag may only apply to cap 1/2, and must pass BOTH delays and BOTH periods: 20bp guarded CAGR >=20%, PF >=1.15; 40bp guarded positive account return, PF >=1.05; all guarded rows N>=80, MDD<15%, no DD15 halt; full KST years 2022/2023/2024 net return positive at BOTH costs; 40bp guarded net account P&L remains positive after arithmetic removal of the five best positive entry dates, and those dates contribute <50% of positive-entry-date P&L. Display every neighbour and the unlimited control. Such flags are exploratory, not independently verified/promotable candidates, and never rewrite V25 rejection. Record failed conditions rather than tuning them after outcomes.

Separately report whether every primary guarded cap-1/2 period/delay at 40bp achieves calendar-day mean >=0.7%. Material-growth flags that fail the daily goal do not meet the user's objective. No daily profit guarantee.

## Validation and continuation
Before dispatch: verify source/frozen hashes and exact matrix; test causal delay/no stop relocation, gap/cancellation handling, 24h deadlines, entry-minute chronology, actual-entry quota accounting, KST reset, rejected intents not consuming quota, default engine equivalence and complete 8-shard/16-policy/384-account synthetic pipeline. Run all V26/older research regressions in the same process to expose global state leakage. Preserve implementation failures without tuning economic rules.

Use a separate research branch only; never main/live config/watcher state or real orders. Final checkpoint uses current branch/run identity guards, records actual terminal stages and ECONOMIC_AUDIT_PENDING, never calls calculation success profitability. Audit actual outputs after completion and preserve failure before a next preregistered economic hypothesis. At no stage claim an optimum or pristine holdout from this selected historical family.
