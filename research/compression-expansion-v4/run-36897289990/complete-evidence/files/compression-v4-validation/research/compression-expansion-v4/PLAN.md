# Compression Expansion V4: preregistered trend mechanism

Frozen before any V4 market outcomes, 2026-10-01 UTC / 2026-10-02 KST.
Research-only branch: research-compression-expansion-v4. Parent code:
84fe6a116dd21fec405120cfe2c4ad38cf04a65a. Never main/orders/live/watcher changes.

## Prior evidence and economic hypothesis

V3 confirmed shock reversion ran 96 DEV policy cells and eight account scenarios.
It produced zero strict account survivors. The 20bp guarded DEV account returned
+0.3490% in 851 full KST dates, CAGR 0.1495%, MDD 7.0656%, with 213 executed trades
and 85.90% no-entry dates. The 2024 historical gate lost 2.1032% at 20bp and 4.3870%
at 40bp; only 10/365 dates reached +0.7%. The 214 gate independent intents became
138 executions, with 76 risk/exposure rejections. Eight arithmetic audits passed
and selected-ledger chronology exclusions were zero. Full results remain on
research-shock-confirm-v3-results-36866930929 and the original failed trials stay
preserved. Selection of independent positive returns did not create account profit.

New hypothesis: moderate directional expansion out of recent volatility
compression, accompanied by unusually high actual quote turnover and aggressive
trade flow, may identify a developing trend more often than rare crash reversals.
Previous compression is measured BEFORE the breakout candle, so the candle cannot
create its own favourable baseline. This is a hypothesis, not established alpha.
The repository's external Donchian/compression studies are related mechanisms;
V4 tests a distinct closed-15m compression/volume/flow combination, not an
independent discovery simply because it uses another label.
The academic motivation is intraday momentum conditional on volume/volatility
(https://onlinelibrary.wiley.com/doi/10.1111/fire.12290); its findings do not validate
this rule, the alt universe, costs or our daily account objective.

## Frozen inputs and data boundaries

Same immutable source run 36095439671, all eight official Binance UM 15m shards,
delisted-inclusive historical catalogue. Coins require >=30 observable source
days before 2024 and >=$20m known closed-24h quote turnover at entry signal.
BTC comes from preserved day-edge-v2-btc artifact, run 36858492497.
DEV: 2021-09 through 2023; arrays physically cut before 2024.
GATE: 2024; arrays physically cut before 2025. GATE is a historically observed
research gate, NOT untouched independent evidence. No 2025-2026 or September
outcomes are used for selecting or changing V4 rules.
This line previously tested V1 one entry; V2 36 entries/144 fixed-time cells,
27 account policies/216 scenarios; V3 16 entries/96 canonical DEV cells and
one account policy/eight scenarios. Other conversation studies are additional
multiple-testing exposure. Do not call repeated favourable backtests proof.

## Frozen 16 entry settings x 2 holds x 3 exits = 96 DEV policies

Entry settings are side LONG/SHORT x prior breakout window 16/48 bars (4h/12h)
x prior compression ratio ceiling 0.50/0.75 x BTC context ANY/ALIGN4H.

At closed 15m bar i:
- TR[k] = max(high-low, abs(high-prior-close), abs(low-prior-close)).
- Compression = mean(TR of i-4..i-1) / mean(TR of i-96..i-1).
- Current close breaks max high / min low of the preceding 16/48 bars.
- Current quote turnover >=2 times preceding-96-bar mean quote turnover.
- Actual taker-buy-quote / total-quote >=55% LONG, <=45% SHORT.
- Candle closes in its top 20% LONG / bottom 20% SHORT, with a signed
  positive candle body and signed positive close-to-previous-close return.
- Absolute current 15m close-to-close move <=3%, separating moderate
  trend beginnings from the violent shock family.
- Previous closed EMA50 is on the entry side of price and its signed
  eight-bar slope is positive. EMAs, TR and windows restart after gaps.
- ALIGN4H additionally requires signed same-timestamp closed BTC4h return >0.
  ANY does not use missing BTC as a false zero; BTC context data is still audited.
- Trigger state onset and fixed 16-bar intent cooldown per coin/configuration.
  Never suppress using future exit outcomes or use future-only liquidity.

Enter next 15m OPEN; decision timestamp is prior bar close. No implied promise
that Telegram/real execution reaches that price. Subsequent finalist validation
must use actual 1m/3m delayed entries; this first research batch does not claim
delay robustness.
Structural SL uses low of current and prior four closed bars minus 0.25 prior
ATR14 for LONG; mirror SHORT. At actual entry: reject invalid-side stops,
minimum distance 0.5% of entry, reject >8%, reject nonpositive levels.
Simultaneous intent score = signed breakout excess/prior ATR14 * sqrt(known
current volume multiple). This score is fixed before outcomes.
Hold ceilings: 24/48 bars = 6h/12h.
Exits: TP2, TP3, or closed-15m 3ATR trail activated after one holding hour.
Every policy retains initial SL and time OPEN expiry.
Use unchanged canonical official-1m entry-bar / TP+SL collision ordering;
entry-minute exit touch, including TP-only, means conservative SL/LOSS.
Ignore pre-entry touches. Missing/mismatched official data is excluded, counted
and recorded per symbol/entry/policy; never guessed as a win or loss.
MDD/risk controls are observed at 15m account marks, not guaranteed intrabar caps.

## DEV selection and actual account verification

Calculate all 96 canonical DEV cells at 40bp ordinary round trip, plus 10bp
adverse SL/forced-exit slip and 2bp per holding day funding stress. The latter
is a stress assumption, NOT actual historical funding cash flows.
Keep zero-observation cells, all 96 diagnostic/annual rows, all original signal
ledgers, exact input hashes, settings, per-minute input provenance and exclusions.

A seed must have >=300 outcomes, >=10 coins, >=80 outcomes AND >=60 distinct KST
entry dates in EACH full year 2022 and 2023. Require positive net40 mean price
return and positive mean net40/cost-inclusive-stop-risk in both years.
Top coin positive net contribution share <=30%.
Rank by smaller 2022/2023 mean of per-KST-entry-date mean net40 R, then policy
name. Daily aggregation here is a dependence diagnostic, NOT account daily profit.
Choose one policy per entry key, <=3 per side and <=6 total.
Freeze selected policies, holds and exits before inspecting GATE outcomes.

Replay selected policies and a predeclared union on DEV/GATE, ordinary 20/40bp,
guarded and diagnostic accounts. Union same-symbol/time tie uses frozen DEV
selection priority. Strict provisional survivor criteria stay unchanged:
20bp CAGR>=20% and PF>=1.15 in each DEV/GATE, 40bp positive and PF>=1.05,
>=80 executed trades each, MDD<15%, no 15% halt, positive complete 2022/23/24
20bp guarded years. Account CAGR is primary ranking; diagnostic PF/EV isn't.
If no seed qualifies, preserve all 96 cells and preregister another mechanism.
If a seed survives, it is provisional: neighbours, cost/slip stress, real entry
delay, time-block dependence, already-seen comparative years and new fixed
forward data remain mandatory. A one-month recent check alone is insufficient.

## Frozen account contract and objective gap

Start equity 1, entry reserved loss budget 0.5%, aggregate 2%, symbol notional
30%, gross 200%, six positions; same-symbol duplication/opposition forbidden.
KST date -2% observation triggers flatten/block; peak DD10% halves future risk,
DD15% flattens/halts rest split. No reset to hide loss and no positive profit cap.
Funding reserve reflects intended maximum holding time. Caps are risk budgets,
not guaranteed fill prices/max realized loss. Equal capital/cost/universe/risk
for all accounts. Report net return/CAGR, 15m MTM MDD, executed N, PF/win/EV,
loss streak, concurrency, utilization, full KST dates including inactive/halted,
and +0.7%/+2% day rates. Small positive CAGR is NOT the requested daily result.

## Durable evidence, failures and implementation recovery

Preserve PLAN commit separately before executable trigger. Validation must cover
future perturbations, excluded current-bar baselines, gaps, side symmetry,
actual-fill structural risk, fixed cooldown, missing source rejection, all
96 cells/selection integrity, canonical chronology and account arithmetic.
Source-cache hits must be explicit; an empty/partial directory cannot count as
valid source coverage. Require eight complete matching DEV parts for selection.
Every input, selected ledger and output gets SHA256. Binance source documentation:
https://github.com/binance/binance-public-data (official fields/checksums).
Legacy 1m processed caches, where original ZIP checksum was not retained, must
be labelled as processed input hashes; never claim an absent original ZIP hash.
Such provenance limitations must be resolved before final deployment/acceptance.

Full DEV shards, selection, account trades/daily curves, validation logs and
partial errors are copied to dedicated git research result/evidence branches,
not left only in expiring Actions artifacts. Preserve V2/V3 existing account
details and previous validation when still available; record original artifact
IDs/digests and code/run IDs. Repairs stay the same hypothesis, not new discoveries.
Central research/CONTINUATION.json must point at actual branch/run/result locations.
