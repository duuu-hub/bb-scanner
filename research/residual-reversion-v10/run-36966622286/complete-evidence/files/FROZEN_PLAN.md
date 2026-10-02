# Relative-Price Residual Reversion V10 — preregistered plan

Frozen before V10 implementation or outcomes,2026-10-02 UTC. Branch
research-residual-reversion-v10; parent V9 code2e1bb92891c2c480028c075b30afe91275cc0c43.
Research only; no main, orders, live/demo settings or watcher changes.
Follow AGENTS.md, RESEARCH_RULES.md and official Binance1m chronology.

## Failure and changed economic hypothesis

V9 actual run36955250542 completed232 tests/eight DEV shards/96 fixed cells,
223,218 overlapping parameterized outcomes.94/96 overall net40 price/R means
were negative. Two small-positive LONG2day/top5%/24h trail cells had N153/234,
PF1.0242/1.0271 and2022 price means-26.1148/-85.3235bp. All96 have negative
2022 R/equal-date R. Frozen selection0; account/GATE skipped. Saved source,
96-cell count and exclusion audit passed:856 files/256 prior hashes,496 minute
month checksums,54DATA_GAP policy exclusions over9 coin/entry events. Full
archive byte/original ledger-mean audit remains a separate pending task on
the existing V9 preservation run; no duplicate rerun. See V9 FAILURE_AUDIT.*.

V10 conjecture: when an historically correlated coin/BTC log-price relation
shows a bounded, rapidly relaxing residual, a sufficiently large relative-value
deviation may revert after its first confirmed inward movement. New observable
is a PRIOR-fitted log-price residual, standard deviation and AR1 half-life,
not a prior-day cross-sectional rank or V6 BTC-impulse underreaction. V6 fit
returns before an impulse and traded delayed momentum transmission; V10 fits
past relative price levels, screens their observed relaxation and trades inward.
V3/V5 wick/reversal patterns had no prior estimated relative-price equilibrium.

Primary motivation, not crypto/execution proof: Avellaneda/Lee's2009 paper
https://math.nyu.edu/inmemoriam/avellaneda/AvellanedaLeeStatArb20090616.pdf
models residual relative value and mean-reversion in US equities. V10 is our
own simplified, UNHEDGED single-coin experiment. It does not reproduce that
paper's portfolio, market neutrality or results. AR1/R2 are quality diagnostics,
not a stationarity test or independent evidence of cointegration. Repeating
V1-V10 is adaptive/multiple testing, not clean statistical discovery.

## Immutable source and time contract

Same Binance UM15m source run36095439671/all8 original catalogues;256 historic
baseline hashes and entire856-file V9 catalogue frozen before implementation.
Same BTC run36858492497/day-edge-v2-btc, exact BTC SHA256
0f38299f9ebc61729d745db43dd0cd4bab9447493105ba5205843a52bad107bf.
Processed CSV hashes are not unavailable original15m ZIP hashes. Verify all
stage inputs against source metadata,256 baseline and frozen856 catalogue.
Reuse official1m original ZIP/CHECKSUM/slice preservation; fail closed on
source mismatches, and explicitly count unavailable feature windows/chronology.

DEV UTC [2021-09-01,2024-01-01), GATE [2024-01-01,2025-01-01).2024 is seen
research gate;2025-2026-08 and September are seen/not clean, may not tune V10.
No unseen/forward data for choosing this grid. >=30 elapsed AND observed UTC
source days, closed24h quote turnover >=$20m eligibility. Later listings have
no backfilled DEV history. Coin/BTC alignment exact, no nearest/fill/missing=0.

## Sixteen entries × two holds × three exits =96 fixed policies

LONG/SHORT × fit672/1344 bars (7/14days) × z2/3 × PRICE_ONLY/FLOW55.
All96 policies, including zero N, retained. No parameter edits after outcomes.

At closed coin bar i, x=log(BTC close), y=log(coin close). Fit y=a+b*x using
ONLY contiguous matched closes j=i-W,...,i-1, an intercept and population
covariance/variance. Current bar i NEVER contributes to coefficients, residual
standard deviation, R2 or relaxation estimate. All windows restart after coin
timestamp gaps; any missing BTC close invalidates until W complete pairs.
Require finite .5<=b<=3, level R2>=.5, positive x/y/residual variance and residual
standard deviation>=1e-6. Compute e_i=y_i-a-b*x_i, z_i=e_i/sigma.

Using the SAME frozen b_i, fit AR1 with an intercept to W-1 residual pairs
within that prior W-point window. rho=Cov(e_j,e_{j-1})/Var(e_{j-1}); alpha
cancels from covariance. Require0<rho<1 and half-life=-log(2)/log(rho) between
1 and96 bars inclusive (15m-24h). No rho imputation/capping to force a pass.
This diagnoses past relaxation; it does not guarantee future reversion.

