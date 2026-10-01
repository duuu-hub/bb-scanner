# Funding Crowding V7: preregistered research plan

Frozen before implementation or V7 market outcomes,2026-10-01UTC.
Branch research-funding-crowding-v7, parent65095198c689b09a3028ec460db8198f819b0bcc.
Research only; never main, real/demo orders, live config or watcher mutable files.
Follow AGENTS.md/RESEARCH_RULES.md and canonical execution throughout.

## Audited rejection and new economic source

V6 actual run36906530309 completed all20jobs,130tests/canonical smoke and96DEV
policy cells. Original549,129DEV observations read/reconciled; one frozen
candidate, eight actual account scenarios, ZERO strict survivors.
DEV20guarded:1278executions,+10.2064%total,CAGR4.2543%,MDD10.6651%,PF1.0536;
DEV40guarded:-10.8515%,DD15halt. Seen2024gate20:-12.9077%,MDD15.0191%,PF.7856;
gate40:-13.6266%,DD15halt. All8saved account files audited independently.
Daily+0.7% frequency64/853DEV and15/367GATE; +2%15/853 and2/367. Inactive,
post-halt and2partial edge dates included. This does not meet the user's goal.
Same-timestamp independent signals clustered up to114; only23.92%ofDEV20
intents executed. Trade-weighted R+.05274 versus equal-active-date R-.21406.
Risk/exposure and slot rejections explicit.2024still loses with guards disabled;
MDD then20.78%/26.66%. No profitable claim or retuning on seen2024.
Fullarchive2238files/70,349,092bytes includes2028minute slices.1888downloaded
DEV/account originals verified against durable SHA/bytes; original outcomes,
curves and failure breakdown retained underresearch/btc-factor-lag-v6/.

New conjecture: an unusually expensive direction of perpetual leverage may
represent crowded inventory. After the coin's closed price/flow reverses against
that paid-funding direction, inventory unwind may continue. SHORT after high
positive paid funding and downward confirmation; LONG after strongly negative
paid funding and upward confirmation. This adds an external historical funding
observable rather than retuning V6 BTC sensitivity or another candle threshold.
Funding is an imperfect crowding proxy, not proof of OI, liquidation or a causal
inventory mechanism. Adaptive after prior failures; count in the same search.
Past published paid funding is a SIGNAL ONLY. Never assume future announced
funding or harvest a future settlement credit to create profits.

Primary source checked2026-10-01:
https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
and https://github.com/binance/binance-public-data . Binance history associates
funding rates with settlement timestamps; archive availability must be actually
verified in Actions. Monthly schema is verified against original downloaded data,
not assumed correct from third-party claims. No third-party profit evidence.

## Frozen inputs, archive integrity and availability

Same original15m Binance UM source36095439671, eight frozen full catalogues,
same delisted-inclusive universe,256prior processed CSV SHA hashes and original
timestamps/row statistics verified before each DEV/GATE/account stage. SameBTC
context day-edge-v2-btc/run36858492497. Baseline hashes byte-identical to V6.
Same>=30source-day age and closed24h quote turnover>=$20m eligibility.
Processed15m CSV hashes are not absent original15m Binance ZIP checksums.

New funding source:
https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY-MM}.zip
Require adjacent.CHECKSUM and SHA256 match. Preserve original ZIP bytes and
checksum text plus parsed timestamp/interval/rate arrays and hashes. Cache hit
must verify frozen raw bytes against verified sidecar; corruption stops without
downloading a replacement. Missing/malformed/checksum mismatch is explicit
FUNDING_DATA_GAP with affected months/bars; no zero or current rate imputation.
Exhausted transient HTTP/rate errors stop with partial evidence, not permanent
gaps. Never substitute another symbol/exchange, current fundingInfo or API data
silently. An archive schema repair is recorded as repair, not another hypothesis.

Monthly records: calc_time in UTCms, funding_interval_hours, last_funding_rate.
Validate finite values, integer timestamps, strictly increasing unique times
inside the requested month, positive intervals<=24h, finite abs(rate)<=1.
Use supplied historical interval, never today's interval.8h-equivalent rate
=last_funding_rate*8/funding_interval_hours. This is linear normalization for
selection, not a realized funding PnL model. Preserve each observed interval.
At signal close decision=i_open+15min, allow a paid record only after
calc_time+15min<=decision. This conservative availability lag can make a slightly
off-boundary settlement available one additional bar later. No future backfill.
Retain only when age<=min(12h, recorded interval+15min). Exact as-of lookup;
missing current-month source invalidates that month rather than carrying rates
through missing archives. Load previous month's final known record where valid.
Physically filter funding records to stage end before use.

Record eligible bars, available/unavailable funding bars, source months and
per-year coverage for every coin. DEV coverage gate: known eligible funding bars
>=95%of all frozen eligible15m bars separately in2022/2023. Below this, no
candidate may pass; report SOURCE_COVERAGE_INSUFFICIENT, not an alpha failure or
silently narrowed universe. Reconstruct missing data only with the exact official
catalogue and saved source hashes; no outcome-directed repairs.

DEV physical cut[2021-09-01T00:00Z,2024-01-01T00:00Z);GATE2024UTC through2025
start, already-seen research gate.2025-2026-08are seen comparisons, not clean
holdout or retuning data. September may have been observed elsewhere and one
month is inadequate independence. Freeze before new recent/forward validation.
Known trials: V1oneentry;V2=36entries/144fixed-time cells,27canonical policies/
216accounts;V3=16entries/96cells/eightaccounts;V4/V5each16entries/96cells/no
accounts;V6=16entries/96cells/eightaccounts. Other chats add exposure. V7 plans
16entries/96policies. Repairs/source probes/calendar audits are not discoveries.

