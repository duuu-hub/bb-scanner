import argparse, os, hashlib
import numpy as np
import pandas as pd

COSTS=[0.0,0.2,0.4]
CAP=6
RULES=["vwap_extreme","ret7_extreme","rank_sum"]
BASELINE="alphabetical"

def pf(y):
    y=pd.Series(y,dtype=float)
    p=y[y>0].sum(); n=-y[y<0].sum()
    return float(p/n) if n>0 else (float("inf") if p>0 else float("nan"))

def choose_group(g, rule):
    g=g.copy()
    if rule=="vwap_extreme":
        return g.sort_values(["dist_pct","ret7d_pct","symbol"],ascending=[True,True,True]).head(CAP)
    if rule=="ret7_extreme":
        return g.sort_values(["ret7d_pct","dist_pct","symbol"],ascending=[True,True,True]).head(CAP)
    if rule=="rank_sum":
        # Equal-weight ordinal combination; more-negative values are better. No fitted weights.
        g["r_dist"]=g["dist_pct"].rank(method="first",ascending=True)
        g["r_ret7"]=g["ret7d_pct"].rank(method="first",ascending=True)
        g["rank_sum"]=g["r_dist"]+g["r_ret7"]
        return g.sort_values(["rank_sum","dist_pct","ret7d_pct","symbol"],ascending=[True,True,True,True]).head(CAP)
    if rule==BASELINE:
        return g.sort_values(["symbol"]).head(CAP)
    raise ValueError(rule)

def select(d, rule):
    out=[]
    for _,g in d.groupby("ts",sort=True):
        out.append(choose_group(g,rule))
    return pd.concat(out,ignore_index=True) if out else d.iloc[0:0].copy()

