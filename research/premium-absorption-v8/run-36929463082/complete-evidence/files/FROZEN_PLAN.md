# Premium Basis Absorption V8: preregistered research plan

Frozen before V8 implementation or market outcomes,2026-10-01UTC. Branch
research-premium-absorption-v8. Research only:never main,live/demo orders,
configuration or watcher mutable state. Follow AGENTS.md,RESEARCH_RULES.md and
the canonical execution contract.

## Prior failure and genuinely new observable

V7 actual run36922945306 completed16entries/96DEVcells/two frozen candidates/
24accounts with ZERO strict survivors. Required guards made DEV union20
-13.2144%,PF.5284,DD15halt and2024union20-10.3696%,PF.8343,DD15halt.
Even without guards,the least-bad2024 candidate was-1.9948%at20bp and-13.1389%
at40bp,with22.30%/27.81%MDD. The result and32,837original evidence files are
preserved. Paid funding is a sparse settled state; it did not distinguish a
persistent discount from an actively normalizing dislocation. Do not retune V7.

V8 conjecture: a temporary futures-versus-index dislocation that has already
started normalizing, while closed price and taker flow confirm absorption, may
continue for6-24hours. LONG requires a still-negative premium that is moving
toward zero plus upward price/flow;SHORT mirrors a positive premium moving down.
This uses the high-frequency premium-index path,not another paid-funding cutoff.
Premium is a basis proxy,not proof of liquidation,inventory or causality.
Adaptive after V1-V7 failures;not independent discovery evidence.

Official specification checked2026-10-01:
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
documents `/fapi/v1/premiumIndexKlines`,12 fields and signed OHLC premium values.
Historical archive to test and preserve:
`https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines/{SYMBOL}/15m/{SYMBOL}-15m-{YYYY-MM}.zip`
with adjacent`.CHECKSUM`. Actual Actions must probe path/schema/checksum before
DEV. If the archive path/schema differs, record a source repair without changing
the economic hypothesis or counting another discovery. No third-party values.

## Frozen source and causality

Same source run36095439671,eight fixed15m shards,256prior CSV hashes,age>=30days,
closed24hquote turnover>=$20m and same BTC artifact run36858492497. DEV UTC
[2021-09-01,2024-01-01);already-seen2024GATE to2025-01-01.2025-2026-08 and
September are seen/not clean. Physically cut before each stage.

For every requested premium month require original ZIP bytes,CHECKSUM text and
matching SHA256. Preserve parsed arrays/NPZ/hash. Verified cache reuse rechecks
raw ZIP,CHECKSUM,NPZ and array hashes;any corruption fails closed without
replacement.404/schema/checksum failures are explicit PREMIUM_DATA_GAP;
transient exhaustion stops while preserving partial bytes. No REST/current
premium substitute,no zero,no cross-month forward-fill through a missing month.

Parse exactly12 kline fields;integer15m UTC open timestamps,strictly increasing
and unique within month. Signed finite OHLC premium values(abs<=1),with
high>=max(open,close,low),low<=min(open,close,high). Ignore documented unused
fields except validate row width. A premium bar i is usable only at decision
`i_open+15m`; exact timestamp alignment with the closed coin bar is required.
No current unfinished premium bar,nearest match or future backfill. Load the
previous month only for timestamp continuity;never carry a value across a missing
current archive. Record per-symbol/year eligible,known,missing and source months.
Before any candidate can pass,exactly-known eligible premium coverage must be
>=95% separately in partial2021,2022 and2023;otherwise
SOURCE_COVERAGE_INSUFFICIENT,not alpha failure or silent universe shrinkage.

## Sixteen entry settings x two holds x three exits =96 policies

LONG/SHORT x absolute premium threshold5bp/10bp x normalization lookback1/4bars
x BTC ANY/ALIGN4H.

At closed15m bar i:

- extreme state:`-side*premium_close[i]>=.0005/.001`;
- observable normalization:`side*(premium_close[i]-premium_close[i-lookback])
  >= threshold/4`,with every intervening premium timestamp contiguous;
