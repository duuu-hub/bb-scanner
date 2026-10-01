# Trading research continuation checkpoint

Objective: mainly intraday, maximum seven-day hold; whole-account NET daily
+0.7% to +2%, additional upside allowed. Small positive CAGR/scout EV does not
meet this goal. User authorized sustained research and asks to preserve failures
as assets. Research only: never main/live/demo orders/watcher state changes.

## Current actual execution

V7 actual run36922945306/code4db8b13a5ea64f3aef610609c994b2fe2cd0fc18 completed
ALL20jobs successfully:158tests/canonical smoke,eightDEV shards,selection,eightGATE
shards,24accounts and full preservation.96cells/271514resolved outcomes;two frozen
LONG candidates;ZERO strict survivors. All24saved accounts independently audited.
Full rejection numbers and evidence are in the completed section below.

V8 attempt1 run36928892394/code7f93ea6b0b21a624aaa24dfcd5a502bf9e990bd0
completed FAILURE before any market scan. Actual validation job110592960293 passed
181tests,ALL_CHRONOLOGY_SMOKE_PASS,ALL_CANONICAL_INVARIANTS_PASS and official
BTCUSDT2022-01 premium probe(2,976bars,range-.00392945..+.00323124). All eight
DEV jobs then failed at frozen-source verification because
research/premium-absorption-v8/FROZEN_INPUT_HASHES.json was absent from the
implementation tree. Zero policy cells/market outcomes;selection/gate/accounts
skipped. Preserve-cycle also failed on that same absent file. Durable failure
record is research/premium-absorption-v8/ATTEMPT_1_SOURCE_FAILURE.json.

V8 source-repair attempt2 run36929463082/codec049138866f628bb2de891b81a77b133de13359d is IN
PROGRESS. It adds the exact preregistered256-file baseline(blob5cd01ee5...,text
SHA256 0c3f09f4...) and a fail-fast validation preflight. Economic hypothesis,
16entries/96policies,selection and execution rules are unchanged;this is not a
new discovery. Actual attempt2 validation/DEV logs and all market outcomes remain
pending. No V8 profitability,account-growth or daily-goal claim;do not duplicate.

## Completed failures and durable assets
- V7 actual run36922945306/code4db8b13a5ea64f3aef610609c994b2fe2cd0fc18:
  all20jobsSUCCESS,158tests/canonical smoke,16entries/96DEVcells/271514resolved
  outcomes,twofrozenLONG candidates,24accounts,ZEROstrictsurvivors. Funding
  coverage2022 98.6088%,2023 99.9477% across256symbols. Required DEVunion20
  guarded111trades,-13.2144%,CAGR-5.8950%,MDD15.0270%,PF.5284,DDhalt;
  +.7%5/853days. UnguardedDEV20+64.7880% butMDD32.8711%/PF1.1051;40bp
  -10.3300%,MDD43.6053%. Already-seen2024 least-badALIGN20 guarded-6.7238%,
  PF.8920,DDhalt;diagnostic20-1.9948%,MDD22.2977%,PF.9822;diagnostic40
  -13.1389%. Removing guards therefore does not rescue2024. Full24saved
  fee/funding/PnL/cash/MDD/PF/risk/cap/hold/calendar auditsPASS. Two DEV
  DATA_GAPpolicy outcomes explicitly excluded;gatechronology exclusionszero.
  Fullarchive32837files/912814457bytes, evidence branch
  research-funding-crowding-v7-evidence-36922945306 commit
  47917fd464cc7be06f90b50c70edab956b7aebb9. DEV/account branches retained.
  Failure audit/SAVED_ACCOUNT_AUDIT onV8PLANcommit
  d4ec06ebc2633062535bd5ec418a8470a7b1d8e8,path research/funding-crowding-v7/.
  Daily target unmet;no2024retuning/no profit claim.


