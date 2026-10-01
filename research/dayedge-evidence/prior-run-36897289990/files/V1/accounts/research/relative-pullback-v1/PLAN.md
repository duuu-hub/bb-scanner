# Relative Trend Pullback V1 — frozen research plan

Frozen on 2026-10-01 before looking at any strategy outcomes. This is a new hypothesis, not a verified edge. Main and live/Demo trading configuration are outside this study.

## Objective and data

- Account net return, daily target attainment (+0.7% and +2%), drawdown, frequency, and inactive days are the primary outputs.
- Day trading: fixed maximum holding period 12 hours; user absolute ceiling 7 days.
- Evaluation: 2021-09-01 through 2026-09-01 exclusive, UTC. Indicators receive August 2021 warm-up data.
- TRAIN: before 2025-01-01 UTC. HOLDOUT: from that boundary. Fresh account in each split, no cross-boundary position. No holdout tuning.
- Fixed basket: BTC, ETH, BNB, XRP, ADA, DOGE, SOL, LTC, BCH, LINK, ETC, TRX, XLM, EOS, DOT, UNI, AAVE, AVAX (USDT linear perpetuals). BTC provides context and is not an entry instrument.
- The basket is a practical long-history sample; this is not a survivorship-bias-free historical exchange universe. Missing/delisted archives are recorded and never filled synthetically.
- Official Binance USD-M monthly 15m archives; official 1m archives resolve entry-bar exits and parent-bar TP/SL collisions. Downloaded ZIP hashes and missing months are recorded.

## One fixed signal family

All inputs below are available at the completed 15m signal candle CLOSE. Enter at the immediately following 15m OPEN. This is an OPEN taker model, not a post-only fill claim.

- BTC context: last completed 1H CLOSE above EMA200 and EMA50 higher than six completed hours ago for LONG; mirrored inequalities for SHORT.
- Coin trend: completed 1H EMA20 > EMA50 and CLOSE > EMA50 for LONG; mirrored for SHORT.
- Relative strength: coin's completed 4H price return minus BTC's same-window return >= +1 percentage point for LONG, <= -1 point for SHORT. This is excess return, not a fitted regression beta or cross-sectional rank.
- Pullback/recovery LONG: prior 15m LOW touches/breaches its EMA20, prior CLOSE <= its EMA20; signal candle CLOSE crosses above its EMA20 and above prior HIGH, and CLOSE > OPEN. SHORT mirrors this with prior HIGH, CLOSE >= EMA20, and signal CLOSE below EMA20/prior LOW and below OPEN.
- Stop: minimum LOW of prior/signal candles minus 0.1 * completed 1H ATR14 for LONG; mirrored high/buffer for SHORT.
- Require stop on the correct side of actual entry and stop distance 0.5%–3.0% of entry.
- TP: actual entry plus/minus 2 * actual stop distance. SL/TP fixed after fill; no hindsight trailing or retuning.
- Maximum hold: 48 * 15m = 12h. Close at that exact time's OPEN if neither SL nor TP occurred earlier.
- Simultaneous candidates ranked by absolute BTC-relative 4H excess return, then symbol. Generate all valid independent candidates; same-symbol overlap is rejected only by the account engine.
- Report LONG-only, SHORT-only, and BOTH as diagnostic views of this one frozen family. None is selected after seeing holdout.

## Execution integrity

- Gross short return is (entry - exit) / entry, using linear USDT perpetual P&L, never entry / exit - 1.
- Entry candle: any exit touch in the same entry 1m is a conservative LOSS. Later 1m exits use their proven chronology.
- Established parent-bar collision: official 1m chronology, same-1m both => LOSS. No parent-bar blanket SL shortcut.
- Parent/1m entry or exit mismatch, missing/malformed 1m, and unresolvable data gaps are explicit exclusions. Report counts.
- SL gap fill uses the adverse OPEN if already beyond the stop; TP fill is conservatively the fixed limit.
- Exit timestamps never precede entry; unknown single-touch parent-bar exit time is conservatively its end, so slots/capital are not released early.

## Frozen account rules

- Starting equity 1.0 per split/scenario; linear P&L, fees at entry and exit.
- Round-trip fee + ordinary slippage scenarios: 20bp and 40bp. Additional adverse stop/forced-close slippage: 10bp. Funding stress charge: 2bp per day of actual holding, regardless of direction. This is an explicit conservative assumption, not actual historical funding.
- Per-trade budget 0.5% of current equity, including projected stop fill, round-trip costs, and funding to maximum hold.
- Entry notional cap 30% of equity, gross exposure cap 200%, max 6 positions. Same-symbol additional/opposite entry forbidden.
- Aggregate reserved stop budgets <= 2% of current equity; scales proportionally when capacity is insufficient.
- Daily net liquidation-equity loss trigger -2% relative to Korea-time 00:00 equity: close all at the first observed 15m boundary, then no entries until the next Korea day.
- At a -10% drawdown from observed account high-water mark, latch reduced risk (0.25% per trade and 1% total open risk) for the remainder of that split. At -15%, flatten and halt for the remainder; no automatic restart.
- All account guards include unrealized P&L and estimated liquidation costs. Monitoring is at 15m boundaries, not a promise of an exact intraminute loss cap. Stop fills and adverse execution can exceed budgets. Report observed trigger overruns and observation frequency.
- No daily profit cap; +2% does not force liquidation or prevent a new valid signal.

## Report and decision discipline

- Evaluate all calendar days, including no-trade days; Korea-time complete days only. Show arithmetic daily mean, geometric daily growth, +0.7/+2 attainment, loss/no-trade days, worst day and quarterly/yearly net returns.
- Show account return, CAGR, 15m-observed MTM MDD, PF from realized account P&L, win rate, executable trades, average/max hold, max losing streak, time-weighted exposure and concurrency.
- Also report unguarded diagnostic account results so halting at -15% cannot conceal a weak edge; the guarded account remains the authorized operating model.
- A success-labelled workflow only means the calculation completed. It does not mean the strategy passed. No sizing increase or promotion to Demo/live from this study alone.
- Preserve all outputs/failures. Any fix to implementation semantics requires a labelled new run, without changing frozen signal thresholds in response to performance.
