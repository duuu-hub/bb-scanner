# Continuation execution verdict — 2026-09-25

## One-line conclusion

The frozen high-volatility continuation SHORT family survived corrected OOS execution and cost/delay stress, but the evidence is only a 53-day OOS window and is concentrated in a few symbols. Promote one central representative to **demo/shadow only**, not to the funded core-off module.

## Frozen representative for forward shadow

Do not select the highest-PF exit after seeing OOS. Use the central robust setting:

- Side: SHORT
- Candidate impulse: ret_1h <= -1% and ret_4h < 0
- Frozen filter: rv_24h >= 1.315, rv_4h >= 1.214, ret_24h <= -9.858%
- Entry: next 15-minute bar open
- Exit: TP 4%, SL 2%, wall-clock limit 6 hours
- Same-symbol rule: one open position at a time
- Status: demo/shadow only; no live trading

The TP5/SL3 point had the highest measured PF, but choosing it now would be a same-OOS best-point selection. TP4/SL2 is preferred as a central point within a broad profitable surface, not because it was the maximum.

## Corrected OOS evidence

Period: 2026-07-31 01:00 UTC to 2026-09-22 22:45 UTC (about 53 days).

For TP4/SL2/6h after suppressing repeated same-symbol entries:

| Case | Trades | Symbols | Avg net/trade | PF |
|---|---:|---:|---:|---:|
| Base total round-trip cost 0.20% | 792 | 24 | +0.761% | 1.691 |
| Base +0.25% extra cost (0.45% total) | 792 | 24 | +0.511% | 1.416 |
| Base +0.50% extra cost (0.70% total) | 792 | 24 | +0.261% | 1.193 |
| Extra 15-minute entry delay, 0.20% total cost | 808 | 24 | +0.645% | 1.563 |
| Extra 15-minute delay, +0.50% extra cost | 808 | 24 | +0.145% | 1.103 |

Calendar thirds at base cost were all positive:

- First third: n=351, PF 1.751
- Middle third: n=183, PF 1.747
- Final third: n=258, PF 1.574

There were about 41 non-overlapping 24-hour marketwide signal windows. Trade count is therefore not the same as independent-event count.

## Concentration and repeat signals

- Raw signals: 1,626
- Accepted after same-symbol one-position rule: 792
- Suppressed repeated same-symbol signals: 834 (51.29%)
- Symbols: 24
- Top contributor: BEATUSDT, 44.0% of total net trade PnL
- Top 3 contributors: 73.8% of total net trade PnL
- Worst leave-one-symbol-out case: removing BEATUSDT, PF 1.558
- Maximum simultaneous accepted positions observed: 6
- Five slots captured 789/792 signals (99.62%)

Concentration is material, but removing the largest symbol did not erase the edge.

## Execution corrections and audit

Two execution defects and one optimistic ambiguity treatment were corrected on the research branch:

1. Row-count time limits could exceed the intended wall-clock horizon across missing candles.
2. Same-entry-candle exits were processed before entries in the portfolio event loop, leaving positions stuck and invalidating slot counts.
3. Trades touching both TP and SL in one 15-minute candle were previously dropped. They are now scored conservatively as SL.

Final timing audit:

- Over-horizon trades: 0
- Maximum wall-clock hold for the 6-hour SHORT family: 5.75 hours
- Slot capture is now plausible; five slots captured at least 99.33% for all SHORT exit variants.

## Important limitations

- Only about 53 days of chronological OOS execution data.
- Only 24 SHORT symbols and roughly 40–41 independent 24-hour windows.
- Symbol/PnL concentration remains high.
- The +15-minute test is not a 1/2/3-minute latency test.
- The slot-study MDD is not full mark-to-market MDD; active positions are held at stake value until exit. Do not use the small reported slot MDD to size real risk.
- This 2026 OOS window does not overlap the 2018–2025 BTC/ETH core replay, so it does not prove that the strategy earns specifically during core-off periods.
- Entry/filter discovery used an earlier training window and chronological OOS, but many features/combinations and exit candidates were researched. False-discovery risk remains.
- Live trading remains disabled.

## Portfolio interpretation

Current evidence ranking:

1. BTC/ETH frozen core remains the primary strategy.
2. Frozen LONG3 L2 remains the strongest evidenced core-off module because it survived a multi-year replay.
3. Continuation TP4/SL2/6h SHORT becomes the highest-priority new **parallel demo/shadow hedge candidate**, ahead of Rank5 SHORT on sample size, but not ahead of L2 on time-span reliability.
4. Rank5 SHORT remains a separate thin-sample hedge shadow.
5. ASL1 and AOA remain rejected for the core-off role.

Do not combine L2 and Continuation into a funded portfolio yet. The first forward question is whether Continuation remains profitable outside this 53-day episode and whether its returns actually occur when the BTC/ETH core is inactive.

## GitHub record

- Branch: research-continuation-mining
- Wall-clock/slot fix commit: c4bb7416aa95b83719e404606b1538ed959e26e4
- Conservative ambiguous-bar commit: 441369273ec9215dcedcc493da97978050449b53
- Corrected run before ambiguity stress: 36026820579
- Final conservative run: 36030563168
- Final artifact: continuation-execution-results (artifact 10822952106)