- V6 actual run36906530309/code65095198c689b09a3028ec460db8198f819b0bcc:
  16entries/96DEVcells/549129overlapping original outcomes;onecandidate frozen,
  eightactualaccounts;ZEROstrictsurvivors.130tests/canonical smoke and20jobsSUCCESS.
  DEV20guarded1278fills,+10.2064%total,CAGR4.2543%,MDD10.6651%,PF1.0536;
  DEV40guarded-10.8515%,DD15halt. Seen2024gate20-12.9077%,MDD15.0191%,PF.7856;
  gate40-13.6266%,DD15halt. Goal+.7%64/853DEV(7.5029%),15/367GATE(4.0872%);
  +2%15/853 and2/367. Allinactive/posthalt/partialedgeKSTdates count. Targetunmet.
  All8savedPnL/fees/funding/cash/DD/PF/risk/hold/calendar products checked.
  Max114signals/same timestamp;tradeR+.05274 butequal-active-dateR-.21406.
  Only23.92%DEV20rawintents executed. Disablingguards still2024lossandDD>20%.
  Fullarchive2238files/70,349,092bytes;manifestgit sizesmatch;1888downloadedoriginal
  DEV/accountfiles SHAverified;1809DEVminute slices/1354official ZIP checksums.
  Selectedchronologyexclusionszero. Full2028minute slices retainedDEV+GATE.
  DEVbranch research-factor-lag-v6-dev-36906530309;
  accounts research-factor-lag-v6-results-36906530309;
  complete research-factor-lag-v6-evidence-36906530309,
  root research/btc-factor-lag-v6/run-36906530309/.
  Failure/audits preservedonV7plancommit17e019d3980c4a03cd9f2aa00ebc824c937751ee,
  path research/btc-factor-lag-v6/. No marketrerun, no2024retune, no successclaim.
- V5 actual run36902157396/code5b26f467543d6724461e8f10557c1e8839075f02:
  16entries/96cells,99tests plus canonical smoke succeeded;ZEROseeds,gate/accounts
  skipped.99,382overlapping outcomes read/reconciled to all96original cell means.
  Net40 price means-79.2490 to-31.6549bp, global price/R positivezero;every2022
  price/Rnegative.31gross-positive means defeated by45.28-49.77bp cost drag.
  26DATA_GAPpolicy exclusions cover3unique coin/entry events.10ENTRY_PATH_GAP
  and80STOP_ABOVE_8PCT are intent/config exclusions. Archive523files/5,972,165bytes;
  all484downloaded originalDEVfile SHA/bytes match,436validated exactminute slices,
  397officialZIP/CHECKSUM matches. Original ledgers audited, no market rerun.
  DEV branch research-liquidity-v5-dev-36902157396,
  path research/liquidity-sweep-v5/run-36902157396/development.
  Full branch research-liquidity-v5-evidence-36902157396,
  path research/liquidity-sweep-v5/run-36902157396/complete-evidence.
  Failure/audit/decomposition on V6 plan commit23589bd985b56934b4698710896f610be168740c,
  path research/liquidity-sweep-v5/. All failures remain research assets.
- V1 Relative Pullback: actual run36853787624, rejected. Results commit
  0717d432abd0e7c040fa24799218d2a4ab76bce0.
- V2 scout run36858492497:36 entries/144 fixed-time cells. Then canonical
  run36861359640 completed8shards but account curve auditing failed because a
  legitimate pre-entry peak was not stored. Preserve failure and repair.
  Corrected account run36863290334:27 policies/216 scenarios, ZERO strict
  growth survivors. Best DEV20 CAGR5.81%; same policy2024 return2.56%.
  Fifty-six parameterized chronology exclusions, not56 unique losing trades.
  Branch research-day-edge-v2-results-36863290334,
  path research/day-edge-lab-v2/run-36863290334.
- V3 actual run36866930929/code84fe6a116dd21fec405120cfe2c4ad38cf04a65a:
  16 entries/96 DEV policies/eight account scenarios, ZERO survivors.
  All account arithmetic audits passed; selected chronology exclusions zero.
  DEV20 guarded +0.3490% total across851KST dates, CAGR0.1495%, MDD7.0656%,
  213executions,85.90% no-entry days; DEV40 -3.2785%.
  Seen2024 gate20 -2.1032%,PF0.9050,MDD5.8065%,138executions;
  gate40 -4.3870%,PF0.8036. Only10/365dates achieved+0.7%.
  Branch research-shock-confirm-v3-results-36866930929,
  path research/shock-confirmation-v3/run-36866930929/accounts.
  Full96DEV diagnostics on research-shock-confirm-v3-dev-36866930929.
