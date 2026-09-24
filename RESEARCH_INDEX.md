# Research Index

This branch is an isolated research workspace. Production/main behavior is not changed.

## Active: continuation mining
- Branch: `research-continuation-mining`
- Script: `research/continuation_mining.py`
- Data: existing `market_data_store/bitget/research_auto100_15m`
- Question: after a trend is already underway, which point-in-time features distinguish another +1% to +2% continuation from adverse movement?
- Primary label: +1.5% before -0.75%, evaluated at 1h/2h/3h.
- Candidate seed (not a trading threshold): |1h return| >= 1% with 4h return in same direction.
- Outputs: baseline rates, target/stop/horizon grid, single-feature lift, 2/3-feature intersections, labeled events.
- Guardrail: exploratory thresholds are not live rules. Any survivor requires chronological OOS validation and costs/slippage before demo use.

## Existing research families
- `research-bb-event-anatomy`: L1 event continuation vs reversal anatomy.
- `research-long3-relative-strength`: LONG3 relative-strength work.
- `research-l3-cross-universe`, `research-l3-lite-frequency`, `research-l3-time-limit`, `research-l3-tpsl-sweep`: L3 robustness/frequency/exit studies.
- `research-asl1-mirror`: mirrored ASL1 short study.
- `research-btc-eth-core`: BTC/ETH core research.
- `regime-breadth-research`: market breadth/regime work.

Do not rewrite historical branches merely for organization; preserve them as research provenance.
