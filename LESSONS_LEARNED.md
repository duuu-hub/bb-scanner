# Lessons Learned Registry

This is the reusable failure-pattern registry for research and automation in this repository.
Read it with AGENTS.md and RESEARCH_RULES.md before extending prior work.

## Maturity

- DOCUMENTED — generalized failure and prevention are recorded.
- GUARDED — an automated assertion/preflight/workflow guard detects the class early.
- REGRESSION-TESTED — a stable automated test reproduces/prevents the class across future changes.

## Rules

- Add a lesson only when it generalizes beyond one run or incident.
- Consolidate duplicate root causes instead of creating near-identical lessons.
- Record the failure signature, detection, prevention, and affected lineage when material.
- Promote lessons from DOCUMENTED -> GUARDED -> REGRESSION-TESTED as controls improve.
- If a failure invalidates research, apply RESEARCH_RULES.md quarantine/invalid procedure separately.
- Git history stores incident detail; this registry stores reusable prevention knowledge.

| ID | Failure pattern | Detection | Prevention/control | Maturity |
|---|---|---|---|---|
| LL-001 | Scripted edits can inject literal escape sequences such as `\\n` or damage indentation | compile embedded Python/scripts; inspect generated workflow snippets | preflight syntax/known-corruption guard before expensive jobs | GUARDED |
| LL-002 | Trigger-only edits can comment out/corrupt executable code or fail to create the intended run | syntax check plus confirm actual Actions run/head SHA | separate trigger files from executable code; verify run creation | GUARDED |
| LL-003 | Data schema differs across sources, e.g. `timestamp_ms` vs `open_time` | explicit required-column/schema check | source-aware loader and fail-loud schema assertion | GUARDED |
| LL-004 | Future confirmation/look-ahead can approve an earlier entry | chronology audit of every feature/confirmation vs entry timestamp | mechanically enforce signal/confirmation then next-known entry | DOCUMENTED |
| LL-005 | Partial/empty shard or missing historical coverage can look like a valid experiment | input counts, rows, symbols, manifests, coverage, artifact count | fail on zero/partial inputs; verify all expected shards before aggregation | GUARDED |
| LL-006 | Higher-timeframe resampling can include partial/gappy candles or misalign boundaries | count source bars per HTF candle; audit gaps/alignment | retain only complete deterministic HTF candles | GUARDED |
| LL-007 | Analysis eligibility and post-run sanity can contradict each other | compare analyzed symbols against the same eligibility set | derive sanity eligibility from the analysis rule, not all audited rows | GUARDED |
| LL-008 | Workflow success can hide wrong/empty outputs | inspect output contents, nonzero universe/rows and artifacts | final verification job produces VERIFIED artifact only after all checks | GUARDED |
| LL-009 | Same failed repair can be repeated without new information | compare failure signature and attempted diff/evidence | require new evidence for each retry; bounded transient retries | DOCUMENTED |
| LL-010 | Temporary Actions artifacts can expire and silently remove canonical research data | inspect expiry and canonical asset inventory/checksums | preserve reusable datasets in versioned long-lived storage with SHA256 | DOCUMENTED |

## Promotion rule

Whenever a lesson receives a durable automated regression test, update its maturity to REGRESSION-TESTED.
Prefer turning repeated prose warnings into executable controls.