- V4 actual run36897289990/codee4c78b629ae0a0ff6cc4e9712385248588c89b32:
  16entries/96cells, all8DEVshards and85tests/canonical smoke succeeded.
  ZERO selected seeds;2024 gate/accounts skipped. All net40 global price/R
  means negative and all2022R means negative. Net40 price means ranged
  -134.5623 to-13.0059bp;97,686overlapping parameterized outcomes.
  Forty-eight DATA_GAP policy observations cover two unique coin/entry events.
  C50 policies sparse(55-125N);C75 frequent(1,416-2,519N)but negative.
  Thirty-nine gross-positive means became zero net40-positive means;
  fee/funding/stop-slip deductions averaged43.38-48.82bp per policy.
  PLAN's annual price check was omitted in code; adding it cannot change
  zero selection because every cell already fails2022R/global price.
  Preserve deviation. V5 explicitly tests per-year price AND R/KST labels.
  Selection branch research-compression-v4-dev-36897289990,
  path research/compression-expansion-v4/run-36897289990/development.

Prior complete V1/V2/V3 curves/rawV3DEV/logs permanently preserved by actual
successful job on run36897289990:808files/155,194,348bytes, four actual run logs,
branch research-dayedge-evidence-36897289990,
path research/dayedge-evidence/prior-run-36897289990. Manifest sizes match git.

V4 original full archive failed when gh api blocked ANSI bytes in job logs.
Archive-only recovery actual run36898980631 succeeded:88files/5,069,566bytes,
12completed-job logs. All96rawcell N/net metrics reconcile exactly. Branch
research-compression-v4-evidence-36897289990,
path research/compression-expansion-v4/run-36897289990/complete-evidence.
Derived timeout field initially used TIMEOUT; frozen engine uses TIME/SPLIT_END.
Corrected derivation/history commit7297eed57a6c652ce601a3b0520c51606b75c04c;
no raw data/outcomes changed or rerun. Do not count repairs as discoveries.

## Frozen evaluation contract

Follow AGENTS.md,RESEARCH_RULES.md and each PLAN. Official Binance1m authority:
pre-entry touches ignored; entry-minute ANYexit inclTP-only =>SL/LOSS;
established same-minute collision=>SL/LOSS; proven earlier1m exit honoured.
Missing/malformed/mismatched source is excluded/count/reviewed, never invented.

Identical equity1,entrySLbudget0.5%,aggregate2%,coinnotional30%,gross200%,
sixpositions,samecoin duplicate/opposite forbidden;KST observed-2% flatten/block,
DD10%future risk half/DD15%flatpermanent halt restsplit. Caps are budgets and
15m observations, not guaranteed actual fill prices or loss ceilings. No profitcap.
Ordinary20/40bp roundtrip +10bp adverseSL/forced-slip +2bp/holding-day funding
stress, not historical funding. Full KST calendar dates include inactivity/halts.

DEV2021-09~2023;2024 already-seen research gate.2025~2026-08 already-seen
comparison, NEVER pristine holdout or post-result retuning. September may seen
elsewhere, and one month is insufficient independent proof. Freeze before recent
data; require neighbours/stronger costs/actual1m+3m entrydelay/timeblocks/forward.
Strict historical pass remains provisional and cannot equal user daily goal.

Source run36095439671, eight original manifests/shards,256prior CSV SHA hashes
verified before every stage; same BTC artifact day-edge-v2-btc/run36858492497.
Raw processed CSV hashes are explicitly not absent original Binance ZIP hashes.
V4 legacy minute ZIP hashes were not retained; V5 adds verified original checksum
and slices. If environment disconnects, continue through Actions and git records.
Source artifacts expire; exact fixed-catalogue reconstruction must fail closed on
hash mismatches, never replace universe/prices. Do not rely on old scratch.

## Next

Implement/test frozen V7 funding source and causal rules, execute actual workflow, inspect logs/results.
If failed, diagnose numerically and preregister/implement/validate/execute the next
economic mechanism. If provisional survivor, freeze before neighbours/stress and
fresh forward validation; report target attainment on ALL calendar dates.
Preserve cumulative trials, adverse results, code/input hashes and every repair.
