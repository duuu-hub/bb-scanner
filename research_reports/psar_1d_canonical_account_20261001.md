# PSAR 1D SHORT canonical account validation — 2026-10-01

**판정: frozen signal의 원시 PF와 달리, 현재 계좌 규칙(포지션당 MTM equity 30%, 최대 6개, 진입 시 gross 200%)에서는 실전 승격 불가.** 40bp TRAIN은 큰 손실이고, HOLDOUT은 15분 MTM 기준 account equity가 0 이하가 되는 구간이 있어 hard-stop 파산 처리된다. 이 검증은 이미 관찰된 historical data를 사용하므로 pristine OOS가 아니다.

## 실행 근거

- Repo: `duuu-hub/bb-scanner`
- Branch: `research-rank5-binance-15m-5y`
- Frozen source Run: [36817442657](https://github.com/duuu-hub/bb-scanner/actions/runs/36817442657)
- Account Run: [36819257201](https://github.com/duuu-hub/bb-scanner/actions/runs/36819257201) — SUCCESS
- Job: `110231050426`
- Artifact: `11142867061` (`psar-1d-canonical-account-validation`)
- Account Run commit: `14d770bbb3205e8e52f1f6c880afe704d61aeebc`
- Frozen signal remains: 1D PSAR BEAR, Age=3, D0 > 2.0546962455657463, SHORT, first BULL reflip OPEN else max 7D OPEN.
- No signal threshold, horizon, regime, or HOLDOUT condition was retuned.

## Account contract frozen before results

1. Existing positions due at the OPEN are closed first.
2. Remaining positions are marked at the same synchronous OPEN.
3. Every accepted signal at that OPEN uses one common pre-entry MTM equity snapshot.
4. Position notional = 30% of that snapshot.
5. Maximum 6 simultaneous positions.
6. New entry is rejected if gross notional after entry would exceed 200% of that snapshot.
7. Same symbol overlap is forbidden.
8. Simultaneous candidates are sorted by **symbol ascending** only. No outcome/performance rank is used.
9. Round-trip costs 20/40/80bp are split half at entry and half at exit.
10. Funding is not included.

## Risk contract

- Primary MDD: synchronous **15m OPEN** mark-to-market equity.
- Additional stress: every open short marked at each symbol's 15m HIGH in the same 15m bucket. This is deliberately conservative because exact intrabar high timestamps differ by symbol.
- Short PnL is linear and uncapped. A loss beyond 100% of entry notional is retained rather than clipped.
- Primary hard stop: account MTM equity <= 0 => terminal account return -100%.
- Exact Binance forced-liquidation price is not claimed because margin mode, leverage and maintenance-margin tier are unspecified.
- `price >= 2x entry` is reported only as a 1x-isolated liquidation-risk proxy.

## Main result — 40bp

| Period | Raw signals | Executed | Trade PF (all daily exits) | Win% | 15m MTM result | 15m MDD | Max trade-loss streak |
|---|---:|---:|---:|---:|---:|---:|---:|
| TRAIN | 4,322 | 695 | 0.9380 | 49.50% | -77.68% | 96.43% | 15 |
| HOLDOUT | 6,349 | 515 | 0.9923 | 54.37% | **BANKRUPT / -100%** | **100%** | 11 |
| FULL | 10,671 | 1,210 | 0.9484 | 51.57% | **BANKRUPT / -100%** | **100%** | 15 |

HOLDOUT daily-OPEN-only accounting would have ended at -10.28%, but this is misleading for risk because the 15m path crossed zero before the daily exit. The hard-stop account result is therefore -100%.

### HOLDOUT 40bp capacity / use

- Executed: 515 / 6,349 raw signals.
- Skipped due max-6 cap: 5,726.
- Skipped due 200% gross cap: 108.
- Same-symbol skips: 0.
- Maximum simultaneous raw signals at one OPEN: 186.
- Days where signals exceeded available slots: 421.
- Average gross exposure while active: 163.19% of MTM equity.
- P95 gross exposure while active: 200.05%.
- Time with any position: 99.67%.
- Time at 6 positions: 75.91%.
- Entry cap is checked only at entry. When equity collapses afterward, gross/equity can exceed 200%; the very large max exposure near insolvency is therefore a denominator effect, not an entry-cap violation.

## Bankruptcy event

40bp HOLDOUT first reaches MTM equity <= 0 at:

- UTC: **2025-09-10 10:30**
- KST: **2025-09-10 19:30**
- Trades accepted before bankruptcy: 204.
- Trades closed before bankruptcy: 199.
- PF before bankruptcy in account currency: 1.2325.
- Win rate before bankruptcy: 58.29%.

The decisive tail-risk case is `BAKEUSDT`:

- Entry OPEN: 2025-09-08 00:00 UTC at **0.0393**.
- Position notional: about **0.7120** when initial account equity is normalized to 1.
- 2025-09-10 daily HIGH in the pinned Binance USD-M source: **0.2200**, about **5.60x** the entry.
- Observed worst mark loss for the position family: **-459.80% of entry notional**.
- Scheduled/reflip exit for this accepted trade: 2025-09-11 OPEN at 0.1218, gross trade return **-209.92% of notional**.
- Therefore a 30%-of-equity short can individually lose more than the whole account before the daily OPEN exit is reached.

The result is not fee-driven: FULL and HOLDOUT also hit the same 15m bankruptcy timestamp at 20bp and 80bp.

## Cost stress

Daily-OPEN accounting, before the 15m insolvency hard-stop:

| Period | 20bp | 40bp | 80bp |
|---|---:|---:|---:|
| TRAIN | -65.67% | -77.68% | -90.49% |
| HOLDOUT | +21.27% | -10.28% | -81.44% |
| FULL | -58.37% | -79.97% | -98.24% |

15m MTM hard-stop:
- HOLDOUT: bankrupt at 20/40/80bp.
- FULL: bankrupt at 20/40/80bp.
- TRAIN does not cross zero, but 40bp MDD is 96.43%.

## Interpretation / limitation

The original per-signal PF is not a portfolio PF. Signals are heavily clustered; under max 6 positions only a small minority can actually be taken, so the deterministic tie rule materially changes the realized sample.

The present symbol-ascending tie policy was frozen before this Run and must not be replaced after seeing these results as if a better ordering were untouched validation. Any alternative tie/ranking rule (for example D0 rank or deterministic hash) must be recorded only as a separate post-hoc sensitivity study, not as a replacement OOS result.

This account study also does not implement exact Binance maintenance-margin tiers, funding, ADL, mark-price liquidation, or leverage-specific liquidation price. Those would generally force liquidation before or around the modeled zero-equity boundary rather than rescue the canonical 30% short sizing from the BAKE tail event.

## Files

New code:
- `scripts/validate_psar_1d_canonical_account.py`
- `.github/workflows/psar-1d-canonical-account-validation.yml`

Artifact files:
- `account_validation.json`
- `account_summary.csv`
- `executed_trades.csv.gz`
