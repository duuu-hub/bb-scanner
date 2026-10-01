# Relative Trend Pullback V1 — account results

One configuration frozen before outcomes; no holdout selection. All return targets use net account equity.
20/40bp round-trip costs + 10bp adverse stop/forced-close slip + 2bp/day funding stress charge.
Guarded: 0.5% risk/trade, 2% aggregate reserved risk, 30% notional/coin, 6 positions, 200% gross cap;
Korea-day -2% flatten; high-water -10% halves future entry risk, -15% flattens and halts.
MTM and account guards observed every 15m; intraminute losses can exceed limits.
Days include inactive days; only complete Korea calendar days count in daily/quarterly statistics.
Diagnostics retain the same portfolio constraints but disable daily/DD guards; they expose edge hidden by stopping early.
Fixed 18-symbol long-history basket has selection/survivorship limitations. Historical actual funding is not reconstructed.

| Split | Side | Cost bp | Guards | N | Return % | MDD % | PF | Daily mean % | Days >=0.7% | Days >=2% | Flat days | Halted |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| TRAIN | BOTH | 20 | True | 170 | -13.520 | 15.167 | 0.626 | -0.012 | 0.493% | 0.000% | 96.960% | True |
| TRAIN | BOTH | 20 | False | 2570 | -87.529 | 88.082 | 0.667 | -0.167 | 9.449% | 2.054% | 35.333% | False |
| TRAIN | BOTH | 40 | True | 155 | -13.780 | 15.099 | 0.557 | -0.012 | 0.247% | 0.000% | 97.042% | True |
| TRAIN | BOTH | 40 | False | 2551 | -96.055 | 96.202 | 0.530 | -0.261 | 6.738% | 1.479% | 35.333% | False |
| TRAIN | LONG | 20 | True | 141 | -13.450 | 15.097 | 0.570 | -0.012 | 0.247% | 0.000% | 97.124% | True |
| TRAIN | LONG | 20 | False | 1462 | -73.536 | 74.547 | 0.633 | -0.107 | 5.094% | 0.575% | 62.366% | False |
| TRAIN | LONG | 40 | True | 104 | -13.717 | 15.036 | 0.482 | -0.012 | 0.082% | 0.000% | 98.028% | True |
| TRAIN | LONG | 40 | False | 1456 | -86.222 | 86.432 | 0.497 | -0.161 | 3.451% | 0.493% | 62.366% | False |
| TRAIN | SHORT | 20 | True | 161 | -13.052 | 15.028 | 0.579 | -0.011 | 0.329% | 0.000% | 97.124% | True |
| TRAIN | SHORT | 20 | False | 1108 | -52.879 | 55.137 | 0.735 | -0.059 | 4.437% | 1.479% | 70.583% | False |
| TRAIN | SHORT | 40 | True | 122 | -13.472 | 15.013 | 0.468 | -0.012 | 0.164% | 0.000% | 97.453% | True |
| TRAIN | SHORT | 40 | False | 1095 | -71.368 | 72.507 | 0.591 | -0.101 | 3.533% | 0.986% | 70.583% | False |
| HOLDOUT | BOTH | 20 | True | 244 | -13.014 | 15.056 | 0.758 | -0.022 | 1.483% | 0.494% | 88.962% | True |
| HOLDOUT | BOTH | 20 | False | 904 | -46.562 | 52.888 | 0.724 | -0.100 | 7.084% | 1.812% | 44.481% | False |
| HOLDOUT | BOTH | 40 | True | 162 | -14.359 | 15.003 | 0.630 | -0.025 | 0.988% | 0.329% | 92.422% | True |
| HOLDOUT | BOTH | 40 | False | 898 | -65.271 | 66.225 | 0.573 | -0.171 | 5.931% | 1.318% | 44.481% | False |
| HOLDOUT | LONG | 20 | True | 204 | -13.586 | 15.014 | 0.707 | -0.024 | 1.483% | 0.165% | 88.138% | True |
| HOLDOUT | LONG | 20 | False | 407 | -27.596 | 28.847 | 0.698 | -0.052 | 3.460% | 0.494% | 71.829% | False |
| HOLDOUT | LONG | 40 | True | 165 | -14.362 | 15.007 | 0.603 | -0.025 | 0.988% | 0.165% | 90.939% | True |
| HOLDOUT | LONG | 40 | False | 407 | -40.229 | 40.678 | 0.554 | -0.084 | 2.965% | 0.329% | 71.829% | False |
| HOLDOUT | SHORT | 20 | True | 222 | -14.113 | 15.397 | 0.672 | -0.025 | 1.153% | 0.329% | 89.292% | True |
| HOLDOUT | SHORT | 20 | False | 497 | -26.195 | 35.867 | 0.754 | -0.048 | 3.624% | 1.318% | 71.664% | False |
| HOLDOUT | SHORT | 40 | True | 135 | -14.417 | 15.191 | 0.531 | -0.025 | 0.494% | 0.329% | 93.575% | True |
| HOLDOUT | SHORT | 40 | False | 491 | -41.897 | 46.421 | 0.593 | -0.088 | 2.965% | 0.988% | 71.664% | False |

A completed workflow is not a strategy pass. Signal thresholds are unchanged across all rows.
Use the independent ledger and scan_meta.json for explicit DATA_GAP / ENTRY_MISMATCH / EXIT_MISMATCH counts.