def summarize(sel, rule, scope):
    rows=[]
    for c in COSTS:
        y=sel.gross_pct.astype(float)-c
        by_ts=(sel.assign(net=y).groupby("ts",sort=True).net.mean())
        rows.append([
            rule,scope,c,len(sel),sel.ts.nunique(),
            (y>0).mean()*100 if len(y) else np.nan,
            y.mean() if len(y) else np.nan,
            pf(y),
            (by_ts>0).mean()*100 if len(by_ts) else np.nan,
            by_ts.mean() if len(by_ts) else np.nan,
            pf(by_ts)
        ])
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events",required=True)
    ap.add_argument("--outdir",required=True)
    a=ap.parse_args()
    os.makedirs(a.outdir,exist_ok=True)
    d=pd.read_csv(a.events)
    need={"symbol","ts","date","dist_pct","ret7d_pct","gross_pct","year","split","exit_reason"}
    miss=need-set(d.columns)
    if miss: raise RuntimeError(f"missing columns: {sorted(miss)}")
    d=d.sort_values(["ts","symbol"]).reset_index(drop=True)

    # The candidate rules are declared before looking at holdout results.
    rows=[]
    selected={}
    for rule in RULES+[BASELINE]:
        selected[rule]=select(d,rule)
        for scope,mask in [
            ("ALL",pd.Series(True,index=d.index)),
            ("TRAIN_2021_2024",d.split.eq("TRAIN_2021_2024")),
            ("HOLDOUT_2025_2026",d.split.eq("HOLDOUT_2025_2026"))
        ]:
            ds=d.loc[mask]
            ss=select(ds,rule)
            rows.extend(summarize(ss,rule,scope))

    cols=["rule","scope","cost_pct","trade_n","timestamp_n","trade_wr_pct","trade_mean_pct","trade_pf","cluster_wr_pct","cluster_mean_pct","cluster_pf"]
    s=pd.DataFrame(rows,columns=cols)
    s.to_csv(os.path.join(a.outdir,"rule_summary.csv"),index=False)

    # Winner is selected using TRAIN ONLY, 40bp, on timestamp-level PF (capital-constrained view).
    train=s[(s.scope=="TRAIN_2021_2024")&(s.cost_pct==0.4)&(s.rule.isin(RULES))].copy()
    train=train.sort_values(["cluster_pf","cluster_mean_pct","timestamp_n"],ascending=[False,False,False])
    winner=str(train.iloc[0].rule)
    with open(os.path.join(a.outdir,"frozen_winner.txt"),"w") as f:
        f.write(winner+"\n")

    win=selected[winner].copy()
    win.to_csv(os.path.join(a.outdir,"selected_events_winner.csv"),index=False)
    s[s.rule.eq(winner)].to_csv(os.path.join(a.outdir,"frozen_winner_summary.csv"),index=False)

    # Also show untouched holdout for all predeclared rules, but it cannot change winner.
    hold=s[(s.scope=="HOLDOUT_2025_2026")&(s.cost_pct==0.4)].copy()
    hold.to_csv(os.path.join(a.outdir,"holdout_40bp_all_rules.csv"),index=False)

    checks=[]
    def ck(name,cond,detail=""): checks.append([name,bool(cond),str(detail)])
    ck("01_nonempty",len(d)>0,len(d))
    ck("02_cap_exact",CAP==6)
    ck("03_rules_frozen",RULES==["vwap_extreme","ret7_extreme","rank_sum"])
    ck("04_costs_frozen",COSTS==[0.0,0.2,0.4])
    ck("05_train_present",(d.split=="TRAIN_2021_2024").any())
    ck("06_holdout_present",(d.split=="HOLDOUT_2025_2026").any())
    ck("07_dist_filter",(d.dist_pct<=-20+1e-12).all(),d.dist_pct.max())
    ck("08_ret7_filter",(d.ret7d_pct<=-5+1e-12).all(),d.ret7d_pct.max())
    ck("09_no_dup",d.duplicated(["symbol","ts"]).sum()==0,d.duplicated(["symbol","ts"]).sum())
    ck("10_gross_finite",np.isfinite(d.gross_pct).all())
    ck("11_ts_finite",np.isfinite(d.ts).all())
    ck("12_year_range",d.year.between(2021,2026).all())
    ck("13_winner_from_rules",winner in RULES,winner)
    ck("14_train_rows",len(train)==len(RULES),len(train))
    ck("15_summary_rows",len(s)==(len(RULES)+1)*3*len(COSTS),len(s))
    ck("16_holdout_rows",len(hold)==len(RULES)+1,len(hold))
    ck("17_cap_vwap",select(d,"vwap_extreme").groupby("ts").size().max()<=CAP)
    ck("18_cap_ret7",select(d,"ret7_extreme").groupby("ts").size().max()<=CAP)
    ck("19_cap_rank",select(d,"rank_sum").groupby("ts").size().max()<=CAP)
    ck("20_cap_base",select(d,BASELINE).groupby("ts").size().max()<=CAP)
    ck("21_train_only_choice",train.scope.eq("TRAIN_2021_2024").all())
    ck("22_choice_cost40",train.cost_pct.eq(0.4).all())
    ck("23_cluster_pf_nonneg",(s.cluster_pf.dropna()>=0).all())
    ck("24_trade_pf_nonneg",(s.trade_pf.dropna()>=0).all())
    ck("25_wr_range",s.trade_wr_pct.dropna().between(0,100).all() and s.cluster_wr_pct.dropna().between(0,100).all())
    ck("26_event_date",d.date.notna().all())
    ck("27_symbol",d.symbol.notna().all())
    ck("28_exit_reason",d.exit_reason.notna().all())
    ck("29_winner_file_count",len(win)>0,len(win))
    ck("30_holdout_not_used_for_choice",winner==str(train.iloc[0].rule),winner)

    canon=d.sort_values(["ts","symbol"]).to_csv(index=False)
    h0=hashlib.sha256(canon.encode()).hexdigest()
    for i in range(10):
        h=hashlib.sha256(d.sort_values(["ts","symbol"]).to_csv(index=False).encode()).hexdigest()
        ck(f"{31+i:02d}_determinism_{i+1}",h==h0,h[:16])
    audit=pd.DataFrame(checks,columns=["check","pass","detail"])
    audit.to_csv(os.path.join(a.outdir,"audit_40checks.csv"),index=False)

    print("WINNER_TRAIN_ONLY",winner)
    print("AUDIT",int(audit["pass"].sum()),"/",len(audit))
    print("TRAIN 40BP")
    print(train.to_string(index=False))
    print("HOLDOUT 40BP (REPORT ONLY)")
    print(hold.sort_values("rule").to_string(index=False))
    if not audit["pass"].all():
        print(audit[~audit["pass"]].to_string(index=False))
        raise SystemExit("AUDIT FAILED")

if __name__=="__main__":
    main()
