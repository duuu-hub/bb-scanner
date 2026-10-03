# V21 Contract–Mark Dislocation Reversal — Frozen Research Plan

## Status and prior result

Preregistered before implementation, source probing, or any V21 market outcome.

V20 actual Actions run `37116650810` completed 501 tests, the canonical chronology smoke, eight DEV shards and all 96 frozen policies. Zero policy survived. Every cell had negative net40 risk-normalized R; only one cell had positive mean price return, but it contained two outcomes from two symbols, 100% contribution concentration and negative R. V20 has no account or daily-target result. Its full numerical audit is frozen in `research/session-opening-range-sweep-v20/V20_ACTUAL_REJECTION.json`.

As V20 preregistered, the opening-range family is retired. V21 does not adjust an opening width, session window, boundary or reversal direction.

## Distinct economic hypothesis

Binance's transaction-price candle can temporarily extend farther than the exchange's contemporaneous mark-price candle during aggressive futures-only impact. If that contract-only extension is large, the mark price does not confirm it, and the contract closes back toward the mark while aggressor flow is still concentrated in the extension direction, the tail may reflect forced or liquidity-taking inventory rather than a durable move in reference value. Enter at the next open against the contract-only extension.

This is a falsifiable proxy, not proof of liquidation or order-book state. It is distinct from:

- V5's sweep of a lagged local price range, because V21 requires a same-timestamp external mark-price benchmark;
- V8's mark-versus-index premium normalization, because V21 compares traded contract OHLC with mark-price OHLC and does not require an extreme or normalizing mark-index basis;
- V17's session VWAP failed auction and V19/V20 opening ranges, because V21 has no session anchor, session range or session VWAP;
- V11/V12's price/taker cascade and absorption families, because a contract-only excursion unconfirmed by the mark candle is mandatory.

If V21 fails, do not reinterpret mark-confirmed moves or locally tune dislocation thresholds from its outcomes.

## Frozen sources and causal alignment

Price/flow/universe source remains the immutable official Binance USD-M 15-minute source run `36095439671`, eight shards, 856 source files, context SHA256 `4cbf469c0c0d4efa9c63d19c9650bbaf1e8ded8f38bc4195ec2d702d2eea9614`. DEV is 2021-09 through 2023 only. The 2024 gate and 2025 through 2026-08 comparisons are already observed and cannot tune V21; September is not independent proof.

New official source to probe and preserve before DEV:

`https://data.binance.vision/data/futures/um/monthly/markPriceKlines/{SYMBOL}/15m/{SYMBOL}-15m-{YYYY-MM}.zip`

Require the adjacent `.CHECKSUM`, exact SHA256 match, original ZIP bytes and a verified parsed cache. Validate the documented 12-field kline geometry, finite positive OHLC, high/low consistency, integer unique increasing 15-minute UTC open times and exact symbol/month coverage. A mark candle is available only after it closes. Exact timestamp equality with the transaction-price candle is required; no nearest match, forward fill, current API substitution, zero imputation or cross-gap carry is allowed. Missing, malformed, checksum-failed or misaligned mark data is `MARK_DATA_GAP`, excluded and counted. Transient source failure halts with partial evidence rather than becoming a zero signal.

Before selection, known aligned mark coverage must be at least 95% of otherwise eligible bars separately in partial 2021, 2022 and 2023. Failure is `SOURCE_COVERAGE_INSUFFICIENT`, not an alpha result.

## Frozen causal signal and 16-entry grid

All features use a fully closed 15-minute event bar `i`; entry is the next exact contiguous 15-minute open.

Shared requirements:

1. Prior transaction-price ATR14 and prior 96-bar mean quote turnover exclude event bar `i` and reset after gaps.
2. Listing/liquidity eligibility is unchanged: at least 30 elapsed and observed source days, and closed rolling 24-hour quote turnover at least $20m.
3. Mark and contract event candles share the exact open timestamp and close before the decision time.
4. Contract quote turnover is at least the registered multiple of its shifted prior mean.
5. The mark candle's own directional range from prior mark close is no more than 1.0 prior transaction ATR; this prevents labeling a reference-price shock as contract-only impact.
6. A favorable next-open catch-up gap greater than 0.5% is excluded; an adverse gap is retained.

