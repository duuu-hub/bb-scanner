# Execution safety layer

This layer sits between a strategy signal and the Bitget order adapter. It is intentionally exchange-agnostic and sends no orders by itself.

A new signal is allowed only when all checks pass: active market-regime switch, TTL, entry range, spread, 1-minute path sanity, duplicate protection, verified current-position snapshot, same-symbol position check, and gross-exposure cap.

The default regime is **OFF**. Changing regimes affects only new entries. Existing positions keep the TP/SL/max-hold values stored when they were opened.

The default policy is deliberately fail-closed: if current exchange positions cannot be verified, new automated entries are blocked. This matters until the Bitget Classic position-read permission issue is resolved.

Regime values:
- BULL: rising-market strategy group
- RANGE: sideways-market strategy group
- BEAR: falling-market strategy group
- OFF: no new entries

Current research defaults in `config/trading_mode.json` are placeholders for the execution environment, not validated strategy parameters.
