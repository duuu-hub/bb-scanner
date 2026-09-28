# EMA600 Idle Overlay — Canonical Research Decision (2026-09-29)

## Scope
This note freezes the current research conclusion for combining:
1. Stage5 BTC/ETH Core LONG
2. PD 4H Breakdown SHORT
3. EMA600/Funding55 BTC LONG as an idle-only overlay

Research only. Do not modify main or enable live trading from this note.

## Canonical overlay semantics
- Period: 2023-01-01 through 2026-08-21 (END 2026-08-22 exclusive for EMA overlay comparison).
- Overlay mode: REGIME_REENTRY.
- Overlay may enter only while Stage5 Core is inactive and no PD position is open.
- Strict-idle interval also requires zero material base return.
- If the base ceases to be idle, the EMA overlay is closed at interval open before base exposure resumes.
- EMA rules remain frozen:
  - BTCUSDT 1H EMA600
  - funding rolling 72h mean, 180d percentile < 55 or NaN at crossover
  - entry next hour open
  - exit below EMA*0.98 next hour open
  - 15% trailing stop
  - 0.10% per-side EMA execution cost
- Missing bounded BTC 15m bars are modeled as zero-return synthetic bars while preserving strategy state. Do NOT force-flat solely because of a bounded archive gap.
- Gap audit proved that force-flat created one artificial re-entry:
  - state-preserving canonical: 74 trades
  - force-flat variant: 75 trades
  - extra entry: 2023-03-24 14:00 UTC

## Canonical results
### 8bp Stage5+PD cost case
- Base Stage5+PD: 11.080235x, MTM MDD 29.4156%
- Max-MDD-boundary EMA weight: 22.6%
- Combined: 13.411248x, MTM MDD 29.99799%
- EMA overlay opportunities taken: 74
- Overlay trade stats: WR 39.19%, PF 1.74296, mean trade +1.18494%

The 22.6% weight is NOT a magic optimum; it is the point where the 30% MTM-MDD constraint binds.

### Cost sensitivity, best EMA weight under 30% MTM MDD
| Stage5+PD stress | Base final | EMA weight | Combined final | MTM MDD |
|---|---:|---:|---:|---:|
| 8bp | 11.080235x | 22.6% | 13.411248x | 29.9980% |
| 12bp | 10.658982x | 17.4% | 12.362479x | 29.9991% |
| 16bp | 10.253220x | 12.1% | 11.377044x | 29.9996% |
| 20bp | 9.862398x | 6.7% | 10.452421x | 29.9995% |
| 24bp | 9.485984x | 1.2% | 9.586125x | 29.9989% |

Interpretation: the overlay itself does not suddenly fail at higher PD cost. The Stage5+PD base consumes more of the 30% risk budget, so the permissible EMA sleeve shrinks.

## Robust fixed-weight candidate
A fixed 12% EMA sleeve is the current practical candidate if Stage5+PD realized all-in cost is expected to remain within roughly the 8–16bp stress range.

At fixed 12%:
- 8bp: 12.284351x, MDD 29.7213% (+10.87% final equity vs base)
- 12bp: 11.817319x, MDD 29.8592% (+10.87% vs base)
- 16bp: 11.367462x, MDD 29.9970% (+10.87% vs base)
- 20bp: 10.934168x, MDD 30.1347% (violates 30% cap)
- 24bp: 10.516848x, MDD 30.2722% (violates 30% cap)

Therefore 12% is robust through the tested 16bp stress case, but must be reduced if observed Stage5+PD costs resemble 20bp+.

## Time robustness
8bp overlay annual trade PF:
- 2023: n=21, PF 4.492
- 2024: n=20, PF 1.748
- 2025: n=23, PF 0.747
- 2026: n=10, PF 1.226

The overlay hurt relative performance in 2025, so a causal meta-gate was tested rather than pretending every year was favorable.

## Meta-gate audit
Causal gates using only prior completed shadow trades:
- LAST6_POS
- LAST10_POS
- D180_POS
- D365_POS

Full-sample D365_POS was slightly better than NONE, but only marginally:
- 8bp optimal boundary: NONE 13.411248x vs D365_POS 13.489174x
- D365_POS used 59 of 74 opportunities.

This was NOT accepted based on full-sample improvement.

### Time-split selection test
Fixed EMA weight: 12%
- Train: 2023-01-01 through 2024-12-31
- Test: 2025-01-01 through 2026-08-21
- Gate chosen only by train return.

Train selected: **NONE**
- NONE train return +181.285%, train MDD 29.718%
- NONE test return +336.838%, test MDD 27.355%

D365_POS happened to produce a slightly higher test return (+339.304%) and lower test MDD (25.786%), but it was not selected by the frozen train rule. Therefore it remains a research observation, not an adopted rule.

## Current frozen candidate
Use:
- Stage5 Core LONG as primary
- PD 4H Breakdown SHORT only while Core is off
- EMA600 REGIME_REENTRY only while both Core and PD are idle
- No EMA performance meta-gate
- Preserve state across bounded 15m archive gaps
- Candidate EMA sleeve: 12% for robust 8–16bp base-cost envelope
- 22.6% is a research boundary result, not the default operational size
- If measured Stage5+PD costs move materially above 16bp, reduce EMA sleeve according to the cost/MDD table rather than keeping 12% fixed.

## Authoritative runs
- Stateful original audit: 36430830085
- Vectorized canonical cross-check: 36437630012
- Canonical gap/weight audit: 36439791639
- Exact cost grid: 36442110654
- Lagged meta-gates: 36474135042
- Time-split gate audit: 36475403013
