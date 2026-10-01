# V3 rejection audit: preserve as research evidence

Actual run: https://github.com/duuu-hub/bb-scanner/actions/runs/36866930929
Executed commit: 84fe6a116dd21fec405120cfe2c4ad38cf04a65a.
Policy: CONFIRM_T6_S+1_RECLAIM_ANY__H16__TP2; selected from 96 DEV cells.
All eight account audits passed; chronology exclusions in selected ledgers: 0.
Full originals: branch research-shock-confirm-v3-results-36866930929,
research/shock-confirmation-v3/run-36866930929/accounts.

| Guarded account | DEV 20bp | DEV 40bp | 2024 20bp | 2024 40bp |
|---|---:|---:|---:|---:|
| Net return % | 0.34905 | -3.27847 | -2.10319 | -4.38702 |
| CAGR % | 0.14949 | -1.41886 | -2.09893 | -4.37823 |
| 15m MTM MDD % | 7.06561 | 8.35905 | 5.80653 | 7.73396 |
| PF | 1.01096 | 0.89828 | 0.90498 | 0.80360 |
| Executed trades | 213 | 213 | 138 | 138 |
| Mean calendar day % | 0.000669 | -0.003676 | -0.005449 | -0.011933 |
| Days reaching +0.7% % | 1.52761 | 1.41011 | 2.73973 | 2.73973 |
| No-entry days % | 85.89894 | 85.89894 | 76.98630 | 76.98630 |

DEV 331 independent policy outcomes yielded 213 guarded entries: 115 risk/exposure
rejections, one same-symbol rejection and two daily-block rejections. Gate 214
yielded 138: 76 risk/exposure rejections. Thus roughly 35.5% of gate intents
could not become trades under fixed risk, despite very low average gross
occupancy (0.4524%). Opportunities cluster rather than filling quiet dates.
DEV average occupancy was 0.3058%.

Doubling ordinary costs moved DEV return down 3.62752 percentage points and gate
down 2.28382 points. Those are replay differences including changed equity sizing,
not falsely labelled pure fee cash sums. At 20bp, removing daily/DD controls
changed DEV +0.34905% to +2.74611% (215 instead of 213 entries); this diagnostic
still fails material growth and is not permission to remove controls.
Gate controls never triggered, so its loss is not caused by the kill switch.
The observed DEV -2.32490% day illustrates that a -2% trigger is not a guaranteed
fill/cap. No 15% halt occurred in the selected accounts.

Diagnosis: confirmed reversion has inadequate executable frequency and too little
net advantage after fixed costs/sizing/conflicts. Independent net40 R +0.09337
in DEV did not establish account growth. Selected historical gate lost money.
No 2025-2026 or September replay is needed to declare this batch unsuitable.
Do not tune to these gate results or call more parameter trials independent proof.

Next mechanism: separately preregistered V4 moderate compression/volume/flow trend
expansion. Original failed numbers and source ledgers remain unchanged.
