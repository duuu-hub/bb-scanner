# Breakout Level Retest V13 — preregistered research plan

Frozen 2026-10-02 UTC before V13 implementation, tests, launch or outcomes.
Branch `research-breakout-level-retest-v13`; parent V12 code
`793820a5dee7577663e94f9e45157468e05850aa`. Research only. No changes to main,
orders, live/demo settings, watchers, unrelated workflows or other automations.
Read AGENTS.md and RESEARCH_RULES.md; the shared canonical engine is authoritative.

## Previous failure and distinct economic conjecture

V12 actual run36989682215 completed actual307 tests in23.130s, canonical chronology
and account invariants, synthetic8-shard validation, all8 DEV scans, selection
and durable preservation. All96 cells failed the fixed screen. Original raw
market ledgers on the complete-evidence branch contain654 parameterized outcomes,
109 entry-key events and67 distinct symbol/entry-time events.24 cells have zero
outcomes; maximum per-policy N52 versus required300. All96 also fail every
annual frequency/date gate;92 fail positive-coin concentration.51 of72 nonempty
cells have positive net40 price/R, but the largest mean,1019.1332704bp, is one
short trade. This is inadequate evidence, not a discovered strategy. Largest-N
long W4/Q60/PRICE_ONLY/H16/TRAIL has N52, net40 -28.9449602bp, R -.24795798,
PF .70546167. No account/gate stage ran and no daily target was measured.

Independent audit rehashed all8 original compressed market ledgers, checked their
Git blob SHA and saved manifest SHA256, reconciled each price/fee/slip/funding/R
and all96 annual/date cells with arithmetic error0. Full204-file durable archive
has7,707,395 bytes; tree sizes match. Audit does not claim to have independently
rehash-downloaded every archive file. Selection DEV/dev-N files are intentionally
filtered empty candidate ledgers, not the raw market trials; use complete-evidence/
files/taker-absorption-release-v12-dev-N for original outcomes.

The V11 malformed BNX June2022 ZIP was recovered at its exact previously recorded
SHA256 during V12 validation. Invalid parser status/excluded V11 outcomes remain
unchanged. It is evidence repair, not a new market experiment. Actual V12 chronology
exclusions0; one official BLZ June2023 month resolves ambiguity. Original minute ZIP,
checksum text and exact slices remain preserved. Do not duplicate V11 or V12.

V13 conjecture: a sustained crossing of a prior one-day/three-day price boundary
may change the inventory available near that boundary. A later failed attempt to
return inside the old range may show that the level is now defended, after the
initial aggressive burst has passed. Test the subsequent closed retest/recovery
event rather than immediate breakout chasing. Low participation during the
pullback is a separate fixed diagnostic variant. No passive inventory or actual
orders are observed, so the mechanism is an inference to falsify.

Prior background: Carol Osler, New York Fed Staff Report125, April2001
https://www.newyorkfed.org/research/staff_reports/sr125.html studies clustered
FX stop/take-profit orders and price response at technical boundaries. Her
July2000 paper https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html
studies firm-provided intraday FX support/resistance. Neither paper establishes
our algorithmic channel/retest, crypto transfer, low-volume condition, or alpha.
The rules below are our conjecture. We do not observe their bank-order data.
This remains adaptive V1-V13 multiple testing and cannot make lucky backtests
independent evidence.

V13 has a frozen breakout-memory state followed by a distinct level retest. It
does not use V1 coin-minus-BTC relative strength/EMA pullback, V9 daily global
rank, V4 compression, V11 large return/taker persistence or V12 inverse absorbed
flow. Its level is frozen at the breakout, never moved to later highs/lows.

## Identical frozen data and search count

Copy the complete856-symbol source catalogue and256 prior baseline hash contract
byte-for-byte from V12. DEV source run36095439671, BTC canonical context run36858492497;
the context records complete source/run URLs and digests. Verify all856 source
files, prior256/BTC hashes and physical cutoffs before selection. Keep missing,
gap, invalid geometry and unavailable history in denominators/counts. Listing
eligibility needs30 elapsed AND observed source dates and closed24h quote>=20m.
No survivor-filtered current universe, fabricated prices or third-party bars.

DEV2021-09--2023 only.2024 is an already observed research gate.2025--2026-08
and September are seen/not independent and cannot tune V13. Forward/new evidence
begins only after a specific survivor is frozen. All earlier failed trials remain.

Exactly16 entries = side(+1/-1) × prior channel96/288 bars(1d/3d) × maximum
retest wait8/24 bars(2h/6h) × participation ANY/LOWVOL75.
Each × maximum hold48/192 bars(12h/2d) × TP2/TP3/TRAIL =96 cells.
No other threshold search. Error repairs/repeated runs do not add hypotheses.
V3-V12 each96 fixed cells=960 investigated cells, excluding V1/V2/other-chat
exposure. V13 adds96; explicitly report this repeated search if one passes.

## Causal level and breakout snapshot

On closed breakout bar b, channel high=max highs[b-N..b-1], channel low=min lows
over the same window. ATR A=shared prior14-Wilder ATR available through b-1;
baseline B=mean quote[b-96..b-1]. All windows restart after timestamp gaps.
Current quote/baseline>=1.25, directional body, side*(close[b]-level)>=.25*A;
long CLV>=.75, short CLV<=.25. Exact BTC current/prior endpoints must be available,
abs BTC15m return<=1.5%, abs coin15m return<=8%, A/B positive finite and listing/
liquidity eligibility true. No taker-share condition.

