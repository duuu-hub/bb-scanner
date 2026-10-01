# PSAR 1H Pre-Break Fixed-SL checkpoint — 2026-10-01

원본 Run: [36811700963](https://github.com/duuu-hub/bb-scanner/actions/runs/36811700963)
원본 engine blob: `6ef1f72760e468afed3f2386744fa6da9385def6`.
중간 합산: 성공 42개 shard, 562/855 파일. BNXUSDT 제외.
전체 완료 결과가 아니며 수수료·슬리피지·funding·포트폴리오 제약은 반영하지 않았다.

| 승률 구간 | 방향 | 확정 신호 수 | 승률 | Gross PF (R) | Gross EV (R) | 진입거리 설정 | SL | PSAR 기준 TP |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 20~30% | SHORT | 347,472 | 20.42% | 1.13556 | +0.10788R | 0.1% | 3 ATR | 20% |
| 30~40% | SHORT | 350,079 | 31.50% | 1.10258 | +0.07027R | 7% | 2 ATR | 4% |
| 40% 이상 | SHORT | 349,964 | 44.35% | 1.09542 | +0.05311R | 6% | 3 ATR | 3% |

- 승률 ≥20% 및 진입거리 >0 조합은 4,704개, 그중 Gross PF ≥1 조합은 2,131개. 전부 SHORT.
- LONG 최고는 `P0.1|SB2|TP10|LONG`: 348,615개, 승률 22.09%, Gross PF 0.97085, EV -0.02271R.
- 동일 시각·동일 종목·다른 regime 포지션이 겹칠 수 있으므로 이 수는 계좌에서 실행 가능한 거래 수가 아니다.
- 0거리 조합은 PSAR에 닿은 후 진입하는 비교용 조건이라 엄격한 Pre-Break 후보에서 제외했다.
- 상위 조건 주변에도 숏 Gross PF >1 구간이 있었으나 SL 3 ATR 경계, 장거리 TP, 보유기간 및 미종결 거래를 추가 검증해야 한다.
- 기간 전체의 탐색 결과이며 독립 holdout 통과나 실전 수익을 증명한 결과가 아니다. 결과 확인 뒤 임계값 재튜닝은 하지 않는다.

## 검증과 수정

기존 workflow의 30+10 반복은 `assert True`였고 실제 검증이 아니었다.
고정 원본 엔진을 대상으로 30개 실제 동작 검사 및 전체 검사 10회 연속 반복을 로컬에서 통과했다.
검사 범위는 PSAR/ATR 인과성, 정상 SL geometry, 접근 방향, regime 중복 진입/reset,
공식 1m 파일 parser, 같은 1m entry+exit LOSS, mismatch/gap 처리 및 accounting이다.
1m 검사 데이터는 합성 archive이며 실전 체결 품질을 검증하는 검사는 아니다.

추가로 원본에서 발견한 오류:
1. intrabar 접근 trigger를 maker로 분류함.
2. 진입선을 뛰어넘는 15m OPEN gap을 straddle 조건 때문에 놓칠 수 있음.

연구 브랜치의 `scripts/psar_1h_breakthrough_compare.py`는 이후 검증용으로 수정했다.
모든 접근 진입은 taker로 분류한다. 15m OPEN gap은 실제 OPEN으로 체결하며,
이미 PSAR에 닿거나 넘은 fill은 금지한다. 정상 intrabar trigger는 방향별 HIGH/LOW로 확인한다.
SL은 실제 fill 기준 N ATR이며 TP는 고정 PSAR 기준으로 유지한다.
수정 엔진도 강화된 30개 검사와 10회 연속 반복을 통과했다.
미래 workflow validate도 실제 runtime audit로 교체했다.

기존 실행 Run은 기존 commit/blob에 고정되어 있으므로 수정 사항이 그 결과에 반영되지 않는다.
원본 전체 scan의 성공 shard를 재실행하지 않았다. 수정 엔진과 원본 shard를 같은 합산에 섞지 않는다.
다음 비용/ledger 검증은 수정된 엔진으로 별도 수행하며 1m 내부 gap/slippage, 손절 slippage,
same-symbol overlap, 노출 제한, equity curve 및 MDD도 따로 확인한다.

자동 원본 검증·합산 Run:
[36817181279](https://github.com/duuu-hub/bb-scanner/actions/runs/36817181279).
원본 64개 성공 shard만 합산한다. 실패가 발생하면 실패 shard 번호를 남기고 멈추며,
성공한 원본 artifact는 그대로 보존한다.

`main`과 live forward-state 파일은 수정하지 않았다.
