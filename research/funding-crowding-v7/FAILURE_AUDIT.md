# V7 paid-funding crowding: completed rejection audit

Actual workflow run36922945306 executed commit
4db8b13a5ea64f3aef610609c994b2fe2cd0fc18. All20 jobs succeeded:158 tests,
canonical chronology/invariants, eight DEV shards, selection, eight GATE shards,
24 account scenarios and complete preservation. This is a rejected historical
research result, not a trading recommendation or the user's daily-goal result.

## Input and execution integrity

- Official BTCUSDT2022-01 funding probe retained the original ZIP/CHECKSUM and
  parsed93 records. Original ZIP SHA256
  22ee19079b620f5c6d820e7d7f8bafa7fde866d89bd664863b8bd527749c12cb.
- DEV funding coverage passed the frozen95% gate:2022
  3,269,096/3,315,216=98.6088%;2023 3,545,606/3,547,461=99.9477%,256symbols.
-96policy cells contained271,514 resolved overlapping outcomes. Five had
  positive net40 price means and five positive net40 R means. Global net40 price
  means ranged -75.3299bp to+34.0217bp.
- Two frozen LONG candidates passed the development screen, both requiring
  normalized funding<=-0.10%, taker-buy share>=55%,24h maximum hold and3ATR
  trailing exit:ANY-BTC and aligned-positive-BTC4h. Their independent counts
  were2,048 and1,218; the union held2,250 unique candidate events.
- Two DEV DATA_GAP policy outcomes (one underlying event duplicated across the
  selected policies) were excluded; GATE had zero chronology exclusions and32
  STOP_ABOVE_8PCT intent exclusions. No missing outcome became a loss or win.
- All24 saved account trade/daily/curve scenarios passed an independent
  file-only audit:fees,funding-stress,netPnL,final cash,MDD,PF,0.5% entry risk,
  2% aggregate reserve,30% coin notional,200% gross,six positions,no same-coin
  overlap,24h maximum hold,halt entry cutoff and853/367 all-KST-date products.

## Why the candidate fails

The provisional strict survivor count is ZERO.

With required guards, every selected DEV20/40 account hit the15% permanent DD
halt during the early DEV path. FUNDING_UNION20 executed111/2,250 candidates
(4.93%),returned-13.2144%,CAGR-5.8950%,MDD15.0270%,PF.5284. It reached+0.7%
on5/853dates(.5862%) and+2%on2/853(.2345%).40bp returned-13.2969%,PF.5026.

Removing guards does not rescue robustness. The union20 diagnostic returned
+64.7880% over DEV but MDD32.8711%,PF1.1051 and only130/853dates(15.2403%)
reached+0.7%. At40bp it became-10.3300%,MDD43.6053%,PF.9778. The DEV20
diagnostic path was regime-dependent:2021-17.6255%,2022+44.0994%,
2023+38.8259%. That early loss explains the guarded halt; later independent
trade averages cannot be converted into an executable safe account.

The already-seen2024 gate invalidates the mechanism. The least-bad candidate,
ALIGN4H, returned-6.7238% guarded20(PF.8920,MDD15.0351,DD halt) and-1.9948%
diagnostic20(PF.9822,MDD22.2977); diagnostic40 was-13.1389%,PF.8788,
MDD27.8084%. The union20 guarded returned-10.3696%,PF.8343,MDD15.0090;
union40 guarded-12.0137%,PF.7811. Its+0.7% frequencies were15/367(4.0872%)
and11/367(2.9973%), respectively. Removing guards made the union even worse:
-26.2176% at20bp and-38.4016% at40bp.

Cost decomposition reinforces the rejection. DEV union20 diagnostic had
+1.48009 equity units gross after saved fills/SL slip, but paid.80971 in entry
and exit fees plus.02249 funding stress;40bp lost despite+1.02995 gross because
fees rose to1.11777 plus.01549 funding. On2024 ALIGN diagnostic20, gross+.11272
was smaller than.12942 fees plus.00325 funding, yielding-1.9948%. This is not a
risk-rule artifact:unguarded2024 accounts also lose, while their DD is22-44%.

The economic diagnosis is that an extreme negative paid rate often describes a
persistent stressed regime rather than a short-lived exhaustion event. A single
8-hour settlement plus closed price/flow reversal cannot identify whether the
perpetual discount is actively normalizing. The next study therefore does not
retune the funding threshold. It adds a different high-frequency external state:
the closed premium-index path itself, and requires observable basis normalization.

## Durable evidence

- DEV branch research-funding-crowding-v7-dev-36922945306, commit
  b50bf033c75018ba8ec5a2a236709506e15910c5.
- Account branch research-funding-crowding-v7-results-36922945306, commit
  b4deda4fc8451200c07fd78890393a22c7f9482c.
- Full evidence branch research-funding-crowding-v7-evidence-36922945306,
  commit47917fd464cc7be06f90b50c70edab956b7aebb9. Archive contains32,837
  original files /912,814,457bytes; Git tree contains32,838 blobs including its
  10,670,670-byte manifest. Actual preservation log records successful push.
- Root path research/funding-crowding-v7/run-36922945306/.

No 2024 retuning, profit claim, clean-holdout label or daily-goal success is made.

