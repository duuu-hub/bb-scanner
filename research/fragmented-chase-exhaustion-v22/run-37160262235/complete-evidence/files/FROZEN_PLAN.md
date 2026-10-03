# V22 Fragmented-Chase Exhaustion Reversal — Frozen Research Plan

## Status and prior failure

Preregistered after reading V21's fixed result and before implementing or observing any V22 outcome. V21 actual run `37138299485` completed 519 registered tests, eight DEV shards, 856 source symbols, 4,687 checksum-validated official mark months and all 96 cells. Mark coverage passed, but every cell had negative net40 price expectancy and negative risk-normalized R in the aggregate and separately in 2021, 2022 and 2023. The least-bad mean was -38.8247 bp/trade; zero policies survived. V21 is retired and will not be inverted or threshold-tuned.

## Distinct economic hypothesis

During a large directional 15-minute shock, unusually high quote turnover can be composed either of large informed trades or a burst of many unusually small trades. A simultaneous collapse in average quote value per trade, explosion in trade count, and extreme taker direction is a falsifiable proxy for fragmented crowd chasing. If a separate, fully closed next bar rejects the shock, the crowded inventory may unwind over the following hours.

This is not a claim about trader identity. It is distinct from V21 because mark price is not used; from V11/V12 because average quote value per trade and trade-count fragmentation are mandatory, and entry requires a separate post-event reversal bar; and from session/range/breadth families because there is no session anchor, market-wide breadth, opening range or external reference price.

## Frozen source and causal features

Use immutable Binance USD-M 15-minute source run `36095439671`, eight shards and 856 files. DEV is 2021-09 through 2023. The already observed 2024 and 2025 through 2026-08 periods cannot tune V22, and September alone is not independent proof.

For event bar `i`, every rolling feature is shifted and uses only bars through `i-1`, restarting after gaps:

- ATR14;
- 96-bar mean quote turnover;
- 96-bar median trade count;
- 96-bar median average quote value per trade, where average trade value is quote turnover divided by the positive integer trade count.

Reject zero, noninteger or impossible trade counts and nonfinite values. Require at least 30 elapsed and observed source days, closed rolling 24-hour quote turnover of at least $20m, event quote turnover at least 1.5 times its shifted mean, and event trade count at least 2.0 times its shifted median.

## Frozen 16-entry grid

Entry configurations are side × event shock × fragmented average-size ratio × taker-share threshold:

- side: LONG after a downside chase, SHORT after an upside chase;
- absolute event close-to-prior-close shock: at least 1.50 or 2.25 prior ATR;
- event average quote value per trade divided by shifted 96-bar median: at most 0.65 or 0.85;
- failed-direction taker share: SHORT requires buy share at least 0.60 or 0.67; LONG requires buy share at most 0.40 or 0.33.

Total: `2 × 2 × 2 × 2 = 16` entry configurations. The event must close in its shock direction and in the outer 25% of its range.

Confirmation bar `i+1` must be exact and contiguous, fully closed, have an opposite-direction body, close back through at least 35% of the event's high-low range, and not make a new extreme in the shock direction by more than 0.10 prior ATR. Entry is the exact `i+2` open. A favorable entry gap above 0.5% is excluded; an adverse gap is retained. Cooldown is 16 event bars from intent time and cannot depend on outcome.

## Frozen risk and 96 policies

The structural stop is 0.10 prior ATR beyond the more adverse of the event and confirmation extremes, with a 0.5% actual-fill risk floor and 6% cap. Freeze the event midpoint as a structural mean-reversion target; exclude that target when it is not favorable from actual entry.

For every entry configuration:

- hold: 16 or 32 bars;
- exit: frozen event midpoint, 1.5R, or 2.5R.

Total DEV cells: `16 × 2 × 3 = 96`. No other shock, fragmentation, trade-count, flow, confirmation, gap, stop, hold or exit value may be searched after outcomes.

## Frozen selection and account contract

Preserve all 96 cells including empty cells. Reject unless N ≥300, symbols ≥60, top-symbol positive-contribution share ≤30%, aggregate net40 mean bp and net40 R are positive, and 2021, 2022 and 2023 each have N ≥30, at least 20 active KST dates, positive net40 mean bp, positive net40 R and positive equal-date diagnostic R. Freeze any non-isolated survivors before processing 2024.

Only frozen DEV survivors may use the common account engine: identical starting equity, 0.5% trade risk, 2% aggregate risk, 30% symbol nominal cap, 200% gross cap, six positions, no same-symbol duplicate/opposite entry, KST -2% daily flatten/block, 10% peak-DD risk halving and 15% flatten/halt. Compare 20/40bp round-trip costs, 10bp stop/forced-exit slippage and 2bp funding stress per holding day. Daily +0.7% and +2% rates use all intersected KST calendar dates including inactive and post-halt days.

Official Binance 1-minute chronology remains mandatory: entry-minute exit touch and same-minute TP/SL ambiguity are losses; only proven earlier exits count; missing or mismatched minute evidence is explicitly excluded. Workflow success is computation evidence, never a profit claim.

## Mandatory validation and continuation

Before market DEV, test trade-count schema/integer validation, shifted average-trade-size baseline, gap resets, mirrored long/short shock and flow, separate confirmation timing, future-data perturbation, next-open entry, favorable/adverse gaps, stop floor/cap, midpoint target, fixed cooldown, exact 16/96 geometry, all-cell selection, canonical chronology/account/calendar rules and an eight-shard synthetic pipeline.

If no DEV policy survives, preserve the numerical failure and move to a genuinely different preregistered mechanism. If a candidate survives, freeze it before neighbor checks, 60/80bp costs, stronger slippage, +1/+3 minute entry delays, time-block dependence, recent data and forward evidence.
