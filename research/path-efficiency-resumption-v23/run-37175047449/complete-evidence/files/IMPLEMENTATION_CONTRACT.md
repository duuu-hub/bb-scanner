# V23 Implementation Contract

This file freezes the executable interpretation of PLAN.md before market outcomes are observed.

- Sixteen entry configurations: side {long, short} × formation bars {8,16} × path efficiency {0.55,0.70} × confirmation {PRICE_ONLY,FLOW55}.
- Formation uses only completed closes through i-2; pause is i-1; confirmation is completed bar i; entry is the next bar open i+1.
- Formation move is 1.5 prior ATR and at least 1%, capped at 8%; at least 62.5% of returns agree with direction and one bar contributes at most 60% of gross path.
- Pause retraces 5–35%, stays beyond the formation midpoint, and has quote turnover no greater than its causal 96-bar shifted mean.
- Confirmation resumes direction, closes beyond pause open, and has turnover at least 1.25 times its causal shifted mean. FLOW55 additionally requires taker-buy share >=55% long or <=45% short.
- Favorable entry catch-up gap above 0.5% is excluded. First qualifying event consumes a fixed 16-bar cooldown.
- Stop is 0.1 prior ATR beyond pause extreme, floored at 0.5% fill risk and capped at 6%.
- Holds are 16 or 32 bars. Exits are 1.5R, 2.5R, or the repository canonical closed-bar TRAIL resolver.
- Costs, official one-minute chronology, source hashes, DEV/gate/account criteria, calendar-day target rates, and portfolio risk limits remain those in PLAN.md and central research rules.
- Shared V20 scan is wrapper-bound to the V23 context explicitly; BTC uses the source loader and source features.
