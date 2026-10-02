# Aggressive Taker-Flow Cascade V11 — preregistered plan

Frozen before V11 implementation, tests, launch or market outcomes on
2026-10-02 UTC. Branch `research-aggressive-flow-cascade-v11`; parent V10 code
`18270ece2a1923591e14ae706ce9b91e79ddd76a`. Research only. Do not modify
main, orders, live/demo configuration, watchers or unrelated workflows.
Follow AGENTS.md, RESEARCH_RULES.md and the repository's conservative official
Binance one-minute chronology.

## V10 failure and the changed economic hypothesis

V10 actual run `36966622286` completed the actual validation, eight DEV scans
and selection. It produced 340,491 overlapping parameterized outcomes across
16 entries / 96 cells. Every cell had negative global net40 price and
cost-inclusive stop-risk R; selection was zero, so GATE/accounts were skipped.
Price means ranged -73.5048 to -9.5794 bp, R means -0.31940 to -0.04375 and PF
0.47327 to 0.94600. The best short W1344/z3/FLOW55/H96/TP2 cell had N1,278,
194 symbols, -9.5794 bp, -0.04375R and PF0.946; its 2021/2022 price means were
negative and 2023 equal-active-date R was negative. All 96 cells had negative
R in both 2022 and 2023; all 96 had negative equal-date R in 2023. Mean-target,
TP2 and trail groups were all negative. Saved all96 cells, eight scan/source
metas and all exclusions audit passed:856 source files,256 prior hashes,
814 official minute-month checksums and159 DATA_GAP policy outcomes. The
existing preserve job retains full raw ledgers/logs; do not duplicate V10.

V11 tests a distinct short-horizon conjecture: a large coin-specific breakout
whose aggressive taker flow persists may continue for the next two to six
hours because a forced-liquidity cascade or split metaorder is not completed in
one fifteen-minute bar. V11 trades WITH the shock. It is not V3's confirmed
post-shock reversal, V4's low-volatility compression breakout, V6's delayed BTC
factor catch-up, V9's prior-day cross-sectional leader continuation, or V10's
relative-price level reversion. V4 required prior compression and only a 55%
current flow split; V11 has no compression premise and requires a contemporaneous
1.5%/3% idiosyncratic shock, >=3x prior volume and 65% current or multi-bar
persistent aggressive flow.

