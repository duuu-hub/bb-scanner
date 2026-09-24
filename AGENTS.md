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
