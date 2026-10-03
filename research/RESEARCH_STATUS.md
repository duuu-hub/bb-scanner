# Trading research continuation checkpoint

Objective: mainly intraday, maximum seven-day hold; whole-account NET daily
+0.7% to +2%, additional upside allowed. Small positive CAGR/scout EV does not
meet this goal. User authorized sustained research and asks to preserve failures
as assets. Research only: never main/live/demo orders/watcher state changes.

## Current actual execution

V12 actual run36989682215/code793820a5dee7577663e94f9e45157468e05850aa:
actual307 tests23.130s, canonical chronology/invariants, synthetic8 shards PASS;
all8DEV scans,selection,preserve SUCCESS.96 cells/654 overlapping outcomes,
109 entry-key events/67 unique coin-entry events;ZERO selected/accounts.
Every cell fails frequency;N0--52,24 empty,92 concentrated.51/72 nonempty
net40-positive cells cannot establish alpha. Best mean1019.13bp is ONE trade.
Largest-N52 long has net40 -28.945bp/R-.24796/PF.70546.
Original8 compressed ledgers SHA256/Gitblob/arithmetic and all96 cells PASS.
204 files/7707395bytes;complete evidence3d64282d571f32736ac8a9bf6ed3d7b42299b5af.
V11 malformed BNX2022-06 ZIP/checksum exact bytes recovered and preserved in
V12 validation;invalid parser exclusion/V11 outcomes unchanged,no market rerun.

V13 breakout level retest preregistered BEFORE implementation at
1859c6096948fc851bca09d267ce3642028da838,
branch research-breakout-level-retest-v13;code869fbc6ca98c23880d9d64ffb71a322509b73d3d.
Prior1d/3d breakout level frozen then closed retest/recovery;16entries/96cells.
Local338 PASS19.213s;one local terminal-newline immutable copy error repaired and saved.
Actual run36992677948/validation job110792275453:338 tests PASS26.970s,
pinned numpy2.3.3/pandas2.3.3/numba0.62.1;canonical chronology/invariants and
synthetic8 shards PASS. Actual validation log/attestation saved on central research
branch under research/breakout-level-retest-v13/run-36992677948/observed-validation.
All8 DEV canonical scan steps active in Actions metadata;no completed market
ledger/outcome read yet. Expected complete evidence branch
research-breakout-level-retest-v13-evidence-36992677948 and root
research/breakout-level-retest-v13/run-36992677948/complete-evidence.
Monitor existing run,no duplicate. Own lease released after verified handoff.
Whole-account daily+0.7%-2% remains unmet.

## Completed failures and durable assets
- V10 actual36966622286:16entries/96cells/340491outcomes,all96 global net40 price/R negative,zero selected/accounts. Best -9.5794bp/-0.04375R/PF.946/N1278;2022 and2023 all96 R negative,2023 all96 equal-dateR negative. DEV branch research-residual-reversion-v10-dev-36966622286 commit 8d0f1aa84b5aacf432ea688e3f26b165f11f4b87. Full evidence preservation still running; raw original ledger audit pending.
- V9 actual36955250542:16entries/96cells/223218outcomes,zero selected/account.
  Net40 price means-63.6210 to+5.3485bp,R-.27166 to+.04636,PF.57707-1.02713.
  Two small-positive cells N153/234,2022negative;all96 2022R negative.
  Failure audit onV10 PLAN commita42078eb11baa5442e4009a3e96a9e64f4665fce;DEV8ee2ea6255e914329032496bd14be07a70cece0e.
  Full archive1764files/777727705bytes atd64413ffdd50f2dbfaa16bf89e8aa1e6d63cb10a.
  Original8ledger hash/arithmetic audit PASS inactualV10 validate;57gross-positive,
  only2net40-positive;mean cost drag45.0845-50.4517bp. No market rerun.

- V8 actual run36929463082/codec049138866f628bb2de891b81a77b133de13359d:
  workflow operationally SUCCESS;256 frozen hashes,181tests/canonical checks,
  eightDEV shards and selection completed.16entries/96cells/531854resolved
  outcomes;ZERO positive overall net40 price cells,ZERO positive overall R cells
  and ZERO selected policies. Price means -62.2867 to -19.0345bp,R means
  -.22681 to-.06066,PF.58295-.88342. Every2021 cell negative price/R;only one
  2023 price cell positive. Premium coverage2021 100%,2022 99.8844%,2023
  99.7796%;4626/4687 premium months validated,61 explicit gaps;868 official
  minute months checksum-proven.160DATA_GAP+6ENTRY_MISMATCH excluded;399wide-stop
  and2catch-up-gap intents rejected. Gate/accounts skipped,so no daily-goal
  evidence and target unmet. Full archive21608files/2006309686bytes on
  research-premium-absorption-v8-evidence-36929463082 commit
  a35f285fb57926439fac98437c5d4a75f3cc52e4;DEV commit
  b05d753bff7a8fb0f752b221e1052b7fdaf768fe. Failure audit and V9 preregistration
  on commit53fea7dd385bdd1ec7048f58be23087f5168158a. Attempt1 packaging failure
  remains separately recorded and is not counted as a discovery.

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

