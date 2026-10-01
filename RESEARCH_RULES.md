# Research Rules

Ruleset version: `2026-10-02`

These rules govern every strategy study on this repository. Read them together
with `AGENTS.md`. If a required item is unknown or cannot be verified, fail
closed and label the result `PROVISIONAL` or `INVALID`; do not fill the gap with
an assumption.

## 0. Result classes

- `EXPLORATORY`: hypothesis generation or broad scan. It may guide the next
  TRAIN-only experiment but is not evidence of deployable edge.
- `PROVISIONAL`: potentially useful, but one or more required provenance,
  execution, cost, split, shard, or audit checks are incomplete.
- `VALIDATED_CANDIDATE`: frozen logic passed the canonical engine, cost stress,
  portfolio simulation, declared split, and required audits. This is still not
  permission for live trading.
- `FORWARD_CONFIRMED`: the frozen candidate survived a genuinely subsequent
  paper/demo period without retuning.
- `INVALID/REJECTED`: leakage, non-canonical chronology, broken accounting,
  irreproducibility, missing required shards/data, or another hard-rule
  violation. Invalid metrics must not be compared with valid results.

## 1. Register the experiment before viewing its outcome

Before a selection or validation run, record in code, workflow inputs, or an
artifact:

- experiment ID and hypothesis;
- strategy direction and exact signal condition;
- decision timestamp, order-live timestamp, entry/fill rule, exit rule, and
  maximum hold/time-limit rule;
- data source, market type, point-in-time universe rule, timeframe, and date
  range;
- TRAIN/VALIDATION/FORWARD split;
- complete parameter grid and which metric/constraints select a finalist;
- canonical execution engine/version;
- cost, slippage, funding, and portfolio assumptions;
- acceptance, rejection, and minimum-sample criteria.

Anything chosen or changed after viewing the relevant result is post-hoc. It
must be labeled `EXPLORATORY` and tested in a new frozen experiment; it must not
be presented as a clean confirmation.

## 2. Data, split, and leakage rules

- Use only information available at the simulated decision timestamp. Indicators
  use completed prior candles unless a canonical open-time projection is
  explicitly defined in `AGENTS.md`.
- The default five-year crypto development split is TRAIN `2021-01-01` through
  `2024-12-31` UTC and VALIDATION `2025-01-01` through the available 2026 data.
  A study may pre-register another chronological split when its data coverage
  requires it, but may not change the split after seeing results.
- Repository-level 2025-2026 results have already been inspected repeatedly.
  Therefore that period is **VALIDATION/SEEN**, not pristine holdout or true
  OOS, unless an experiment-specific sealed record proves otherwise.
- Final confirmation requires later unseen chronological data or a frozen
  forward paper/demo run. Once viewed and used to modify the strategy, a period
  can never be renamed untouched OOS.
- TRAIN selection must require the full trade horizon/exit to end before the
  split boundary. Do not let trades leak future exit information across the
  boundary.
- Freeze thresholds, ranks, feature transforms, universe filters, sizing, and
  tie-breaking before revealing VALIDATION/FORWARD results.
- Record the raw data artifact/run ID or checksum, timestamp timezone, missing
  intervals, duplicate candles, and exclusions. Official exchange timestamps
  are authoritative.
- Disclose survivorship bias. A universe made only from currently listed
  symbols is not a point-in-time historical universe. Include delisted symbols
  when the study claims full historical-universe results, or label the result
  survivorship-biased.

## 3. Canonical execution and costs

- The intrabar and same-bar chronology in `AGENTS.md` is mandatory for every
  OHLC backtest. Strategy-local shortcuts are forbidden.
- Entry cannot occur before the signal and order-live timestamp. Exit touches
  before actual fill are ignored. Unresolved chronology must use the canonical
  conservative or exclusion outcome; it must never be guessed in the favorable
  direction.
- Time-limit exits must use the first executable price at the declared deadline
  under a pre-registered convention. Do not substitute a later terminal price
  or drop unresolved positions.
- Default cost scenarios are **20bp and 40bp total round-trip all-in cost**, not
  20/40bp per leg. Apply the rate to the declared round-trip notional convention
  consistently. If a study uses a different cost model, declare it before the
  run and report 20/40bp comparability separately.
- State whether funding is modeled. If omitted, write `funding excluded`; do
  not imply that 20/40bp automatically includes unknown realized funding.
- Gross-only scans may be used for exploration but must be labeled `GROSS ONLY`
  and cannot become a validated candidate until net results pass cost stress.
- Maker/taker classification, post-only behavior, gaps, latency, partial fills,
  and price-source/exchange differences must be disclosed. A touched limit is
  not automatically a guaranteed real fill unless the canonical fill policy
  says so.

