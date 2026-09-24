# Core-long integration checkpoint — 2026-09-24

## Decision summary

Current evidence supports a demo-only regime switch for forward validation:

1. **BTC/ETH frozen core** while its frozen regime is active.
2. **Frozen LONG3 L2 (4h +30% explosive)** as the primary core-off shadow module.
3. **Rank5 SHORT** remains a small conditional hedge/shadow candidate only.
4. Keep **D2_0 early exit** as a parallel shadow overlay, not a replacement for the frozen BTC/ETH core.
5. Do not promote AOA or ASL1 as core-off modules.

This is a forward-test decision, not a production or live-trading decision.

## BTC/ETH frozen core

Stage8 run 15, 2018–2025, 0.25% round-trip cost:

- Base basket cumulative return: **+618.31%**
- Sharpe: **0.874**
- MDD: **-39.13%**
- Positive years: **5/8**

Post-hoc D2_0 overlay (exit after two completed position days when basket return is non-positive):

- Cumulative return: **+831.40%**
- Sharpe: **0.998**
- MDD: **-29.47%**
- Positive years: **7/8**
- Worst year: **-4.46%**

D2_0 is supported by neighboring variants but was discovered on the same sample. It must remain a shadow variant until forward evidence accumulates.

## LONG3 reconstruction parity

Corrected reconstruction changes:

- evaluate the signal at the new 15m candle **open**, matching the frozen scanner;
- provide 180 days of warm-up for the weekly BB;
- compare only the common frozen-source/canonical universe.

Like-for-like 120d result:

- canonical universe: 856
- frozen source universe: 74
- common universe: 70
- original common signals: 125
- reconstructed signals: 123
- exact symbol/timestamp/strategy matches: 102
- recall: **81.60%**
- precision: **82.93%**

This is sufficiently close for cross-dataset structural research, but not exact enough to treat the 5Y replay as executable PnL parity.

## Corrected 5Y LONG3 structural replay

The aggregate LONG3 bundle is not robust over the long sample:

| Strategy | Trades | Avg net/trade | PF | PF at +0.25% extra cost | PF at +0.50% extra cost |
|---|---:|---:|---:|---:|---:|
| L1 | 5,885 | -0.138% | 0.958 | 0.888 | 0.825 |
| L2 | 1,492 | +0.500% | 1.262 | 1.120 | 1.000 |
| L3 | 222 | -0.706% | 0.729 | 0.657 | 0.593 |
| LONG3 aggregate | 7,599 | -0.029% | 0.990 | — | — |

L2 was positive in five of six calendar years. Its base PF by year was:

- 2021: 0.818
- 2022: 1.351
- 2023: 1.160
- 2024: 1.433
- 2025: 1.213
- 2026 partial: 1.311

L2 by BTC regime:

- BEAR: n=644, PF 1.254; +0.25% cost PF 1.113
- BULL: n=613, PF 1.370; +0.25% cost PF 1.215
- SIDEWAYS: n=235, PF 1.020; +0.25% cost PF 0.904

Interpretation: the recent 120d evidence for the full LONG3 bundle does not generalize. L2 has the strongest structural case; L1 and L3 should remain controls until stronger independent evidence appears.

## Core-active versus core-off integration

Using BTC/ETH frozen-core episode dates to split L2 trades:

| L2 bucket | Trades | Avg net/trade | PF | PF +0.25% | PF +0.50% |
|---|---:|---:|---:|---:|---:|
| Core off, all | 1,170 | +0.612% | 1.324 | 1.175 | 1.050 |
| Core active, all | 322 | +0.093% | 1.047 | 0.928 | 0.828 |
| Core off, 2021–2025 | 585 | +0.485% | 1.255 | 1.113 | 0.993 |

The frozen L2 horizon is one hour. Maximum observed simultaneous L2 positions was three, so 30% per position implies 90% peak signal exposure in this replay; the 200% total cap never bound.

This makes L2 the best currently observed profitable core-off module. The split is still post-hoc and must be validated forward without changing the frozen entry/exit rule.

## ASL1 is not the core-off module

Crypto-only ASL1 LONG24, classified against BTC/ETH core activity:

- 2021–2025 core off: n=773, avg **-0.232%**, PF **0.890**
- 2021–2025 core active: n=232, avg **+0.891%**, PF **1.570**
- Including 2026, core-off PF rises to 1.077, driven by the recent 2026 cohort.

ASL1 behaves more like an additional risk-on/core-active long than an off-season diversifier. Do not add it for the stated portfolio role.

## Other complementary candidates

- **AOA:** zero-cost hybrid showed a weak edge, but 0.12% round-trip cost changed it to -68.64% with PF 0.892. Reject.
- **Rank5 SHORT:** still the best directional hedge candidate, but sample/concentration sensitivity is material. NEW66 had 32 trades / 20 independent events, PF 1.355 and PF 1.091 at +0.50% cost. Keep frozen shadow/demo only.
- **Continuation:** execution-validation run remains in progress. Do not use it in the strategy decision until its cost, delay, overlap, and slot artifacts finish successfully.

## Proposed demo-forward lanes

### Stable lane

- BTC/ETH frozen core only.
- Base frozen exit remains the primary result.
- D2_0 runs in parallel shadow accounting.
- No live trading.

### Research lane

- When BTC/ETH core is inactive, shadow frozen L2:
  - rank >= 6
  - 4h return >= 30%
  - LONG
  - TP 10%
  - SL 2.5%
  - 1h time limit
- Position size: retain the existing 30% convention.
- Preserve 200% portfolio cap, although historical L2-only concurrency reached only 90%.
- Measure real +1/+2/+3 minute entry delay and actual venue slippage before any promotion.

### Hedge lane

- Rank5 SHORT as a separate low-confidence shadow stream.
- Do not let its thin sample justify portfolio leverage or replacement of the core.

## Limitations

- LONG3 parity is about 82%, not 100%.
- The 5Y replay is structural, based on canonical 15m data and a boundary-open/next-bar execution approximation, not the exact 1m executable engine.
- Extra-cost stress is applied arithmetically to completed trade returns.
- The core-off L2 finding is an integrated post-hoc hypothesis from already observed data. It requires frozen forward confirmation.
- Portfolio MTM drawdown and cross-module simultaneous-loss analysis are still required before capital allocation.
