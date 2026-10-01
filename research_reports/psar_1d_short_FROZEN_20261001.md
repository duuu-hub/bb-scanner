# PSAR 1D SHORT — FROZEN / CLOSED

Date: 2026-10-01  
Branch: `research-rank5-binance-15m-5y`  
Status: **FROZEN — no further tuning**

## Decision

The PSAR 1D SHORT standalone research is closed and preserved as-is.

Do not:
- retune D0 threshold, age, hold period, stop, admission rule, or sizing on the observed HOLDOUT,
- overwrite or delete prior successful/failed runs or artifacts,
- modify `main`,
- present this strategy as production-ready.

Future use is limited to:
- historical reference,
- correlation/diversification analysis versus other strategies,
- combination studies that clearly treat PSAR 1D SHORT as a frozen component,
- a genuinely new research version with a new predeclared hypothesis and separate identity.

## Frozen concept

Daily PSAR bearish reversal continuation:
- enter SHORT at Age=3 OPEN,
- D0/ATR threshold from canonical TRAIN freeze,
- early exit on bullish PSAR reflip OPEN,
- otherwise max 7-day OPEN exit.

The later practical retrofit added:
- protective stop: 10%,
- micro-risk high-breadth portfolio construction.

## Key evidence

### Canonical signal-level result
- TRAIN raw signal PF around 1.43 at 40 bp in the canonical reflip study.
- HOLDOUT raw signal PF around 1.18 at 40 bp.
- Raw signal edge did not translate cleanly into a constrained practical portfolio.

### Max-6 practical portfolio
- Concentrated admission destroyed most of the apparent edge.
- Tail risk without a stop was unacceptable.
- 10% stop materially reduced short-tail risk but did not create strong account returns.

### Micro-risk high-breadth rescue
Run: `36821615172`  
Artifact: `11143222587`

Frozen TRAIN selection:
- max positions: 200,
- per-position stop-risk: 0.02%,
- per-position notional at 10% stop: 0.20% equity,
- total open stop-risk cap: 2%.

Results:
- TRAIN: +6.4349% total, CAGR +2.0752%, MDD 8.7975%, 4,004/4,326 signals accepted.
- HOLDOUT: +1.8607% total, CAGR +1.1174%, MDD 9.5146%, 4,918/6,354 signals accepted.
- Moderate cost stress: -2.2485%.
- Severe cost stress: -7.5354%.

Interpretation:
- Increasing breadth recovered a small positive edge, which suggests the max-6 bottleneck was a major source of degradation.
- The recovered return is still too weak relative to drawdown and trading-cost sensitivity to justify further standalone optimization.

## Final research conclusion

**Weak edge, insufficient practical payoff. Freeze and move on.**

Keep all code, reports, runs, and artifacts for auditability and possible future diversification analysis.
