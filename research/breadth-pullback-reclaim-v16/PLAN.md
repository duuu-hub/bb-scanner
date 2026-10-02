# V16 Preregistered Plan — Breadth Expansion First Pullback Reclaim

Status: frozen before implementation and before any V16 market outcome.

## Economic hypothesis

V15 showed that entering immediately at the close of a whole-market breadth expansion pays an exhaustion premium: all 96 cells had negative cost-adjusted R and the best price-return cell failed badly in 2022. V16 tests a distinct two-stage mechanism, not a V15 parameter rescue:

> A broad directional impulse identifies a market-wide information shock. The first controlled counter-directional pullback removes the immediate chase premium; a later closed-bar reclaim identifies continuation demand/supply returning after liquidity refresh.

No V15, 2024, 2025–2026-08, or September outcome may be used to tune V16.

## Frozen universe and chronology

- Exact frozen source: Binance USD-M USDT perpetual 15m source run `36095439671`, 856 expected symbols, plus the already frozen BTC artifact.
- DEV: 2021-09 through 2023. DEV selection is frozen before any 2024 processing.
- 2024 is an already observed gate, not a clean holdout. Later seen periods are comparison only.
- All signals use closed 15m bars. Entry is the next contiguous 15m open after the reclaim bar.
- Official Binance 1m exit chronology and the repository's conservative entry-minute / same-minute TP-SL rules are mandatory.
- DATA_GAP, ENTRY_MISMATCH and EXIT_MISMATCH are excluded, counted and preserved.

## Stage A: breadth impulse event

For side `+1` use UP breadth; for side `-1` use DOWN breadth.

Fixed event grid:

- side: long, short
- closed return horizon: 16 bars, 96 bars
- breadth crossing threshold: 10%, 20%
- pullback depth: 0.5 ATR, 1.0 ATR

This is 2 × 2 × 2 × 2 = 16 entry configurations.

Impulse requirements at event bar `e`:

1. Both current and previous whole-market denominators are at least 30.
2. Previous directional fraction is below the configured threshold.
3. Current directional fraction is at or above it.
4. Directional breadth expanded by at least 5 percentage points.
5. The coin's exact closed horizon return is at least +2% / -2% for horizon 16 or +5% / -5% for horizon 96 in the trade direction.
6. Directional candle body; close beyond the prior four-bar high/low; CLV >= 0.75 for long or <= 0.25 for short.
7. Quote-volume multiple versus prior 96 closed bars >= 1.25.
8. Event-bar |coin 15m return| <= 8%; |BTC 15m return| <= 2%.

## Stage B: first controlled pullback and reclaim

- Watch only bars `e+1` through `e+12`; all bars must be contiguous.
- The first qualifying pullback touch must occur in bars `e+1` through `e+10`.
- Long touch: closed bar low <= event close - configured depth × event prior ATR.
- Short touch: closed bar high >= event close + configured depth × event prior ATR.
- Invalidation before reclaim: long low < event low - 0.25 ATR; short high > event high + 0.25 ATR.
- Reclaim must be on a later bar than the touch, no later than `e+12`.
- Long reclaim: directional up body, close above the immediately prior bar high, close above event candle midpoint, CLV >= 0.75.
- Short reclaim: directional down body, close below the immediately prior bar low, close below event candle midpoint, CLV <= 0.25.
- Reclaim quote-volume multiple >= 1.00; reclaim |coin 15m return| <= 8%; |BTC 15m return| <= 2%.
- Only the first valid reclaim per impulse is eligible. Same-symbol cooldown is 16 bars from entry decision.

This design forbids same-bar pullback/reclaim sequencing assumptions.

## Entry, stops, ranking and policy grid

- Entry: next contiguous 15m open.
- Favorable catch-up gap above 0.5% is excluded; adverse gaps are retained.
- Stop: pullback extreme minus/plus 0.25 event ATR, with minimum distance 0.5% and maximum 6%.
- Score fixed before outcome: `breadth_delta × sqrt(event_volume_multiple) × sqrt(reclaim_volume_multiple) × abs(event_horizon_return) / (risk_pct × sqrt(1 + delay_bars))`.
- Holds: 16 and 48 bars.
- Exits: TP2, TP3, TRAIL using the canonical engine.
- Total: 16 entries × 2 holds × 3 exits = 96 policies.

## Costs, selection and account contract

- Same 20/40 bp cost convention, adverse 10 bp stop/forced-exit slippage and 2 bp funding per holding day as the prior registered suite.
- DEV survival requires the repository's strict frequency, symbol concentration, positive net40 mean, positive net40 R, per-year and calendar-day rules. No 2024 outcome enters selection.
- Account comparison: starting equity 1; 0.5% risk/trade, 2% aggregate risk, 30% coin nominal, 200% gross, max six positions, no duplicate symbol, KST -2% daily flatten/block, DD10% half risk, DD15% flatten/halt.
- Calendar target rates include every KST calendar day, including idle and halted days.
- Goal remains whole-account net +0.7% to +2% per day. A small positive PF/EV is not success.

## Mandatory validation and preservation

- Causality, exact event/touch/reclaim geometry, same-bar rejection, missing-bar restart, future perturbation, frozen eight-shard global breadth, canonical 1m chronology and account invariants.
- Preserve parameters, code commit, source hashes, all 96 cells, exclusions, original minute evidence, failures and complete Actions logs on research branches.
