# V21 implementation contract

This file binds the implementation to `PLAN.md`; it adds no tunable economics.

- Frozen transaction source: run `36095439671`, eight shards, 856 symbols,
  verified by the copied immutable V20 source catalogue and hashes.
- New reference source: official Binance USD-M monthly `markPriceKlines`, 15m,
  adjacent checksum required. Original ZIP/checksum, parsed cache and metadata
  are retained. Missing or invalid data are explicit gaps; transient failures halt.
- Exact contract/mark timestamps only; no interpolation, nearest match or fill.
- Grid: 16 entries, holds 16/32 bars, exits frozen mark close/1.5R/2.5R,
  exactly 96 DEV cells.
- Entry is the next contiguous open. Favorable gap above 0.5% is excluded;
  adverse gap remains. Stop is 0.10 prior ATR beyond the event extreme with a
  0.5% actual-fill floor and 6% cap.
- Fixed 16-bar intent cooldown. Official 1m chronology and the shared account
  engine remain authoritative.
- Selection requires at least 95% aligned mark coverage separately in 2021,
  2022 and 2023 plus every frozen sample, breadth, concentration, price, R and
  equal-date-R gate in the plan.
- GATE and account jobs run only after DEV policies are frozen. Workflow success
  is computation evidence, never a profitability or daily-target claim.
