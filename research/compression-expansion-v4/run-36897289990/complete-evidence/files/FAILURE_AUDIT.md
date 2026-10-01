# V4 development failure audit

Original actual run: 36897289990. Trading code: e4c78b629ae0a0ff6cc4e9712385248588c89b32.
All eight DEV shards completed; 85 unit tests and canonical chronology smoke passed.
The preregistered selection processed all 96 policy cells and selected ZERO.
No 2024 GATE/account calculation was run because no development candidate qualified.

- 97,686 resolved **parameterized, overlapping** outcomes, 256 historically eligible
  symbols, 856 original source CSV files; 48 parameterized DATA_GAP exclusions.
- Every policy had negative 40bp net independent price mean: range
  -134.5623 to -13.0059 bp per outcome. Every global mean net40 R was negative.
- All 96 failed positive mean net40 R in 2022. Only six had positive 2023 net40 R;
  this cannot qualify without 2022. This is not a profitability finding.
- The 0.50 compression group (48 policies) had only 55–125 resolved observations
  per policy and failed the frozen N/year/date-coverage thresholds.
- The 0.75 group had 1,416–2,519 observations per policy. Its failure was negative
  returns after costs, not merely low frequency.
- Best diagnostic independent price mean: EXPAND_S+1_W48_C50_ANY__H48__TP3,
  68 observations, -13.0059 bp, PF 0.9003, net40 R -0.05328.
  Its 2022/2023 counts were 27/28, on only 22/24 active entry dates.
  This weak sparse cell is rejected; it is not a selected trade strategy.
- The 48 exclusions are outcome/policy counts covering two unique symbol/entry events: LRCUSDT and XLMUSDT at 1645830000000.
  Original exclusions and minute-input provenance must be preserved/reviewed.
- Original minute loader retained processed NPZ/array hashes, not raw archive ZIP
  checksums. Do not call those processed hashes original ZIP hashes.

Preservation status at audit: V1/V2/V3 history permanently committed to
research-dayedge-evidence-36897289990 (808 files, 155,194,348 bytes, includes four
actual run logs and all original account curves). Manifest sizes match the git tree.
V4 selection and all 96 diagnostics are permanently committed to
research-compression-v4-dev-36897289990. Full current raw-ledger archive failed
because gh api rejected terminal escape sequences in the downloaded job logs.
The recovery workflow allows raw escape sequences and skips jobs with no logs;
this is evidence repair, no new economic hypothesis or market rerun.

Next hypothesis is adaptive exploration prompted by the failure, not independent
confirmation: test rejection back inside a prior range with aggressive taker flow
opposite the completed price move (possible absorption / failed liquidity sweep).
It uses a different signal event, not the mathematical inverse of these V4 trades.
All global trials and seen-period labels remain in the cumulative register.


Audit found a selection implementation omission: the PLAN required positive mean price return separately in each year; the code checked global mean price return and per-year mean R. Every cell already failed 2022 mean R and global price mean, so this cannot change ZERO selection. Preserve the mismatch, do not rerun outcomes or weaken the PLAN. The next study must enforce both per-year price and R and use KST year labels.