Entry grid: side × contract-only excursion × close-to-mark tolerance × volume multiple.

- side: LONG after downside excursion, SHORT after upside excursion;
- excursion beyond the same-timestamp mark extreme: 0.10 or 0.25 prior ATR;
- absolute contract-close minus mark-close tolerance: at most 0.10 or 0.25 prior ATR;
- contract quote-volume multiple: 1.25× or 1.75×.

Total entry configurations: `2 × 2 × 2 × 2 = 16`.

For SHORT, contract high must exceed mark high by the registered excursion, contract close must be within the registered tolerance of mark close, the contract candle must close below its midpoint, and taker-buy quote share must be at least 55%. LONG mirrors this: contract low below mark low, close near mark close and above the contract midpoint, with taker-buy quote share at most 45%. These flow conditions deliberately require aggression in the failed excursion direction.

Only the first qualifying event after 16 signal bars per symbol/configuration is eligible. Cooldown is fixed from intent time and cannot depend on exit or profit. Require contract and mark event OHLC plus all prior inputs to be invariant to future-data perturbation.

## Entry, risk and 96 policy cells

Enter reversal at the next contiguous transaction-price open. Structural stop is 0.10 prior ATR beyond the contract event extreme, with a 0.5% actual-fill risk floor and 6% cap. Freeze the event mark close as a structural target; exclude that target only when it is not favorable from actual entry.

For every entry configuration:

- maximum hold: 16 or 32 bars (4h or 8h);
- exit: frozen event mark close, 1.5R, or 2.5R.

Total DEV policies: `16 × 2 × 3 = 96`. No other excursion, tolerance, flow, volume, stop, hold or exit value may be searched after outcomes. Error recovery with identical economics is not a new hypothesis.

## Frozen DEV screen

Preserve all 96 cells including zero-observation cells. Reject unless all pass:

- resolved N at least 300;
- at least 60 symbols;
- top-symbol positive-contribution share at most 30%;
- global net40 mean bp above zero and net40 R above zero;
- separately for 2021, 2022 and 2023: N at least 30, at least 20 active KST dates, positive net40 mean bp, positive net40 R and positive equal-date diagnostic R.

If multiple cells survive, freeze a non-isolated region and candidates before any 2024 processing. PF-only, sparse, single-year or boundary results are failures.

## Execution and identical account contract

Use the shared official Binance 1-minute chronology: pre-entry exits ignored; every entry-minute exit touch is loss; established same-minute TP/SL collision is loss; only a proven earlier minute exit is honored. Missing/malformed/mismatched minute data becomes an explicit exclusion, never an invented outcome. Preserve original ZIPs, checksums, slices and hashes.

For frozen DEV survivors only, compare identical accounts at 20bp and 40bp round-trip costs, 10bp adverse stop/forced-exit slippage and 2bp funding stress per holding day:

- starting equity 1;
- 0.5% risk per trade, 2% aggregate reserved risk;
- 30% symbol nominal cap, 200% gross cap;
- at most six positions, no same-symbol duplicate or opposite position;
- KST calendar-day -2% flatten and block;
- peak drawdown 10% halves future risk; 15% flattens and halts the split;
- no profit cap.

Daily +0.7% and +2% rates use all intersected KST calendar dates, including inactive and post-halt dates. These are research targets, not fill-price or return guarantees.

## Mandatory validation and continuation

Before market DEV: validate exact 16/96 geometry; archive path/schema/checksum and cache corruption; exact close-time alignment; missing-month and gap behavior; mirrored contract/mark excursion; mark-range guard; close tolerance; flow in failed direction; shifted ATR/volume; next-open gap; actual-fill stop/frozen mark target; future perturbation; fixed cooldown; all-96 selection; source-coverage gate; shared chronology/account/calendar invariants; and an eight-shard synthetic pipeline.

Preserve every failure, parameter, source hash, exclusion, log, 96-cell table and account output on research branches. A workflow success means only that computation completed. Historical survival would still require a prior-frozen candidate, neighboring settings, 60/80bp costs, stronger slippage, actual +1/+3 minute entry delays, time-block dependence, recent data and genuinely forward evidence before any promotion.
