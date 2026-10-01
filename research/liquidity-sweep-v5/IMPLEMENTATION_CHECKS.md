# V5 implementation checks before first market run

Frozen PLAN commit: 6492c32200739e2902e3c824cb94722f52eb539d.
Implementation was prepared after that commit and before any V5 market outcomes.

- 30 new V5 tests passed: mirrored failed-sweep/opposite-flow event and threshold
  boundary, range rejection, prior-only baselines, future perturbation, gap resets,
  actual-fill structural stops/minimum/maximum risk, fixed intent cooldown,
  all 96 cells including zero cells, annual price AND R, KST year/date grouping,
  incomplete inputs, official header parsing/checksum/legacy-cache/corruption,
  definitive versus transient errors and preserved exact authoritative slices.
- 69 shared evidence/confirmation/canonical/scout/account regression tests passed.
  Actual local runner reported 99 tests, zero failures; no market backtest implied.
- Direct integration tests call the unchanged canonical resolver through the new
  verified official loader. Entry-minute TP-only remains SL/LOSS; checksum
  disagreement becomes explicit DATA_GAP, never a guessed trade return.
- New workflow trigger/branch/concurrency and six-job dependencies were parsed.
  Every Bash body and embedded Python block passed syntax checks.
  Eight source shards plus known CSV hashes are verified on every data stage.
- No original source or legacy canonical resolver was modified. Historical source
  baseline is byte-identical to V4, with processed-versus-original hash semantics.
  New official 1m loader verifies original ZIP SHA against its official CHECKSUM,
  retains metadata and exact used 15-minute slices, and rejects verified-cache
  corruption without replacing frozen observations.
- CI separately installs pinned Python 3.12/pandas2.3.3/numpy2.3.3/numba0.62.1,
  runs these 99 tests and canonical chronology smoke BEFORE development.
  The local environment lacked numba; actual CI smoke must be read before trust.
- Period-end canonical SPLIT_END uses the last closed mark if the following
  period's OPEN is intentionally absent; normal TIME expiry uses exact OPEN.
  This unchanged shared convention is not a guarantee of an actual exit fill.
- V4 actual archive recovery 36898980631 succeeded; all original 96 cells and
  97,686 raw parameterized outcomes matched N/net-cost metrics. Its 88 evidence
  files/5,069,566 bytes and 12 completed-job logs are committed permanently.
  A derived timeout diagnostic initially checked TIMEOUT instead of canonical
  TIME/SPLIT_END; correction/history are on evidence commit
  7297eed57a6c652ce601a3b0520c51606b75c04c. No raw outcomes were changed or rerun.
  V4 had 39 positive gross independent means but zero positive net40 means;
  mean total fee/funding/stop-slip deductions were 43.38-48.82 bp. No seed passed.
- No main, live/demo orders, watcher state or unrelated automation changed.
  Market outcomes, selected candidates and account profits are still unknown.

