# Implementation checks before first V4 market execution

- Frozen plan commit: ae35b31876eec83ff82771022986d83e8c4f2e6c.
- Prepared code passed 85 actual local unit tests on 2026-10-01 at 15:39 UTC,
  including 16 new causal/screening tests and four source/evidence integrity tests.
  Exact observed unittest output: Ran 85 tests in 1.339s; OK.
- YAML/Bash syntax checks passed; DEV/GATE/account inputs each require full
  original catalogue, manifest statistics and historical SHA256 matches.
- Local canonical smoke was BLOCKED by missing numba, not passed. No local
  market-data backtest was executed. Actions validation installs pinned numba
  and must actually pass the smoke before any market computation.
- A preliminary syntax-check assertion mistakenly expected eight rather than
  seven workflow jobs; the check was corrected. It was not a strategy retune.
- No V4 performance numbers have been observed before first executable trigger.
  The prior preparation was interrupted; source is now checkpointed durably.