## Sixteen entry settings x two holds x three exits =96

LONG/SHORT x absolute8h-normalized funding threshold.05%/.10%
x RECLAIM/FLOW confirmation x BTC ANY/ALIGN4H.
At CLOSED15m bar i, require -side*known_funding_rate8h>=.0005/.001:
long negative funding, short positive funding, never the converse.
Both modes require side*(close-open)>0, side*coin last15m return>0, and
current quote>=1.25*preceding96-bar mean(excluding i).
RECLAIM additionally closes above prior4-bar high for LONG or below prior4-bar
low for SHORT, excluding i. FLOW additionally taker-buy quote>=55%LONG or
<=45%SHORT. Actual quoted turnover ratio, not base-volume substitution.
ALIGN4H requires side*exact-aligned CLOSED BTC4h return>0; missing BTC rejected.
ANY has no BTC direction filter. Funding availability always required.
Onset of qualifying state; fixed16-bar per-coin/config INTENT cooldown,
independent of future hold/exit/profit. Indicators restart after price gaps.

Entry next15m OPEN using prior closed-bar decision; not a scanner latency claim.
Reject side*(actualopen/lastclose-1)>.005, retain adverse entry gaps. Stop=actual
fill +/-2priorATR14(priceATR excluding i); floor distance.5% of fill, reject>8%
or invalid/nonpositive stop. TP from actualfill and actual risk. Priority:
(-side*funding_rate8h)/(priorATR/closedcoinprice)*sqrt(currentvolume multiple),
all pre-entry; never rank by future returns or fees ultimately earned.
Holds24/96bars=6h/24h. ExitsTP2/TP3/closed-bar3ATR trail after one holding hour.

Unchanged canonical chronology: official Binance1m entry-parent touches and
ambiguous parent TP/SL. Preentry touches ignored; entry-minute ANYexit touch
inclTP-only=>SL/LOSS; established same-minute both=>SL/LOSS; first proven later
minute honoured. Missing/misaligned/unreproducible=>DATA_GAP/ENTRY_MISMATCH/
EXIT_MISMATCH excluded and saved. Preserve original1m ZIP/CHECKSUM and exact
used minute slices/array hashes. Never invent chronology or an outcome.

## Frozen selection, account comparison and daily objective

Preserve all96cells includingzeroN/rawtrials. Require the above funding coverage
gate and unchanged V5/V6 DEV screen:>=300 independent observations,>=10coins,
2022/2023each>=80observations and>=60distinctKSTentrydates; net40 PRICE and
cost-inclusive-stop-risk R means positive separately in BOTH years and overall;
top positive coin contribution<=30%. Rank smaller yearly mean of KST-entry-date
meanR, policy-name tie; one per key,<=3per side,<=6total. Preserve dependence,
including negative equal-date means. No screen success claim; account required.
Freeze selected hold/exit before2024GATE; predeclared FUNDING_UNION if>1selected.

Same equity1,20/40bp roundtrip costs,10bp adverse SL/forced slip,2bp/day funding
STRESS charge. Historical funding is signal only; do not add realized funding
credit/debit here, keeping costs comparable with priorstudies. A later separate
actual-funding sensitivity check is required if a candidate passes.
Same .5%entrySLbudget,2%aggregate reserve,30%coin notional,200%gross,6positions,
no same-symbol duplicate/opposite; KST observed-2% flatten/block; peakDD10%
future risk half andDD15% flatten/permanent haltrestsplit. No profit cap.
Budgets/15m observations are not guaranteed fill prices or loss ceilings.

Strict provisional gate unchanged: both DEV/GATE>=80executions,MDD<15%,noDD15
halt,positive20/40returns; each20CAGR>=20%,PF>=1.15; each40PF>=1.05; complete
2022/2023/2024net20guardedKSTyearspositive. Rank minDEV/GATE20CAGRthen lower40DD.
Report actual return/CAGR/DD/PF/win/EV/lossstreak,N,rejections,concurrency/exposure,
goal+.7%/+2%frequencies on ALL intersectedKSTdates includinginactive/posthalts.
SameUTCcuts,853DEV/367GATEdates; two partial edge dates15h/9h flagged, never
annualized into full-day returns. Dailyreturn product must match finalcash.
Old complete-only reports/history retained; V3reporting-only correction preserved
onresearch-calendar-audit-v3. No market replay or change to old PnL/risk.

## Execution and further falsification

Separate PLAN commit BEFORE code/marker. Meaningful tests: originalfunding
ZIP/schema/checksum/cache corruption; variableintervalnormalization; laggedasof
timestamps/no future rates; missing/stale month windows; mirroredfunding/flow
direction; causal feature/known entry gaps/actual risk; coveragegate/annual PRICE
andR/full96cells; sharedcanonical chronology/account/calendar invariants.
Actual Actions source/schema probe and canonical smoke before trusting DEV.
Probe does not inspect strategy outcomes, and cannot select a winning threshold.
Branch-specific marker/concurrency; no duplicate active run or watcher coupling.
Preserve all原rawfunding archives,sourcehashes/parameters/minute slices/trades/curves,
actual logs,partial errors and failures on separate research evidence branches.
Update centralCONTINUATION.json with actual code/run/resultpaths. No main merge.

Pass is only provisional: freeze first; neighboursettings,strongercost/slip,
actual1m/3mentrydelay, chronological/blockdependence, actualfunding-PnLsensitivity,
newrecent/meaningfulforward. Never treat endless-search luckywinner or small
positive CAGR as the user's dailyNET+.7%~+2%goal. If rejected, decompose and
preregister/implement/test/ACTUALLY execute next economic hypothesis; persist.
