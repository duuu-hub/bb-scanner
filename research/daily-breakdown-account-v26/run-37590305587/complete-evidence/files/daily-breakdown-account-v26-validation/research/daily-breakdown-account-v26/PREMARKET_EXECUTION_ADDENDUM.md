# V26 pre-market execution audit addendum

Registered before any V26 account/GATE result. Code inspection found the shared account liquidation function applies the frozen 10bp adverse slip to SL and explicitly forced guard closes, but its scheduled split-boundary `SPLIT_END` reason did not enter that branch. V25 independent policy rows already charge SPLIT_END slip; V25 never ran an account replay and its rejection remains unchanged.

For V26, correct exactly that shared accounting condition to apply the same 10bp adverse slip to `SPLIT_END` as SL/explicit forced closes. Keep all prices, fees, funding, risk sizing, calendar arithmetic and guard rules unchanged. This is a missing application of the preregistered split-forced-close cost, not a favorable cost/threshold change or a new fill engine. TIMEOUT remains the existing fixed-horizon exit assumption. Add a real flat-price split-end regression proving the adverse fill and both fees plus funding reconcile, alongside the real seven-day hold/calendar audit. Run all previous tests. Preserve source/code hashes and the initial fixture errors.

This addendum explicitly qualifies the original plan's phrase 'unchanged account engine': the common simulator is reused with one documented cost-correction line. No V26 outcome is available at registration; no qualification rule changes.
