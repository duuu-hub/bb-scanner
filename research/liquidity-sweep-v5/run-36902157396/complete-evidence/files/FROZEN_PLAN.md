# Liquidity Sweep Absorption V5: preregistered research plan

Frozen before ANY V5 outcome, 2026-10-01 UTC / 2026-10-02 KST.
Branch: research-liquidity-sweep-v5. Parent: a4f45fcb6e890dfb3da9806f1b38308939aefe2f.
Research only. Never modify main, live/demo orders, operating configuration or watcher state.

## Failure diagnosis and genuinely changed event

V4 actual run 36897289990 completed all eight DEV shards and 96 cells.
Zero selected seeds. All 96 net40 price means and global R means were negative;
all failed 2022 R. Range of net40 independent price mean: -134.5623 to -13.0059 bp.
The 48 strict-compression policies had only 55-125 observations; the 48 moderate
ones had 1,416-2,519 observations but still negative returns. There were 97,686
overlapping parameterized outcomes and 48 DATA_GAP exclusions covering two unique
coin/entry events. No V4 GATE/account policies ran. This is not account profit.
Full failure audit and the harmless-to-rejection per-year-price implementation
omission are preserved. V4 archival recovery is not another hypothesis.

Economic conjecture: a failed intrabar excursion beyond a known range while
aggressive trades favour that excursion may show price pressure being absorbed,
followed by reversion inside the range. Test the price/flow disagreement itself.
SHORT after a failed upper sweep despite net aggressive buying; LONG after a
failed lower sweep despite net aggressive selling. This is NOT the numerical
inverse of V4's successfully closed breakout events: V5 closes INSIDE the range.
This adaptive idea was motivated after observing V4 failure and counts in the
same cumulative search. It is not independent proof or a claimed new discovery.

Primary motivation/reference (not strategy validation): Cont/Kukanov/Stoikov,
The Price Impact of Order Book Events, https://arxiv.org/abs/1011.6402 .
That study concerns best-bid/ask order-flow imbalance in U.S. stocks; trade-volume
imbalance is a weaker proxy. Our candle taker-share cannot establish order-book
absorption, identify liquidation prints, or prove the proposed return mechanism.
The above is an explicit hypothesis to falsify, not a causal claim.

## Frozen inputs, seen periods and multiplicity

Use unchanged official Binance UM 15m source run 36095439671, eight original
shards/manifests, delisted-inclusive catalogue. Verify all file catalogues, original
timestamp/row statistics and 256 prior processed CSV SHA256 hashes before each
DEV/GATE/account stage. FROZEN_INPUT_HASHES.json is byte-identical to V4 baseline.
Quote volume and actual taker-buy quote volume are official kline fields:
https://github.com/binance/binance-public-data .
Coins require >=30 source days before 2024 and >=$20m closed-24h known turnover at
the signal. BTC context: preserved day-edge-v2-btc artifact/run 36858492497.

DEV 2021-09 through 2023, arrays physically cut before 2024.
2024 GATE is already seen research data, not pristine holdout; cut before 2025.
2025-2026-08 are already-seen comparison periods and are NOT used for retuning.
September may have been seen in another chat and is not sufficient independent
evidence. Freeze finalist before opening recent data; then require new forward data.
Known cumulative line: V1 one entry; V2 36 entries/144 fixed-time cells and 27
canonical account policies/216 scenarios; V3 16 entries/96 cells/eight scenarios;
V4 16 entries/96 cells/zero account scenarios. Other chats add search exposure.
V5 plans 16 entry settings/96 policy cells; repairs never count as discoveries.

## Frozen 16 entries x two holds x three exits = 96 policies

Entry settings: LONG/SHORT x prior range 48/96 closed bars (12h/24h)
x opposing aggressive-flow threshold 55%/60% x BTC ANY/ALIGN4H.

At fully CLOSED 15m candle i:
- Prior range = maximum high and minimum low of preceding 48/96 candles,
  excluding i. ATR14 and preceding-96 mean quote turnover also exclude i.
- Prior range, ATR, volume features and BTC returns restart after a time gap.
- Candle open and close are strictly inside this prior range.
- SHORT: high >= prior range high +0.1 prior ATR; close < open;
  upper wick (high-max(open,close))/(high-low) >=50%; taker-buy quote share
  >=55% or >=60% (opposite to intended short direction).
- LONG: low <= prior range low -0.1 prior ATR; close > open;
  lower wick (min(open,close)-low)/(high-low) >=50%; taker-buy quote share
  <=45% or <=40% (opposite to intended long direction).
- Current quote turnover >=1.5 times its prior 96-candle mean.
- No compression condition, EMA filter or retrospective event ranking.
- ALIGN4H: same-timestamp CLOSED BTC four-hour return in entry direction >0.
  ANY never treats missing BTC as zero. All BTC inputs remain audited.
- Signal state onset plus fixed 16-candle per-coin/config intent cooldown,
  independent of future outcomes.

