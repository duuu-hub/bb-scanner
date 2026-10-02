# V15 broad-market expansion continuation — preregistration

Registered 2026-10-02 before V15 code or market outcomes. Research-only branch `research-breadth-expansion-continuation-v15`; do not alter main, live/demo orders, watcher state, runtime configuration, or unrelated workflows.

## V14 actual rejection and distinct economic hypothesis

V14 actual retry run36998066185/code3b309e6b025fe0eb66c8be3ec0dbaad84b7e8344 passed pinned377 tests and all eight canonical DEV scans. It examined 96 policies with N291–1778 and 126–185 symbols. Zero policies survived. After 40bp costs, 92/96 policy means were non-positive and 96/96 risk-normalized R means were non-positive. Net means ranged -123.121048 to +4.083955bp, R -0.339145 to -0.016948, PF0.360349–1.023408. Best price cell BREADTH_S+1_H96_P35_PRICE_ONLY__H48__TP2: N1778/185symbols, +4.083955bp, R-0.030562, PF1.023408; its 2021/2022/2023 R means were all negative. Best R cell BREADTH_S+1_H96_P35_FLOW55__H48__TP3: N713/163symbols,+2.808992bp,R-0.016948,PF1.016830; 2021 and2023 R were negative. All48 SHORT cells lost on price; their best mean was -13.952704bp. Gate/account jobs correctly skipped. V14 tested pressure withdrawal plus local reversal; no account/daily-goal conclusion.

V15 conjecture is economically opposite and not a V14 threshold repair: a newly expanding fraction of liquid coins moving in one direction indicates a common information/liquidity impulse that can persist for several hours. Enter a coin already participating strongly in the same direction after a closed continuation bar. This is breadth expansion plus local momentum continuation, not pressure withdrawal/reversal, local level retest, BTC-only lag, or cross-sectional rank reversion. No order-book/liquidation mechanism is claimed.

## Frozen source and periods

Use the identical immutable Binance USD-M15m source run36095439671, eight original shards,856 source files, prior256 baseline and BTC context. Exact inherited hashes must match V14 contextSHA256 4cbf469c0c0d4efa9c63d19c9650bbaf1e8ded8f38bc4195ec2d702d2eea9614 and baselineSHA256 0c3f09f41127e29cbf7130891e9f2c1389c99798d43e470825c973acbcd5121f. DEV is2021-09..2023. 2024 and2025..2026-08 are already observed and cannot tune V15. September may also be exposed. Physically cut inputs at stage end.

Eligibility is unchanged from V14: source existed before stage end minus30days; at decision time at least30 elapsed days and30 distinct observed source days; closed rolling24h quote turnover>=20m; exact contiguous finite returns; no future catalogue, imputation, nearest-time or forward-fill. Global fractions require all eight verified source shards and denominator>=30 at current and immediately previous bar.

## Exact 16 entry families / 96 policies

Side LONG/SHORT × horizon16/96 closed15m bars × breadth threshold10%/20% × PRICE_ONLY/FLOW55 =16 entry families. Holds16/48bars × TP2/TP3/TRAIL =96 fixed policies. No other thresholds/horizons/exits may be searched.

For horizon16 use directional return magnitude2%; for horizon96 use5%. At each closed timestamp t:
- UP fraction=count eligible coins with exact horizon return>=magnitude / eligible_n.
- DOWN fraction=count eligible coins with exact horizon return<=-magnitude / eligible_n.
- LONG requires previous UP fraction<threshold, current UP fraction>=threshold, and increase>=5 percentage points.
- SHORT requires analogous DOWN-fraction crossing and increase.
- The coin's exact horizon return at the current closed bar must be >=magnitude LONG or <=-magnitude SHORT.

Local continuation confirmation, all known at the closed decision bar:
- directional candle body;
- close above prior4-bar high LONG / below prior4-bar low SHORT;
- CLV>=0.75 LONG / <=0.25 SHORT;
- current quote turnover / prior96-bar mean>=1.25;
- prior ATR14 positive and finite;
- current abs15m coin return<=8%;
- exact contemporaneous closed BTC abs15m return<=2.0%;
- FLOW55 additionally taker-buy share>=0.55 LONG / <=0.45 SHORT; PRICE_ONLY has no taker-share threshold.

Entry is next exact contiguous15m OPEN. Reject only favorable gap>0.5%; retain adverse gaps. Structural stop is opposite extreme of current and prior4 bars minus/plus0.25 prior ATR, on the loss side of actual fill; risk floor0.5%, reject risk>6%. No entry-bar H/L/C influences decision or sizing. Cooldown16 signal bars exit-independent. Fixed priority=breadth_delta*sqrt(volume_multiple)*abs(horizon_return)/risk_pct. Preserve numerator/denominator/fraction current+previous, exact timestamps, return, volume, stop and all provenance.

## Execution, selection and account contract

Use the unchanged official Binance1m conservative chronology: ignore pre-entry exits; entry-minute any exit touch including TP-only is LOSS; established same1m TP/SL collision LOSS; proven earlier TP honored. DATA_GAP/ENTRY_MISMATCH/EXIT_MISMATCH are excluded, counted and audited with original ZIP/checksum preserved.

Costs:20/40bp roundtrip, SL/forced adverse10bp, funding2bp/holding-day. Broad DEV selection requires N>=300, positive global net40 mean and net40 R, required annual/equal-date positive checks, adequate active dates and concentration rules inherited from V14. Freeze candidates before any2024 processing.

Account comparison: same equity1,0.5% trade-risk budget,2% aggregate risk,30% coin nominal,200% gross,max6,no same-coin overlap; KST observed day-2% flatten/block; peakDD10% risk-half/DD15% flatten/permanent split halt. Include all853DEV and367GATE calendar days including idle/halted/partial days. Primary judgment is net account growth,MDD,executable N,target-day rate; PF/EV are diagnostics.

If no DEV survivor, preserve every cell/ledger/source/hash/exclusion/log and stop V15 gate/account stages. Quantify price,R,annual,side,horizon,cost and sample failures before preregistering a genuinely different V16. Repairs do not count as discoveries. If a candidate survives, freeze first, then account/gate and only afterward neighbours,60/80bp,+1/+3m entry delay,time blocks,recent and forward data. No historical result is a profit guarantee or clean holdout.
