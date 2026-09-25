from __future__ import annotations
import json, urllib.request
from pathlib import Path
import pandas as pd, numpy as np
IN=Path("research_output/asl1_5y_oos/trades.csv"); OUT=Path("research_output/asl1_5y_crypto"); OUT.mkdir(parents=True,exist_ok=True)
# Prefer USD-M underlyingType. GitHub-hosted runners can receive Binance HTTP 451,
# so fall back to the public data-api spot universe as a conservative crypto-only proxy.
mode="USD_M_UNDERLYING_TYPE"
try:
    with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo",timeout=30) as r:
        info=json.load(r)
    coin={x["symbol"] for x in info["symbols"] if x.get("quoteAsset")=="USDT" and x.get("underlyingType")=="COIN"}
except Exception as e:
    mode="SPOT_USDT_PROXY"
    print(f"USD_M_EXCHANGEINFO_UNAVAILABLE {type(e).__name__}: {e}; using {mode}",flush=True)
    with urllib.request.urlopen("https://data-api.binance.vision/api/v3/exchangeInfo",timeout=30) as r:
        info=json.load(r)
    coin={x["symbol"] for x in info["symbols"] if x.get("quoteAsset")=="USDT" and x.get("status")=="TRADING"}
d=pd.read_csv(IN); before=d.symbol.nunique(); d=d[d.symbol.isin(coin)].copy(); after=d.symbol.nunique()
print(f"CRYPTO_FILTER mode={mode} symbols_with_trades {before} -> {after}; rows {len(d)}",flush=True)
def stat(g):
    r=g.net_ret.astype(float); gp=r[r>0].sum(); gl=-r[r<0].sum(); eq=(1+r).cumprod(); dd=eq/eq.cummax()-1
    return pd.Series({"trades":len(g),"win_rate_pct":(r>0).mean()*100,"avg_net_pct":r.mean()*100,"PF":gp/gl if gl>0 else np.inf,"sum_net_pct":r.sum()*100,"trade_seq_MDD_pct":dd.min()*100})
o=d.groupby(["side","hold_h"]).apply(stat,include_groups=False).reset_index(); o.to_csv(OUT/"overall.csv",index=False)
d["year"]=pd.to_datetime(d.entry_dt,utc=True).dt.year
y=d.groupby(["side","hold_h","year"]).apply(stat,include_groups=False).reset_index(); y.to_csv(OUT/"yearly.csv",index=False)
s=d.groupby(["side","hold_h","symbol"]).apply(stat,include_groups=False).reset_index().sort_values(["side","hold_h","sum_net_pct"],ascending=[True,True,False]); s.to_csv(OUT/"symbols.csv",index=False)
# deeper diagnostics for the leading LONG 24h variant
q=d[(d.side=="LONG") & (d.hold_h==24)].copy()
q["entry_dt"]=pd.to_datetime(q.entry_dt,utc=True); q["month"]=q.entry_dt.dt.to_period("M").astype(str); q["quarter"]=q.entry_dt.dt.to_period("Q").astype(str); q["hour"]=q.entry_dt.dt.hour
for col,name in [("month","monthly"),("quarter","quarterly"),("reason","exit_reason"),("hour","entry_hour")]:
    q.groupby(col).apply(stat,include_groups=False).reset_index().to_csv(OUT/f"{name}.csv",index=False)
# robustness: exclude tiny-history symbols and summarize per-symbol dispersion
sc=s[(s.side=="LONG")&(s.hold_h==24)].copy(); sc.to_csv(OUT/"long24_symbols.csv",index=False)
for n in [5,10,20,50]:
    z=sc[sc.trades>=n]; print(f"LONG24 SYMBOLS trades>={n}: symbols={len(z)} medianPF={z.PF.replace([np.inf,-np.inf],np.nan).median():.4f} positivePF={(z.PF>1).mean():.3f}",flush=True)
d.to_csv(OUT/"trades_crypto.csv",index=False)
# Market-regime comparison using only information available at entry.
# Join BTC daily trend/volatility proxies is handled in the upstream 15m replay next pass;
# here first classify observable trade-cluster regimes from signal frequency and contemporaneous LONG24 outcomes.
q24=d[(d.side=="LONG")&(d.hold_h==24)].copy(); q24["entry_dt"]=pd.to_datetime(q24.entry_dt,utc=True); q24["date"]=q24.entry_dt.dt.floor("D")
daily=q24.groupby("date").apply(stat,include_groups=False).reset_index(); daily["signal_count"]=q24.groupby("date").size().values
daily.to_csv(OUT/"long24_daily_regime.csv",index=False)

print("\n=== CRYPTO OVERALL ===\n"+o.to_string(index=False)); print("\n=== CRYPTO YEARLY ===\n"+y.to_string(index=False))
print("\n=== LONG24 MONTHLY ===\n"+q.groupby("month").apply(stat,include_groups=False).reset_index().to_string(index=False))
print("\n=== LONG24 EXIT ===\n"+q.groupby("reason").apply(stat,include_groups=False).reset_index().to_string(index=False))
