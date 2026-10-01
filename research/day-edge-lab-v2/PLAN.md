# Day Edge Lab V2: frozen first research batch

Frozen on 2026-10-01 before this batch's event outcomes. This is an ongoing
search, not a claim that a profitable strategy must exist or that unlimited
trials prove one. Preserve failures and record every tested cell.

## Objective and account contract

Find economically material net account growth for predominantly intraday
trading; seek +0.7% to +2% net account days, allowing larger profits. Never
translate a gross coin move or raw-event expectancy into account profit.
Initial proposed risk remains 0.5% equity/entry and 2% summed reserved stop
budgets, six positions, 30% notional per coin, 200% gross. No overlapping
same-coin positions. Korea-calendar -2% loss triggers flatten/entry block;
peak-equity -10% halves new risk and -15% flattens/halts. These are observation
triggers, not guaranteed fill-price bounds. No live trading or main changes.

## Prior evidence and data boundaries

Relative Pullback V1, actual run 36853787624, is rejected and preserved. Its
gross independent expectancy was -2.45bp in training and +2.98bp in its old
holdout, far below the 20/40bp transaction-cost assumptions. Do not retune its
thresholds using that holdout.

- Development: 2021-09-01 <= entry < 2024-01-01 UTC.
- Selection gate: calendar 2024. This is a research gate, **not a pristine
  independent test**: other studies already examined this historical market.
- 2025-01 through 2026-08: already-observed comparative replay only. It must
  never be relabeled untouched holdout or fed back into this batch's parameters.
- Reserve 2026-09-01 through 2026-09-30 (UTC) as a single-use recent check,
  acquired only after finalists/exit variants are frozen. It was not examined
  in this chat's V1 run. Other chats may have examined September; accordingly
  even this month is only a recent check, not proof of an independent edge.
- A convincing independent conclusion ultimately requires forward paper
  execution after the final strategy is frozen. One recent month is insufficient.

The scout physically truncates both coin and BTC arrays before 2025 prior to
feature/outcome construction. Events cannot cross development/gate endpoints.
Every stage restarts capital at its boundary; no cross-boundary positions.

## Universe and provenance

Reuse the eight official-Binance USD-M 15m archive dataset shards from actual
source run 36095439671. That collector enumerated the archive catalogue, which
includes delisted contracts, rather than selecting today's successful coins.
Record each compressed source file SHA256 and gap count. Do not hide malformed
data. The collector has explicit errors, including unencoded non-ASCII names;
those are source coverage limitations, not losing trades.

Eligible contracts must have at least 30 days of observable source history
before 2024-01-01; at signal time require 30 days since first source bar and
past closed 24h quote turnover >= $20m. This excludes later-introduced TradFi
perpetuals and very new/illiquid coins without selecting their later outcomes.
It also excludes crypto contracts that only appear after 2023, intentionally.
Binance research cannot prove Bitget availability or execution quality.

## First batch: 36 entry hypotheses and four holding horizons

All decisions use completed 15m bars, execute the following 15m OPEN. Trigger
only on the onset of a qualifying state. Long and short mirror each other.
Rolling statistics reset after a source gap. BTC comparisons require matched
timestamps. Development event studies hold for 1h, 4h, 12h or 24h, exiting at
an exactly scheduled OPEN, with no TP/SL orders. Non-overlap is enforced per
symbol/cell. Cross-symbol account constraints are **not** yet applied.

1. SHOCK_FOLLOW / SHOCK_FADE: 15m or 1h move of at least 3% or 6%; follow or
   oppose the move. Both signs are separate diagnostic hypotheses.
2. ISOLATED_FADE: oppose a 1h move >=3% or >=6% when BTC's absolute 1h move
   <=1.5% and signed coin-minus-BTC return also exceeds that threshold.
3. EXHAUSTION_FADE: oppose a 24h move >=15% or >=30% only after an opposite
   1h move >=0.5%, opposite 4h move >=1%, and a close through the previous 1h
   low (after a pump) or high (after a dump).
4. REJECTION_FADE: oppose a 1h move >=4% or >=8%, with >=2x past quote-volume
   activity and a rejecting 15m close: bottom 35% of range after an upward
   move, top 35% after a downward move.
5. TREND_IGNITION: follow a 4h move >=4% or >=8% if 24h return agrees, 1h
   quote activity >=2x past activity, and close breaks previous 4h high/low.
6. SLOW_PULLBACK: follow a 24h move >=10% or >=20% after an opposite 4h
   retracement >=1% and an agreeing latest 1h recovery >=0.5%.

Volume baseline: preceding 96-bar mean, shifted four bars, times four; compare
to the latest four completed bars. Turnover eligibility includes only closed
bars. No future daily volume, next-bar extrema or ex-post event peaks.

Fixed-time event cost diagnostic: fee+ordinary slip represented by 10/20bp
on entry and exit notional (20/40bp round trip at unchanged price), plus 2bp
per actual holding day funding stress. Historical actual funding, order-book
spread and size-dependent impact are not reconstructed. Large-event slippage
can be greater, and canonical account finalists must be stressed further.

## Frozen selection gate

Examine all 144 cells, preserve the full table. A replay seed must have:
at least 300 development and 100 gate events; >=10 symbols in each; positive
net40 mean in development, 2022, 2023 and 2024; and no symbol contributing
more than 30% of summed positive symbol P&L in either window. Rank by the
smaller development/gate net40 expectancy. Retain one holding horizon per
entry key, at most two per economic family and six seeds total.

This is a screening rule, not a significance or profitability certificate.
Raw events are dependent across coins and time. A cell can survive by chance.
Neighbouring thresholds and holding horizons must be shown, not hidden; no
isolated optimum may be promoted. If no cell passes, save the failure and
move to a separately preregistered batch with a different hypothesis.

## Canonical strategy replay required after the scout

For selected seeds, freeze initial stop/exit variants before examining their
account outcomes. Then use official Binance 1m for entry-minute touches or
parent TP/SL collisions. Entry-minute TP/SL/TP-only => conservative LOSS;
established same-minute both => LOSS; earlier proven 1m exit is respected.
DATA_GAP / ENTRY_MISMATCH / EXIT_MISMATCH must be explicitly excluded/counted.
Execute chronology smoke tests before trusting any large replay. No blanket
parent-bar stop-loss shortcut. Maximum intended hold is 24h in this batch,
well inside the user's seven-day ceiling.

Compare actual constrained account equity, net return/CAGR, 15m MTM MDD,
executable N, PF/win rate/loss streak, concurrent/exposure utilization, and
all complete Korea calendar days, including inactive/halted days. Apply the
same initial account/risk controls to all variants. Show guarded and diagnostic
versions separately. Stress costs, delayed entries and return concentration.

## Continuation log

This branch is research-day-edge-lab-v2, based on preserved V1 result commit
0717d432abd0e7c040fa24799218d2a4ab76bce0. Research is resumable, with batches,
failures and code persisted. A finished workflow is a completed calculation;
only independently verified evidence can make a strategy a survivor.
