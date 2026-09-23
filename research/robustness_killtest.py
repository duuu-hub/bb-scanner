from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd
from research.exhaustive_context_study import build_features, load_total, policy_rows, choose_l1_simple, choose_l1, choose_l3, metrics, decluster

RNG=np.random.default_rng(20260923)
OUT=Path("robustness_killtest_results"); OUT.mkdir(exist_ok=True)

def load():
    tr=pd.read_csv("prior_context/trades_with_market_context.csv.gz")
    sig=pd.read_csv("prior_regime/regime_breadth_results/signals_with_regime.csv")
    return build_features(tr,sig,load_total("market_data_store/tradingview/TOTAL3.csv","t3"),load_total("market_data_store/tradingview/TOTAL3ES.csv","t3es"))

def policies(en):
    return {
      "L1_BTC_VOL_MID_SHORT":policy_rows(en,"L1_MOMENTUM_1H10",choose_l1_simple),
      "L1_VOL_PLUS_TOTAL3_SWITCH":policy_rows(en,"L1_MOMENTUM_1H10",choose_l1),
      "L3_BREADTH_SWITCH":policy_rows(en,"L3_4H_LAG",choose_l3)}

def walk_forward(ps):
    rows=[]
    for name,p in ps.items():
      p=p.copy(); p["dt"]=pd.to_datetime(p.signal_ts,unit="ms",utc=True)
      start=p.dt.min().floor("D"); end=p.dt.max()
      t=start+pd.DateOffset(months=12)
      while t<end:
        te=t+pd.DateOffset(months=3)
        for d in (1,2,3):
          g=p[(p.dt>=t)&(p.dt<te)&(p.delay_min==d)]
          rows.append({"policy":name,"test_start":str(t.date()),"delay_min":d,**metrics(g)})
        t=te
    return pd.DataFrame(rows)

def contributor(ps):
    rows=[]
    for name,p in ps.items():
      for split in ("train70","test30"):
       for d in (1,2,3):
        g=p[(p.split==split)&(p.delay_min==d)].copy()
        contrib=g.groupby("symbol").net_pct.sum().sort_values(ascending=False)
        for k in (0,1,3,5):
          drop=set(contrib.head(k).index) if k else set()
          z=g[~g.symbol.isin(drop)]
          rows.append({"policy":name,"split":split,"delay_min":d,"drop_top":k,**metrics(z)})
    return pd.DataFrame(rows)

def bootstrap_perm(ps,reps=5000):
    rows=[]
    for name,p in ps.items():
      for split in ("train70","test30"):
        g=decluster(p[(p.split==split)&(p.delay_min==1)],24)
        x=pd.to_numeric(g.net_pct,errors="coerce").dropna().to_numpy()
        if len(x)<2: continue
        boots=np.array([RNG.choice(x,len(x),replace=True).mean() for _ in range(reps)])
        # sign randomization: null of no directional edge while preserving magnitudes
        perms=np.array([(np.abs(x)*RNG.choice([-1,1],len(x))).mean() for _ in range(reps)])
        obs=x.mean()
        rows.append({"policy":name,"split":split,"n":len(x),"obs_avg":obs,
          "boot_p_avg_gt0":float((boots>0).mean()),"boot_lo":np.quantile(boots,.025),"boot_hi":np.quantile(boots,.975),
          "signperm_p_one_sided":float((perms>=obs).mean())})
    return pd.DataFrame(rows)

def threshold_sensitivity(en):
    # Rebuild BTC vol state from the already causal percentile; perturb tercile boundaries.
    rows=[]
    l1=en[en.base_strategy=="L1_MOMENTUM_1H10"].copy()
    for lo,hi in [(25,60),(30,63),(33.333,66.667),(37,70),(40,75)]:
      mid=(l1.btc_rv7d_pctile_365d>=lo)&(l1.btc_rv7d_pctile_365d<hi)
      for split in ("train70","test30"):
       for d in (1,2,3):
        g=l1[mid&(l1.split==split)&(l1.delay_min==d)&(l1.direction=="SHORT")]
        rows.append({"policy":"L1_BTC_VOL_MID_SHORT","lo_pct":lo,"hi_pct":hi,"split":split,"delay_min":d,**metrics(g)})
    # Breadth: perturb FLAT/CONTRACTING cut around zero using raw 1d breadth change if present.
    if "breadth_change_1d" in en.columns:
      l3=en[en.base_strategy=="L3_4H_LAG"].copy()
      for eps in (0,1,2.5,5):
       direction=np.where(l3.breadth_change_1d < -eps,"SHORT",np.where(abs(l3.breadth_change_1d)<=eps,"LONG","OFF"))
       for split in ("train70","test30"):
        for d in (1,2,3):
         g=l3[(l3.split==split)&(l3.delay_min==d)&(direction==l3.direction)&(direction!="OFF")]
         rows.append({"policy":"L3_BREADTH_SWITCH","lo_pct":-eps,"hi_pct":eps,"split":split,"delay_min":d,**metrics(g)})
    return pd.DataFrame(rows)

def main():
    en=load(); ps=policies(en)
    walk_forward(ps).to_csv(OUT/"walk_forward_12m_3m.csv",index=False)
    contributor(ps).to_csv(OUT/"leave_top_contributors_out.csv",index=False)
    bootstrap_perm(ps).to_csv(OUT/"bootstrap_permutation.csv",index=False)
    threshold_sensitivity(en).to_csv(OUT/"threshold_sensitivity.csv",index=False)
    print("=== kill-test complete ===")
    for f in OUT.glob("*.csv"): print(f, pd.read_csv(f).shape)
if __name__=="__main__": main()
