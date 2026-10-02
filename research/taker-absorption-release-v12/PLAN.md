# Taker Absorption Release V12 — preregistered research plan

Frozen on 2026-10-02 UTC before V12 code, tests, launch or market outcomes.
Branch `research-taker-absorption-release-v12`; parent V11 code
`7f7c3b517978e3401e80d1d2f9fafea01cc048d1`. Research only. Follow AGENTS.md,
RESEARCH_RULES.md and official Binance1m conservative chronology. Never modify
main, live/demo settings, orders, watcher state or unrelated workflows.

## Previous failure and distinct economic hypothesis

V11 actual run36980518301 completed validation, eight DEV scans, selection and
durable preservation.96 cells/37064 overlapping parameterized outcomes yielded
zero candidates and zero account scenarios. All96 global net40 price/R means
were negative; every2022/2023 R and equal-active-date R was negative. Price means
ranged -133.5542 to -28.0354bp; R -.75216 to -.18367; PF .09010 to .78213.
Seventy-two cells had fewer than300 outcomes. Seven gross-positive cells became
zero net40-positive cells after45.3727-48.9334bp mean deductions. The best price
cell (long,T1.5,B48,PERSIST55,H24,TRAIL) had N204,123 coins,+20.1656bp gross,
-28.0354bp net,-.18367R,PF.78213;2021/2023 negative and2022 R negative.

The eight original compressed ledger hashes, independent fee/slip/funding/R
arithmetic, all96 global/annual cells and complete archive git byte sizes passed
read-only audit. Source856/prior256 hashes;52 DATA_GAP policy exclusions cover
three BNXUSDT entry events in June2022.752 checksum records include one malformed
BNXUSDT month that the existing preservation helper did not retain as raw bytes:
the archive contains751 original minute ZIPs,2611 files/1134733117 bytes. The
bad official month remains excluded. Its published ZIP/hash/checksum will be
recovered byte-for-byte during V12 validation and preserved without recomputing
V11 trades; that preservation repair is not a discovery or new hypothesis.
DEV commit c71ba3e5902af59e6af53b8ecec4fb512ee1e8ae; evidence commit
bf0127ad77d88862af9321a8642acac814d76cf0. Full SAVED_LEDGER_AUDIT and failure
decomposition remain versioned. V11 never reached the account/daily target.

V12 tests a different conjecture: repeated aggressive buys/sells with unusually
high volume but little net price progress may reflect opposing passive supply
or demand. A subsequent break of that entire setup range in the opposite
direction may trap that aggressive flow and create a short-lived release.
Trade AFTER the confirmed opposite range break. It is a multi-bar executed-flow
versus price-response interaction, rather than V11 shock continuation, V3 a
large prior return reversal, V4 prior low volatility compression, V5 a single
liquidity sweep/wick, or V8 premium/basis normalization. No large initial shock,
wick, premium, BTC factor fit or prior-day ranking is required.

Motivation only: Cont,Kukanov,Stoikov, “The Price Impact of Order Book Events,”
arXiv1011.6402v3, revised2011-04-13,
https://arxiv.org/abs/1011.6402. The paper relates price changes to order-book
imbalance and depth in50 NYSE stocks; trade volume has a noisier relation.
V12 uses executed taker volume only, NOT the paper's full order-book imbalance.
Passive absorption/hidden liquidity is an inference, not observed fact. Neither
the paper nor V11 failure demonstrates these new rules work on Binance. This
is another adaptive hypothesis in the cumulative V1-V12 multiple-search record;
historical success must remain provisional.

## Immutable input and time contract

Same source run36095439671, eight original manifests,256 prior processed-CSV
hashes and complete856-file catalogue; FROZEN_CONTEXT bytes copied unchanged,
SHA2564cbf469c0c0d4efa9c63d19c9650bbaf1e8ded8f38bc4195ec2d702d2eea9614.
BTC artifact run36858492497,
SHA2560f38299f9ebc61729d745db43dd0cd4bab9447493105ba5205843a52bad107bf.
All stages fail closed on changed,missing,duplicate or omitted sources. Processed
15m CSV hashes are not absent original15m ZIP hashes. Retain source URLs/hashes,
official1m ZIP/checksum bytes including checksum-verified malformed inputs,
used minute slices, all trials, exclusions and partial/failed execution logs.

DEV UTC[2021-09-01,2024-01-01); GATE[2024-01-01,2025-01-01).2024 is an
already-observed gate;2025-2026-08 already-seen comparisons;September may also
have been seen and is insufficient independent evidence. Never use these to
tune V12 or call them pristine holdouts. Require>=30 elapsed AND observed source
dates and known closed24h quote volume>=$20m. Coin/BTC exact timestamp alignment;
no interpolation,nearest match,forward fill or synthetic zero for missing data.

## Sixteen entries × two holds × three exits =96 fixed policies

`trade side LONG/SHORT × setup window4/8 bars × absorbed-flow share60%/65% ×
release confirmation PRICE_ONLY/FLOW55` =16 entries.

Holds16/48 fifteen-minute bars (4h/12h). Exits TP2,TP3,TRAIL. Preserve all96
cells including zero-outcome cells. No economic parameter changes after results.

At fully closed release bar i and within an exact contiguous coin segment,
the setup consists ONLY of bars i-w..i-1. The following exclude release bar i:

- setup high/low = extrema of those w bars;
- setup quote volume and taker-buy quote volume = sums of those w bars;
- setup buy share = sum(taker-buy quote)/sum(quote), not average bar shares;
- setup volume multiple = setup mean quote / mean quote of the96 bars ending
  at i-w-1, so the baseline also excludes the entire setup;