Follow actual V11 run36980518301; read all eight DEV logs/artifacts and do not
dispatch a duplicate. If all96 reject, quantify failure and preregister a genuinely
different V12 before code. If a policy survives, freeze it before seen2024/account
replay. No daily-target claim without strict account/calendar-day evidence.

## V9 execution handoff — 2026-10-02 02:21 UTC

Follow actual run36955250542 and avoid duplicate execution. Record actual
validation/rank/scan completion only after reading job logs. Future stage/result
locations in CONTINUATION.json are expected locations, not completed evidence.

Queue observation 2026-10-02 02:25:11 UTC: V9 validation is queued. Repository
read-only inventory showed10 in-progress runs and10 queued runs at02:24 UTC;
this does not establish a quota/capacity cause. Unrelated work is unchanged.

V9 progress 2026-10-02T03:13:39Z: actual validation SUCCESS; eight real DEV rank-map jobs
queued. No duplicate execution launched and no unrelated workflow changed.

V9 rank audit 2026-10-02T03:45:29Z: all8 maps+reducer+rank preservation SUCCESS;
82,191 saved ranks/821UTC dates. Eight exit-scan jobs queued; no profit claim.

V9 partial exit audit 2026-10-02T04:14:43Z:7/8DEV shards SUCCESS with actual logs/artifacts;
191,500 parameterized outcomes,38 exclusions; shard0 active, combined cells pending.

V9 final DEV/V10 preregistration 2026-10-02T04:27:24Z:96cells/223218outcomes/zero survivors;
V10 PLANa42078eb11baa5442e4009a3e96a9e64f4665fce,implementation/actual launch pending.

V10 actual validation handoff 2026-10-02T04:57:16Z: run36966622286/code18270ece2a1923591e14ae706ce9b91e79ddd76a;
261 tests, chronology/invariants and synthetic pipeline PASS. Original V9
byte/ledger arithmetic PASS; full validation log durably retained.3 actual DEV
jobs active/5queued; market cells not yet available. No duplicate execution.

V10 partial actual scan 2026-10-02T05:03:36Z:shard7 SUCCESS,34285 parameterized outcomes,
23 chronology exclusions; actual full job log preserved. Remaining7 active;
all96-cell/growth/target evidence pending. Exclusion reasons require saved metadata.

V10 final DEV/V11 handoff 2026-10-02T05:11:06Z:all8 scans and selection SUCCESS;
340491 parameterized outcomes,159 DATA_GAP exclusions,all96 global net40 price/R
negative,zero selected;gate/accounts skipped. Saved outputs audited. V11 distinct
aggressive-flow cascade hypothesis preregistration started; no market run yet.

V11 preregistration checkpoint 2026-10-02T05:16:00Z:PLAN/full856 catalogue
committed before code at 1ffcce44d054adb42a0cff620f8381cfa388b4e2;implementation/tests/launch pending.

V10 durable archive 2026-10-02T07:50Z: preserve job110714511115 SUCCESS;
2805 files/1286245712 bytes including814 official1m original ZIPs and32 candidate
ledgers. Branch research-residual-reversion-v10-evidence-36966622286,
commit830be9d0efa1addb294173fbce59f969b853555d. Manifest read directly.

V11 actual launch 2026-10-02T07:49Z: run36980518301/code7f7c3b517978e3401e80d1d2f9fafea01cc048d1;
actual validation job110753756045 SUCCESS: frozen256/full856 source contract,
281 tests, chronology/canonical invariants and synthetic8/all96 PASS. Eight real
DEV shards queued. Market/account outcomes unavailable; no profit/target claim.


## 2026-10-02 V13 actual rejection; V14 preregistration
V13 run36992677948 completed actual338 tests/all8DEV/selection/preserve:145968 overlapping rows,96/96 cost40 price/R negative,54 N>=300,zero candidates/accounts. All24 DATA_GAP exclusions/two unique IOTA/YFI events saved. Evidence commit6f3766792984f15d2a1422f6672d7a06be8f3b7e(2693files/1209702764bytes); full tree sizes checked. Original binary >=1MiB connector blocked independent arithmetic, runner audit required before V14 market; no pending pass claim.
V14 preregistration77b9dc2b54cd58b9c5d98c5a9a59e5c4522924b2 on research-market-breadth-recovery-v14: synchronized shock fraction withdrawal+closed local recovery; exact16/96 global8-map/reduce. Implementation/test/actual execution pending. No daily-goal survivor; no main/order/watcher/unrelated automation changes.


