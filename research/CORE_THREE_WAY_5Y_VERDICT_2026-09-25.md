# Frozen core + L2 + Continuation: five-year portfolio transfer check

## Decision

Do **not** run all three modules together. The frozen Continuation SHORT did not
transfer from the Bitget AUTO100 research universe to the 2021–2026 Binance
USD-M archive. Keep original BTC/ETH core as the operational control; shadow
frozen L2 separately, with core-flat admission as the conservative variant.
The D2_0 early exit remains a post-hoc parallel shadow, not a replacement.

## Evidence and alignment

- Window: 2021-09-01 through 2026-08-31 inclusive, five complete calendar
  years; 2021 and 2026 columns are partial calendar years. BTC/ETH are Binance
  spot daily data, base fee 0.25% round trip. The newly reconstructed 2022–2025
  annual returns match the completed Stage8 artifact to displayed precision
  for **both** original and D2_0, without changing the core signal or exits.
- LONG3 L2 uses the **existing** run `36019652969` transaction CSV (Binance
  USD-M, 15-minute boundary, TP10/SL2.5, one-hour limit, fee 0.12%). Window
  contains 1,404 L2 candidates, mean +0.487%, PF 1.256. Prior signal parity
  versus the original 120-day scanner is ~82%, so this is a structural replay.
- Frozen Continuation SHORT uses 1h return <= -1%, 4h return <0, 24h return
  <= -9.858%, rv4h >= 1.214%, rv24h >= 1.315%; enter next 15m open,
  TP4/SL2, six-hour wall clock, once per symbol until exit, same-bar TP/SL
  counted as SL. Base fee plus slippage = 0.20%. **This is a venue and universe
  transfer**, not a repeat of the completed Bitget AUTO100 OOS run. On Binance
  it yields 60,393 trades, 696 symbols, mean -0.411%, PF 0.732. All six
  individual calendar years have PF below 1 (2021 .516; 2022 .775; 2023
  .473; 2024 .576; 2025 .787; 2026 .783). The July 31–August 31 2026 overlap
  alone has 2,222 trades, PF .822. These results invalidate that short as a
  cross-venue complement at the tested sizing; they do not disprove its
  original 53-day Bitget result.

## Three-module comparison (frozen costs)

All returns compound daily. MDD is **daily-close, booked-exit MDD**, and does
not include intraday open-position marks. Never interpret it as a verified
worst-case trading drawdown.

| Core exit | Allocation | Return | Daily-close MDD | L2 fills | SHORT fills | Max nominal exposure |
|---|---|---:|---:|---:|---:|---:|
| Original | Core only 100% | +136.17% | -26.09% | 0 | 0 | 100% |
| D2_0 | Core only 100% | +167.66% | -24.06% | 0 | 0 | 100% |
| Original | Separate 50/25/25 pools | -99.83% | -99.85% | 1,390 | 38,176 | 102.5% |
| D2_0 | Separate 50/25/25 pools | -99.82% | -99.84% | 1,390 | 38,176 | 102.5% |
| Original | Same pools; subs enter only when core flat | -99.92% | -99.93% | 1,042 | 38,775 | 195% |
| D2_0 | Same pools; subs enter only when core flat | -99.94% | -99.95% | 1,084 | 39,813 | 195% |
| Original | Core 100%; flat-period subs 30% per position | -100% (rounded) | -100% | 729 | 25,414 | 160% |
| D2_0 | Core 100%; flat-period subs 30% per position | -100% (rounded) | -100% | 768 | 26,057 | 160% |

Separate pools mean 50% of account to core, 25% to L2, 25% to short;
each alt position is 30% of its *own* pool (7.5% of whole account). The
equal-budget gate keeps these same notionals but admits subs only on a
core-flat signal day. Shared priority commits the full account to active
core and 30% of the whole account per sub position only at core-flat signal
times, reserving room for tomorrow's core; positions already open may finish
after the core turns on. These are different capital risks, hence compare
the first two rows against each other to isolate gating and use the shared
rows only alongside their actual exposure.

## Reuse the finished transactions: core + L2 only

