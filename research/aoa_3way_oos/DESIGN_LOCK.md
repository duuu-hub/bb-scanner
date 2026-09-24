# AOA 3-Way OOS Design Lock

Locked before viewing any 2022+ backtest result.

## Research question

Compare three implementations derived only from:
- AOA/Wonyotti XBTUSD execution history through 2021-12-31
- User BTCUSDT/ETHUSDT candle history through 2021-12-31 for rule extraction
- Existing frozen BTC/ETH core regime rule already defined in this repository

The forward test window is 2022-01-01 onward. No strategy threshold may be changed after the first successful OOS run. Engine-only bug fixes are allowed and must not change these constants.

## Shared execution assumptions

- Trading instrument: BTCUSDT only, so all three variants are directly comparable.
- Context: ETHUSDT is allowed only for the pre-existing BTC/ETH core direction used by HYBRID.
- Decision frequency: completed 15m candle.
- Execution: next 15m candle open.
- Maximum gross exposure: 1.00x equity.
- Base tranche: 0.25x current equity.
- One management action maximum per 15m bar.
- Costs are charged per traded notional:
  - BASE: 0.12% round trip = 0.06% per side.
  - STRESS: 0.25% round trip = 0.125% per side.
- No funding credit/debit is modeled. This deliberately keeps the test conservative and portable.
- OOS begins 2022-01-01 UTC and ends at the last stored candle.
- Metrics: compounded return, CAGR, Sharpe, MDD, active time, average/max gross exposure, turnover, leg count, win rate, PF, yearly return.

## Frozen AOA direction model

A logistic classifier is fit only on AOA ENTRY/FLIP_ENTRY decisions from 2019-07 through 2021-12.

Predictors are the causal market features already used in the AOA market-context study:
ret15m, ret1h, ret4h, ret24h, ret3d, ret7d, rv4h, rv24h, atr14_pct, atr96_pct,
bb_z20, bb_width20, rsi14, vol_z96, range_pos24h, dd7d, er24h, ema20_80,
trend_z24h, high_vol.

The model predicts P(LONG). It is refit on all pre-2022 observations only after the 2021 OOS direction result was observed.

## Variant 1 — AOA-CLONE

Purpose: preserve the major observed Wonyotti behavioral structure, including continuous exposure and early countertrend reversal.

- Start direction:
  - LONG if frozen P(LONG) >= 0.50, otherwise SHORT.
- After a completed profitable/losing leg, the strategy remains in the market by flipping direction when its flip rules trigger.
- Initial tranche:
  - 0.25x after a winning leg.
  - 0.1875x after a losing leg (75% of base), restored after a winning leg.
- Adverse equal-tranche ladder:
  - first add at -25 bp from current weighted average entry;
  - subsequent add levels every additional -17.5 bp;
  - equal tranche size;
  - no martingale;
  - max gross exposure 1.00x.
- Favorable equal-tranche pyramid:
  - first add at +25 bp;
  - subsequent add thresholds +30 bp and +35 bp;
  - equal tranche size;
  - only while signed 1h return is positive.
- Partial reduction:
  - reduce one tranche at +40 bp, then at each additional +40 bp;
  - never voluntarily reduce below one tranche before a final flip.
- Profitable flip:
  - current leg >= +60 bp;
  - signed prior 1h move in current direction >= +50 bp;
  - holding time >= 4 bars (1 hour).
- Damage control:
  - if leg <= -300 bp, reduce one tranche per decision opportunity until one tranche remains;
  - if leg <= -500 bp OR (holding time >= 192 bars / 48h AND leg < 0), close and flip.
- Flip execution is aggressive in the model: close current quantity and open the new initial tranche at the same next-bar open, paying turnover costs on both.

## Variant 2 — AOA-CORE

Purpose: keep the robust behavioral components while removing the most dangerous observed habits.

Entry requires all of:
- frozen direction probability:
  - P(LONG) >= 0.60 for LONG, or <= 0.40 for SHORT;
- prior 1h move is at least 50 bp in the opposite direction;
- Bollinger z-score is at least 0.75 sigma in the opposite direction.
Thus CORE only trades an explicit countertrend extreme and may remain flat.

Management:
- initial tranche 0.25x, or 0.175x after a losing leg (70% risk throttle);
- adverse equal-size adds at -25, -50, -75 bp;
- an adverse add is suppressed if prior signed 1h move is worse than -100 bp;
- favorable adds at +25, +50, +75 bp only when signed 1h move >= +50 bp;
- max exposure 1.00x;
- partial reduce one tranche at +40 bp, then +80 bp, while retaining at least one tranche;
- take the remaining position flat at +80 bp if no stronger favorable-pyramid action is pending;
- hard risk exit to flat at -200 bp;
- if the frozen entry model emits the opposite qualified CORE entry while a position is open, exit to flat first; a new opposite position may be entered only on a later qualified signal;
- no automatic LONG<->SHORT flip.

## Variant 3 — AOA-HYBRID

Purpose: combine the strongest AOA management ideas with the user's already-frozen BTC/ETH core direction rule.

Direction source is not retuned:
- daily BTC and ETH 30-day return signs must agree;
- average 30-day efficiency ratio >= 0.193654;
- common sign is the desired direction;
- only the previous fully completed UTC daily bar may set today's direction.

Execution/management:
- enter 0.25x when core direction becomes non-zero;
- use the same CORE equal-tranche adverse/favorable add logic and -200 bp hard risk exit;
- use CORE partial reduction logic;
- if core direction returns to zero, flatten;
- if core direction changes sign, close old direction and enter 0.25x new direction at the next open;
- no AOA logistic direction model is used for HYBRID.

## Interpretation rules

- CLONE is a research control, not the presumed best strategy.
- CORE is the extracted-behavior candidate.
- HYBRID tests whether Wonyotti-like management adds value when direction comes from an independent pre-existing alpha.
- No winner will be selected by one headline return alone. Stability by year, MDD, PF, turnover/cost drag, and exposure must be considered.
