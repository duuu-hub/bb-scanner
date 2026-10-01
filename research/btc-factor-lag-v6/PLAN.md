# BTC Factor Lag V6: preregistered research plan

Frozen before implementation and before ANY V6 outcome, 2026-10-01 UTC.
Branch: research-btc-factor-lag-v6. Parent: 5b26f467543d6724461e8f10557c1e8839075f02.
Research only; never main, orders (real/demo), live configuration or watcher state.

## Recorded failure and changed economic conjecture

V5 actual run36902157396 completed eight DEV shards, all96 frozen policy cells,
99 implementation tests and canonical chronology smoke. ZERO selected seeds;
GATE and account jobs skipped. Its99,382 overlapping parameterized outcomes are
not executed account trades. Every global net40 price/R mean and every2022
price/R mean was negative. Net40 independent price means ranged-79.2490 to
-31.6549bp. Sixty-six cells had>=300 observations; all96 covered>=10 coins.
Reading all eight saved ORIGINAL ledgers reconciled all96 counts/price/R means,
actual-fill gross returns, stop slippage, fees and funding-risk budgets.
Thirty-one cell means were positive gross, zero after net40 costs. Gross means
ranged-29.7550 to+14.7984bp; mean cost drag ranged45.2756 to49.7718bp.
Twenty-six DATA_GAP policy exclusions cover three unique coin/entry events.
Ten ENTRY_PATH_GAP and80 STOP_ABOVE_8PCT exclusions are intent/config counts,
not unique losing trades. Full523-file archive includes436 exact minute slices
and397 officially checksum-verified original minute ZIP records. All484 downloaded
original DEV files matched the durable archive's SHA256 and byte counts.
See research/liquidity-sweep-v5/FAILURE_AUDIT.* and ORIGINAL_LEDGER_AUDIT.json.

New conjecture: during a BTC common-factor impulse, a previously BTC-sensitive
coin may underreact, then continue in the impulse direction when its own closed
bar starts catching up. Test the delayed factor transmission, not another wick
pattern or numerical sign reversal. BTC beta and explanatory R-squared are
estimated BEFORE the impulse; the residual lag is observed BEFORE entry.
V1 favoured already-relatively-strong coins after pullbacks; V6 explicitly tests
coins UNDERREACTING relative to a prior measured BTC relationship. This is an
adaptive conjecture after prior failures, not an independent discovery or proof
of causal information diffusion. No new winning mechanism is presumed.

## Frozen source, multiplicity and periods

Same official Binance UM15m source run36095439671, eight full original catalogues
and manifests, delisted-inclusive universe, processed CSV hashes and timestamps.
Same256 prior CSV SHA hashes verified before DEV/GATE/accounts; byte-identical
FROZEN_INPUT_HASHES.json. BTC artifact day-edge-v2-btc/run36858492497. Official
kline field reference: https://github.com/binance/binance-public-data .
Same>=30 source-day age and closed24h known quote turnover>=$20m eligibility.
Original processed CSV hashes are not absent original15m Binance ZIP hashes.
Reuse V5 original1m ZIP/CHECKSUM verification and retained exact chronology slices.

Physical source cuts unchanged: DEV [2021-09-01T00:00Z,2024-01-01T00:00Z),
GATE [2024-01-01T00:00Z,2025-01-01T00:00Z). Features/outcomes cannot use prices
beyond their stage cutoff.2024 is already-seen research gate, NEVER clean holdout.
2025-2026-08 are seen comparisons, not retuning data. September may have been
seen elsewhere and one month cannot prove independence. No new recent data used
for choosing this grid. Finalist must be frozen before new recent/forward checks.
Known cumulative line: V1 one entry; V2 36entries/144 fixed-time cells and27
canonical policies/216 accounts; V3 16entries/96cells/eight accounts; V4 andV5
each16entries/96cells/zero accounts. Other conversations add exposure.
V6 adds16 entry settings/96 policies. Repairs/audits are not new hypotheses.

## Sixteen entries x two holds x three exits =96

Settings: LONG/SHORT x BTC impulse horizon4/16bars (1h/4h)
x signed factor log-gap1%/2% x PRICE_ONLY/FLOW55 confirmation.

At CLOSED15m candle i:
- Coin and BTC log one-bar returns must have exact timestamp alignment.
- Prior rolling OLS coin return versus BTC return uses672 contiguous matched
  observations (seven days), including an intercept through demeaned covariance.
  For horizon L, shift the estimate by L: the last estimation return ends at
  i-L, before the impulse interval (i-L,i]. No impulse returns enter beta/R2.
- beta=cov(coin,BTC)/var(BTC), R2=covariance^2/(var(coin)*var(BTC)).
  Require finite .5<=beta<=3 and R2>=.10. Nonpositive variance is invalid.
  Coin time gaps restart windows; missing matched BTC returns invalidate a window
  until672 valid contiguous pairs accumulate. Missing BTC is never zero-imputed.
- Signed BTC SIMPLE return>=1% for1h or>=2% for4h, in intended entry direction.
- Signed factor log-gap = side*(beta*log(1+BTCreturnL)-log(1+coinreturnL))
  >=.01 or.02. These are log-return gaps, not an account profit target.
- Coin's last closed15m simple return AND candle close-open are positive in the
  intended direction: a catch-up confirmation. No future rebound required.
- Current quote turnover>=1.25 times the preceding96-bar mean, excluding i.
- PRICE_ONLY has no taker-share filter. FLOW55 additionally requires current
  taker-buy quote share>=55% for LONG or<=45% for SHORT.
