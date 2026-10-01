# Shock Confirmation V3: second hypothesis batch

Preregistered 2026-10-01 before V3 outcomes. Prior V1 and V2 failures remain
preserved. V2's fixed-time event edge did not survive as material account growth:
actual account retry 36863290334 completed 216 scenarios/27 policies; no frozen
survivor. Best DEV 20bp CAGR 5.81%; same policy's 2024 return 2.56%.
There were 56 parameterized chronology exclusions; these are not 56 unique
executed losses. They must be reviewed, not silently treated as wins/losses.

## Hypothesis, data and boundaries

Immediate buying/selling may catch continuation of forced liquidation. Test
waiting for a closed reversal and aggressive flow to change before entry.
Protect the observed local extreme, allowing adverse tails to stop while
holding a larger favourable move. Linear fixed-time means cannot by themselves
reject or validate this asymmetric payoff.

Use the same frozen source run 36095439671, all eight shards, delisted-inclusive
catalogue, >=30 observable days before 2024 and closed 24h quote turnover >=$20m.
DEV arrays are physically cut before 2024; GATE before 2025. V3 selection uses
DEV 2021-09 through 2023 only. Calendar 2024 is a historically viewed research
gate, NOT untouched independent confirmation. 2025-2026 old comparisons and
reserved September are not used to choose V3 policies. PLAN.md explains the
recent-check limitations; ultimately freeze before forward observation.

## Frozen 16 entries x six exits/holds = 96 policies

Use a +/-3% or +/-6% 15m shock in the preceding four completed bars, excluding
the current confirming bar. Long follows a downward shock; short an upward.
Current candle must turn in the proposed direction, close beyond previous
close and hold the previous low (long) / high (short).

Confirmation alternatives:
- RECLAIM: current close also exceeds previous high (long) / breaks previous
  low (short).
- FLOW: current taker-buy quote/total quote >=55% long, <=45% short.
  These are trade-flow observations, not reconstructed order-book depth.

BTC context alternatives ANY or RECOVERY: for the latter, signed latest closed
BTC 15m return must be >0. BTC timestamps must match. Trigger state onset and
fixed four-bar intent cooldown per coin/config; never suppress using future
outcomes. All indicators/window extrema reset after data gaps.

Enter next 15m OPEN. Long structural SL = minimum LOW of current and preceding
four bars minus 0.25 closed ATR14; mirror short. At actual fill, stop distance
is at least 0.5% of entry; reject an invalid-side stop or distance >8%. Rank
simultaneous coin intents by known recent-shock magnitude/closed ATR fraction.

Hold ceilings 4h or 12h. Exit alternatives fixed TP2 (2R), TP3 (3R), or a
closed-bar 3ATR trail activated only after one holding hour. All keep the
structural initial SL and exact time OPEN timeout. Canonical 1m chronology,
entry-minute conservative LOSS, gap fills, and mismatch/data exclusion rules
remain unchanged. Save exclusions with coin, entry timestamp and policy.

## Development screening and actual account objective

First calculate every policy's canonical independent DEV outcomes at 40bp
ordinary round trip +10bp adverse stop slip +2bp/day funding stress.
Mean return and risk-normalized return are ONLY diagnostics, never realizable
account growth. Preserve all 96 rows/years, input hashes and exclusions.

DEV seed gate: >=300 outcomes, >=10 coins, >=80 in each 2022/2023, positive
mean net40 price return, positive mean net40/initial-cost-inclusive-stop-risk
in both 2022 and 2023, and top positive coin contribution <=30%.
Rank by smaller 2022/2023 mean net40 R. One policy per entry key, <=2 per
(side, confirmation type), <=6 total. Stop/holding/exits are frozen before gate.

Then replay selected policies on DEV and GATE with identical accounts,
20/40bp costs and guarded/diagnostic controls. Add one predeclared union;
same-symbol/time policy ties use frozen development selection priority.
The practical primary ranking is actual net account CAGR under risk, not
the independent screen's PF/EV. Reuse V2's strict material-growth survivor
criteria (20bp CAGR>=20% in both windows, stressed positive return, PF and
minimum N, positive 2022/2023/2024 years, no 15% halt). No parameter changes
using gate/comparison results. Neighbours and exclusions must be reviewed.

Risk unchanged: 0.5% entry and 2% aggregate reserved budget, six positions,
30% coin notional, 200% gross, KST-day -2% flatten/block, DD10% half/DD15%
halt, no profit cap. Funding budget uses actual max intended hold. Report
all complete KST days and separately +0.7%/+2% rates/daily mean.
A research survivor below the user's daily goal is not goal completion.
If none survives, record why and preregister another economic hypothesis.

No main/live/watcher-state writes. Branch research-shock-confirmation-v3.
