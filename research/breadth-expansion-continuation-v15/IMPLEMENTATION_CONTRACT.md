# V15 Implementation Contract

Frozen before the first V15 market execution.

- Hypothesis: a new whole-market directional breadth expansion plus same-direction local momentum continuation, economically distinct from V14 pressure-withdrawal reversal.
- Fixed grid: 16 entries = side {long,short} × horizon {16,96} × breadth crossing {10%,20%} × flow {PRICE_ONLY,FLOW55}; 2 holds × 3 exits = 96 policies.
- Shock thresholds: 2% over 16 bars and 5% over 96 bars.
- Breadth event: previous directional fraction below threshold, current fraction at/above it, expansion at least 5 percentage points, minimum 30 eligible coins in both bars.
- Local confirmation: exact current closed-horizon return in signal direction, directional candle and close beyond prior four-bar extreme, CLV 0.75/0.25, volume multiple 1.25, coin |15m| <=8%, BTC |15m| <=2%.
- Entry: next 15m open; favorable catch-up gap above 0.5% excluded, adverse gap retained.
- Stops/exits, official Binance 1m chronology, costs, account constraints, DEV/GATE order and success criteria are exactly those in PLAN.md and RESEARCH_RULES.md.
- Selection is frozen on DEV before any 2024 gate processing. No seen-period result may be called clean holdout evidence.
