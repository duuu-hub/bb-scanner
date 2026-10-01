# PSAR 1D SHORT — micro-risk high-breadth rescue

Date: 2026-10-01  
Branch: `research-rank5-binance-15m-5y`  
Run: `36821615172`  
Job: `110238202233`  
Artifact: `11143222587`  
Run head SHA: `e8c6f5ad91ee0674166a6faf8b6346e133e5be7a`

## Purpose

Test whether the practical portfolio failure was mainly caused by the max-6 admission bottleneck rather than the frozen PSAR 1D SHORT signal itself.

No signal retuning was performed in this run.

## Fixed rules

- Current live-pipeline canonical source: Run `36817230406`
- Current canonical max hold: 7 days
- Current canonical D0 threshold in the downloaded artifact: `2.0542569527384407`
- Protective stop: fixed 10%
- Admission order: deterministic neutral hash, frozen seed 8
- Round-trip trading cost: 40 bp
- Stop slippage: 10 bp
- Funding proxy: 2 bp/day
- Same-symbol overlap rejected
- Total open stop-risk cap: 2%
- Because every position has the same 10% stop, the 2% stop-risk cap is implemented as a 20% gross notional cap.

## TRAIN-only grid

Max positions: 20 / 30 / 50 / 100 / 200

Per-position stop-risk:
0.01 / 0.02 / 0.03 / 0.05 / 0.075 / 0.10 / 0.125 / 0.15% of account equity.

Selection:
highest TRAIN ending equity among non-bankrupt configurations with ending equity > 1 and MDD <= 25%; tie lower MDD, higher signal coverage, lower risk, then lower breadth.

## Frozen selection

- Max positions: **200**
- Per-position stop-risk: **0.02%**
- Per-position notional at 10% stop: **0.20% of equity**
- Total open stop-risk cap: **2%**
- Actual max concurrent positions observed: 102 TRAIN / 104 HOLDOUT

## Results

### TRAIN

- Return: **+6.4349%**
- CAGR: **+2.0752%**
- 15m MTM MDD: **8.7975%**
- Accepted: **4,004 / 4,326**
- Signal coverage: **92.5566%**
- Stop exits: 1,295
- Base exits: 2,709
- Worst single-position account impact: **-0.02130%**

### HOLDOUT

- Return: **+1.8607%**
- CAGR: **+1.1174%**
- 15m MTM MDD: **9.5146%**
- Accepted: **4,918 / 6,354**
- Signal coverage: **77.4001%**
- Stop exits: 1,887
- Base exits: 3,031
- Worst single-position account impact: **-0.02130%**

## Cost / funding stress on frozen HOLDOUT configuration

- Base: **+1.8607%**, MDD 9.5146%
- No funding proxy: **+2.9098%**, MDD 9.1417%
- Moderate (60 bp RT, 25 bp stop slip, 5 bp/day): **-2.2485%**, MDD 11.0563%
- Severe (80 bp RT, 50 bp stop slip, 10 bp/day): **-7.5354%**, MDD 13.0956%

## TRAIN neighborhood robustness

The selected point was not an isolated one-cell spike:

- 200 positions / 0.02% risk: **+6.4349%**, MDD 8.7975%
- 100 positions / 0.02% risk: **+6.4105%**, MDD 8.7975%
- 200 positions / 0.01% risk: **+5.9299%**, MDD 4.4955%
- 100 positions / 0.01% risk: **+3.2688%**, MDD 4.4955%
- 100 or 200 positions / 0.03% risk: **+3.1037%**, MDD 11.6691%
- 50 positions / 0.03% risk: **+0.4535%**
- 20 and 30 position versions remained negative.

## Interpretation

The prior max-6 portfolio constraint was a major source of edge destruction. The signal behaves materially better when a large fraction of the cross-section is accepted with very small per-position risk.

However, the frozen HOLDOUT return is only +1.86% under the base cost/funding assumptions and becomes negative under the moderate cost stress. Therefore this is a rescue of the portfolio construction concept, not evidence of a production-ready high-return strategy.

HOLDOUT has been observed in earlier retrofit research, so this result is diagnostic rather than pristine untouched OOS.