## 2026-10-02T10:46:31Z V14 actual run launched
Exactly one research run[36997190420](https://github.com/duuu-hub/bb-scanner/actions/runs/36997190420),codea69d51067a21a8cc8dcb144fe671c8576cf381d3,prereg77b9dc2b54cd58b9c5d98c5a9a59e5c4522924b2. Actual validation job110806509575 executing independent saved V13 original ledger audit; pinned376 tests/chronology/invariants/global32source8map8scan smoke required before any V14 market. Local explicit376/22.525s and isolated synthetic graph passed; initial incomplete checkpoint failure and validator stdout limitation preserved. No V14 profitability/goal claim. Continue this run without another marker. Expected result paths in CONTINUATION; not yet a saved result.


## V14 run1 execution failure before economic outcomes
Run36997190420 validation passed actual376/23.242s plus independent V13 raw145968 cost/R/hash audit. Full856 global8maps/reducer passed and saved. Eight canonical scan jobs failed; first110807272688 proves SameFileError copying dev-0/source_check.json to itself. No V14 market policy outcomes or economic rejection. Evidence preserved277files/21965024bytes at faeb20b5eb8cc052c973616f31cb8632e064d764. Repair input/output identity handling and real same-directory integration; parameters unchanged,new economic discovery count0. Continue repair on same research branch.


## 2026-10-02T10:57:59Z V14 path repair validated and actual retry active
Same frozen V14 hypothesis/prereg77b9dc2b54cd58b9c5d98c5a9a59e5c4522924b2; repaircode3b309e6b025fe0eb66c8be3ec0dbaad84b7e8344/run[36998066185](https://github.com/duuu-hub/bb-scanner/actions/runs/36998066185). Actual validation110809234109 proves377/37.213s,pinned NumPy2.3.3/pandas2.3.3/numba.62.1, officialchronology/accountinvariants and real32source8map8scan same-source-check-output integration PASS. Economicparametersunchanged,newdiscovery0; priorrun1 SameFileError beforeoutcomes saved277files/faeb20b5eb8cc052c973616f31cb8632e064d764. Source map/research graph active; no V14 net profit result read. Existingrunonly,do not duplicate.
Full V13 original8 SHA/cost/R/annual all96 audit saved centrally research/breakout-level-retest-v13/SAVED_LEDGER_AUDIT.json and validation logs. V13 zero survivors remains; account dailygoal0.7..2% still unmet. Own lease released with actual retry log/attestation and continuation. Next economic rejection advances distinct preregistered V15, not error repair counting.


## 2026-10-02T12:52:30Z V14 actual rejection; V15 preregistration
V14 repair run[36998066185](https://github.com/duuu-hub/bb-scanner/actions/runs/36998066185) completed: pinned377 tests, all8DEV canonical scans and selection SUCCESS; gate/accounts skipped because zero of96 policies survived. N291--1778/symbols126--185; net40 means -123.121048..+4.083955bp, R -0.339145..-0.016948, PF0.360349..1.023408.92/96 price means non-positive and96/96 R means non-positive. Best price cell N1778,+4.083955bp,R-.030562,PF1.023408; all three annual R means negative. Best R cell N713,+2.808992bp,R-.016948,PF1.016830. All48 SHORT cells price-negative. Original development and complete evidence retained on research-market-breadth-recovery-v14-dev-36998066185 and research-market-breadth-recovery-v14-evidence-36998066185. No account/daily-target success.

Distinct V15 breadth-expansion continuation preregistered before code/outcomes at commit1092fd6cbc84acbd0f42fc6408ad717bdc81f7f5 on research-breadth-expansion-continuation-v15. It tests newly expanding market-wide directional participation plus local momentum continuation (16entries/96policies), not V14 pressure-withdrawal reversal or parameter repair. Implementation/tests/actual launch pending; execution attempts0 and market outcomes0.


## 2026-10-02 — V15 implementation and first trigger handoff

- Distinct preregistered hypothesis: whole-market directional breadth expansion plus same-direction local momentum continuation.
- Frozen implementation branch: `research-breadth-expansion-continuation-v15`.
- Added exact 16-entry / 96-policy implementation, 39 new signal/global-artifact tests, and a 416-test registered regression validator.
- Frozen sources remain source run `36095439671` plus BTC artifact run `36858492497`; the V15 workflow retains the eight-shard map/reduce, canonical Binance 1m exit scan, DEV-first selection, conditional 2024 gate, account constraints, and complete-evidence preservation.
- Implementation last commit before trigger: `2002f85d5914c0803a1ba15a3c3f19452d385893`.
- Trigger commit: `fb42a7154df6c10ad7df412d9ace07065bf651f5`.
- Audit state: trigger push is confirmed; Actions run ID and actual validation result are still pending discovery. Do not claim the workflow executed and do not launch a duplicate until the run/log is found.


## 2026-10-03 — V15 run 1 validation failure and test-only repair

- Actual run [37019476663](https://github.com/duuu-hub/bb-scanner/actions/runs/37019476663) reached validation only.
- Actual log: 416 registered tests, 415 passed, 1 failed, 0 errors in 48.051 s. Every market stage was skipped; this run produced no strategy outcome.
- The failed assertion retained V14's 4% 16-bar count expectation. Under the frozen V15 2% threshold, returns `[-4%,-3%,+4%]` correctly produce down counts `[1,1,0]`, not `[1,0,0]`.
- Failure evidence: `research/breadth-expansion-continuation-v15/V15_RUN1_FAILURE.json`; complete-evidence branch `research-breadth-expansion-continuation-v15-evidence-37019476663`.
- Only the assertion was corrected at `e67900eae2f8742488daab1665562cef2837cd42`; no signal, cost, chronology, selection, or account parameter changed.
- Retry trigger: `fe7775f16203282a4574173202624dc70ca8acfa`. Its Actions run ID is pending discovery; do not launch another duplicate.


## 2026-10-03 — V15 actual rejection and V16 preregistration

- Actual V15 run: [37025951533](https://github.com/duuu-hub/bb-scanner/actions/runs/37025951533), code `fe7775f16203282a4574173202624dc70ca8acfa`.
- Log-backed execution: 416/416 tests passed; all eight DEV breadth maps passed; eight-shard reduction passed; all eight canonical Binance 1m DEV scans passed; selection and complete-evidence preservation passed.
- Result: 96 cells examined, zero survivors. 2024 gate and account jobs were correctly skipped.
- All 96 cells had negative net40 R. 93/96 also had non-positive net40 mean bp.
- Ranges: N 225–15,500; symbols 116–238; net40 mean -74.7411 to +13.8584 bp; net40 R -0.20748 to -0.02815; PF 0.4803–1.0820.
- Best gross-looking cell `EXPAND_S+1_H96_B10_PRICE_ONLY__H48__TP2`: N=476, 154 symbols, +13.8584 bp/trade, PF 1.0820, R=-0.02815. Its yearly R was +0.0887 / -0.1805 / +0.1012, and calendar-day R was negative in all three DEV years.
- Durable audit: `research/breadth-expansion-continuation-v15/V15_ACTUAL_REJECTION.json` at `fdc689b6be16a50ff244982ab4012d160f8c0a1d`.
- V16 was preregistered before implementation/outcomes at `0f16d614794396ecf7b7cbec3af6b9af138b7792` on `research-breadth-pullback-reclaim-v16`.
- V16 is economically distinct: it does not enter the breadth impulse immediately. It requires a later controlled first pullback and a still-later closed-bar reclaim, explicitly forbidding same-bar touch/reclaim assumptions. Frozen grid remains 16 entries and 96 policies.


## 2026-10-03 — V16 implementation and first trigger handoff

- Preregistered branch: `research-breadth-pullback-reclaim-v16`; preregistration commit `0f16d614794396ecf7b7cbec3af6b9af138b7792`.
- Implemented the frozen 16-entry / 96-policy breadth-impulse → separate controlled first-pullback → still-later reclaim state machine. Same-bar touch/reclaim is forbidden; invalidation, pullback/reclaim windows, favorable-gap exclusion, adverse-gap retention, and stop bounds are explicit.
- Added 42 V16-specific tests and registered 458 total regression tests. This is a registration count only until the Actions log confirms execution.
- Preserved the eight-shard global breadth map/reduce, canonical official Binance 1m scans, DEV-first selection, conditional seen-2024 gate/accounts, and complete-evidence archive.
- Final implementation commit before trigger: `6b8ddcdc97090fcbf2a8030f56497f256c2f8118`.
- Exactly one trigger was pushed at commit `11d1d8c6aeda6b7da41d5a937a74ff796a9f531f`.
- Audit state: Actions run ID and actual validation result are pending discovery. Do not claim execution, do not treat registered tests as passed, and do not launch a duplicate.


## 2026-10-03 — V16 run 1 validation failure and fixture-only retry

- Actual run: [37074012724](https://github.com/duuu-hub/bb-scanner/actions/runs/37074012724), trigger/code commit `11d1d8c6aeda6b7da41d5a937a74ff796a9f531f`.
- Log-backed result: 458 tests ran in 59.405 s; 455 passed, 3 failed, 0 errors. The independent saved V13 audit passed first. Every V16 market map/scan/selection/account job was skipped, so market outcomes remain zero.
- The three failures were synthetic-fixture defects: two tests left the fixture's bar 104 as an earlier valid pullback touch, and the stop-floor test's price geometry produced a 0.8168% structural stop rather than exercising the 0.5% floor.
- Test-only repair commit: `eb360c6bc81fcb4ad9bdf5494dd6b2143803dabe`. It removes the unintended earlier touch and changes only the synthetic entry price needed to engage the floor; signal/economic parameters are unchanged.
- Run-1 evidence branch: `research-breadth-pullback-reclaim-v16-evidence-37074012724`.
- Exactly one retry trigger commit: `217a46b0e752800b70115eb9a63528d809242ce6`. Its Actions run ID/log is pending discovery; do not launch another duplicate.


## 2026-10-03 — V16 retry validation passed; DEV maps active

- Existing retry run [37074366568](https://github.com/duuu-hub/bb-scanner/actions/runs/37074366568), code/trigger `217a46b0e752800b70115eb9a63528d809242ce6`.
- Actual validation job `111060825788`: frozen input contract passed (256 prior hashes / 856 source files); independent saved V13 audit passed; 458/458 tests passed in 59.852 s; canonical chronology smoke passed.
- All eight DEV breadth-map jobs are now active. This establishes implementation validation only, not market profitability, account growth, or daily-target achievement.
- Continue the existing run through map/reduce, eight canonical scans, selection, and evidence; do not launch a duplicate.


## 2026-10-03 — V16 actual rejection; V17 preregistration

- Actual V16 retry run: [37074366568](https://github.com/duuu-hub/bb-scanner/actions/runs/37074366568), code `217a46b0e752800b70115eb9a63528d809242ce6`.
- Log-backed execution: 458/458 validation tests passed; all eight global breadth maps, reduction, all eight canonical Binance 1m DEV scans, selection, and complete-evidence preservation succeeded.
- Result: 96 cells examined, zero survivors; 2024 gate and accounts were correctly skipped.
- 122,718 parameterized resolved outcomes. N range 95–3,743; symbols 68–214; net40 mean -87.6113 to +15.6357 bp; net40 R -0.33857 to +0.05203; PF 0.4518–1.1867.
- Only 9/96 cells had positive mean bp and 5/96 positive net40 R. No SHORT cell had positive global net40 R.
- The dominant failure was calendar distribution: all 96 cells had non-positive day R in 2021 and 2022; 94/96 were non-positive in 2023.
- Best mean-bp cell `RECLAIM_S-1_H96_B10_D10__H16__TP2`: N=328, +15.6357 bp, PF 1.1867, but R=-0.03069 and yearly R -0.1893/+0.0727/-0.2060.
- Best-R cell `RECLAIM_S+1_H96_B20_D05__H48__TRAIL`: N=477, +13.0131 bp, PF 1.0899, R=+0.05203, but 2022 R=-0.06071 and day R -0.06154/-0.13296/+0.00718.
- Full evidence: 963 files / 342,209,447 bytes, branch `research-breadth-pullback-reclaim-v16-evidence-37074366568`, commit `62efcc275b981621c7655a85c6cb4db714ef6685`. Failure audit is preserved on the V17 branch.
- V17 was preregistered before code/outcomes at `34f8a1dea44b09ce3720e6bed676ef4d7d1d33bb` on `research-session-vwap-failed-auction-v17`.
- V17 is economically distinct: fixed 00:00/08:00/16:00 UTC session-VWAP failed-auction reversal after ATR displacement, volume climax, and a separate closed confirmation. It uses no breadth, premium, taker-flow, or cross-sectional residual trigger. Frozen grid: 16 entries × 2 holds × 3 exits = 96 policies. Implementation and execution are pending.


## 2026-10-03 — V17 implementation and actual launch

- Frozen preregistration remains `34f8a1dea44b09ce3720e6bed676ef4d7d1d33bb`; no V17 market outcome was observed before implementation.
- Implemented the exact 16-entry / 96-policy fixed-session VWAP failed-auction reversal on `research-session-vwap-failed-auction-v17`.
- Session VWAP is cumulative quote volume divided by a causal base-volume proxy from exact 00:00/08:00/16:00 UTC anchors. Gaps invalidate the segment; prior 96-bar median volume and prior ATR are shifted/frozen.
- Same-bar exhaustion/confirmation is impossible. Confirmation is a separate closed bar within four bars, entry is the next contiguous open, event-time VWAP is frozen, and stop floor/cap plus favorable-gap exclusion are explicit.
- Added 15 V17-specific causal/grid/target tests. Local evidence: 15/15 passed; the local eight-shard synthetic pipeline preserved all 96 zero-outcome cells. This is implementation evidence only, not profitability.
- Frozen trigger commit: `9c9da99e9cb57041190ae3841cfd561bc0cb309d`.
- Exactly one actual Actions run was launched: [37081846528](https://github.com/duuu-hub/bb-scanner/actions/runs/37081846528), validation job `111083860167`.
- Current observed state: dependency installation/validation active. No Actions test result and no market outcome may be claimed until logs are read. Do not launch a duplicate.


## 2026-10-03 — V17 run 1 validation failure and hash-only retry

- Run [37081846528](https://github.com/duuu-hub/bb-scanner/actions/runs/37081846528), validation job `111083860167`, failed before any test or market stage.
- The downloaded validation artifact `11258862620` contained the actual `v17-tests.log`. It proves an assertion at validator line 16: the V17 frozen input file had deterministic V17 bytes while the copied validator still expected V13's byte hash.
- Actual V17 hashes: inputs `75db339a...`, context `4ea5c86b...`. Source semantics remained 256 frozen hashes / 856 market files; no economic parameter changed.
- Tests started: 0. Market outcomes: 0. DEV/gate/accounts were skipped.
- Failure JSON and exact log are preserved on `research-session-vwap-failed-auction-v17-evidence-37081846528`.
- Hash-attestation-only repair: `c7a4ffc2b40a6d007bba43be7d8d5563e351e1f2`. Local repaired validator evidence: input contract passed and 15/15 new tests passed.
- Exactly one retry was triggered at `a9c3fecb7b3c6170ffab543f30ca933f9f5593de`: [37082108726](https://github.com/duuu-hub/bb-scanner/actions/runs/37082108726). Do not duplicate it.


## 2026-10-03 — V17 retry validation passed; DEV scans active

- Existing retry run: [37082108726](https://github.com/duuu-hub/bb-scanner/actions/runs/37082108726), code/trigger `a9c3fecb7b3c6170ffab543f30ca933f9f5593de`.
- Downloaded actual validation artifact `11258868229` (digest `sha256:aa972077...`) and read all three logs.
- Actual validation job `111084675598`: 473/473 tests passed in 60.514 s, zero failures/errors.
- Official conservative 1m chronology smoke and canonical invariants passed.
- Eight-shard synthetic pipeline passed and preserved all 96 cells; it intentionally generated zero market outcomes.
- All eight real DEV scan jobs are active. This is implementation validation only; no V17 profitability, account growth, survivor, or daily-goal claim exists yet.


## 2026-10-03 — V17 actual DEV result and V18 preregistration

V17 run [37090734742](https://github.com/duuu-hub/bb-scanner/actions/runs/37090734742) completed validation, all eight DEV scans and selection. Actual logs show 473/473 tests passed and the 96-cell selector returned zero survivors. The durable DEV branch is `research-session-vwap-failed-auction-v17-dev-37090734742` at commit `b00b135`.

Numerical rejection:
- resolved N range: 318–3,506; symbols: 135–215
- net40 mean: -69.7103 to +53.0550 bp/trade
- cost-adjusted R: -0.38547 to +0.10369; PF: 0.3930–1.6966
- 24/96 cells had positive mean and R, all on the long side; short positive-R cells: 0/48
- best cell `SVWAP_S+1_D30_V20_C10__H16__R15`: N 318, +53.0550 bp, R +0.10369, PF 1.6966
- the best cell still had 2021 R -0.36323, only 25 trades across 14 dates in 2021, and negative equal-calendar/active-date diagnostic R in 2021, 2022 and 2023
- therefore 2024 gate and account simulation were correctly skipped; there is no evidence for the +0.7%–2% daily account target

The workflow's final status is failure only because the post-result preservation job attempted to copy an absent optional `PREEXECUTION_VALIDATION.json`. A targeted evidence-only recovery, run [37097336607](https://github.com/duuu-hub/bb-scanner/actions/runs/37097336607), is collecting the already-produced artifacts and actual logs without rerunning market research.

V18 was preregistered before implementation on branch `research-session-opening-impulse-v18`, commit `be48e9335b2fd9564627022429b8cd8de7273bcc`. It tests fixed-session opening impulse continuation after cross-sectional breadth, session-VWAP acceptance, a distinct shallow pullback and later reacceleration. The grid is frozen at 16 entries × 2 holds × 3 exits = 96 policies.


V17 evidence recovery completed successfully: 1,606 files were preserved on branch `research-session-vwap-failed-auction-v17-evidence-37090734742` at commit `d38be1a`. This closed only the recordkeeping failure; it did not rerun or alter market results.


## 2026-10-03 — V18 implementation validated; DEV breadth maps active

- Frozen preregistration remains `be48e9335b2fd9564627022429b8cd8de7273bcc`; no V18 market outcome was observed before implementation.
- Implemented the exact 16-entry / 96-policy session-opening impulse continuation, full-universe opening breadth, causal session VWAP, distinct shallow pullback and later reacceleration on `research-session-opening-impulse-v18`.
- Corrected selection to the V18 plan's own gates (300 outcomes, 60 symbols, 30 observations and 20 active KST dates per DEV year) before execution; it does not inherit V9 sample gates.
- Actual Actions run: [37100246454](https://github.com/duuu-hub/bb-scanner/actions/runs/37100246454), trigger/code `bc72e1d7d90ba3eff0e9b1efe4107750c8c61d2a`.
- Actual validation job `111138197429` succeeded. Logs prove the frozen 256/856 source contract, 483/483 registered tests, conservative chronology smoke, and eight-map/eight-scan synthetic 96-cell pipeline passed.
- All eight real DEV opening-breadth map jobs are active. This proves implementation consistency only; it is not profitability, account-growth, survivor, or daily-target evidence.
- Continue this existing run through breadth reduction, eight canonical DEV scans, selection and durable evidence. Do not launch a duplicate.


## 2026-10-03 — V18 actual rejection; V19 preregistration

- Actual V18 run: [37100246454](https://github.com/duuu-hub/bb-scanner/actions/runs/37100246454), code/trigger `bc72e1d7d90ba3eff0e9b1efe4107750c8c61d2a`.
- Log-backed execution: 483/483 validation tests passed; all eight opening-breadth maps, full-856 reduction, all eight canonical DEV scans and selection succeeded.
- Result: 96 cells examined, zero survivors. The 2024 gate and account jobs were correctly skipped.
- Every cell had negative cost-stressed mean return and negative risk-normalized R. Ranges: N 1,220–4,063; symbols 187–223; net40 mean -62.7614 to -22.2427 bp; R -0.35381 to -0.11791; PF 0.2487–0.7752.
- Least-bad cell `OPEN_S-1_D15_B65_R382__H32__R15`: N 1,221, 195 symbols, -22.2427 bp/trade, R -0.11791, PF 0.7752. Its annual R was -0.25558 / -0.06139 / -0.13019 and equal-date R was negative in all DEV years.
- Durable DEV output: `research-session-opening-impulse-v18-dev-37100246454`, commit `be55501`. Rejection audit: `research/session-opening-impulse-v18/V18_ACTUAL_REJECTION.json`, commit `e70023c1b4e420e64998c602435047981230d7fd`.
- The initial complete-evidence push failed only with transient GitHub HTTP 500 after archive creation. Same preserve job rerun `111146131308` is active; no market stage is rerun.
- V19 was preregistered before implementation/outcomes at `0485ee848e8802b398b91b9d911d0b9804ee73d6` on `research-session-opening-range-breakout-v19`.
- V19 is economically distinct: it rejects immediate opening-impulse chasing and instead requires a quiet frozen opening range, multi-bar two-sided VWAP balance, and a later volume/taker-confirmed range escape. Frozen grid: 16 entries × 2 holds × 3 exits = 96 policies.


## 2026-10-03 — V18 oversized evidence recovery

- V18 economic result remains unchanged: actual run [37100246454](https://github.com/duuu-hub/bb-scanner/actions/runs/37100246454), 483/483 validation tests, all eight breadth maps/scans, 96 cells, zero survivors.
- The initial preserve job and its targeted rerun both built the same 5,268-file / 2,276,505,454-byte archive, then failed at the final monolithic Git push with HTTP 500. No market computation failed or reran.
- The expected evidence branch did not exist after either failure.
- Added a dedicated evidence-only recovery at workflow commit `fb89783979fc9de3b1e5ff6ecc0c9213fbf8096e`; trigger `cf67e91eadf5c437f7d78391d23785d412ffe12a`.
- Recovery run [37103484177](https://github.com/duuu-hub/bb-scanner/actions/runs/37103484177) is active. It preserves all result tables, ledgers, exclusions, source checks, plans, rejection audit and actual job logs, plus a complete path/size/SHA256 manifest and GitHub artifact digests for every original file. Reproducible large official-minute binary/cache payloads are hash-only in Git to avoid a third 2.28GB pack failure.
- This is recordkeeping recovery only. V19 remains preregistered at `0485ee848e8802b398b91b9d911d0b9804ee73d6`; implementation has not yet begun.


V18 evidence recovery [37103484177](https://github.com/duuu-hub/bb-scanner/actions/runs/37103484177) completed successfully. The durable branch is `research-session-opening-impulse-v18-evidence-37100246454`, commit `698a6fb`. It retains 211 material result/log/audit files (7,689,281 bytes) and a complete exact path/size/SHA256 manifest for all 5,270 collected files; 5,059 reproducible minute/cache binaries (2,269,966,705 bytes) are represented hash-only together with original Actions artifact digests. This resolves the recordkeeping failure without rerunning market research.

## 2026-10-03 — V19 implementation and actual launch

- Frozen preregistration remains `0485ee848e8802b398b91b9d911d0b9804ee73d6`; no V19 market result was observed before implementation.
- Implemented exact-session two-bar opening-range freeze, 4/8-bar two-sided causal-VWAP balance, later four-bar volume/taker-confirmed escape, next-open entry, structural stop and frozen measured-move/R exits.
- Registered 16 entry configurations and 96 policy cells, with 15 new causal/grid tests and the inherited chronology/account regression suite.
- Workflow commit: `282ea906d9521aed380fff5bcdd1eab01c366e6b`. Trigger/code commit: `a623fd81bd88383604ff6e68c1f8ab1a91823b9a`.
- Exactly one actual run is active: [37103772006](https://github.com/duuu-hub/bb-scanner/actions/runs/37103772006), validation job `111148239722`.
- No test pass, market outcome, survivor or daily-target claim exists until the actual log is read. Do not duplicate this run.

## 2026-10-03 — V19 actual rejection; V20 preregistration

- Actual V19 run: [37103772006](https://github.com/duuu-hub/bb-scanner/actions/runs/37103772006), code/trigger `a623fd81bd88383604ff6e68c1f8ab1a91823b9a`.
- Log-backed execution: 498/498 registered tests passed; official 1-minute chronology smoke, synthetic eight-scan/96-cell pipeline, all eight real DEV shards, selection and preservation succeeded.
- Result: 96 cells examined, zero survivors. The 2024 gate and account jobs were correctly skipped.
- Original policy-table ranges: N 12–1,065; symbols 12–194; net40 mean -138.2429 to +10.3986 bp; R -0.75697 to -0.03152; PF 0.0522–1.1257. Exactly one cell had positive mean/PF; no cell had positive R.
- Least-bad cell `ORBREAK_S-1_W10_B04_V175__H32__R25`: N 47, 39 symbols, +10.3986 bp, R -0.03152, PF 1.1257. Annual R was -0.12948 / +0.31276 / -0.38963 for 2021/2022/2023, so it is sparse and regime-specific, not a candidate.
- Durable DEV: `research-session-opening-range-breakout-v19-dev-37103772006`, commit `388ae60`. Durable complete evidence: `research-session-opening-range-breakout-v19-evidence-37103772006`, commit `6410bc1`. Rejection audit: `research/session-opening-range-breakout-v19/V19_ACTUAL_REJECTION.json`, commit `06b013365a9966cfb11359d425eb341ca453095c`.
- The opening-range continuation family is retired without local tuning.
- V20 was preregistered before implementation/outcomes at `3e8da35699b8e987b31a39e7264c49cb0e7ec8e0` on `research-session-opening-range-sweep-v20`.
- V20 is an opening-range liquidity-sweep reversal: after frozen two-sided range acceptance, it requires an outside wick with a decisive close back inside plus swept-direction taker aggression, then trades toward frozen accepted value. Grid remains 16 entries × 2 holds × 3 exits = 96 policies under unchanged hard gates.

## 2026-10-03 — V20 implementation frozen and actual run launched

- Frozen preregistration remains `3e8da35699b8e987b31a39e7264c49cb0e7ec8e0`; no V20 market outcome was observed before implementation.
- Implemented the exact opening-range liquidity-sweep reversal: 4/8-bar two-sided acceptance, later outside wick plus decisive inside close, swept-direction taker aggression, next-open reversal, stop beyond actual sweep extreme and frozen midpoint/opposite-boundary/R exits.
- Registered 16 entry configurations and 96 policy cells.
- Local pre-execution evidence: 18/18 new causality tests, 501/501 full regression tests, bytecode compilation and the synthetic eight-scan/96-cell pipeline passed. This is implementation validation, not profitability evidence.
- Implementation commit: `41b4f7dffea2f3bc8d7b8d37fca98bf71b32d551`. Trigger commit: `3d3f315a426f72fefd7933347f71061e725cdf54`.
- Exactly one actual run is active: [37116650810](https://github.com/duuu-hub/bb-scanner/actions/runs/37116650810).
- Do not duplicate the run or claim a survivor/daily target until actual logs and result tables are read.
