# Repository Operating Rules for ChatGPT/Agent Changes

Ruleset version: `2026-10-02`

These rules apply to every ChatGPT conversation, coding agent, research agent,
manual workflow, and automated workflow that reads from or writes to this
repository. They exist so separate sessions cannot silently change research
semantics or interfere with the live LONG3 forward watcher.

## 0. Mandatory start gate (fail closed)

Before changing a file, dispatching a workflow, interpreting a result, or
claiming that work is running, the agent must:

1. Read this entire `AGENTS.md` and the entire `RESEARCH_RULES.md` from the
   target branch. A summary, remembered copy, or prior-chat instruction is not
   a substitute for the current files.
2. Resolve and record the repository, target branch, and current remote HEAD
   SHA. The default branch for this research workstream is
   `research-rank5-binance-15m-5y` unless the user explicitly names another
   non-`main` branch.
3. State the task scope and identify which canonical execution engine, data
   split, cost model, and validation gate apply. Unknown items must be marked
   unknown; they must not be guessed.
4. Check for newer commits before writing. Never overwrite unrelated work from
   another conversation or workflow.
5. Stop without producing or promoting results if the rules cannot be read in
   full, the branch cannot be verified, required data are unavailable, or a
   required validation gate cannot be run.

A generic request such as "continue", "run it", "do the research", or "fix
it" authorizes work only on the declared research branch. It does **not**
authorize a commit, push, pull request merge, or direct change to `main`.

Current explicit user instructions control the requested task. If they change
a frozen repository rule, record the changed rule, scope, and effective run in
the resulting commit/report instead of silently treating the old and new
results as comparable.

## 1. Research/code changes

- Do research and code changes on the declared non-`main` branch.
- Do not develop, commit, push, or dispatch a research run from `main`.
- Do not merge to `main` unless the user explicitly approves that merge in the
  current conversation after seeing the tested change and its impact.
- Run the relevant tests on the research branch before requesting or
  performing an approved merge.
- A tested change can technically be merged while a 4-hour watcher is active,
  but this generation-handoff capability is not permission to merge.
- A running watcher is pinned to the exact code commit it started with.
- Code merged to `main` during that session must take effect only when the next watcher starts.

## 2. LONG3 watcher generation handoff

Current intended behavior:

`Watcher A starts on code v1`
→ explicitly approved research/code v2 may be merged to `main`
→ Watcher A continues using v1 for all remaining 15-minute boundaries
→ Watcher A persists only forward-state/log files without updating its live source checkout
→ Watcher A ends
→ next queued Watcher B checks out latest `main`
→ Watcher B starts on v2 automatically

Do not change this into a hot-reload model unless explicitly requested.

## 3. Forward-state files

Research branches and research PRs must not intentionally edit, reset, replace, or backfill live forward-state files:

- `state.json`
- `paper_signals.csv`
- `scan_runtime.json`
- `signals/pending.jsonl`
- `state/trading_state.json`
- `logs/executions.jsonl`

The live watcher owns these mutable files.

State persistence is implemented with an isolated temporary Git worktree based on the latest remote `main`, overlaying only the forward-state files. This must not pull/rebase new source code into the currently running watcher checkout.

## 4. LONG3 execution safety

Unless the user explicitly makes a new research/operating decision, keep these frozen:

- `trading_mode = DEMO`
- `live_trading_enabled = false`
- active portfolio = `LONG3`
- enabled strategies = `L1, L2, L3`
- position size = 30% of current equity
- max gross exposure = 200%
- max open positions = 6
- same-symbol additional entry disabled
- entry order type = post-only maker limit
- maker wait = 180 seconds
- exchange TP/SL protection required

Never enable real/live trading.

## 5. Before merging runtime changes

For an explicitly approved merge touching the live LONG3 path, inspect current
`main` first because other chats/workflows may have modified it. Reading or
testing against `main` is allowed; developing or writing directly on `main` is
not.

At minimum, validate:

- `scanner.py`
- `long3_watcher.py`
- `long3_signal_adapter.py`
- `auto_trader.py`
- `trade_guard.py`
- `trade_state.py`
- `config/trading_config.json`
- `.github/workflows/scan.yml`

Run the LONG3 unit/safety workflow or equivalent tests before merging.

## 6. Concurrent analysis workflows

Backtests/research workflows may run while the 4-hour watcher is active. They use separate runners and should not share the LONG3 watcher concurrency group.

Prefer research branches for code modifications so live state persistence and research work do not compete for the same code history unnecessarily.

## 7. Workflow dispatch and status truth

An agent must never describe work as queued, running, completed, or successful
from memory or intention. Use the following exact meanings:

- `NOT_DISPATCHED`: no external run ID exists.
- `QUEUED`: a GitHub Actions run ID exists and the current GitHub status is
  queued/pending.
