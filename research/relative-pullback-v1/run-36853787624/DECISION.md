# Relative Pullback Day V1: REJECTED

The fixed BTC-relative trend/pullback hypothesis failed. It does not meet the user's day-trading profit objective and must not be promoted to Demo/live on these results.

- Official completed GitHub run: https://github.com/duuu-hub/bb-scanner/actions/runs/36853787624
- Executed code: `fe56f3deb956d13ff8520729e9f6e975502471fb`.
- Evaluation: September 2021–August 2026. TRAIN ends before January 2025; fresh-capital HOLDOUT is January 2025–August 2026.
- One frozen signal configuration; LONG, SHORT and BOTH are diagnostic views. Signal thresholds were not changed after results.
- 30 new regression tests and the existing canonical 1m chronology/invariant tests passed on the actual runner.
- All 4,010 independent candidates resolved: 3,002 TRAIN / 1,008 HOLDOUT. No reported 1m data-gap or entry/exit-mismatch exclusions.
- 1,083 monthly 15m archives available, 15 EOSUSDT months unavailable. Fixed long-history basket is not a complete historical exchange universe.

## Primary account: BOTH, authorized risk controls

| Holdout scenario | Executed trades | Win rate | PF | Account return | 15m-observed MTM MDD | Halt date, Korea time |
|---|---:|---:|---:|---:|---:|---|
| 20bp round trip | 244 | 34.02% | 0.758 | -13.01% | 15.06% | 2025-04-13 |
| 40bp round trip | 162 | 33.33% | 0.630 | -14.36% | 15.00% | 2025-03-08 |

Both rows additionally include 10bp adverse stop/forced-close slippage and a 2bp/day funding stress charge. Actual historical funding and execution latency were not reconstructed. Guards are evaluated at 15m boundaries and are triggers, not guaranteed exact loss caps.

At 20bp, +0.7% account days occurred on **9/607 complete Korea calendar days (1.48%)**, and +2% days on **3/607 (0.49%)**. The denominator includes inactive days after the drawdown halt.

Turning off the daily-loss/drawdown guards while keeping position sizing, concurrency and exposure limits exposes the negative edge: BOTH holdout 20bp has 904 trades, PF 0.724, account return -46.56%, MDD 52.89%; 40bp has 898 trades, PF 0.573, return -65.27%, MDD 66.22%.

LONG-only and SHORT-only are also negative at both costs. None of the six side/cost views survives holdout; these are not six independently discovered strategies. Maximum realized holding time is 12h.

## Integrity and preservation

The official result ZIP's SHA256 was checked against the GitHub artifact digest. Twenty-four account scenarios were independently checked against their executed trade ledgers, equity curves and daily target counts. See `result_audit.json`.

The plan, code, complete independent candidate ledger, all summary scenarios, yearly/quarterly results and source download manifest are preserved on this research branch. Detailed executed ledgers and 15m curves are also in the original workflow artifact. This rejection is about this exact frozen family; it is not proof that all day-trading strategies fail.
