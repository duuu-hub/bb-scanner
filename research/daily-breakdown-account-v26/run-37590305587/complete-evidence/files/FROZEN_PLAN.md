# V26 Daily Breakdown — Preregistered Account Diagnostic

## Status and purpose
V25 remains REJECTED: 0/96 passes its original DEV robustness gates. Its actual run 37559595896 completed successfully but selected no policy and skipped account replay. Among the rejected cells the short family has positive cost-stressed aggregate results. The highest mean-R cell has 1,344 overlapping outcomes / 175 symbols, PF 1.72545, net40 mean R 0.15464; only six entry dates in 2021 and negative equal-entry-date mean R in 2023. This is observed exploratory evidence, not a qualified candidate.

V26 asks whether that short-family pattern produces net account money under actual capital/concurrency/guard constraints, or whether high-count crash dates and portfolio crowding erase it. This is a separate diagnostic study after reading V25, not a change to V25 gates or an unobserved holdout. V25's rejection persists regardless of diagnostic results. Do not promote, deploy, describe an optimum or claim the daily target from a PF number.

## Exact frozen probes before implementation
All eight V25 short entry keys, not only its numerical peak:
- side = -1;
- preceding complete daily Donchian width = 5 or 20;
- BTC daily 20-day regime = ANY or ALIGN20;
- preceding daily ATR20 stop multiple = 1.5 or 2.5;
- each with max hold 672 x 15m = 168h and fixed TP 2R (R20).
Exactly 8 independent policies. No union, no threshold tuning, no optimized sizing, no new entry filter. The 168h/2R slice is chosen after seeing the V25 family result and this selection dependence must remain in every conclusion. Other original 88 cells stay archived. Endpoint settings cannot be called optimal; the user's maximum hold of one week is a hard limit.

Keep V25's exact complete-day aggregation, preceding-only ATR/channel, price onset, 30 observed-day / USD20m closed-24h eligibility, entry at next exact UTC 00:00 open, 25% stop cap, two-day outcome-independent cooldown and causal score. No 2024 return is read or used to select these eight keys. Original V25 DEV chronology outcomes and exclusions are reused byte-for-byte at their input boundary.

## Data and periods
Frozen original 15m run 36095439671, BTC artifact run 36858492497 and V25 FROZEN_CONTEXT SHA256 dab527bef32197364070e1d1cd17b5f9260cbf17f099e9698070ce5843c87faa. Original source inventory is 856 files; DEV history exists for 256. Verify complete source mapping and eight original DEV ledgers against preregistered SHA256s before any replay. A modified, duplicate, missing or incomplete shard aborts.

DEV = original 2021-09 through 2023. GATE = 2024, the same shared interval boundaries. Both have been seen across prior research; GATE is a historical diagnostic, never pristine OOS. Scan GATE independently under these frozen eight policies, include all decision-time eligible listed symbols from the frozen inventory rather than silently restricting to the old DEV listings. No interpolation and no cross-split position carry. Official Binance 1m authority resolves required TP/SL chronology; same-entry-minute or same-established-minute collision => LOSS; DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH explicitly excluded, not guessed. Archive exclusions and original minute evidence.

## Identical account rules and comparison
For each of 8 policies, each DEV/GATE split, each 20/40bp notional roundtrip cost, each guarded/unguarded diagnostic:
8 x 2 x 2 x 2 = 64 account scenarios, no policy union. Same equity 1, 0.5% initial stop-risk per entry, 2% aggregate reserved stop risk, 30% symbol notional, 200% gross, max 6 positions, no same-symbol additional entry, known score then symbol ordering. Funding stress 2bp/day and adverse stop/forced-close slip 10bp. Actual historical funding, liquidity impact and entry delay are not reconstructed; no real execution claim.

Guarded account has KST -2% daily flatten/block, high-water DD10% halves new-entry risk, DD15% halt/flatten; post-halt inactive dates remain in statistics. Unguarded diagnostics retain all sizing/concurrency limits but disable daily/DD guards; they can show unrecoverable risk and never qualify deployment. Independent outcomes and executable trades must be reported separately.

Reuse the unchanged relative_pullback_portfolio.simulate engine, not a new fill/account model. Its actual risk budget already accepts <=7 days. Do not use the old generic day_edge_canonical.accounts audit's <=1440-minute assertion for this one-week strategy. The new diagnostic wrapper verifies per-policy <=10080-minute actual holding, actual stop-risk sizing, cash arithmetic, MTM drawdown, calendar return compounding and every calendar day including boundaries and post-halt dates. It does not fabricate ACCOUNT_REPLAY_REQUIRED selection or call the old choose function.

Report total return, CAGR, 15m MTM MDD, PF, win rate, N, EV, longest loss streak, average/max concurrency, gross/capital occupancy and idle dates. Daily targets +0.7/+1/+2% include all intersected KST dates; report partial-day flags. Show each KST year and quarter, same start capital per split, no fitted account resets inside a split.

## Correlation and fragility diagnostics
For every frozen policy/split from the independent ledger:
- actual active entry dates, per-date counts and trade-weighted vs equal-date mean net40 R;
- top five positive entry-date share of all positive entry-date net P&L;
- mean net40 R after deleting the single best and five best positive entry dates, identified retrospectively and labeled fragility tests (never used to change entries);
- chronology exclusion counts/rate and explicit missing-path limitations.
For executable 40bp guarded accounts, apply the same entry-date concentration/deletion diagnostics to realized net account P&L (no hypothetical reallocation/replay after deletion). This tests sample clustering and cannot prove an independent edge.

## Interpretation and preservation
Every output and decision says DIAGNOSTIC_ONLY_V25_REJECTION_RETAINED; profitable DEV or GATE account numbers do not rewrite V25 selection. Account returns must support discussion, never raw-N times EV. If useful after this diagnostic, next research must preregister nearby parameters, harsher costs/funding, 1–3 minute delays, clean forward paper observations and complementary long/hedge behavior before deployment. If accounts fail, preserve that failure and use the measured cause for a new hypothesis. No guarantee of eventual profit.

Preregister this plan before implementation and before V26 GATE/account outcomes. Preserve the eight original ledger hashes, full 96-cell V25 rejection, code/plan commits, all 64 scenario summaries/curves/trades/calendars, cluster diagnostics, actual Actions logs, official minute files/checksums and complete/partial evidence on research-only branches. Final workflow checkpoint records actual stage status with current branch/run identity guard, leaving economic audit pending; never label an ended computation as still RUNNING.