- side*(coin close-open)>0 and side*coin15mreturn>0;
- side-confirming taker quote share>=55% LONG or<=45% SHORT;
- current quote>=1.25*preceding96bar mean(excluding current);
- ALIGN4H additionally requires side*exact-aligned closed BTC4hreturn>0;ANY does
  not filter direction. Missing BTC invalidatesALIGN only.

Use onset plus fixed16bar intent cooldown per symbol/config,independent of future
exit. Enter next15m OPEN. Reject favorable catch-up gap
`side*(actualopen/closedprice-1)>.005`;retain adverse gaps. Stop=actual fill
+/-2priorATR14 price units excluding current,floor.5%of fill,reject>8%or invalid.
Priority=`(-side*premium_close)*(side*premium_change)/(priorATR/coinclose)
*sqrt(volume_multiple)`,all known before entry. No future return or exit ranking.

Holds24/96bars=6h/24h. ExitsTP2/TP3/closed-bar3ATR trail after one holding hour.
TP/SL use actual fill and risk. Official Binance1m resolves the entry parent and
ambiguous collisions:preentry ignored,entry-minute any exit=>SL,established same
minute both=>SL,proven later minute first wins. Missing/malformed/misaligned or
unreproducible becomes DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH and is excluded,
counted and preserved. Retain original1m ZIP/CHECKSUM and exact used slices.

## Frozen selection and account test

Preserve all96cells includingzeroN. Screen requires>=300 observations,>=10coins,
overall positive net40price and cost-inclusive-stop-riskR,top-positive-coin<=30%.
Both2022/2023 need>=80observations,>=60KSTentrydates and positive price,R and
equal-entry-date meanR. Because V7 exposed an untested early-path collapse,the
partial2021 DEV block now also requires>=40observations,>=20entrydates and
positive price,R and date-meanR. This stronger rule is learned from seen DEV and
is explicitly adaptive,not independent validation. Rank minimum of2021/22/23
date-meanR then policy name;one per key,<=3per side,<=6. Freeze before2024;
predeclared union PREMIUM_ABSORPTION_UNION if multiple.

Same equity1,20/40bp roundtrip,10bp adverse SL/forced slip,2bp/day funding stress;
.5%entry risk,2%aggregate reserve,30%coin notional,200%gross,sixpositions,no
same-symbol overlap;KST observed-2% flatten/block,DD10%future risk half,DD15%
flat/permanent halt. Budgets are not fill guarantees. Full853DEV/367GATE KST
calendar dates including inactivity,posthalt and15h/9h partial boundaries.

Strict provisional gate unchanged:both splits>=80executions,MDD<15%,noDD15halt,
positive20/40;20CAGR>=20%andPF>=1.15each;40PF>=1.05each;2021partial,
2022,2023,2024 guarded20 periods positive. Account return/CAGR/MDD/PF/win/EV,
loss streak,execution N,rejections,concurrency/exposure and daily+.7%/+2% on all
dates are primary. Independent trade means are diagnostics only.

## Required falsification

Commit this PLAN before code/marker. Meaningful tests:originalZIP/schema/checksum,
signed OHLC geometry,cache corruption,missing/current month,exact close-time
alignment,no future premium,1/4bar normalization continuity,mirrored directions,
future perturbation,entry gap/actual risk,cooldown,coverage and partial2021 gate,
all96cells,annual price/R/dateR,shared chronology/account/calendar invariants.
Actual source probe and canonical smoke must pass before DEV. Preserve all raw
archives,hashes,ledgers,curves,logs and failures on separate branches.

A historical pass is only provisional. Freeze first;then neighbours,stronger
cost/slip,actual1m/3mentry delays,time blocks,source perturbations,new recent and
future data. Never call a lucky adaptive result or small CAGR the user's daily
NET+.7%-2% goal. Economic failure must be decomposed and followed by a separately
preregistered mechanism;source repair is not a new discovery.

