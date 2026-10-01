# V8 premium absorption — actual failure audit

Actual repair run 36929463082, code
c049138866f628bb2de891b81a77b133de13359d. Validation job 110594857319
passed the frozen 256-input preflight, 181 tests, canonical chronology/invariants
and the official BTCUSDT 2022-01 premium-index probe (2,976 bars). All eight DEV
shards succeeded. The development branch is
research-premium-absorption-v8-dev-36929463082, commit
b05d753bff7a8fb0f752b221e1052b7fdaf768fe.

The complete immutable evidence archive is on branch
research-premium-absorption-v8-evidence-36929463082, commit
a35f285fb57926439fac98437c5d4a75f3cc52e4, under
research/premium-absorption-v8/run-36929463082/complete-evidence: 21,608 files,
2,006,309,686 bytes. Its 7,287,192-byte manifest is Git blob
531da70f0e8a6d33df832534f6f4052b9eb3da88.

## Result

- 16 entries / 96 policies / 531,854 resolved parameterized outcomes.
- 160 DATA_GAP and 6 ENTRY_MISMATCH policy outcomes were excluded; 399
  over-wide stops and 2 favourable catch-up gaps were rejected before outcomes.
- Premium coverage passed: 2021 100%; 2022 99.8844%; 2023 99.7796%, across
  256 symbols. Of 4,687 requested premium months, 4,626 were checksum-validated
  and 61 were explicit gaps. All 868 used minute months retained official
  checksum proof.
- **Zero development survivors.** Gate and account jobs were correctly skipped.

Every cell lost after the frozen 40 bp cost model. Cell mean price return ranged
from **−62.2867 to −19.0345 bp**; mean cost-inclusive risk return from
**−0.22681R to −0.06066R**; PF from **0.58295 to 0.88342**. Thus there were
0/96 positive overall price cells and 0/96 positive overall R cells.

The least-negative price cell,
PREMIUM_S+1_P10_N1_ANY__H96__TRAIL, still produced −19.0345 bp,
−0.09258R and PF 0.8834 over 2,951 outcomes/205 symbols. Its annual price means
were −97.3257 bp in partial 2021, −21.6002 bp in 2022 and −2.5580 bp in 2023.

The failure is not one unlucky side or setting. Group mean net40 was −36.51 bp
LONG and −36.12 bp SHORT; −39.92 bp at the 5 bp premium threshold and −32.71 bp
at 10 bp; −34.92 bp with BTC ANY and −37.70 bp with ALIGN4H; −36.87 bp at 6h
and −35.76 bp at 24h. Every cell had negative 2021 price/R, and only one cell
had positive 2023 price. The source gate passed, so this is an economic
rejection—not a missing-data explanation.

No account curve exists because no policy earned the right to reach account
replay. V8 therefore provides no account return or daily-target evidence and
does not meet the user's +0.7%–2% objective.

V9 changes the observable: prior-day cross-sectional residual leadership
and next-day intraday resumption. It does not retune funding or premium numbers.