Enter next 15m OPEN, decision prior close. No claim actual alerts/fills achieve it.
SL = current closed candle low -0.25 prior ATR for LONG; high +0.25 ATR for SHORT.
At actual entry reject invalid-side/nonpositive SL, enforce minimum stop distance
0.5% of entry and reject above 8%. Wider actual gap changes risk/TP correctly.
Pre-entry simultaneous score = distance closed back inside swept boundary / prior
ATR * sqrt(current quote-volume multiple). Score never depends on later returns.
Hold ceilings 24/48 candles =6h/12h. Exits TP2, TP3 or closed-candle 3ATR trail
after one holding hour; retain initial stop and exact expiry OPEN.

Unchanged shared canonical resolution: official Binance 1m entry bar / parent
TP+SL collision. Pre-entry touches ignored; entry-minute any exit touch including
TP-only =>SL/LOSS, established same-minute collision =>SL/LOSS. Proven earlier
1m exit honoured. Missing/malformed/misaligned or mismatched official inputs
are explicit DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH exclusions; never fabricate exits.

## Source integrity improvement, without chronology retuning

V4 retained only processed minute hashes. V5 requests original official 1m ZIP
and its adjacent .CHECKSUM and verifies SHA256 before trusting parsed candles.
Retain raw ZIP SHA, official checksum text/hash, source URL and validated NPZ/array
SHA. Cache reuse requires a verified sidecar and exact NPZ SHA; legacy caches
without original checksums are not falsely promoted. A verified-cache mismatch
fails closed instead of downloading a replacement that changes frozen observations.
Preserve each exact 15-minute authoritative slice actually used by chronology,
its SHA256 and month provenance. This allows replay even if the public monthly
archive changes. Raw processed 15m hashes are still labelled processed CSV hashes,
not absent original ZIP hashes.
A definitive 404/malformed/checksum discrepancy is excluded and counted. Exhausted
transient network/rate errors stop the shard with partial evidence for repair,
not a fabricated permanent DATA_GAP. No change to conservative execution order.

## Frozen DEV screen and account gate

Keep all 96 cells including zero N, original ledgers, parameters, input hashes,
monthly/slice minute provenance and every exclusion.
Net40 independent diagnostics: 20bp per entry/exit leg, additional 10bp adverse
SL/forced-exit slippage and 2bp per holding day funding stress (not actual funding).
Require >=300 observations, >=10 coins, >=80 observations and >=60 distinct KST
entry dates in each KST-labeled year 2022 and 2023, positive mean net40 PRICE
return AND mean net40/cost-inclusive-stop-risk separately in BOTH years,
positive overall net40 price/R, and top positive coin contribution <=30%.
This explicitly fixes the V4 annual-price check omission before V5 outcomes.
Rank by the smaller of two years' means of per-KST-entry-date mean R, policy-name
tie; select one policy per entry key, <=3 per side, <=6 total. Active-date means
are dependence diagnostics, not account returns. Freeze holds/exits before GATE.

Compare frozen selected policies and predeclared SWEEP_UNION on DEV/GATE under
20/40bp costs, guarded/diagnostic accounts. Same starting equity1; entry SL risk
budget0.5%, aggregate reserved2%, coin notional30%, gross200%, max6 positions,
same-symbol duplicate/opposite entries forbidden. KST -2% observed day flatten/
block; peak DD10% halves future entry risk and DD15% flattens/permanently halts
rest of split. No profit cap. These are budgets/15m observations, not guaranteed
intrabar fills or maximum losses. Reserve funding against actual max holding time.

Strict provisional gate unchanged: both DEV/GATE >=80 executed trades, MDD<15%,
no DD15% halt, positive returns20/40bp; 20bp CAGR>=20% and PF>=1.15 each period,
40bp PF>=1.05 each period; complete 2022/2023/2024 net20 guarded years positive.
Rank by min DEV/GATE20 CAGR then lower40 MDD. Report return/CAGR/MDD/PF/win/EV,
loss streak, actual executed N, risk/conflict rejections, exposure/concurrency,
and +0.7%/+2% goal rates on ALL KST calendar dates including inactive/halted days.

Strict pass is only provisional: freeze finalist, examine neighbouring rules,
stronger costs/stop slip, actual 1m/3m entry delays with official data, time-block
dependence and meaningful unseen forward observations. No trading deployment.
Small positive CAGR, scout EV, isolated PF or a one-month recent win is not the
user's whole-account daily +0.7% to +2% objective.

## Execution and durable records

Commit this PLAN separately before implementation/execution trigger. Tests must
prove no future leakage, excluded current baselines, gaps, mirrored wick/flow
semantics, no confirmed-breakout qualification, actual-fill structural stops,
fixed cooldown, annual price AND R/KST dates, complete 96 cells, checksum/cache
integrity and canonical chronology/account invariants.
Launch only the branch's explicit marker. No duplicate active study or shared
watcher concurrency. Preserve raw DEV trials, selected/account trades and daily
curves, hashes, actual logs, partial errors and failure diagnoses on git research
evidence branches. Skip log downloads for skipped jobs, allow raw ANSI log bytes.
Update central research/CONTINUATION.json with actual commit/run/result paths.
If no candidate passes, decompose failure numerically and preregister a new
economic mechanism; do not stop at one failed hypothesis.