- `RUNNING`: a run ID exists and a fresh status check says it is in progress.
- `COMPLETED`: the run concluded successfully and every required shard and
  artifact exists.
- `FAILED`, `CANCELLED`, or `TIMED_OUT`: report the actual terminal state; do
  not hide it behind "still checking" or silently substitute a retry.
- `PARTIAL`: only some required shards/artifacts completed. Partial output must
  not be reported as the full result.

After dispatch, verify within approximately one minute that a run ID actually
exists. If dispatch failed before an ID was created, retry once and disclose
the retry. A transient infrastructure/download failure may be retried once
after diagnosis; a code, data-integrity, or strategy-logic failure must not be
rerun unchanged and presented as progress.

Every progress report for a long run must include the workflow name, run ID,
current state, branch/commit SHA, and GitHub run URL (the user's inspection and
cancel path). For sharded work, report the total shard count and the IDs/states
of all shards. A chat turn ending does not mean research continues: say it is
continuing only when a verified external run is still queued or running.

## 8. Main-branch merge timing

After explicit user approval, there is no technical need to wait for the
current 4-hour watcher to finish before merging tested research/code into
`main`.

Expected behavior is automatic generation handoff:

- current watcher: old pinned code until session end
- next watcher: newest `main` automatically

If this behavior is not true, treat it as an operational bug and fix it before further runtime changes.

## 9. Signal universe vs Demo execution universe

The canonical LONG3 signal scanner must use the normal live/public crypto
USDT-perpetual universe so forward signals remain comparable with research and
with the future real-account deployment environment.

Bitget Demo supports only a subset of that universe, so Demo availability is an
execution capability filter, not a signal-generation filter.

- Do not restrict the canonical LONG3 scanner to the Demo-only symbol catalog.
- Generate and shadow-track LONG3 signals from the full live/public crypto
  USDT-perpetual universe.
- Before sending an order, the Demo executor must validate the symbol against
  the authenticated Bitget Demo contract catalog.
- A live-only signal that Demo cannot trade must be recorded as
  `DEMO_SYMBOL_UNSUPPORTED` (shadow/counterfactual only), not hidden from the
  signal sample and not retried later as STALE.
- If the Demo contract catalog cannot be resolved, fail closed for orders.
- Do not loosen LONG3 entry ranges or TTL to compensate for scanner latency;
  solve latency separately without shrinking the canonical signal universe.

This separation keeps the forward-research environment aligned with eventual
real trading while respecting the smaller Demo orderable universe.

## 10. PSAR research canonical timing definition

This section is authoritative for all PSAR research unless the user explicitly changes it.

The strategy acts on the PSAR value that is visible/available at the exact OPEN of a new strategy timeframe candle. It must never use that candle's future high, low, or close.

For timeframe TF (currently compare 1H and 4H):

- At candle i OPEN, compute/project the PSAR value using only information that was closed before candle i opened.
- ATR or any other volatility input used at candle i OPEN must likewise use only data available before candle i opened.
- The order becomes live immediately at candle i OPEN. Do NOT delay it to candle i+1 merely because a conventional end-of-bar PSAR implementation labels a value with index i.
- Backtests must reproduce the PSAR dot that would actually be visible on the chart when the new TF candle opens. No same-candle future H/L/C may influence that value.
- Entry target: LONG = open-time PSAR + N*ATR; SHORT = open-time PSAR - N*ATR.
- If the market at candle OPEN is already at a better price than the designed target, execute immediately at the OPEN as taker; do not delete the opportunity.
- Otherwise place the target as a limit and test fills only after the order is live.
- Freeze the open-time PSAR as `PSAR_ref` for the trade. SL is anchored to `PSAR_ref`: at buffer 0, `SL = PSAR_ref`; ATR buffer variants move SL only by the configured buffer from that frozen PSAR.
- After the actual fill is known (OPEN taker or target-limit fill), define `actual_risk = abs(actual_fill - SL)`.
- TP uses the actual fill and that PSAR-anchored actual risk: LONG `TP = actual_fill + R*actual_risk`; SHORT `TP = actual_fill - R*actual_risk`. Never anchor TP to the designed entry target, and never recompute SL from the actual fill.
- Any PSAR result that anchors TP to the designed entry target is non-canonical and must not be used. GitHub Actions run `36403136426` is explicitly INVALID/REJECTED for this reason.
- Every large PSAR backtest must first pass a small timestamp audit proving: input-data cutoff < order-live timestamp, and sampled trades reproduce the intended open-time PSAR/ATR without lookahead.
- Results from implementations that violate this timing definition must be labeled invalid and must not be used for strategy selection or sizing.

