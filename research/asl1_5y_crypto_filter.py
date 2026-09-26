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
# Pre-entry-only filter grid: avoid ex-post calendar labels. Filters are frozen simple thresholds.
d["rv_ratio"]=d.asset_rvpre/d.asset_rvmed.replace(0,np.nan)
base=d[(d.side=="LONG")&(d.hold_h==24)].copy()
filter_rows=[]
filters={"BASE":pd.Series(True,index=base.index),"DROP_GE_-3":base.asset_ret4h<=-3,"DROP_GE_-4":base.asset_ret4h<=-4,"RV_LT_1.5":base.rv_ratio<1.5,"RV_LT_2.0":base.rv_ratio<2.0,"DROP3_RV2":(base.asset_ret4h<=-3)&(base.rv_ratio<2.0)}
for nm,m in filters.items():
    z=base[m]
    st=stat(z); filter_rows.append({"filter":nm,**st.to_dict()})
pd.DataFrame(filter_rows).to_csv(OUT/"long24_filter_grid.csv",index=False)
# BTC regime + signal breadth filters, all known at entry
base["entry_dt"]=pd.to_datetime(base.entry_dt,utc=True); base["day"]=base.entry_dt.dt.floor("D")
breadth=base.groupby("day").size(); base["day_signals"]=base.day.map(breadth)
base["btc_above50"]=base.btc_open>base.btc_ma50; base["btc_above200"]=base.btc_open>base.btc_ma200
regimes={"BTC24_GT_-2":base.btc_ret24h>-2,"BTC24_GT_-4":base.btc_ret24h>-4,"BTC_ABOVE50":base.btc_above50,"BTC_ABOVE200":base.btc_above200,"BREADTH_LE_10":base.day_signals<=10,"BREADTH_LE_20":base.day_signals<=20,"BREADTH_LE_40":base.day_signals<=40,"BTC24_-4_B20":(base.btc_ret24h>-4)&(base.day_signals<=20),"DROP3_RV2_B20":(base.asset_ret4h<=-3)&(base.rv_ratio<2)&(base.day_signals<=20),"DROP3_RV2_B20_BTC4":(base.asset_ret4h<=-3)&(base.rv_ratio<2)&(base.day_signals<=20)&(base.btc_ret24h>-4)}
for n in [30,40,50,60,80,100]:
    regimes[f"BTC_ABOVE{n}"]=base.btc_open>base[f"btc_ma{n}"]
reg_rows=[]
for nm,m in regimes.items():
 z=base[m]; st=stat(z); reg_rows.append({"filter":nm,**st.to_dict()})
pd.DataFrame(reg_rows).to_csv(OUT/"long24_regime_grid.csv",index=False)
print("\n=== LONG24 REGIME GRID ===\n"+pd.DataFrame(reg_rows).to_string(index=False))

print("\n=== LONG24 FILTER GRID ===\n"+pd.DataFrame(filter_rows).to_string(index=False))

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

# Event-driven equal-capital portfolio comparison.
def portfolio(g,name,slots=10,alloc=0.10):
    z=g.sort_values(["entry_dt","symbol"]).copy()
    z["entry_dt"]=pd.to_datetime(z.entry_dt,utc=True); z["exit_dt"]=pd.to_datetime(z.exit_dt,utc=True)
    cash=1.0; eq=1.0; peak=1.0; mdd=0.0; active=[]; executed=0; skipped=0; exposure=0.0
    start=z.entry_dt.min(); end=z.exit_dt.max()
    for t,batch in z.groupby("entry_dt",sort=True):
        due=[p for p in active if p["exit"]<=t]
        for p in sorted(due,key=lambda x:(x["exit"],x["symbol"])):
            pnl=p["stake"]*p["ret"]; cash+=p["stake"]+pnl; eq+=pnl
            peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
        active=[p for p in active if p["exit"]>t]
        stake=eq*alloc
        for row in batch.sort_values("symbol").itertuples():
            if len(active)>=slots or cash+1e-12<stake: skipped+=1; continue
            cash-=stake; active.append({"exit":row.exit_dt,"stake":stake,"ret":float(row.net_ret),"symbol":row.symbol}); executed+=1
            exposure+=(row.exit_dt-t).total_seconds()/86400.0*stake
    for p in sorted(active,key=lambda x:(x["exit"],x["symbol"])):
        pnl=p["stake"]*p["ret"]; cash+=p["stake"]+pnl; eq+=pnl
        peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
    years=max((end-start).total_seconds()/(365.25*86400),1/365.25)
    return {"variant":name,"signals":len(z),"executed":executed,"skipped":skipped,"skip_pct":skipped/max(len(z),1)*100,
      "final_equity":eq,"total_return_pct":(eq-1)*100,"CAGR_pct":(eq**(1/years)-1)*100,
      "MDD_realized_pct":mdd*100,"avg_capital_use_pct":exposure/max((end-start).total_seconds()/86400.0,1)*100}
pbase=base.copy()
variants={"BASE":pbase,"BTC24_GT_-2":pbase[pbase.btc_ret24h>-2],"BTC_ABOVE50":pbase[pbase.btc_open>pbase.btc_ma50]}
port=pd.DataFrame([portfolio(v,k) for k,v in variants.items()])
port.to_csv(OUT/"long24_portfolio_compare_corrected.csv",index=False)
print("\n=== CORRECTED LONG24 PORTFOLIO 10 SLOTS x 10% ===\n"+port.to_string(index=False))
