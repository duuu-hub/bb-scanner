# V11 implementation contract

The economic rules and complete856-file catalogue were committed at
`1ffcce44d054adb42a0cff620f8381cfa388b4e2` before implementation, testing or
V11 market outcomes. The implementation does not change any planned shock,
breakout, flow, stop, hold, exit, cost, selection or account parameter.

`scripts/aggressive_flow_cascade_v11.py` implements16 entries and96 policies.
All signals use a fully closed15m bar and enter only at the next contiguous
open. ATR, volume, breakout and prior-flow windows exclude the signal bar;
BTC closes match exact timestamps. Missing/gapped inputs fail closed. Current
taker flow is stored separately from the previous three-bar persistence window.
The fixed16-bar cooldown follows accepted intents and never depends on exits.

Stops use the actual fill,1.5 prior ATR,.75% floor and6% maximum. TP15/TP25
use explicit targets through the shared fixed-target resolver; TRAIL uses the
existing3ATR canonical trail. Official Binance1m chronology and the repository's
conservative entry-minute/collision rules remain authoritative. Excluded paths
are saved and counted rather than assigned returns.

All eight shards verify the original source manifest,256 historical hashes,
the full856-file catalogue and exact BTC bytes. Partial failures checkpoint the
ledger, source coverage, exclusion counts, official minute ZIP/checksum records
and used slices. Selection requires all eight complete DEV shards and preserves
all96 cells. GATE cannot run before the selection is fixed.

Selected policies, if any, use the unchanged canonical account engine and V9
strict annual wrapper with identical starting equity,costs,universe,risk/caps,
KST -2% day handling,DD10% risk reduction,DD15% halt and all853/367 dates.
Existing workflows, live/demo settings, order code and watcher state are not
changed. This workflow triggers only from a new V11 marker on its research
branch and preserves complete or partial evidence on separate result branches.

Twenty new causal/source/selection tests and the complete281-test suite pass
locally. The synthetic eight-shard pipeline passes and contains no market-profit
claim. The local chronology smoke reports missing `numba`; it is recorded as a
dependency failure. Actual Actions installs pinned numba and must log a passing
chronology/canonical smoke before any market scan can start.

The first new test run had one fixture expectation error: it expected TP15/TP25
from a two-point stop although the preregistered and implemented actual stop was
1.5 points. The expected fixture values were corrected; implementation and all
economic parameters were unchanged. This repair is not a hypothesis or result.

2024 and later seen data are not clean holdouts. Any historical survivor remains
provisional pending frozen neighbors,60/80bp costs,stronger slippage,actual
+1/+3 minute delay,time-block dependence,new recent observations and forward
data. The whole-account daily +0.7%-2% objective remains unproved.
