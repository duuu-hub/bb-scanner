# Repository Operating Rules for ChatGPT/Agent Changes

These rules exist so work from separate ChatGPT conversations does not interfere with the live LONG3 forward watcher.

## 1. Research/code changes

- Do research and code changes on a separate branch.
- Do not develop directly on `main`.
- Run the relevant tests on the branch before merging.
- It is safe to merge tested code to `main` while a 4-hour LONG3 watcher is already running.
- A running watcher is pinned to the exact code commit it started with.
- Code merged to `main` during that session must take effect only when the next watcher starts.

## 2. LONG3 watcher generation handoff

Current intended behavior:

`Watcher A starts on code v1`
→ research/code v2 may be merged to `main` at any time
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

For changes touching the live LONG3 path, inspect current `main` first because other chats/workflows may have modified it.

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

## 7. Main-branch merge timing

There is no need to wait for the current 4-hour watcher to finish before merging tested research/code into `main`.

Expected behavior is automatic generation handoff:

- current watcher: old pinned code until session end
- next watcher: newest `main` automatically

If this behavior is not true, treat it as an operational bug and fix it before further runtime changes.

## 8. Signal universe vs Demo execution universe

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

## 9. SMC Demo freshness

SMC Demo uses closed 1H candles and must fail closed if the latest completed
hour is missing or stale.

- The newest retained 1H candle must be exactly the immediately preceding hour.
- Do not place or keep SMC pending orders from stale market history.
- `expiry_bars` is enforced by wall-clock age as well as replay-bar count, so
  an old setup cannot be resurrected after being blocked by another position.
- Re-check freshness before retrying a setup that was previously skipped as
  `SYMBOL_BUSY_LONG3_OR_OTHER`.
- Pending orders that cross the wall-clock expiry must be cancelled.
- SMC Telegram order messages should show current market price, limit entry,
  setup creation time, and setup age so a retracement order is not mistaken
  for a current-price signal.


## 10. Mandatory research audit and handoff protocol

All ChatGPT conversations, agents, and research branches working in this repository must follow the same evidence standard. A new chat must inspect this file and `RESEARCH_RULES.md` before trusting or extending prior research.

- Do not treat a GitHub Actions `success` conclusion as proof that a strategy was actually tested. Verify non-zero universe/input rows, non-zero expected outputs, and artifact/log contents.
- Do not quote an old PF, return, Sharpe, MDD, win rate, or trade count as current unless the exact strategy version has passed the audit rules in `RESEARCH_RULES.md`.
- When a material execution/data bug is found, mark every result produced by the affected implementation as `INVALID` or `QUARANTINED`. Do not silently mix pre-fix and post-fix metrics.
- Fix the root cause on the research branch, rerun on the same canonical data, and compare before/after results.
- Every strategy must have one clearly identified latest valid lineage: branch, commit, rules, data set, time range, costs, and audit state.
- Prefer robust parameter neighborhoods and OOS/cost stability over a single best parameter point.
- Do not start a new strategy family while repository-wide cleanup/audit is explicitly in progress unless the user asks to interrupt it.
- Killzone research is excluded from the current cleanup/audit program and must not be folded into current conclusions unless the user explicitly re-enables it.
- Research changes stay off `main` until audited. Live/demo safety rules above remain authoritative.


## 11. Canonical data asset management

Long-lived research data is a repository asset, not a disposable workflow by-product.

- Before collecting historical market data, check the canonical data locations first. Do not re-download a dataset merely because it is absent from the checked-out repository tree.
- Check, in order: documented canonical storage / GitHub Releases, repository-managed data, then relevant non-expired Actions artifacts.
- The canonical Binance USD-M 15m 5Y snapshot is stored as a versioned Release asset when available. Record its release tag, source run, manifests, coverage, symbol count, and checksums in research handoffs.
- Actions artifacts are temporary transport/results storage and must not be treated as permanent canonical storage.
- Reusable large datasets must be preserved in versioned long-lived storage with SHA256 checksums before temporary artifacts expire.
- Never overwrite an established canonical snapshot in place. If the dataset changes, publish a new version such as v2 and preserve the old version for reproducibility.
- Before a backtest uses a canonical dataset, verify expected assets exist and validate checksums/manifests where available.
- Every long-history result must identify the exact dataset version/snapshot used.
- Do not declare that data is missing until canonical storage, repository data, and relevant Actions artifacts have all been checked.

## 12. Mandatory workflow try-run-verify-repair loop

Creating or editing a GitHub Actions workflow is not completion. The agent that changes or triggers it owns the verification loop until the requested workflow is demonstrably working or an external blocker prevents further progress.

Required loop:

1. Before triggering, inspect the workflow/code for known failure modes and run cheap static/preflight checks where practical.
2. Trigger the real workflow.
3. Confirm that a workflow run was actually created. A commit or trigger file alone is not evidence that Actions started.
4. Inspect the run status and jobs. When it completes, inspect the conclusion and relevant job steps/logs.
5. Verify the actual outputs: expected input count, non-zero rows/universe, expected files, artifacts, sanity assertions, and key result contents. A green Actions badge alone is insufficient.
6. If the run fails, produces empty/partial output, times out, or violates the intended experiment, diagnose the concrete cause, fix it on the research branch, and trigger a new run.
7. Repeat trigger -> inspect -> diagnose -> fix -> rerun -> verify until the workflow and its outputs are confirmed correct.
8. Do not report a task as completed while the run is merely queued/in-progress or while output verification is pending. State the exact current status instead.
9. If an external limitation makes completion impossible, preserve all valid work and report the specific blocker and last verified state rather than pretending completion.

### Pre-run known-error checklist

Before every substantial research run, explicitly check the failure classes that have repeatedly occurred in this repository:

- syntax/indentation damage from scripted text replacement or accidental literal escape sequences such as `\\n`;
- trigger edits accidentally commenting out or corrupting executable code;
- wrong branch, stale commit, wrong workflow, or a trigger that never created a run;
- missing/expired Actions artifacts or incorrect run/artifact IDs;
- wrong data path/schema/column names such as `timestamp_ms` vs `open_time`;
- empty universe, zero-row input, partial shard download, missing symbols, or incomplete historical coverage;
- timestamp disorder, duplicates, gaps, incomplete resampled candles, and incorrect higher-timeframe alignment;
- look-ahead/future-confirmation leakage and signal/entry timestamp mistakes;
- omitted entry bar, TP/SL same-bar ambiguity, and incorrect holding-horizon units;
- accidental reuse of pre-fix/QUARANTINED metrics;
- timeout risk from unnecessarily monolithic jobs; shard/chunk large work when appropriate;
- research workflows accidentally sharing live watcher concurrency/state or modifying protected forward-state files.

Where practical, encode these checks as assertions/preflight steps so the workflow fails loudly instead of producing plausible-looking bad results.
