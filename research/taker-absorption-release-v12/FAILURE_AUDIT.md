# V12 original evidence and failure audit

Audited 2026-10-02 UTC. Actual [run36989682215](https://github.com/duuu-hub/bb-scanner/actions/runs/36989682215).
Preregister c8741743ecff8573c0994878557db38698e14267; execution code793820a5dee7577663e94f9e45157468e05850aa.
Validation: actual307 tests23.130s, ALL_CHRONOLOGY_SMOKE_PASS,
ALL_CANONICAL_INVARIANTS_PASS, synthetic8-shard pipeline and exact BNX archive
recovery PASS. Local missing-numba chronology attempt remains recorded; CI supplied
pinned dependencies and really executed it. All8 DEV jobs,selection andpreserve
success;GATE/accounts skipped because no candidate passed.

96 cells,654 overlapping outcome rows,109 entry-key events,67 distinct coin/
entry-time events. Zero selected/zero accounts. N range0--52;24 empty cells.
Every cell fails300-N and annual frequency/date gates.92 fail concentration.
51/72 nonempty cells positive net40 price/R;62 gross-positive. Nonempty mean
net40 price[-118.4427637,1019.1332704]bp, R[-.648680872,6.801127312].
Largest mean is just ONE short event, insufficient evidence. Do not call
this alpha or infer executable account profits. Largest-N
ABSORB_S+1_W4_Q60_PRICE_ONLY__H16__TRAIL N52/41coins:
gross and detailed cost decomposition in SAVED_LEDGER_AUDIT.json;
net40 -28.9449602bp,R-.24795798,PF.70546167. Cost drag across nonempty cells
38.2186295--50.0740062bp. No daily-account target test was reached.

All8 ORIGINAL compressed raw DEV ledgers downloaded from evidence branch and
rehashed against manifest SHA256,scan metadata and actual Git blob SHA. Independent
price/fee/slip/funding/reserve/R error0; all96 N/symbol/PF/annual/date arithmetic
reconciles. Selected DEV/dev-N ledgers are intentionally empty filtered files.
204 saved evidence files,7,707,395bytes; every manifest git tree size matched.
Full archive not all locally downloaded/rehash-audited; limitation explicit.

DEV commit49b869807879f786cc46c04974f419ec707d7606 on
research-taker-absorption-release-v12-dev-36989682215.
Complete evidence commit3d64282d571f32736ac8a9bf6ed3d7b42299b5af on
research-taker-absorption-release-v12-evidence-36989682215.
Root research/taker-absorption-release-v12/run-36989682215/complete-evidence;
manifest Git blob e09506943051dc05c5c48d112b70d64d05fffe0f.
Original outcomes files/taker-absorption-release-v12-dev-N/independent_candidates.csv.gz.
One official BLZ2023-06 ambiguity month,exact slices,checksum/raw ZIP;0 chronology
excluded. Exact invalid BNX2022-06 ZIP and checksum text recovered from V11
metadata without changing exclusions or rerunning V11, under validation/
recovered-v11-input. This repair is not a new hypothesis.

Next V13 preregisters breakout-level memory and closed retest. It does not
relax V12 share/flatness thresholds or count repairs as discoveries.
