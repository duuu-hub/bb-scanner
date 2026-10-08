# BB OPEN outside: big-data conditional-edge research (2026-10-08)

## Classification
**EXPLORATORY / PROVISIONAL**, NOT validated or suitable for live deployment. Discovery and 2025–2026 "validation seen" periods have been reviewed repeatedly; filtering and basket thresholds in this report were explored post hoc. No truly unseen holdout / new forward, independent cluster-based positive lower confidence bound, production fill simulation, 30-check audit, or 10 independent clean rounds.

## Grounded provenance
- Repo: `duuu-hub/bb-scanner`, branch: `research-rank5-binance-15m-5y` (**never changed main**).
- Frozen canonical 1m chronology run [37757832008](https://github.com/duuu-hub/bb-scanner/actions/runs/37757832008), commit `1588f5ecebae1038f8f25e927d7696c63aa8c844`, artifact `bb-bidir-v1-merged-37757832008` ID `11543115958`; 8/8 shards, merge and chronology smoke successful.
- Base source historical 15m archive run `36095439671`; Binance USD-M USDT perpetual, 2022–2026-08 effective. 2025–2026 already seen, **NOT new OOS**.
- Input raw parameterized trade ledger: 928,712 rows, **not independent events**; approximately 120,422 unique `(side,symbol,signal_ts)` across configurations.
- V2 pregistration: `research/bb_open_bidir_context_edge_scan_v2_20261008.json` (committed before 2,064-point scan); V3 portfolio capacity registration `research/bb_open_bidir_capacity_stress_v3_20261008.json`; V4 many-slot registration `research/bb_open_bidir_basket_v4_20261008.json`.
- Additional V6 "same-side breadth >=10" is **post-hoc exploratory** (selected after earlier observations), **NOT an independent successful validation**.

## Search progression

1. V1 immediate-OPEN and later 15m confirmation alone: all 48 tested configurations PF <1 under 20 and 40bp.
2. V2 causal one-filter scan 2,064 points on 928,712 parameterized trades; train 2022–23 and 2024 check required PF40 > 1.03, N>=150/75. **46 train pass**, **3 still PF40 >1 in 2025–26 seen**. All 3 LONG, `>=5 upper/lower TF` lower-band setup, `FIRST_BODY` 15m green candle, stop 5%, far2/far3 fixed original lower BB target, and minimum RR at actual entry.
3. Crucial failure: **six-slot 30%-notional account portfolios in 2025–26 all lost** (about -34% to -63%, depending on candidate). Independent PF is not actual account growth.
4. V4 explored basket execution: 20/40/60/100 slots, position notional 1/2/3/5%, total gross exposure <=200%, no same-symbol overlap, canonical confirmed trades, 40bp cost; cases exist positive over chronological subperiods with many small positions.
5. V6 exploratory causal market-wide panic filter: for the best underlying candidate, require at the initial 15m OPEN **at least 10 distinct symbols on the same side with a fresh >=5-of-7 lower BB outside signal**. The count is reconstructed from the recorded signal ledger; a point-in-time live universe must reproduce this before any forward claim.

## Concrete frozen **research candidate** (not a validated strategy)

- Direction: **LONG ONLY**.
- Setup: exact 15m OPEN is below lower BB(20,2) on at least 5 of 7 TF `[1W,1D,12H,4H,1H,30M,15M]`, fresh threshold crossing vs prior 15m boundary. BB at each TF uses previous 19 completed TF closes plus currently observable open.
- Market panic: **>=10 distinct same-side qualifying symbols at that setup boundary**, pre-entry observable in the modeled universe.
- Confirmation: first bullish 15m candle (within 4 bars) that closes above its own opening price, then enter at **next 15m OPEN**.
- Target: **third farthest** breached lower BB of the *setup* across TF, frozen at setup. Entry must be below target and remaining target distance >=1.5 × SL distance.
- SL: 5% below actual long entry. Max hold: 4h after fill.
- Basket: up to 40 concurrent symbols, 3% **current modeled equity** nominal per entry, max 200% total gross (40*3%=120% under the slot rule), prohibit same-symbol overlap. If multiple at identical timestamp, deterministic highest ex-ante `distance-to-target / 5% stop` wins.
- Cost: all-in total 40bp roundtrip (other stress 80,120bp); funding excluded; ideal 15m OPEN fill and fixed 1m chronology.

### Account model returns, nominal 40bp, 40 slots x 3% RR-desc
| Period | Portfolio return | Realized-close MDD | Executed trades | Distinct candidate event-hours |
|---|---:|---:|---:|---:|
| 2022 | +12.751% | 9.128% | 292 | 28 |
| 2023 | +1.709% | 2.020% | 80 | 4 |
| 2024 | +11.716% | 5.664% | 310 | 24 |
| 2025 | +15.457% | 5.742% (year segmentation may vary) | 176 | 20 |
| 2026 through Aug | +0.390% | 1.259% | 50 | 18 |
| 2022–23 combined | +14.678% | 9.128% | 372 | 32 |
| 2025–26 combined | +15.908% | 5.742% | 226 | 38 |

These are **synthetic realized-close account portfolio projections**, not actual trades or funding-inclusive returns; apparent MDD understates MTM drawdown.

### Cost stress & fragility
| Span | Full basket 40bp | Full basket 80bp | Full basket 120bp | Remove top 3 profit hours, 40bp |
|---|---:|---:|---:|---:|
| 2022–23 | +14.678% | +9.751% | +5.020% | **-6.122%** |
| 2024 | +11.716% | +7.670% | +3.755% | **-3.287%** |
| 2025–26 seen | +15.908% | +12.928% | +10.014% | +3.271% |

Sensitivity to the highest-profit hours is severe; the lower-band bounce is driven by a handful of synchronized panic events, **not stable typical-hour edge**. Equal-weight 1h-event bootstrap confidence intervals for V4 candidates include zero. Lack of actual tick liquidity and likely strong cross-symbol crash correlation further reduce confidence.

## Research decision
**Promising regime-dependent basket hypothesis, but no promotion to validated or production.** Particularly 2026's +0.39% is negligible before any unmodeled funding, and 2023 has just 4 distinct qualifying event-hours. A practical next gate would reconstruct accurate contemporaneous broad-universe breadth, evaluate conservative fills/market depth, portfolio MTM and 30+10 audit, then freeze parameters for genuinely NEW forward paper data without adjustment. Avoid selecting parameters using 2025–26 as another tuning round.

## Artifacts preserved outside git
Local user-download research package: `bb_open_bigdata_research_20261008.zip`, containing:
- `bb_open_bidir_edge_scan_v2.py`, `bb_bidir_capacity_stress_v3.py`, `bb_bidir_basket_v4.py`, `bb_bidir_crash_breadth_v6.py`, `bb_bidir_breadth10_stress.py`
- train-first full-grid and selected candidate tables, basket sensitivity and leave-best-hours stress, cluster-bootstrapped hour summaries and this report.
These scripts reuse the canonical frozen V1 trade ledger; they do not regenerate 1m exits. Artifacts can be reproduced from the saved source run and 1m replay results.