Snapshot b,N,side,level,A,B,breakout quote/volume multiple and ATR-distance. One
pending snapshot per coin/entry key. While pending, ignore additional breakouts;
never overwrite the level, A or B. A source gap clears it. Any close during
b+1..r with side*(close-level)<-.25*A cancels it; an expired snapshot is cleared.
Evaluate at most b+2..b+max_wait. b+1 can invalidate but cannot signal.

## Closed retest/recovery trigger

At a candidate closed r, long low is in [level-.5*A,level+.25*A]; short high is
in [level-.25*A,level+.5*A]. Require side*(close[r]-level)>=.1*A, directional
body, long CLV>=.60 / short<=.40. Require long close[r]>high[r-1] /
short close[r]<low[r-1] to show recovery from the local counter-move.
Listing/liquidity and exact BTC/coin single-bar guards also hold at r.

ANY adds no quote filter. LOWVOL75 requires arithmetic mean quote[b+1..r]
divided by frozen B<=.75; include closed r, exclude breakout b. It describes
observed participation only, not informed/uninformed trader identity.
Choose the first qualifying r. Consume its snapshot even if the following
known-open validation excludes entry; never retry a failed signal using future
outcomes. Otherwise a snapshot blocks new breakouts through its cancel/expire bar.
Accepted entry intents have a48-bar cooldown independent of hold or P&L.

## Known next-open fill, risk and exits

Entry is next contiguous15m open r+1 within split. Directional favourable catchup
gap side*(entry/close[r]-1)>.5% is excluded; adverse gap retained. Invalid/open
gap, nonpositive levels and stop risk>6% excluded/count. Far retest extreme must
remain on loss side of actual fill. Long structural SL=low[r]-.25*A; short
SL=high[r]+.25*A. Floor distance at .5% of actual fill. Use this actual fill/SL
for TP2/TP3; fixed hold12h/2d; TRAIL shared3ATR after1h. No further inferred fills.

Pre-entry1m ignored. Every touched entry-minute exit incl TP-only is LOSS.
Established same1m TP/SL collision LOSS; proved earlier official1m TP honored.
Missing/minute mismatch excludes/counts; official Binance checksum and original
ZIP bytes, checksum text, exact slice/hash persist. Preserve invalid received
ZIPs before parsing without converting missing/invalid input into a win/loss.
The canonical resolver and common account engine are unchanged.

Net40 diagnostics:40bp roundtrip,10bp adverse SL slip and2bp per holding-day
funding stress. R uses fee/slip/maximum-hold funding-inclusive stop reserve.
These are execution stress assumptions, not observed historical funding/fills.

## Frozen screen and common account verification

Unchanged V9-V12 screen: N>=300,>=10 coins,top positive coin contribution<=30%,
positive global net40 price and R.2021 requires>=40N,>=20 KST entry dates;
2022/23 each>=80N,>=60dates; each positive price,R,equal-active-date R.
Retain all96 including zero. Sort by worst2021/22/23 equal-date R then policy
name; maximum one per entry,three per side,six total. Freeze
BREAKOUT_LEVEL_RETEST_UNION before already-seen2024 GATE.

Same account start equity1,20/40bp costs,entry loss reserve.5%,aggregate2%,
coin nominal30%,gross200%,max6 positions,no samecoin duplicate/opposite entry.
KST -2% observed loss flattens/blocks day;peakDD10 halves future risk;DD15
flattens/permanently halts split. No target cap. Budgets/15m observations do not
guarantee stop prices or maximum losses. Rates use ALL853 DEV/367 GATE KST
calendar dates, including inactive,halted and partial edge dates.

Provisional screen unchanged: each split>=80 fills,MDD<15%,no DD15halt,
positive20/40 growth;20bp CAGR>=20%,PF>=1.15;40bpPF>=1.05;guarded20 annual
DEV2021/22/23 and GATE2024 positive. Prioritize executable whole-account growth,
drawdown,N,costs,+.7%/+2% calendar-day rates and target gap, not raw PF/EV.

## Validation, evidence and continuation

Commit this PLAN/full frozen hashes/V12 failure audit BEFORE V13 code. Meaningful
tests: independent prior channel/ATR/quote cutoff and gap resets; future/current
perturbations; frozen level and ignored intervening highs; mirrored breakout/
retest; min/max wait, failed close,expiry,gap,first event,cooldown and consumed
invalid fill; LOWVOL mean cutoff; actual-open stop/floor/TP; official1m bridge;
complete source/tampered ledger,all96 zero/positive fixture and annual gates.
Run all prior suites, canonical chronology/invariants and synthetic8-shard
pipeline before market shards. Actual logs alone prove executions.

Save all original full/failed/partial ledgers and hashes,policies,exceptions,
logs,code/preregister commits on separate branches. Update central CONTINUATION
with actual run/results. Upon failure numerically decompose and preregister the
next economic hypothesis, without ending at one failed candidate. Upon provisional
survivor freeze first then neighboring settings,60/80bp costs,stronger slip,
actual+1/+3min entry delays,time-block dependence and recent/forward samples.
Repeated searches and a single September month cannot prove independent alpha.
Whole-account NET daily+.7%--2% remains unproved.
