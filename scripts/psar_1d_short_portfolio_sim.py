import argparse,glob,json
import numpy as np,pandas as pd

VARIANTS=("E2.75_SB1_R1","E2.75_SB1.2_R1")
UNIVERSES={"LIQ4":("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"),"LIQ10":("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","ADAUSDT","LINKUSDT","LTCUSDT","BCHUSDT")}
SIZES=(.05,.075,.10,.125,.15,.175,.20,.225,.25,.275,.30,.333333,.40,.50,.60,.75,1.0)
COST_BPS=(0,20,40)
MAX_POS=6
MAX_GROSS=2.0

def replay(x,size,cost_bp):
    equity=1.0; peak=1.0; mdd=0.0; openp={}; accepted=skip_sym=skip_cap=skip_gross=0
    wins=losses=0; pnl_list=[]; counts=[]; expos=[]; start=None; end=None
    def snap():
        nonlocal peak,mdd
        peak=max(peak,equity)
        if peak>0:mdd=max(mdd,(peak-equity)/peak*100)
        counts.append(len(openp)); expos.append(sum(p["notional"] for p in openp.values())/max(equity,1e-12)*100)
    def close_through(ts):
        nonlocal equity,wins,losses,end
        due=sorted([(p["exit_ts"],s,p) for s,p in openp.items() if p["exit_ts"]<=ts])
        for et,s,p in due:
            if s not in openp:continue
            del openp[s]
            net=(p["pnl_pct"]-cost_bp/100.0)/100.0
            equity += p["notional"]*net
            pnl_list.append(net)
            if net>0:wins+=1
            elif net<0:losses+=1
            end=max(end or et,et);snap()
    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts); start=ts if start is None else start
        close_through(ts)
        base=equity
        # deterministic: lower stop distance first, then symbol
        g=g.sort_values(["stop_pct","symbol"],ascending=[True,True],kind="mergesort")
        for r in g.itertuples(index=False):
            sym=str(r.symbol)
            if sym in openp:skip_sym+=1;continue
            if len(openp)>=MAX_POS:skip_cap+=1;continue
            notional=base*size
            gross=sum(p["notional"] for p in openp.values())+notional
            if gross>MAX_GROSS*max(equity,1e-12)+1e-12:skip_gross+=1;continue
            openp[sym]={"exit_ts":int(r.exit_ts),"notional":notional,"pnl_pct":float(r.pnl_pct)}
            accepted+=1
        snap()
    close_through(10**19)
    years=((end-start)/1000/86400/365.25) if start and end and end>start else np.nan
    cagr=(equity**(1/years)-1)*100 if equity>0 and np.isfinite(years) and years>0 else np.nan
    pf=(sum(v for v in pnl_list if v>0)/-sum(v for v in pnl_list if v<0)) if any(v<0 for v in pnl_list) else np.nan
    avgexp=float(np.mean(expos)) if expos else 0; maxexp=float(np.max(expos)) if expos else 0
    avgpos=float(np.mean(counts)) if counts else 0; maxpos=int(np.max(counts)) if counts else 0
    return dict(size_pct=size*100,cost_bp=cost_bp,accepted=accepted,skipped_same_symbol=skip_sym,skipped_cap=skip_cap,skipped_gross=skip_gross,final_multiple=equity,total_return_pct=(equity-1)*100,cagr_pct=cagr,mdd_pct=mdd,pf=pf,win_pct=(wins/(wins+losses)*100 if wins+losses else np.nan),avg_positions=avgpos,max_positions=maxpos,avg_gross_exposure_pct=avgexp,max_gross_exposure_pct=maxexp,period_years=years)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--source",default="source");ap.add_argument("--out",default="psar_1d_short_portfolio.json");a=ap.parse_args()
    fs=glob.glob(a.source+"/**/events_*.csv.gz",recursive=True);assert len(fs)==8,(len(fs),fs)
    parts=[]
    for f in fs:
        d=pd.read_csv(f,usecols=["variant","symbol","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"])
        d=d[d.variant.isin(VARIANTS)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()].copy()
        parts.append(d)
    d=pd.concat(parts,ignore_index=True);d.fill_ts=d.fill_ts.astype("int64");d.exit_ts=d.exit_ts.astype("int64")
    out=[]
    for uname,syms in UNIVERSES.items():
      for v in VARIANTS:
        x=d[(d.variant==v)&d.symbol.isin(syms)].sort_values(["fill_ts","symbol"],kind="mergesort")
        print("UNIVERSE",uname,"VARIANT",v,"raw",len(x),flush=True)
        for s in SIZES:
            for bp in COST_BPS:
                z=replay(x,s,bp);z["variant"]=v;z["universe"]=uname;out.append(z)
                if bp==20: print("SIZE20",uname,v,s,z["final_multiple"],z["cagr_pct"],z["mdd_pct"],z["accepted"],z["avg_positions"],z["avg_gross_exposure_pct"],flush=True)
    r=pd.DataFrame(out);r.to_csv("psar_1d_short_portfolio.csv",index=False)
    json.dump({"definition":{"max_positions":MAX_POS,"max_gross_exposure_pct":200,"same_symbol_overlap":False,"ranking":"lower stop_pct then symbol","universes":UNIVERSES,"position_size":"fixed fraction of realized equity at fill","mdd":"realized-equity only","cost_bps":COST_BPS},"results":out},open(a.out,"w"),indent=2)
    for bp in COST_BPS:
        print("LEADERS",bp,flush=True)
        print(r[r.cost_bp==bp].sort_values(["final_multiple","mdd_pct"],ascending=[False,True]).head(20).to_string(index=False),flush=True)
if __name__=="__main__":main()
