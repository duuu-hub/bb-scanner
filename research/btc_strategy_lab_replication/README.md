# BTC Strategy Lab Replication

Purpose: independently reproduce and stress-test wiktorj137/btc-strategy-lab EmaCrossFunding before combining it with existing bb-scanner research.

## Frozen source specification
- Market: BTC/USDT spot
- Timeframe: 1h
- Headline timerange: 2019-10-01 onward
- Fee: 0.1% per side
- Leverage: none
- Single position
- Entry: price crosses above EMA(600)
- Exit: close 2% below EMA
- Entry filter: skip entries when perpetual funding is above the 55th percentile threshold defined by the source implementation over its trailing 180-day history.

## Source headline benchmark
- Total return +1604%
- CAGR 50.9%
- MDD -33.8%
- Sharpe 1.31
- 96 trades
- Walk-forward: 8/13 profitable windows; compound OOS +1125%
These are source-reported numbers, not bb-scanner results.

## Validation order
1. Reproduce source implementation without parameter changes.
2. Compare EmaCross baseline vs EmaCrossFunding.
3. Re-run on bb-scanner's available BTC history.
4. Report yearly returns/trades, CAGR, Sharpe, PF, MDD, exposure.
5. Fee sensitivity and parameter-neighbourhood test.
6. Walk-forward / OOS.
7. Only if robust: test interaction with BTC/ETH Core and regime layer.

## Data status
A default-branch code search did not locate committed BTCUSDT 1h candles or funding data. Do not fabricate results. Use existing research data if available on non-default research branches; otherwise add a reproducible data acquisition step.

## Rule
Do not tune parameters to rescue a failed replication.
