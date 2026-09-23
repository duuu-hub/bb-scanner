from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

SMC=Path("research/results/smc_candidate_filter/trades.csv")
LONG3=Path("candidate1_artifact/candidate1_trades.csv")
OUT=Path("research/results/smc_long3_correlation")
LONG3_NAMES=["L1_MOMENTUM_1H10","L2_EXPLOSIVE_4H30","L3_4H_LAG"]


def safe_corr(a,b):
    if len(a)<3 or a.std()==0 or b.std()==0:
        return np.nan
    return float(a.corr(b))


def spearman(a,b):
    if len(a)<3:
        return np.nan
    return safe_corr(a.rank(method="average"),b.rank(method="average"))


def summarize_pair(candidate, smc, l3, label):
    start=max(smc.entry_time.min(),l3.entry_time.min())
    end=min(smc.entry_time.max(),l3.entry_time.max())
    s=smc[(smc.entry_time>=start)&(smc.entry_time<=end)].copy()
    l=l3[(l3.entry_time>=start)&(l3.entry_time<=end)].copy()

    if s.empty or l.empty:
        return {
            "candidate":candidate,"long3_component":label,
            "overlap_start":start,"overlap_end":end,
            "smc_trades":len(s),"long3_trades":len(l),
        }

    # Scale does not affect correlation. SMC uses 2% risk; LONG3 net_pct is
    # underlying trade return. Use daily sums and weekly sums to test whether
    # the PnL streams tend to win/lose together.
    sd=(s.assign(day=s.entry_time.dt.floor("D"))
          .groupby("day").r_multiple.sum().mul(0.02))
    ld=(l.assign(day=l.entry_time.dt.floor("D"))
          .groupby("day").net_pct.sum().div(100.0))

    days=pd.date_range(start.floor("D"),end.floor("D"),freq="D",tz="UTC")
    frame=pd.DataFrame(index=days)
    frame["smc"]=sd.reindex(days,fill_value=0.0)
    frame["long3"]=ld.reindex(days,fill_value=0.0)
    active=frame[(frame.smc!=0)|(frame.long3!=0)]

    weekly=frame.resample("7D").sum()
    active_weeks=weekly[(weekly.smc!=0)|(weekly.long3!=0)]

    s_days=set(s.entry_time.dt.floor("D"))
    l_days=set(l.entry_time.dt.floor("D"))
    same_days=len(s_days & l_days)

    # Signal-time proximity regardless of symbol: count each SMC trade if any
    # LONG3 trade opened within +/- 6h. This measures clustering of risk.
    ltimes=l.entry_time.sort_values().astype("int64").to_numpy()
    sixh=6*3600*10**9
    prox=0
    for t in s.entry_time.astype("int64").to_numpy():
        i=np.searchsorted(ltimes,t)
        near=False
        for j in (i-1,i):
            if 0<=j<len(ltimes) and abs(int(ltimes[j])-int(t))<=sixh:
                near=True
        prox+=int(near)

    return {
        "candidate":candidate,"long3_component":label,
        "overlap_start":start,"overlap_end":end,
        "smc_trades":len(s),"long3_trades":len(l),
        "smc_active_days":len(s_days),"long3_active_days":len(l_days),
        "same_active_days":same_days,
        "smc_same_day_pct":same_days/len(s_days)*100 if s_days else np.nan,
        "smc_within_6h_of_long3_pct":prox/len(s)*100 if len(s) else np.nan,
        "daily_corr_all_days":safe_corr(frame.smc,frame.long3),
        "daily_spearman_all_days":spearman(frame.smc,frame.long3),
        "daily_corr_active_union":safe_corr(active.smc,active.long3),
        "daily_spearman_active_union":spearman(active.smc,active.long3),
        "weekly_corr":safe_corr(active_weeks.smc,active_weeks.long3),
        "weekly_spearman":spearman(active_weeks.smc,active_weeks.long3),
    }


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    smc=pd.read_csv(SMC)
    smc["entry_time"]=pd.to_datetime(smc["entry_time"],utc=True)

    l=pd.read_csv(LONG3)
    l=l[(l.delay_min==1)&(l.strategy.isin(LONG3_NAMES))].copy()
    l["entry_time"]=pd.to_datetime(l["entry_ts"],unit="ms",utc=True)
    l["net_pct"]=pd.to_numeric(l["net_pct"],errors="coerce").fillna(0.0)

    rows=[]
    for candidate,g in smc.groupby("candidate"):
        rows.append(summarize_pair(candidate,g,l,"LONG3_ALL"))
        for strat in LONG3_NAMES:
            rows.append(summarize_pair(candidate,g,l[l.strategy==strat],strat))

    out=pd.DataFrame(rows)
    out.to_csv(OUT/"correlation.csv",index=False)
    print(out.to_string(index=False))


if __name__=="__main__":
    main()