## 4. Primary objective: realizable account growth, not cosmetic metrics

The objective is realizable account growth over the full test period at a
tolerable risk level. Do not select a strategy merely because it has the
highest per-trade expectancy, PF, win rate, raw signal count, theoretical gross
R, or best isolated parameter point.

Evaluate in this order:

1. total account return and CAGR when meaningful;
2. MDD, drawdown duration, and drawdown-adjusted return;
3. capital occupancy, concurrency, and exposure utilization;
4. PF, win rate, executable trade count, expectancy, and losing streaks.

Prefer a stable parameter region where extra risk produces meaningful extra
account return. Do not call a boundary point optimal; extend the search first.
Reject isolated peaks that collapse under nearby parameters, years, symbols,
or 40bp cost stress.

## 5. Raw signals, independent events, and account trades are different

- Report raw signals, filled trades, executable account trades, and independent
  events separately.
- Signals clustered at the same time or driven by the same market event are
  not independent evidence. Report event-level N and symbol concentration.
- When overlapping positions are allowed, raw resolved N is not executable
  account N. Never multiply raw N by per-trade EV and call it realizable profit.
- Theoretical signals that Demo/live execution could not trade must remain in a
  separate counterfactual ledger rather than being silently deleted or counted
  as fills.

## 6. Required portfolio comparison and report

Before calling a parameter set practical or optimal, compare candidates using
the same:

- starting capital and compounding convention;
- position/risk sizing rule;
- maximum gross exposure and concurrent positions;
- same-symbol and opposing-signal conflict rules;
- execution/fill semantics and time limits;
- cost, slippage, and funding assumptions;
- data period, point-in-time universe, and eligibility rules.

Report at minimum:

- total return, CAGR when meaningful, MDD, and drawdown duration;
- PF, win rate, expectancy, maximum losing streak, and yearly/period stability;
- raw signal N, fill N, executable account N, independent-event N, and excluded
  outcome counts;
- average/max concurrent positions, capital/exposure utilization, and time in
  market;
- 20bp and 40bp net results plus funding status;
- TRAIN versus VALIDATION versus FORWARD results without pooled selection;
- symbol and regime concentration and the worst relevant slice;
- exact run/provenance fields required by `AGENTS.md`.

## 7. Optimization workflow

1. Generate hypotheses and run broad scans on TRAIN only.
2. Preserve every tested grid point, failure, error, and exclusion.
3. Reject unstable/isolated peaks and extend any winning boundary.
4. Refine robust TRAIN regions with a frozen tighter grid.
5. Run identical portfolio simulations on finalists.
6. Stress at 20bp and 40bp and disclose funding.
7. Freeze the candidate and selection rule before revealing VALIDATION.
8. Use VALIDATION as a falsification check, not as another tuning set. Any
   validation-driven change creates a new exploratory generation.
9. Confirm the final frozen generation through later forward paper/demo data.

## 8. Mandatory audit gate: 30 checks plus 10 clean rounds

Before labeling a result `VALIDATED_CANDIDATE` or admitting it to Demo:

1. Pass at least 30 explicit, named checks covering data integrity, timestamp
   cutoff, look-ahead prevention, signal/entry chronology, 1m collision logic,
   fees/accounting, split isolation, portfolio constraints, shard completeness,
   artifact reproducibility, and representative trade reconstruction.
2. Then pass 10 consecutive clean audit/reproduction rounds on the frozen code,
   data reference, and configuration with zero unexplained mismatch.
3. Record the checklist, round outputs, commit SHA, configuration hash, and data
   reference in artifacts.
4. Any code, rule, data, or parameter change that can affect outcomes resets the
   10-clean-round streak. Cosmetic report-only changes do not, but must be
   identified as cosmetic.

Repeating the same command ten times without checking independent invariants is
not sufficient. Failed checks remain recorded; they are not erased by a later
pass.

## 9. Research integrity and forbidden claims

- Never modify `main` for research and never merge without the explicit approval
  required by `AGENTS.md`.
- Never silently retune, change exclusions, shrink the universe, alter the
  date range, or swap execution engines after seeing results.
- Never report a missing shard, excluded mismatch, data gap, timeout, or failed
  job as a loss, win, zero, or completed result unless the frozen contract
  explicitly defines that treatment.
- Never compare gross results with net results, independent-signal scans with
  account simulations, or different timing engines as though they were the same
  experiment.
- Never claim guaranteed future profit. A backtest establishes historical
  evidence under stated assumptions, not a proof of future returns.
- Preserve failures because they prevent duplicate searches and reveal fragile
  regions. Every new generation must state what changed and why.
- When evidence is incomplete, say exactly what is missing. Do not manufacture
  confidence, progress, citations, run status, or metrics.
