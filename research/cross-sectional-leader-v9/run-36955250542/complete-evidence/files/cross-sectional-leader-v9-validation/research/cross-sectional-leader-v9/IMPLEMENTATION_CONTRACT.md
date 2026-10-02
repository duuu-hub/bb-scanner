# V9 implementation details fixed before market execution

This document resolves integer and boundary details of the already committed
PLAN.md. It introduces no observed-market tuning. Parent preregistration commit:
53fea7dd385bdd1ec7048f58be23087f5168158a. Every V9 market execution and repair
must remain distinguishable in CONTINUATION.json.

- Daily rank rows use the exact close at UTC midnight. The bounding closes are
  the 15m bars opening at midnight minus 15m and that time minus 24h. All 97
  bounding closes, and matching BTC returns, require a contiguous path.
- At least 30 distinct source UTC dates must have been observed, in addition to
  the shared engine's 30 elapsed source days. Missing dates are not invented.
- Global sort is ascending (relative log-return, symbol). For universe size n,
  tail count is ceil(n * tail_pct / 100). Rank fraction = (ascending rank - 1)
  / (n - 1); priority tail extremity is that fraction LONG and 1-fraction SHORT.
- A signal bar must open at or after its exact prior-midnight rank timestamp.
  The 23:45 bar that closes at the new midnight cannot simultaneously act as
  the new day's resumption bar. The first next-day decision is 00:15 UTC.
- Two-day persistence looks up exactly rank_time minus 24h, with that date's
  own universe size and tail count. No nearest timestamp or forward fill.
- Map stages include one preceding day for persistence context. DEV ends
  before 2024-01-01 UTC; GATE starts only after DEV selection is frozen.
- Reducers require all eight unique shards, matching baseline and BTC SHA256,
  every previously frozen historical symbol, unique map rows and source hashes.
  All source files, including ineligible/new listings, remain in the catalogue.
- Scans verify each original processed CSV against both the source check and
  frozen rank manifest. Selection verifies every full DEV ledger hash and the
  identical global rank manifest. The source run's processed-CSV hashes are
  explicitly distinct from absent original 15m Binance ZIP checksums.
- Entry onset, 32-bar cooldown and next-open gap checks are per symbol/config.
  Cooldown advances on a valid intent before exit resolution, and therefore
  does not depend on future TP/SL outcomes. Opposite-side signs are mirrored.
- Shared canonical exit chronology and account arithmetic are imported intact.
  The V9 account decision separately requires DEV 2021/2022/2023 and GATE 2024
  positive guarded20 returns. A DEV 2024 partial boundary cannot replace GATE
  2024. Preserve the shared engine's older decision for audit; the V9 decision
  is authoritative for V9 and is still only provisional historical research.
- All 96 raw cells, explicit rejection reasons and zero-count cells are saved.
  Unit/synthetic fixtures may satisfy statistical gates for testing; none of
  their outputs represent market profitability or real executable trades.

Research push marker starts one workflow. Map/rank artifacts are persisted on
their own research result branches before any exit scan. The final archive
includes completed/failed job logs, full parameterized ledgers, source checks,
official 1m originals/checksums/slices, exclusions and any account curves.
