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

## 9. PSAR research canonical timing definition

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
- TP/SL design levels remain anchored to the open-time PSAR/ATR/target definition; a favorable taker fill must not silently move the strategy's designed levels.
- Every large PSAR backtest must first pass a small timestamp audit proving: input-data cutoff < order-live timestamp, and sampled trades reproduce the intended open-time PSAR/ATR without lookahead.
- Results from implementations that violate this timing definition must be labeled invalid and must not be used for strategy selection or sizing.

Ultimate PSAR research objective: implement the SAME canonical logic on 1H and 4H, then compare them on identical data/execution assumptions before choosing the production timeframe.

## 10. Mandatory intrabar / same-bar execution chronology

This section is authoritative for ALL backtests and research engines in this repository unless the user explicitly changes it. It is not PSAR-specific. A strategy/workflow may not replace, weaken, or silently bypass these rules.

- Never infer TP/SL chronology from OHLC alone when both levels are reachable inside the same parent candle.
- When a parent research candle (for example 15m, 1H, or 4H) contains both TP and SL after entry and their order is ambiguous, resolve chronology using official Binance 1m candles for the relevant interval.
- Entry chronology matters. TP/SL touches that occur before the entry is actually filled are NOT exits.
- For a resting limit order, the first 1m candle that reaches/passes the entry price is the entry minute. A gap-through/reach-through counts as a fill according to the canonical maker-fill logic; do not require the 1m candle to straddle the exact entry price if the canonical engine treats the order as marketable/reached.
- If TP or SL is touched in the SAME 1m candle in which entry occurs, exact post-entry chronology is unknowable from 1m OHLC. Resolve conservatively as SL / LOSS. This includes an entry-minute TP-only touch when the same 1m OHLC cannot prove that TP happened after entry.
- If an already-established position has both TP and SL touched in the SAME 1m candle, exact chronology is unknowable. Resolve conservatively as SL / LOSS.
- If the 1m sequence proves TP occurred first after entry, record TP / WIN. If it proves SL occurred first after entry, record SL / LOSS.
- If the parent candle claimed an entry/fill but authoritative 1m data cannot reproduce the entry, treat it as a data/execution-integrity mismatch; do not guess a win or loss.
- Missing, malformed, misaligned, or otherwise unusable official 1m archive data must be treated as DATA_GAP / excluded according to the canonical engine contract, never guessed as TP or SL.
- All 1H and 4H PSAR research, and any future strategy using OHLC backtests, must call/reproduce this same chronology contract. Do not create a strategy-local shortcut that uses 'TP first', 'SL first', arbitrary OHLC ordering, or same-parent-bar blanket SL without first applying the 1m authority rule.
- Before trusting a large run, execute chronology smoke tests covering at minimum: pre-entry TP ignored, entry-minute TP=>LOSS, entry-minute SL=>LOSS, entry-minute both=>LOSS, established-position both=>LOSS, entry then later TP=>WIN, entry then later SL=>LOSS, and DATA_GAP handling.
- A run that does not use this contract, or cannot prove it passed the chronology smoke tests, is INVALID and its PF, return, win rate, MDD, trade count, and sizing conclusions must not be compared with canonical results.
