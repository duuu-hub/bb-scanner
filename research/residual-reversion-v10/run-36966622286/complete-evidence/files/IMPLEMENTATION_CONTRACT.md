# V10 implementation contract

The economic rules were committed before implementation in `PLAN.md`, commit
`a42078eb11baa5442e4009a3e96a9e64f4665fce`. No V10 market outcomes were inspected
while implementing or fixing synthetic test fixtures. This is an unhedged
single-coin residual reversion hypothesis; regression and AR1 diagnostics do not
prove stationarity, market neutrality, or account profitability.

`scripts/residual_reversion_v10.py` implements the frozen 16 entries, two holds
(24/96 fifteen-minute bars), and three exits (MEAN/TP2/TRAIL): 96 DEV cells. Every
fit uses exactly W matched contiguous closes ending at i-1. Current-bar prices
are evaluated with that past-only fit, including the previous residual used for
the inward change. Independent NumPy OLS/AR1 calculations, future perturbations,
gaps, and missing BTC observations are tested. MEAN fixes the target using the
signal-time BTC close; it never moves the target with future BTC prices.

The full 856-file universe, 256 prior historical hashes and BTC hash are frozen
in `FROZEN_CONTEXT.json` and `FROZEN_INPUT_HASHES.json`. Each shard verifies its
actual source bytes, records the full catalogue, persists partial checkpoints
on failure, and saves official Binance minute ZIP/checksum provenance and used
minute slices. The existing canonical minute resolver supplies chronology and
conservative entry-minute/conflict treatment. Incomplete or mismatched paths
are excluded and counted, never imputed as favorable fills.

Selection requires all eight complete original DEV shards and saves all 96
cells, including zero-outcome cells. It reuses V9's preregistered minimum sample,
year, active-date and concentration rejection rules, selecting at most one
policy per entry key, three per side and six total. GATE cannot start before
the DEV selection is frozen. No survivor skips GATE and accounts.

Selected policies use the unchanged canonical account engine and V9 strict
account checks: same source marking universe and starting equity, 20/40 bp
costs, guarded/unguarded comparisons, 0.5% entry risk reserve, 2% aggregate
reserve, 30% per-symbol nominal cap, 200% gross cap, six positions and no same
symbol overlap. KST observed -2% flatten/block and DD10% risk half/DD15% halt
remain unchanged; these are monitoring rules, not guaranteed execution prices.
All KST calendar days, including inactive/post-halt/partial edge days, are
included (853 DEV, 367 GATE).

The new workflow changes no existing workflow, live/demo setting, order code or
watcher. A push changing `.research/residual-reversion-v10-run` on the separate
V10 branch launches one cycle. Actual Actions validation first verifies the
eight immutable saved V9 ledger blobs and reconstructs their 96 saved cell
statistics without a market rerun. It then runs 261 tests, the existing numba
chronology smoke, and the synthetic eight-shard scan/selection smoke. Market
scans depend on all validation steps passing. Artifacts and complete/partial
original evidence are preserved on separate research branches.

Local tests passed; the local chronology smoke could not run because numba was
absent. This is recorded as a dependency failure, never a smoke pass. CI installs
pinned dependencies and must produce an actual passing log before DEV runs.
Local test fixtures initially had two assertion failures and a later source
column error; their corrections did not change any economic parameter and do
not count as new hypotheses or market discoveries.

2024 and later previously seen data are not a clean holdout. A survivor would
remain provisional: freeze it before neighboring settings, 60/80 bp costs,
execution slippage, actual one/three-minute entry delay, dependent time blocks,
new recent observations and forward data. Daily +0.7% to +2% has not been
established. A favorable overlapping DEV ledger cannot establish that goal.
