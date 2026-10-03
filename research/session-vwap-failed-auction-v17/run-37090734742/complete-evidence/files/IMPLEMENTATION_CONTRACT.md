# V17 Implementation Contract

This file binds the preregistered plan to executable names without changing its economics.

- Strategy: `scripts/session_vwap_failed_auction_v17.py`
- Validation: `scripts/validate_session_vwap_failed_auction_v17.py`
- Workflow: `.github/workflows/session-vwap-failed-auction-v17.yml`
- Trigger marker: `.research/session-vwap-failed-auction-v17-run`
- Frozen source run: `36095439671`; frozen BTC artifact run: `36858492497`
- Development policies: exactly 16 entry configurations × 2 holds × 3 exits = 96
- Entry data: closed 15m bars only; session anchors 00:00/08:00/16:00 UTC
- Session fair value: cumulative quote volume divided by cumulative base-volume proxy (quote volume / typical price) from the exact anchor; gaps invalidate the session segment
- Volume climax denominator: prior 96-bar rolling quote-volume median, shifted one bar
- Exhaustion: session bars 2–24, displacement/volume/wick/outer-quartile rules from PLAN
- Confirmation: separate later closed bar, one through four bars after the event, same contiguous session
- Entry: next contiguous open; favorable gap >0.5% excluded and adverse gap retained
- Stop: event extreme ±0.25 prior ATR, 0.5% floor, 6% cap
- Targets: frozen event-time VWAP, 1.5R, or 2.5R; holds 16/32 bars
- Official Binance 1m conservative chronology, costs, funding stress, account constraints, and all-calendar-day reporting are inherited unchanged
- Gate/accounts only run after a DEV survivor is frozen
- No market result, PF, or validation count is success without cost- and risk-constrained account evidence
