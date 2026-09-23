# Demo auto-trading runtime

This directory is the strategy-agnostic execution layer.

## Safety state
- DEMO only
- live trading disabled in configuration
- auto execution disabled by default
- active strategy OFF by default
- exchange-side TP/SL is required after every fill
- the manager continues to enforce max-hold and TP/SL checks even when new entries are OFF

## Signal contract
Append one JSON object per line to `signals/pending.jsonl`:

```json
{"signal_id":"BULL_L1:BTCUSDT:123456","strategy":"BULL","symbol":"BTCUSDT","side":"LONG","signal_time_ms":123456,"entry_min":85000,"entry_max":86000,"tp":90000,"sl":82000,"max_hold_minutes":720}
```

The future strategy layer only has to emit this contract. It does not need to know Bitget order details.

## Strategy switch
`config/trading_config.json -> active_strategy`

Supported planned regimes: BULL, RANGE, BEAR, plus OFF. Switching affects new entries only. Existing tracked positions retain their own TP, SL and max-hold deadline.

## Two-key execution interlock
New Demo orders require both:
1. `active_strategy` to match the incoming signal
2. `demo_auto_execute=true`

The repository currently ships with OFF + false.

## Persistent files
- `state/trading_state.json`: processed signal IDs and tracked open/closed trades
- `logs/executions.jsonl`: audit log
- `signals/pending.jsonl`: strategy signal inbox