- setup starting ATR = EWM ATR14 through i-w-1;
- setup signed price change = (close[i-1]-close[i-w-1])/setup starting ATR;
- setup count = bars with buy share>=.55 for absorbed buys, or<=.45 for sells;
- release prior ATR = EWM ATR14 through i-1; release volume multiple = current
  quote volume / prior96 mean ending i-1.

For SHORT (absorbed aggressive buys): setup weighted buy share>=.60/.65 and
at least ceil(.75*w) setup bars have buy share>=.55. For LONG (absorbed sells),
buy share<=.40/.35 and at least ceil(.75*w) bars have buy share<=.45.
Both: setup volume multiple>=1.5; abs(setup price change)<=.5 starting ATR;
both setup and release prior ATR finite and positive; age/turnover eligibility.

Release SHORT closes below setup low by at least.1 release prior ATR, has
close<open and close-location<=.25. LONG mirrors above setup high,.1ATR,
close>open and close-location>=.75. Current volume multiple>=1.25. Current
abs(coin15m return)<=8%; exact BTC15m abs return<=1.5%. PRICE_ONLY adds no
current taker-flow condition. FLOW55 requires release current taker-buy share
>=.55 LONG / <=.45 SHORT, separately from opposite-flow setup shares. Save all
setup boundary timestamps, volume sums/baselines/weighted share/count, price
response, ATR cutoffs, release fields and entry gap.

Use the first qualifying bar of a contiguous episode and16-bar per-config
cooldown from accepted intents, independent of future exits. Enter at the next
contiguous15m OPEN only. Favorable gap>0.5% from release close is rejected;
adverse gaps remain. Invalidate a setup if its far range edge is already on the
wrong side of actual fill. SHORT initial SL=setup high+.25 release prior ATR;
LONG SL=setup low-.25 release prior ATR. Extend the distance from actual fill
to at least.5% of fill if narrower. Reject distance>6%, nonpositive levels or
split-crossing/noncontiguous entry. Actual fill sets risk and TP. Priority =
abs(2*setup buy share-1)*sqrt(setup volume multiple)*release breakout ATR units /
actual risk fraction. No outcome/future fill high/low/close affects the entry.

TP2/TP3 use actual fill±2/3 times actual stop distance through the unchanged
canonical fixed-target resolver. TRAIL uses existing closed15m3ATR trail after
one hour. Official Binance1m: pre-entry touches ignored; entry-minute ANYexit
including TP-only=>SL/LOSS; established same-minute collision=>SL/LOSS; proved
earlier1m exit honored. DATA_GAP,ENTRY_MISMATCH,EXIT_MISMATCH excluded/count/saved;
no made-up loss/win. Transient fetch errors checkpoint partial results and fail.

Net40 diagnostics subtract40bp roundtrip,10bp adverse SL slip,2bp per holding
day funding stress. It is a stress assumption, not historical funding. R divides
by cost/slip/maximum-hold funding-inclusive stop reserve of the shared account.

## Fixed selection and identical accounts

Keep V9-V11 DEV screen:>=300 outcomes,>=10 coins,top positive coin contribution
<=30%,positive global net40 price and R. Partial2021>=40N,>=20 KST entry dates
and positive price/R/equal-active-date R.2022/2023 each>=80N,>=60dates and
positive all three. Sort by worst2021/22/23 equal-date R then policy name; at most
one per entry key,three per side,six total. Freeze TAKER_ABSORPTION_RELEASE_UNION
before already-seen2024 GATE. Keep every rejected cell/economic rejection count.

Selected candidates use identical canonical accounts:equity1,cost20/40bp,
entry risk reserve.5%,aggregate2%,coin nominal30%,gross200%,max6positions,
no same-symbol additional/opposite entry. KST observed -2% flattens/blocks the
day; peak DD10% halves future risk;DD15% flattens/permanently halts that split.
Budgets and15m observations do not guarantee fill prices or cap losses. No
profit cap. Daily rates divide by all853 DEV/367 GATE KST calendar dates,
including inactive,post-halt and partial edge dates.

Provisional account pass unchanged:each split>=80fills,MDD<15%,no DD15 halt,
positive20/40 growth;20bpCAGR>=20%,PF>=1.15;40bpPF>=1.05;guarded20 annual
DEV2021/22/23 and GATE2024 all positive. Show actual growth,DD,PF,winrate,EV,
executable N,losing streak,concurrency/exposure/utilization and all-calendar
+0.7%/+2% rates. Raw overlapping diagnostics cannot establish account growth
or the user's daily target. Even this historical pass remains provisional.

## Required validation and continuation

Commit this plan/full catalogue/V11 saved-ledger audit before V12 code or launch.
Test independent setup formulas, quote weighting, 96-bar pre-setup exclusion,
ATR and range cutoffs, future/current perturbations,gap resets,mirrored sides,
flow counts/current confirmation,setup flatness,BTC filters,release breakout,
known next-open fill/structural stop/floor,exit-independent cooldown,fixed TP
and official chronology bridge,missing exclusions,all96/zero cells,annual/date
screen,complete frozen catalogue/tamper rejection and preserved invalid official
archive bytes. Run canonical smoke and synthetic8-shard pipeline before markets.
Actual Actions logs alone establish execution. Keep local/CI failures separate
from economic trials. No duplicate running research or unrelated automation edits.

Retain full completed/failed/partial evidence on separate research branches.
Update central CONTINUATION.json with plan,code,run and result paths. On rejection,
decompose failures and preregister a distinct next hypothesis before its code.
For any survivor freeze first, then neighbors,60/80bp costs,stronger slippage,
real+1/+3min entry delays,time-block dependence,new recent data and forward data.
The whole-account NET daily+0.7%-2% objective remains unproved; repeated searches
and one recent month cannot make a lucky backtest independent evidence.
