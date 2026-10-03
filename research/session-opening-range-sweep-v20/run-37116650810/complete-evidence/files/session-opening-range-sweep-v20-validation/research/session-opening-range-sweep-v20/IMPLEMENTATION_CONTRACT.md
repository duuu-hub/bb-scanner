# V20 Implementation Contract

This file binds the implementation to the preregistered PLAN before any V20 market outcome is read.

- Exact sessions: 00:00, 08:00 and 16:00 UTC.
- Bars 0–1 freeze the opening high, low, midpoint and width.
- Width range: [0.25 prior ATR, registered cap 1.0 or 1.5 ATR].
- Acceptance: exactly the next 4 or 8 closed bars; every close inside the frozen range; at least one close strictly above and one strictly below its then-known causal session VWAP.
- Sweep: within the next four closed bars, wick at least 0.10 prior ATR outside the frozen boundary and close at least 0.05 prior ATR back inside it.
- Flow: registered shifted-volume multiple 1.25 or 1.75 and aggressor share in the swept direction (lower sweep buy share <=45%; upper sweep buy share >=55%).
- Entry: reversal at the next contiguous same-session open; favorable gap above 0.5% excluded, adverse gap retained.
- Stop: 0.10 prior ATR beyond the actual sweep extreme, 0.5% risk floor, 6% cap.
- Exits: frozen opening midpoint, opposite opening boundary or 2.0R; holds 16 or 32 bars. A structural target on the wrong side of actual entry is excluded for that policy.
- Grid: 2 reversal sides x 2 width caps x 2 acceptance windows x 2 volume gates = 16 entries; 96 policies.
- An outside close is not a V20 sweep and cannot overlap V19's accepted breakout definition.
- DEV, selection, official Binance 1m chronology, costs and account risk constraints are exactly those in PLAN.md.
- No V20 economic result was observed before this implementation contract and engine were committed.
