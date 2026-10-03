# V19 Implementation Contract

This file binds the implementation to the preregistered PLAN before any V19 market outcome is read.

- Exact sessions: 00:00, 08:00 and 16:00 UTC.
- Bars 0–1 freeze the opening high, low and width.
- Width range: [0.25 prior ATR, registered cap 1.0 or 1.5 ATR].
- Balance: exactly the next 4 or 8 closed bars; every close inside the frozen range; at least one close strictly above and one strictly below its then-known causal session VWAP.
- Breakout: within the next four closed bars, at least 0.10 prior ATR outside the frozen boundary, directional outer quartile, registered shifted-volume multiple 1.25 or 1.75, and taker-buy share >=55% LONG or <=45% SHORT.
- Entry: next contiguous same-session open; favorable gap above 0.5% excluded, adverse gap retained.
- Stop: 0.25 prior ATR inside the broken boundary, 0.5% risk floor, 6% cap.
- Exits: one frozen opening-range measured move, 1.5R or 2.5R; holds 16 or 32 bars.
- Grid: 2 sides x 2 width caps x 2 balance windows x 2 volume gates = 16 entries; 96 policies.
- DEV, selection, official Binance 1m chronology, costs and account risk constraints are exactly those in PLAN.md.
- No V19 economic result was observed before this implementation contract and engine were committed.
