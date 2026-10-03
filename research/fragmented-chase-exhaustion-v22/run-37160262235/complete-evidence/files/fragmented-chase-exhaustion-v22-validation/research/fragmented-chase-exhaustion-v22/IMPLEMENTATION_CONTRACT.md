# V22 Implementation Contract

Frozen plan: `PLAN.md` at commit `72fe5ff070f763cb6438700a18574f2aaabcd7c9`.

- Source: immutable Binance USD-M 15m run 36095439671, eight shards, 856 files.
- Mandatory fields: OHLC, quote turnover, positive integer trade count, taker-buy quote.
- Causal baselines reset at gaps and use bars through i-1 only.
- Event, separate confirmation, and next-open entry are distinct contiguous bars.
- Frozen geometry: 16 entries × 2 holds × 3 exits = 96 cells.
- DEV selection, official Binance 1m chronology, and common guarded account engine are unchanged.
- No threshold changes after observing V22 results.