Primary motivation only: Jaisson, “Market impact as anticipation of the order
flow imbalance” (https://arxiv.org/abs/1402.1288) models persistent market-order
flow and impact; Scaillet, Treccani and Trevisan, “High-Frequency Jump Analysis
of the Bitcoin Market” (https://arxiv.org/abs/1704.08175) reports clustered
jumps and persistent price changes in historical Mt.Gox data. Neither paper
tests these rules, Binance perpetuals, this universe, these costs or account
growth. They provide an economic reason to test, not evidence that V11 works.
Repeated V1-V11 searches are adaptive multiple testing, not clean discovery.

## Immutable data and time contract

Use the same Binance USD-M USDT perpetual15m source run `36095439671`, all eight
original manifests and the complete856-file catalogue copied byte-for-byte
before code. Verify all856 current source hashes and all256 prior frozen hashes.
Use the same BTC artifact from run `36858492497`, exact SHA256
`0f38299f9ebc61729d745db43dd0cd4bab9447493105ba5205843a52bad107bf`.
Processed CSV hashes are not absent original15m ZIP hashes. Every stage fails
closed on missing, duplicate or changed source files. Preserve source manifests,
actual hashes, complete/partial progress, official one-minute ZIP/checksum
provenance and exact used minute slices.

DEV is UTC [2021-09-01,2024-01-01); GATE is [2024-01-01,2025-01-01). 2024,
2025-2026-08 and September have been seen and cannot be called clean holdouts or
used to tune V11. Require >=30 elapsed AND observed source dates and closed24h
quote turnover >=$20m. Coin/BTC timestamps align exactly; never interpolate,
nearest-match, forward-fill or replace missing values with zero.

## Sixteen entries × two holds × three exits = 96 fixed policies

Entry grid:

`side LONG/SHORT × current-shock 1.5%/3% × prior breakout 16/48 bars ×
flow CURRENT65/PERSIST55` =16 entries.

Holds are8/24 fifteen-minute bars (2h/6h). Exits are TP15, TP25 and TRAIL.
All96 cells, including zero-outcome cells, must be retained. No parameter edits
after inspecting outcomes.

At closed bar i, compute all baselines within each exact contiguous coin segment:

- prior ATR14 is the EWM true range through i-1;
- prior96 quote-volume mean excludes i;
- prior high/low breakout level over16 or48 bars ends at i-1;
- previous three taker-buy shares are i-3..i-1 and exclude i;
- simple coin/BTC15m return uses exact closes i-1 to i; a missing BTC close at
  either endpoint invalidates the signal;
- every current-bar value is known only at the close; enter no earlier.

Common LONG conditions (mirror signs/inequalities for SHORT):

- current simple coin return >=1.5% or3%, and <=8%;
- current return minus exact BTC15m return >=1%, while abs(BTC15m)<=1.5%;
- close exceeds the previous16/48-bar high; current high never sets its level;
- close>open, close-location value >=0.8 (SHORT <=0.2);
- current quote volume / prior96 mean >=3;
- prior ATR finite/positive and eligibility/turnover rules pass.

CURRENT65 requires current taker-buy quote share >=.65 LONG / <=.35 SHORT.
PERSIST55 requires current share >=.60 LONG / <=.40 SHORT, the previous three
closed bars' mean >=.55 LONG / <=.45 SHORT, and at least two of those three
shares >=.55 LONG / <=.45 SHORT. Current flow is never inserted into its prior
persistence baseline. Save current/prior shares and all cutoff timestamps.

Only the first qualifying bar in a contiguous episode is eligible. Apply a
fixed16-bar per-symbol/config cooldown from accepted intents, independent of
future exits. Enter at the next contiguous15m OPEN. Reject a favorable gap
>0.5% from signal close; retain adverse gaps. Stop from actual fill at
1.5 prior ATR with a.75% fill-price floor; reject distance>6%, invalid ATR,
nonpositive levels or split-crossing entries. Risk/priority uses actual fill.
Priority = signed idiosyncratic15m return * sqrt(volume multiple) * absolute
current flow imbalance / (prior ATR / signal close), all known before entry.

TP15/TP25 are fixed actual-fill targets at1.5/2.5 times actual stop distance.
TRAIL uses the unchanged canonical closed15m3ATR trail activated after one hour.
All use the shared canonical resolver; TP15/TP25 pass explicit fixed targets
through its fixed-target path. Official Binance1m rules: pre-entry touches are
ignored; any entry-minute exit including TP-only is SL/LOSS; an established
same-minute TP/SL collision is SL/LOSS; a proven earlier one-minute exit is
honored. DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH are excluded, counted, saved and
reviewed, never assigned invented wins or losses. Transient fetch failure saves
partial outputs and fails the shard.

Net40 diagnostic deducts40bp roundtrip,10bp adverse SL/forced-fill slip and
2bp per holding day funding stress. The funding value is a stress assumption,
not reconstructed historical funding. R divides by the cost/slippage/funding
inclusive stop reserve used by the unchanged account engine.

## Frozen selection and identical account test

Use the V9/V10 DEV screen unchanged: >=300 outcomes,>=10 coins, top positive
coin contribution<=30%, positive overall net40 price and R; partial2021 >=40N,
>=20 KST entry dates and positive price/R/equal-active-date R; 2022 and 2023
each >=80N,>=60 dates and positive all three means. Sort by worst 2021/22/23
equal-date R then name; at most one policy per entry key, three per side and six
total. Freeze `AGGRESSIVE_FLOW_CASCADE_UNION` before seen2024 GATE. Preserve all
rejected cells and economic rejection counts.

Selected candidates use the unchanged same-universe canonical account engine:
equity1,20/40bp comparisons,0.5% entry risk reserve,2% aggregate reserve,30%
per-coin nominal,200% gross,six positions,no same-symbol overlap/opposite.
KST observed -2% flattens/blocks the day; peak DD10% halves future risk and
DD15% flattens/permanently halts that split. These budgets/15m observations do
not guarantee fills or cap losses. No profit cap. Count every one of853 DEV and
367 GATE KST calendar dates, including inactive, post-halt and partial edges.

Unchanged provisional account gate: each split>=80 fills,MDD<15%,no DD15 halt,
positive20/40 growth;20bp CAGR>=20% and PF>=1.15;40bp PF>=1.05; guarded20 annual
DEV2021/22/23 and GATE2024 all positive. Report actual account growth,DD,fill N,
capacity/utilization,all-calendar +.7%/+2% rates. Raw overlapping PF/EV cannot
establish executable account growth or the daily target.

## Required validation, execution and preservation

Commit this PLAN/full856 catalogue/V10 failure audit before code or launch.
Test past-only ATR/volume/breakout/prior-flow windows, exact BTC alignment,
future/current perturbations, mirrored sides, both flow modes, shock and common-
BTC filters, onset/cooldown, known next-open gap, actual-fill stop/priority,
TP15/TP25 shared resolver, no exit-dependent intents, all96/zero cells, annual
price/R/date screens, full catalogue/duplicate/tamper rejection. Run the existing
canonical chronology/account/calendar smoke and a synthetic eight-shard pipeline
before actual DEV scans. A missing local dependency is recorded as failure;
actual CI logs alone prove execution.

Save all trials,96 cells, original ledgers, exclusion reasons, source hashes,
minute originals/slices, curves and completed/failed actual logs on separate
research branches. Central CONTINUATION.json records the PLAN commit, code,
actual run and result paths. Do not duplicate running stages or change unrelated
automations/workflows.

Any survivor remains provisional after repeated searches. Freeze first, then
test neighboring settings,60/80bp costs,stronger slippage, actual+1/+3 minute
entry delays, dependent time blocks,new recent observations and forward data.
Daily whole-account NET +0.7%-2% is still unproved; a lucky DEV cell, small
positive mean or one month cannot satisfy it.