Entry conditions at i:
- LONG z_i<=-2/-3 or SHORT z_i>=2/3;
- remaining signed inward log-gap=-side*e_i>=.015 (1.5% log-price room);
- side*(e_i-e_{i-1})>0 using SAME frozen a_i/b_i, not last bar's fitted model;
- side*(close-open)>0 and abs(coin15m simple return)<=3%;
- abs(exact-aligned BTC4h simple return)<=3%, avoiding large common shocks;
- current quote turnover >=prior96-bar mean, excluding current;
- FLOW55 taker buy quote>=55% LONG/<=45% SHORT; PRICE_ONLY no flow screen.

Use onset plus32-bar per-symbol/config cooldown independent of future exits.
Enter next15m OPEN. Reject favourable inward catch-up gap>.5%; retain adverse
gaps. Stop actual fill +/-2 PRIOR ATR14, floor.75% of fill; reject>8%/invalid
or nonpositive levels. Actual-fill risk, fees/funding reserve unchanged.
Priority = signed inward log-gap*sqrt(prior levelR2)/(priorATR/closed price),
known before entry. Save fit cutoff, beta/intercept/sigma/rho/half-life/z/gap,
matched observation count and equilibrium price for audit.

Holds24/96bars=6h/24h. Exits:
- MEAN: TP=exp(a_i+b_i*x_i), frozen at signal close; do not move it using
  later BTC prices. Reject if actual fill has passed the target; count it.
- TP2: target actual fill+side*2*actual stop distance.
- TRAIL: existing canonical closed15m3ATR trail activated after one hour.

All use unchanged canonical resolver; MEAN/TP2 use explicit frozen TP through
the shared fixed-target path. Official Binance1m for entry-parent touches and
TP/SL ambiguity: pre-entry touches ignored; entry-minute ANYexit inclTP-only
=>SL/LOSS; established same-minute both=>SL/LOSS; proven earlier exit honored.
DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH excluded/count/saved/reviewed, never guess.
Transient exhausted network source failures preserve partial outputs and stop
the shard; they are not silently converted to permanent missing data.

## Frozen DEV selection and identical account comparison

Same V9 screen: >=300 outcomes,>=10 coins,top positive coin contribution<=30%,
overall positive net40 price and cost-inclusive-stop-risk R; partial2021>=40N,
>=20KST entry dates and positive price/R/equal-active-dateR;2022/2023each>=80N,
>=60dates and positive all3 means. Sort worst2021/22/23 equal-dateR then name,
one per entry key,<=3 per side,<=6 overall. Freeze before seen2024 GATE.
Predeclared RESIDUAL_REVERSION_UNION; preserve all rejected settings.

Same equity1,ordinary20/40bp roundtrip +10bp adverseSL/forced slip +2bp/day
funding stress (not historical funding). Entry reserve.5%,aggregate2%,coin
notional30%,gross200%,sixpositions,no same-symbol overlap/opposite. KST observed
-2% flatten/day block,peakDD10% future risk half/DD15%flatpermanent split halt.
Budgets and15m observations are not guaranteed fills/loss caps. No profit cap.
Count all853DEV/367GATE KST dates inclinactive/posthalt/partial boundaries.

Unchanged provisional account gate: each split>=80fills, MDD<15%, no DD15halt,
positive20/40 account growth; each20bp CAGR>=20%,PF>=1.15,each40bpPF>=1.05;
guarded20 annual DEV2021/22/23 and GATE2024 all positive. Shared unchanged
account engine and V9 explicit four-split/year wrapper. Primary account growth,
DD,executableN,utilization/concurrency,all-calendar +.7%/+2%rates, not raw PF/EV.

## Required tests, execution and preservation

Separate PLAN commit BEFORE code/launch marker. Test vectorized beta/intercept/
sigma/R2/rho/half-life against independent direct numpy OLS/AR1 windows,
current/future perturbation, identical frozen model for prior/current residual,
gaps/missingBTC/nonpositive variance, mirrored entry/flow, half-life bounds,
stale/future timestamp rejection, closed turnover/ATR, known entry gap,
actual-fill risk/frozen MEAN targets, no future exit-dependent cooldown,
all96/zero cells, annual price/R/date selection, source catalogue/duplicate
shards/tamper rejection. Actual CI canonical chronology/account/calendar smoke
must pass before large scans. Local dependency failure is recorded, not a pass.

Save all source hashes,96cells,raw ledgers,counts/exclusions/minute originals,
curves and actual completed/failed logs in separate research result branches.
Central CONTINUATION.json records plan/code/run/actual result locations and
active implementation lease; no duplicate dispatch or unrelated workflow edits.
After rejection, numerically preserve it and advance a genuinely new hypothesis.

A survivor is provisional after repeated searches. Freeze before neighbors,
60/80bp costs/stronger slip, actual+1m/+3m delayed entry and time-block
dependence; examine new recent/forward material without tuning. Daily NET
whole-account+.7%-2% remains unmet until independently checked; small positive
means,one month or a lucky repeatedly-searched winner cannot satisfy it.
