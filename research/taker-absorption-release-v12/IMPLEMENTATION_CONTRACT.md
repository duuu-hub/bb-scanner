# V12 implementation and preexecution contract

Economic PLAN and complete856-file source catalogue were committed before code
at c8741743ecff8573c0994878557db38698e14267. No planned setup,share,volume,
flatness,breakout,confirmation,stop,hold,exit,cost or selection parameter changed.

scripts/taker_absorption_release_v12.py implements16 entries/96 policies. Setup
bars end before the release;96-bar setup-volume baseline ends before the entire
setup. Taker share is quote weighted. The starting ATR also precedes the setup.
Closed release fields and exact BTC returns drive the confirmation. Entries use
only the next contiguous open, actual fill, structural far-edge stop/.5% floor
and6% risk-distance limit. Entry priority contains no future outcome. Intent
cooldown follows accepted entries independently of hold/exit.

Unchanged source verification, canonical official1m resolver, shared account
engine and strict annual/calendar wrapper are carried from V11/V9. TP2/TP3
explicit target tests exercise the actual minute resolver for entry-minute
TP-only loss, established earlier TP and same-minute collision. Three explicit
chronology error types remain excluded. All96 cells and zero cells are saved;
selection requires all eight exact frozen-source shards before the seen2024 gate.

New retention wrapper preserves known malformed source ZIP/checksum bytes while
returning the unchanged DATA_GAP. It verifies immutable observed hashes, saves
received bad bytes before a mismatch failure and checkpoints partial outcomes.
V11 BNXJune2022 recovery uses only the preregistered invalid official input
hashes, never executes V11 signals/exit/account code and never changes V11
results. The recovery artifact is archived with V12 validation evidence. This
preservation fix is not another economic experiment.

Local new26 tests and the full307-test suite PASS; synthetic8-shard/all96-zero
pipeline PASS. Local Python3.12.14,NumPy2.3.5,pandas2.2.3;numba is absent.
The required local chronology smoke exits1 with ModuleNotFoundError:numba.
Preserve that real dependency failure. Actual Actions installs pinned
pandas2.3.3,NumPy2.3.3,numba0.62.1 and MUST pass chronology/canonical smoke,
all307 tests and error-input recovery before any actual DEV scan starts.
Synthetic selection fixtures are not market survivors or profit evidence.

V12 workflow uses its own branch/concurrency/marker. It downloads the existing
frozen source/BTC and source manifests; retains raw ledgers,all96 cells,source
coverage,exclusions,minute originals/slices and actual completed/failed logs on
separate research branches. It changes no existing workflow, main, orders,
live/demo settings, watcher state or unrelated automation.

The local full-suite log,missing-numba smoke log,code/tests/workflow and precise
preexecution hashes are retained. Actual CI/run statuses are recorded in central
CONTINUATION.json only after reading actual Actions output. A local passing suite
does not prove a market run executed. Any historical survivor needs frozen
neighbors,cost60/80,real+1/+3min delay,time dependence,recent/forward checks.
The whole-account daily NET+0.7%-2% objective remains unmet.
