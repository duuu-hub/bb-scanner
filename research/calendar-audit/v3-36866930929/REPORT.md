# V3 all-KST-calendar reporting correction

Reporting-only audit of ORIGINAL account curves from run36866930929.
No market replay, new hypothesis, retuning or executions. Original records intact.
All30 retrieved account files match the previously preserved durable manifest
SHA256 and bytes; original Actions artifact ZIP SHA256 also retained.
Two reconstruction tests passed. All8 scenario audits pass: complete original
dates match exactly, every intersected KST date counts, partial covered hours are
flagged, daily product equals final cash and original trades/PNL remain unchanged.

Original851DEV/365GATE complete-only date denominator omitted the partial first
and last dates at UTC source cuts. Corrected853DEV/367GATE counts include them.
These include two partial edge dates, not853/367full24h days. Physical data cuts
remain identical. Calendar mode changes no CAGR/MDD/risk/fee/return/selection.

DEV20bp guarded:13/853dates achieve+0.7% =1.5240% (original13/851=1.5276%).
Seen2024GATE20bp guarded:10/367covered dates =2.7248% (original10/365=2.7397%).
The gate includes2024-01-01from09:00KST and2025-01-01through09:00KST, with the
partial next-year date explicitly flagged. No silent claim of a full2024 year.
Both extra dates are flat in V3; numerator unchanged. V3 remains REJECTED.
DEVtotal+0.3490%, GATEtotal-2.1032%; no meaningful daily+0.7%-2% achievement.

See summary.json for original and corrected reporting metrics, audit.json for
checks/input hashes and details/*/daily_all_kst_dates.csv for all8 corrected
curves. Reconstruction script and tests are committed with this audit.
V1/V2 older denominators require equivalent saved-curve reviews; no relabelling
of complete-only original rates as all-date rates. This is not a new discovery.