- ATR14 is price ATR excluding i. All rolling inputs restart after time gaps.
- Onset of qualifying state; fixed16-bar per-coin/config intent cooldown,
  independent of holding length, future exits or future profits.

Next15m OPEN entry. Reject if side*(actual open/closed coin price-1)>.005;
this known entry gap has already consumed part of the observed catch-up room.
Do not filter entry using future high/low/close. Adverse entry gaps are retained.
Initial SL actual fill +/-2 priorATR; distance floor .5% of fill, reject>8%,
reject invalid/nonpositive levels. TP uses ACTUAL fill and actual risk distance.
Pre-entry priority = signed factor log-gap*sqrt(prior R2)/(priorATR/closed price),
all known at the signal close; never rank by future returns.
Holds24/96bars =6h/24h. TP2/TP3 or closed-bar3ATR trail after one holding hour.
No order submission or claim actual scanner latency achieves this fill.

Canonical resolver unchanged: official Binance1m for entry-parent touches and
ambiguous parent TP/SL collisions; pre-entry touches ignored; entry-minute ANY
exit touch inclTP-only=>SL/LOSS; established same-minute both=>SL/LOSS; proven
first later1m exit honoured. DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH excluded,
counted, saved and reviewed. Never guessed. Exhausted transient source errors
stop with partial evidence, not fabricated permanent gaps. Preserve raw ZIP SHA,
official CHECKSUM proof, exact NPZ slice SHA and replayable data.

## Frozen DEV screen and account gate

Same V5 DEV screen: retain all96 rows inclzeroN;>=300 observations,>=10 coins,
2022/2023 each>=80 observations and>=60 distinct KST entry dates; positive
net40 PRICE and cost-inclusive-risk R means separately in BOTH years and overall;
top positive coin contribution<=30%. Rank min(yearly mean of KST-date meanR),
policy-name tie, one per entry key,<=3 per side,<=6 total. These are exploratory
independent diagnostics, not account gains. Freeze hold/exit before2024 GATE.
Compare chosen policies and predeclared FACTOR_UNION if>1 on DEV/GATE under
20/40bp guarded and diagnostic accounts. Same equity1, .5% entrySL risk budget,
2% aggregate reserve,30% coin notional,200% gross,6positions, no same-symbol
duplicate/opposite. KST observed-2% flatten/block; peakDD10% future risk half,
DD15% flatten/permanent halt restsplit. Budgets/15m observations are not fill
or loss guarantees. No profit cap.20/40bp roundtrip +10bp adverseSL/forced slip
+2bp per holding day stress funding, not historical funding.

Unchanged provisional gate: DEV/GATE each>=80executions, MDD<15%, noDD15halt,
positive20/40 returns; each20bp CAGR>=20%, PF>=1.15; each40bp PF>=1.05;
2022/2023/2024 net20 guarded KST-year results positive. Rank min DEV/GATE20CAGR,
then lower40MDD. Report growth, DD, PF, win/EV, losing streak, executableN,
rejections, utilization, concurrency and all-date daily goal frequencies.

## Pre-outcome calendar reporting correction

Audit found previous portfolio daily.csv omitted partial KST dates at the UTC
split boundaries. V3's851DEV/365GATE denominators are COMPLETE dates only,
not all dates touched by the sample. Original files/history remain unchanged.
This is a reporting defect, not an economic hypothesis or result improvement.
V6 adds an explicit --all-kst-days reporting option; older default retained for
reproduction. Every intersected KST date in [start,end) counts, including both
partial edge dates, inactivity and post-halt days. Flag partial days and their
covered hours; do not describe them as full24h observations. No annualization
of a partial-day return. Calendar daily return product must reconcile final cash.
Trades, order/exits, risks, guard triggers, cash/CAGR/MDD/fees remain identical
between calendar reporting modes. Keep original UTC cuts/universe for comparison.
DEV has853 intersected dates (851full+2partial); GATE367 (365full+2partial),
with the final partial date in the next KST year explicitly labelled.
Goal frequency denominator includes these dates, never only active/trading days.
Older curve-derived goal re-audits may be added without any market rerun;
preserve original values and mark old complete-only denominators explicitly.

## Execution and next falsification

Separate PLAN commit before code/marker. Tests must independently exercise OLS
reference calculations, impulse-excluded fitting, future perturbation, missing
BTC/gaps/zero variance, mirrored lag/flow, known entry gaps, actual-fill stops,
cooldown, annual price AND R and all96 cells; reuse official checksum/cache and
chronology regressions. Calendar tests must prove edge-day profits/costs retained,
inactivity/halts counted, daily product equals final equity, unchanged trades
and risk outcomes. Execute actual CI canonical smoke before trusting large runs.
Launch only this branch's explicit marker; no duplicate run/shared watcher group.
Preserve all raw DEV ledgers, parameters, source hashes, minute evidence, account
trades/daily curves and actual logs/failures on separate GitHub research branches.
Update central research/CONTINUATION.json with actual commits/run/result paths.

A historical pass remains PROVISIONAL. Freeze candidate before neighbour rules,
stronger costs/slip, actual1m/3m delayed entry, time-block dependence and meaningful
new forward observations. Whole-account daily+0.7% to+2% remains the user's goal;
small positive CAGR/PF, one-month win or endless-search lucky winner is insufficient.
If rejected, decompose numerically and preregister/implement/test/actually execute
the next economic hypothesis. Never stop solely because this candidate failed.
