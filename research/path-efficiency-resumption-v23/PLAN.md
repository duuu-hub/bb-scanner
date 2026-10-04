# V23 Path-Efficiency Trend Resumption — Frozen Research Plan

Frozen on 2026-10-04 UTC before V23 code, tests, launch, or any V23 market outcome.
Branch `research-path-efficiency-resumption-v23`; parent V22 rejection commit
`bed86f323386576f326e8a25a207644f13d33c6a`. Research only. Follow
`AGENTS.md`, `RESEARCH_RULES.md`, and the repository's mandatory official
Binance 1-minute chronology. Never modify main, real/Demo orders, live settings,
watcher state, or unrelated workflows.

## Previous failure and distinct economic hypothesis

V22 actual run `37166361208` completed 538 registered tests, eight DEV shards,
all 96 fixed cells, selection, and preservation. It produced only 934
parameterized outcomes. No cell reached N 300 or 60 symbols. The six positive
net40 price/R cells each contained one trade in one symbol, all in 2023. The
most frequent cell had N 80 across 56 symbols and lost 53.4653 bp/trade and
0.177625 R after costs. Zero policies survived; 2024 and account stages were
skipped. V22 is rejected and will not be loosened, inverted, or threshold-tuned.

V23 tests a different mechanism: a smooth, directionally efficient multi-bar
price path can indicate persistent execution rather than a one-bar shock. A
small countertrend pause that preserves most of that path, followed by a closed
directional resumption with renewed turnover, may continue for several hours.
The observable is the ratio of net directional log movement to total absolute
bar-to-bar log movement over a completed formation path.

This is not V11's immediate large-shock/taker cascade, V4's low-volatility
compression breakout, V13's frozen one-/three-day boundary retest, V18's fixed
session impulse, V1's EMA/BTC-relative pullback, or V22's fragmented shock
reversal. V23 requires no extreme single bar, session clock, cross-sectional
rank, BTC factor fit, mark price, funding, or inferred average trade size.
Persistent execution is an economic conjecture, not observed trader identity.
V1–V22 and their 96-cell searches are adaptive multiple testing; even a V23
historical pass would remain provisional.

## Frozen input and causal time contract

Use immutable Binance USD-M 15-minute source run `36095439671`, all eight
shards and 856 processed market files, the exact 256-file baseline hash
contract, and BTC artifact run `36858492497`. V23 copies V22's frozen catalogue
without changing source values. Processed CSV hashes are not absent original
Binance archive hashes. Every stage fails closed on a changed, missing,
duplicated, or omitted source. Preserve source checks, raw data provenance,
official one-minute ZIP/checksum evidence, exact used slices, exclusions,
partial failures, all trials, and code/run commits.

DEV is UTC [2021-09-01, 2024-01-01). 2024 is an already-observed research gate.
2025 through 2026-08 and September may already have been observed and cannot tune
V23 or be called pristine holdout. A candidate is frozen before any subsequent
recent/forward verification.

All rolling features restart after timestamp gaps. At the decision time require
at least 30 elapsed and observed source days and closed prior-24h quote turnover
at least $20m. BTC is retained only for common source/account compatibility; V23
entry logic does not use BTC. No future catalogue, interpolation, nearest-time
match, forward fill, or missing-as-zero value is allowed.

## Frozen formation and sixteen entry configurations

For decision bar `i`, the formation path is the completed closes from
`i-W-2` through `i-2`; bar `i-1` is a distinct pause and `i` is a distinct
confirmation. All timestamps must be exactly contiguous.

Entry grid is:

- side: LONG or SHORT;
- formation length `W`: 8 or 16 return intervals;
- path efficiency `E`: at least 0.55 or 0.70;
- confirmation: PRICE_ONLY or FLOW55.

Total: `2 × 2 × 2 × 2 = 16` entry configurations.

For formation log returns `r_j = log(close_j / close_{j-1})`, signed path
movement is `side * sum(r_j)`; path efficiency is that signed movement divided
by `sum(abs(r_j))`. Require:

- finite positive prices and prior ATR14 known before the pause;
- signed formation movement at least max(1.5 prior ATR / formation-end close,
  1.0% log return), and at most 8% log return;
- efficiency at least E;
- at least 62.5% of the W returns have the intended sign;
- no individual formation bar contributes more than 60% of total absolute path
  movement, preventing V11-style one-bar shock from defining the setup.

The fully closed pause bar `i-1` must move counter to the formation on a
close-to-close basis. Its retracement magnitude must be between 5% and 35% of
the frozen signed formation movement. Its adverse extreme must not cross the
formation midpoint. Pause quote turnover must not exceed the preceding
96-bar shifted mean. These rules test a shallow, lower-participation pause rather
than a new reversal search.

The fully closed confirmation bar `i` must have a directional body and
close-to-close move, close beyond the pause open in the formation direction,
and have quote turnover at least 1.25 times the preceding 96-bar shifted mean.
PRICE_ONLY adds no taker filter. FLOW55 requires taker-buy quote share at least
55% LONG or at most 45% SHORT. The formation, pause, and confirmation are always
separate closed intervals.

Use the first qualifying event per symbol/configuration, then a fixed 16-bar
intent cooldown independent of outcomes. Entry is exact next contiguous bar open.
Exclude a favorable catch-up gap over 0.5%; retain adverse gaps. Priority is
formation efficiency × signed formation movement × confirmation volume multiple,
all known at decision close.

## Frozen risk and 96 policies

Stop is 0.10 prior ATR beyond the pause adverse extreme. Apply an actual-entry
risk floor of 0.5% and reject risk above 6%. Stops and targets never use future
bars.

For each entry configuration:

- maximum hold: 16 or 32 bars;
- exit: 1.5R, 2.5R, or a closed-bar 2-ATR trailing stop that activates only
  after four completed holding bars and never loosens.

Total DEV cells: `16 × 2 × 3 = 96`. No other formation, efficiency, movement,
pause, turnover, flow, gap, stop, hold, or exit threshold may be searched after
V23 outcomes.

## Frozen selection and account contract

Preserve every cell including empty cells. Reject unless N >= 300, symbols >= 60,
top-symbol positive contribution share <= 30%, aggregate net40 mean bp and
net40 R are positive, and 2021, 2022, and 2023 each have N >= 30, at least 20
active KST dates, positive net40 mean bp, positive net40 R, and positive
equal-date diagnostic R. Freeze non-isolated survivors before processing 2024.

Only frozen DEV survivors use the common account engine: start equity 1,
0.5% trade risk, 2% aggregate risk, 30% symbol nominal cap, 200% gross cap,
six positions, no same-symbol duplicate/opposite entry, KST -2% daily
flatten/block, peak DD10% future-risk halving and DD15% flatten/halt. Compare
20/40bp round-trip costs, 10bp adverse stop/forced-exit slippage, and 2bp
funding stress per holding day. These are budgets and conservative execution
assumptions, not fill-price guarantees.

Official Binance one-minute chronology is mandatory: pre-entry touches ignored;
any entry-minute exit touch is SL/loss; established same-minute TP/SL ambiguity
is SL/loss; only proven earlier exits count. Missing/malformed/mismatched minute
evidence is explicitly excluded, counted, preserved, and reviewed.

Report account return/CAGR, MDD, PF, win rate, executable trades, losing streak,
concurrency, utilization, calendar-day +0.7%/+2% attainment including inactive
and post-halt days, and all exclusion counts. No small positive diagnostic may be
presented as the user's daily goal. If V23 fails, preserve it, decompose the
failure numerically, and preregister a genuinely different next hypothesis.