No signal study or full five-year market download was repeated for this
calculation. Remove the failed SHORT and redirect its fixed 25% pool to core,
giving **core 75% / L2 pool 25%**. A matched version admits L2 only when core
is flat. The shared-priority version uses core 100% and L2 at 30% per position.

| Core exit | L2 allocation | Return | Daily-close MDD | Max exposure |
|---|---|---:|---:|---:|
| Original | Independent 75/25 | +215.20% | -18.54% | 97.5% |
| Original | 75/25; admit only when core flat | +193.07% | -18.43% | 82.5% |
| Original | Core 100%, L2 only while flat | +968.98% | -33.84% | 130% |
| D2_0 | Independent 75/25 | +244.59% | -18.93% | 97.5% |
| D2_0 | 75/25; admit only when core flat | +225.86% | -19.19% | 82.5% |
| D2_0 | Core 100%, L2 only while flat | +1188.21% | -31.70% | 130% |

The first two columns isolate core-regime admission at the same core and L2
notionals. Ungated L2 wins at base costs but the extra trades are cost
sensitive. The shared 100%/30% construction dramatically increases gross
notional and trading risk; its backtest return is **not** evidence that it
is a better capital allocation.

Additional *round-trip* L2 cost, with core cost frozen at 0.25%:

| Core | Allocation | +0.25%: return / DD | +0.50%: return / DD |
|---|---|---:|---:|
| Original | Independent 75/25 | +143.1% / -19.3% | +87.4% / -20.8% |
| Original | Flat admission 75/25 | +141.0% / -18.9% | +98.1% / -21.0% |
| Original | Core 100%, L2 30% | +390.8% / -43.3% | +124.7% / -57.3% |
| D2_0 | Independent 75/25 | +165.7% / -19.8% | +104.9% / -20.6% |
| D2_0 | Flat admission 75/25 | +165.9% / -20.0% | +116.9% / -20.8% |
| D2_0 | Core 100%, L2 30% | +473.1% / -40.5% | +154.3% / -55.4% |

At +0.50% the apparent return advantage over the original 100% core
(+136.2%) **disappears** in every original-core allocation shown. D2_0
base-core advantage also disappears against the D2_0-only +167.7%. The
flat-admission alternative is therefore a *risk/cost research candidate*,
not an established superior portfolio. The open-core L2 PF in this window
is 1.168 on 350 raw signals, versus flat-core PF 1.285 on 1,054 signals
before extra costs. Neither gating nor D2_0 can be declared OOS: both
analyses reuse material that informed research decisions.

## Implementation limitations / next forward decision

- L2 replay has ~82% signal identity on the original 120 days. The five-year
  universe and survivorship conditions differ from the present live scanner.
- Exit-booked daily MDD omits the within-day and midnight open-position path;
  financing, borrow, forced-liquidation and daily BTC/ETH overnight price gaps
  are not simulated. Risk comparisons require marked-to-market paper-trading.
- Core regime is checked at each sub signal day. Sub positions may finish
  shortly after core reactivates; maximum measured exposure is reported.
- D2_0 was discovered after looking at core results. Higher historical return
  cannot be used as independent evidence to replace original core.
- The original Continuation OOS and this five-year Binance transfer have
  different exchanges, point-in-time symbol selections and date distributions.
- For demo forwarding: keep original core as control, trial frozen L2 as
  **core-flat shadow with separately tracked virtual capital**, and keep D2_0
  alongside original core as exit-only shadow. Do not attach the transferred
  Continuation short to the three-strategy demo. Do not activate live trading.

## Reproducibility and run state

- Research branch: `research-core-portfolio-5y`; script
  `research/core_three_way_5y.py`, workflow
  `.github/workflows/core-three-way-5y.yml`.
- Completed data-producing run: `36072122659`, artifact
  `core-three-way-5y-portfolio` (`10839041352`); reuse of L2 run
  `36019652969` and canonical release `binance-15m-5y-canonical-v1`.
- Job status is red **only because `tee artifacts/run.log` ran before
  `artifacts/` was created**. Python finished, printed both result tables and
  uploaded all 17 output files. Workflow-only fix creates the log directory;
  this previously completed, expensive computation was not rerun.
- Core+L2 and cost stress use the first run's exact candidate trades and
  core daily file locally; neither is a new historical signal search.
