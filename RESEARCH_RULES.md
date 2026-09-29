# Research Rules

These rules govern strategy research on this branch.

## 1. Primary optimization objective: account-level money, not cosmetic metrics

The primary objective is to maximize realizable account growth over the full test period while taking a tolerable amount of risk.

Do NOT select a strategy merely because it has the highest:
- per-trade expectancy (EV),
- profit factor (PF),
- win rate,
- raw signal/trade count, or
- theoretical gross R.

Those are diagnostic metrics, not the final objective.

## 2. Required portfolio-level comparison

Before calling a parameter set "optimal", compare candidates under the SAME:
- starting capital,
- risk sizing rule,
- maximum total exposure,
- maximum concurrent positions,
- same-symbol conflict rule,
- execution/fill assumptions,
- fees, slippage, funding assumptions where applicable,
- data period and universe.

Report at minimum:
- total account return,
- CAGR when meaningful,
- MDD,
- PF,
- win rate,
- executable trade count,
- per-trade expectancy,
- maximum losing streak,
- average/max concurrent positions,
- capital/exposure utilization.

## 3. Efficiency means return versus risk and capital usage

A higher per-trade EV is NOT automatically more efficient.

A lower-EV strategy can make substantially more money if it provides many more executable opportunities without exhausting capital or increasing drawdown excessively.

Evaluate:
- total return / CAGR first,
- then MDD and drawdown-adjusted return,
- then capital occupancy / concurrency,
- then PF, win rate, trade count and EV as supporting evidence.

Prefer the region where additional risk produces meaningful additional account return. Do not chase a tiny return improvement if MDD or required exposure increases disproportionately.

## 4. Win rate and losing streaks matter

Very high-R / very-low-win-rate configurations must not be promoted solely because PF or per-trade EV is higher.

Always show win rate and losing-streak behavior beside PF/EV. If a minimum win-rate constraint is specified for a study, treat it as a hard constraint.

## 5. Raw signals are not account trades

When overlapping positions are allowed in a signal scan, raw resolved N is NOT equivalent to executable account trade count.

Do not multiply raw N by per-trade EV and present it as realizable account profit unless portfolio constraints have been applied.

## 6. Optimization workflow

1. Use broad parameter scans to identify robust candidate regions.
2. Reject obvious unstable/isolated peaks.
3. Refine promising regions with a tighter grid.
4. Run portfolio simulation on finalists using identical capital/risk/exposure rules.
5. Stress execution costs and slippage.
6. Compare return, CAGR, MDD, concurrency, utilization, PF, win rate, executable N, EV and losing streaks.
7. Select the practical return/risk optimum, not the prettiest single metric.

## 7. Research integrity

- Never modify main for research.
- Do not silently retune thresholds after seeing results.
- Preserve failed results and errors.
- Avoid look-ahead bias.
- Use only information available at the simulated decision time.
- Keep execution semantics identical across compared candidates.
- Do not claim an optimum while the best point is still on the boundary of the searched parameter range; extend/refine the search first.
