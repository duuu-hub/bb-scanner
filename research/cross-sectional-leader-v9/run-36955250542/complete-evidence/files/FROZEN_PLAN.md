# Cross-Sectional Leader Persistence V9 — preregistered plan

Frozen before V9 implementation or outcomes, 2026-10-01 UTC. Research branch:
research-cross-sectional-leader-v9. Research only: never main, orders,
live/demo configuration or watcher state. Follow AGENTS.md,
RESEARCH_RULES.md and the canonical official-1m execution contract.

## Why this is a new mechanism

V8 actual run 36929463082 completed all 16 entries/96 DEV policies and
531,854 resolved parameterized outcomes with zero survivor. All 96 net40 price
and R means were negative; best price mean was −19.0345 bp/PF 0.8834. Source
coverage exceeded 99.77% in every year, so the failure is economic. Funding V7
and high-frequency premium normalization V8 are rejected, not retuned.

V9 conjecture: information/attention and capital rotation may persist across
coins. A coin that ended the prior UTC day in the extreme tail of
market-relative performance, then shows fresh intraday price/volume/aggressive
flow resumption on the next day, may continue for 6–24 hours. The new observable
is an exact point-in-time cross-sectional rank across all eligible coins.
V1 used a fixed coin-minus-BTC 4h threshold after a pullback; V6 tested a
within-coin BTC-factor underreaction; V9 instead freezes a prior-day global rank
before any next-day entry and requires new next-day resumption. This is adaptive
after V1–V8 and not independent discovery evidence.

Crypto factor/cross-sectional research motivates testing ranks, not these rules
or their profitability: https://arxiv.org/abs/1811.07860 . Historical research
does not validate Binance perpetual execution, our costs or daily account goal.

## Frozen source and cross-sectional chronology

Same immutable official Binance UM 15m source run 36095439671, all eight
catalogue shards and the exact 256-hash baseline; BTC artifact run
36858492497. Same closed-24h turnover >=$20m and >=30 observed source days.
DEV UTC [2021-09-01,2024-01-01); already-seen 2024 GATE to 2025-01-01.
2025–2026-08 and September are seen/not clean and may not tune V9.

A separate eight-shard map stage emits one row per symbol/UTC day only when:

- the decision is the exact close of a complete UTC day;
- coin and BTC have exact contiguous 15m timestamps for the prior 24h;
- entry eligibility was known at that daily close;
- relative return is
  log(coin_close/coin_close_96) - log(BTC_close/BTC_close_96).

The reduce stage requires at least 30 eligible symbols at a ranking time and
ranks by (relative_return, symbol) deterministically. It preserves rank-universe
count and input hashes. A rank timestamp becomes usable at that UTC midnight
only; it can affect signals during the following UTC day. No same-day future
bar, future listing, missing-symbol zero, survivor backfill or nearest timestamp.
Two-day persistence requires qualifying at both consecutive prior midnights.
Map/reduce outputs and rank hashes are frozen before exit outcomes are scanned.

## Sixteen entries × two holds × three exits = 96 policies

Entries: LONG/SHORT × prior-day tail 5%/10% × persistence one/two consecutive
daily ranks × BTC intraday context ANY/ALIGN4H.

At closed 15m bar i during the next UTC day:

- the frozen prior-midnight rank is in the configured upper tail LONG or lower
  tail SHORT; two-day mode also requires the preceding midnight tail;
- signed exact-aligned 4h coin-minus-BTC simple return >=0.5%;
- close breaks the preceding four closed-bar highs LONG / lows SHORT;
- signed candle body and close-to-close return are positive;
- current quote turnover >=1.5 times the preceding 96-bar mean, excluding
  current; taker-buy quote share >=55% LONG or <=45% SHORT;
- absolute current 15m close-to-close return <=3%, avoiding the shock family;
- ALIGN4H additionally requires signed closed BTC 4h return >0. ANY does not
  treat missing BTC as zero.

Use onset plus fixed 32-bar per-symbol/config cooldown, independent of future
exits. Enter next 15m OPEN. Reject favourable catch-up gap >0.5%; retain adverse
gaps. Stop = actual fill ±2 prior ATR14, floor 0.75% of fill, reject >8% or
nonpositive. Priority is frozen tail extremity × signed 4h residual ×
sqrt(volume multiple)/(ATR/close), all known before entry.

Holds 24/96 bars = 6h/24h. Exits TP2/TP3 or closed-15m 3ATR trail activated
after one hour. Official Binance 1m resolves entry-parent and ambiguous TP/SL:
pre-entry touches ignored; any entry-minute exit touch => SL/loss; established
same-minute both => SL/loss; later proven first touch wins. Missing/malformed/
unreproducible becomes counted DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH.
Preserve original ZIP/CHECKSUM and exact minute slices.

## Frozen selection and account contract

Preserve all 96 cells including zero N. Require >=300 observations, >=10 coins,
top positive coin contribution <=30%, overall positive net40 price and
cost-inclusive-stop-risk R. Partial 2021 requires >=40 outcomes, >=20 KST entry
dates and positive price/R/equal-date R; 2022 and 2023 each require >=80
outcomes, >=60 dates and positive price/R/equal-date R. Rank the minimum of
2021/22/23 equal-date R, then policy name; one policy per entry key, <=3 per
side, <=6 total. Freeze before 2024; predeclared union
CROSS_SECTIONAL_LEADER_UNION.

Same equity 1; ordinary 20/40 bp round trip, 10 bp adverse SL/forced slip and
2 bp/day funding stress; 0.5% entry risk reserve, 2% aggregate, 30% symbol
notional, 200% gross, six positions, no same-symbol overlap; KST observed −2%
flatten/block; peak DD10% halves future risk and DD15% flattens/permanently
halts the split. Budgets are not fill guarantees. Count all 853 DEV / 367 GATE
KST calendar dates including inactivity, post-halt and partial boundaries.

Unchanged provisional account gate: each split >=80 executions, MDD<15%, no
DD15 halt, positive 20/40; 20bp CAGR>=20% and PF>=1.15 each; 40bp PF>=1.05 each;
partial 2021, 2022, 2023 and 2024 guarded20 periods positive. Primary metrics
are account return/CAGR, MDD, PF, executable N, utilization/concurrency and all
calendar-day +0.7%/+2% rates—not independent trade means.

## Required falsification and preservation

Before DEV: tests for exact prior-midnight availability, shard-order-invariant
global ranks, ties, minimum universe, missing coin/BTC/gaps, no future rank
backfill, one/two-day persistence, mirrored sides, current-bar exclusion,
actual-fill risk, cooldown, all 96 cells, annual selection, canonical chronology
and account/calendar invariants. Actual Actions logs must prove tests and a
rank-map/reduce smoke. Preserve every map/rank row, input hash, raw policy ledger,
minute source, curve, log and failure on separate research branches.

If a candidate survives, freeze it before neighbours, stronger costs/slippage,
actual 1m/3m entry delays, time-block dependence, seen recent comparisons and
new future observations. A lucky result found after repeated V1–V9 searches is
not proof. Small positive performance is not the requested whole-account daily
NET +0.7%–2% result.
