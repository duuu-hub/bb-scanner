# V18 Implementation Contract

This file maps the frozen plan to executable code before any V18 market outcome is observed.

## Signal and timing

- `scripts/session_opening_impulse_v18.py` constructs causal 00:00/08:00/16:00 UTC eight-hour sessions and session VWAP from closed 15-minute bars.
- The opening event is evaluated only when session bar 1 closes. Its displacement is measured from bar 0 open to bar 1 close using prior ATR.
- Both opening closes must be on the directional side of their then-known session VWAP; bar 1 must close in its directional outer quartile.
- `scripts/session_opening_breadth_v18.py` counts the frozen full-universe opening direction only at the bar-1 timestamp. A symbol contributes only with 30 observed days and a causal prior ATR. At least 30 eligible symbols are required before a fraction exists.
- A 10% or greater pullback must appear during the next four closed bars, remain on the opening side of the frozen event VWAP, and never exceed the registered 38.2% or 61.8% depth through confirmation.
- Confirmation is a distinct later directional bar closing beyond the preceding high/low. Entry is the next contiguous 15-minute open in the same session.
- A favorable entry gap above 0.5% is excluded and an adverse gap remains.

## Risk, exits and execution

- The structural stop is the pullback-to-confirmation extreme plus 0.25 prior ATR, then a 0.5% minimum risk floor and 6% maximum risk cap.
- Exit variants are the frozen one-impulse opening projection, 1.5R and 2.5R, each with 16- or 32-bar maximum hold.
- All resolved outcomes call the shared canonical engine. Ambiguous TP/SL and entry-minute chronology use official Binance 1-minute data; gaps and mismatches are explicit exclusions.
- DEV selection uses the exact plan gates: N 300, 60 symbols, 30 observations and 20 active KST dates per year, concentration at most 30%, and positive global/per-year cost-stressed diagnostics.
- Only a frozen DEV survivor permits 2024 processing. Final comparisons use the shared account engine and all KST calendar days under the fixed portfolio and drawdown controls.

## Immutable inputs and preservation

- The original 15-minute market catalogue is Actions run `36095439671`; BTC is the frozen V2 BTC artifact.
- `FROZEN_INPUT_HASHES.json` and `FROZEN_CONTEXT.json` must match their preregistered byte hashes before execution.
- Eight independent maps are required for each breadth aggregate and eight independent scan shards are required for each stage.
- Source checks, maps, breadth aggregates, full 96-cell DEV table, exclusions, official minute inputs/slices, job logs, code commit and every completed or failed artifact are retained on research evidence branches.

Passing validation proves implementation consistency only. It is not evidence of profitability or goal attainment.
