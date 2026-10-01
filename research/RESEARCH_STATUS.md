# Trading research continuation checkpoint

User objective: meaningful intraday strategy, at most seven-day holds, target
+0.7% to +2% NET ACCOUNT days, larger upside welcome. User explicitly authorized
continued iteration instead of stopping after a failed hypothesis, using Work
budget. Research only; never main/live/watcher state changes or real orders.

Risk/cost contract and research rules: see AGENTS.md, RESEARCH_RULES.md,
research/day-edge-lab-v2/PLAN.md and EXECUTION_PLAN.md.
Use matching starting equity/risk/cost/universe for account comparisons.
Report full KST calendar days, not only profitable/active days.

Completed:
- Relative Pullback V1: actual run 36853787624, rejected, results commit
  0717d432abd0e7c040fa24799218d2a4ab76bce0.
- V2 development scout: run 36858492497, 36 entry hypotheses/144 cells,
  selected two immediate crash-rebound entries. Fixed-time event observations
  are NOT account profit.
- V2 canonical scan: run 36861359640, all eight shards succeeded; account
  auditing then stopped because curve missed legitimate pre-entry high-water
  equity at a shared timestamp. Preserve this failure. Not a trading retune.
- V2 corrected account replay: run 36863290334, SUCCESS calculation,
  27 account policies/216 scenarios, ZERO strict material-growth survivors.
  Best DEV 20bp CAGR 5.81%, same policy 2024 return 2.56%.
  56 parameterized chronology exclusions, not 56 unique losing trades.
  Complete original scout/source manifests, selected signals and executable
  account ledgers are on branch research-day-edge-v2-results-36863290334,
  path research/day-edge-lab-v2/run-36863290334.
  Equity curve now includes pre-entry and post-entry values for correct
  independent MDD reconciliation. New regression passed.

Current:
- V3 completed actual run 36866930929 at 2026-10-01 13:18 UTC; calculations succeeded,
  ZERO strict account survivors. All eight account arithmetic audits passed;
  selected-account chronology exclusions: zero.
- DEV 20bp guarded +0.3490% total over 851 KST days, CAGR 0.1495%, MDD 7.0656%,
  213 executed trades; 85.90% days had no entry. DEV 40bp -3.2785%.
- 2024 historical GATE 20bp -2.1032%, PF 0.9050, MDD 5.8065%, 138 trades;
  40bp -4.3870%, PF 0.8036. Only 10/365 full calendar days reached +0.7%.
- Full immutable results: research-shock-confirm-v3-results-36866930929,
  research/shock-confirmation-v3/run-36866930929/accounts.
- Preserve code 84fe6a116dd21fec405120cfe2c4ad38cf04a65a, all 96 DEV cells on
  research-shock-confirm-v3-dev-36866930929, source hashes and rejected outcomes.
- Next economic mechanism under design: compression-to-expansion trend continuation,
  rather than immediate/confirmed shock fading. Not yet executed or selected.
- Current chat is actively implementing; obey the central interactive lease.

Data boundaries: DEV 2021-09 through 2023; 2024 historically seen selection/
gate, 2025-2026 previously seen comparison ONLY, not clean holdout. Reserved
September recent check is opened only AFTER a final freeze; it might have
been seen in other chats and one month cannot independently prove an edge.
Never retune to old comparison years. Track global search count, failures
and economic hypotheses; repeated good backtests are not proof.

Next: inspect V3 and its official-minute exclusions. If no material account
survivor, diagnose and preregister/implement/execute the next economic
hypothesis instead of ending at failure. If one survives, review neighbours,
freeze a finalist and stress costs/slippage, actual delayed execution,
already-seen comparative years, block dependence and fresh forward evidence.
Always report gap between measured account daily results and requested goal;
a small positive CAGR does not meet the user objective.

Local workspace disconnected mid-turn. GitHub connector and Actions remained
available, so code/testing/calculation continued there. Rehydrate git from
these branches when scratch becomes available; do not depend on old scratch.
Source run 36095439671 artifacts expire; cache/reconstruct official Binance
data with the fixed catalogue/provenance if needed, never substitute a
different universe or guessed prices. Do not claim any code/backtest was
executed until actual Actions logs/results prove it.
