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
- Branch research-shock-confirmation-v3, based on 72b6bfae05d53d147bfe3f618fe0ec4203f2073c.
- Frozen V3 plan research/shock-confirmation-v3/PLAN.md.
- 16 confirmed-shock/flow entry hypotheses x 2 holds x 3 exits = 96
  canonical DEVELOPMENT policy diagnostics, then <=6 selected policies
  + predeclared union on actual constrained DEV/2024 accounts.
- Workflow Shock Confirmation V3 Research Cycle triggers only the explicit
  .research/shock-confirmation-v3-run file on this branch.
- Look up the branch's workflow run; don't duplicate an active run.
  Validation must succeed before outcomes can be trusted.
- Every completed selection/account stage preserves data on a dedicated
  result branch whose name includes the run ID.

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