Ultimate PSAR research objective: implement the SAME canonical logic on 1H and 4H, then compare them on identical data/execution assumptions before choosing the production timeframe.

## 11. Mandatory intrabar / same-bar execution chronology

This section is authoritative for ALL backtests and research engines in this repository unless the user explicitly changes it. It is not PSAR-specific. A strategy/workflow may not replace, weaken, or silently bypass these rules.

- Never infer TP/SL chronology from OHLC alone when both levels are reachable inside the same parent candle.
- When a parent research candle (for example 15m, 1H, or 4H) contains both TP and SL after entry and their order is ambiguous, resolve chronology using official Binance 1m candles for the relevant interval.
- Entry chronology matters. TP/SL touches that occur before the entry is actually filled are NOT exits.
- For a resting limit order, the first 1m candle that reaches/passes the entry price is the entry minute. A gap-through/reach-through counts as a fill according to the canonical maker-fill logic; do not require the 1m candle to straddle the exact entry price if the canonical engine treats the order as marketable/reached.
- If TP or SL is touched in the SAME 1m candle in which entry occurs, exact post-entry chronology is unknowable from 1m OHLC. Resolve conservatively as SL / LOSS. This includes an entry-minute TP-only touch when the same 1m OHLC cannot prove that TP happened after entry.
- If an already-established position has both TP and SL touched in the SAME 1m candle, exact chronology is unknowable. Resolve conservatively as SL / LOSS.
- If the 1m sequence proves TP occurred first after entry, record TP / WIN. If it proves SL occurred first after entry, record SL / LOSS.
- If the parent candle claimed an entry/fill but authoritative 1m data cannot reproduce the entry, treat it as a data/execution-integrity mismatch; do not guess a win or loss. Record an explicit ENTRY_MISMATCH and exclude that parameterized signal from fills/outcomes instead of hard-failing the whole shard.
- If a parent-candle TP/SL collision requiring 1m chronology cannot be reproduced by authoritative 1m data, record an explicit EXIT_MISMATCH and exclude that trade outcome from win/loss metrics instead of inventing chronology or hard-failing the whole shard.
- Missing, malformed, misaligned, or otherwise unusable official 1m archive data must be treated as DATA_GAP / excluded according to the canonical engine contract, never guessed as TP or SL.
- All 1H and 4H PSAR research, and any future strategy using OHLC backtests, must call/reproduce this same chronology contract. Do not create a strategy-local shortcut that uses 'TP first', 'SL first', arbitrary OHLC ordering, or same-parent-bar blanket SL without first applying the 1m authority rule.
- Before trusting a large run, execute chronology smoke tests covering at minimum: pre-entry TP ignored, entry-minute TP=>LOSS, entry-minute SL=>LOSS, entry-minute both=>LOSS, established-position both=>LOSS, entry then later TP=>WIN, entry then later SL=>LOSS, and DATA_GAP handling.
- A run that does not use this contract, or cannot prove it passed the chronology smoke tests, is INVALID and its PF, return, win rate, MDD, trade count, and sizing conclusions must not be compared with canonical results.

## 12. Result provenance and admissibility

Every reported backtest result must be traceable to all of the following:

- repository and non-`main` branch;
- workflow name and GitHub Actions run ID/URL;
- exact commit SHA and, when relevant, canonical engine SHA;
- data source, market type, universe rule, date range, and data artifact/run ID
  or checksum;
- frozen configuration or complete parameter grid;
- signal timestamp and order-live semantics;
- fees, slippage, funding, and fill assumptions;
- TRAIN/VALIDATION/FORWARD split and whether each split had been seen before;
- exclusions and counts for `DATA_GAP`, `ENTRY_MISMATCH`, `EXIT_MISMATCH`,
  unresolved positions, and failed shards;
- validation/audit command and its outcome.

If any required provenance is absent, label the result `PROVISIONAL`. If a
canonical timing, chronology, leakage, accounting, or data-integrity rule is
violated, label it `INVALID/REJECTED` and do not use it for comparison,
selection, sizing, demo admission, or production decisions. Never copy a
metric from chat memory when the artifact can be checked directly.

## 13. Rule-change and contradiction handling

- `AGENTS.md` is the operational/safety authority; `RESEARCH_RULES.md` is the
  strategy-research and evidence authority. Both are mandatory.
- A strategy-specific document may add stricter requirements but may not
  weaken either file.
- When code, a workflow, a report, and these rules disagree, stop and expose
  the contradiction. Do not choose the convenient interpretation.
- A rule change applies prospectively unless the user explicitly orders a
  historical rerun. Previously generated results retain the rule version and
  validity status under which they were produced.
- Never weaken a guard or relabel an invalid result merely to obtain a better
  PF, return, win rate, MDD, or trade count.
