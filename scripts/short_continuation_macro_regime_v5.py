import argparse,glob,json,math,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base
import scripts.impulse_pullback_v1 as ip
import scripts.short_continuation_robustness_v3 as rob

BAR=ip.BAR; DAY=86400000; UNIVERSE=ip.UNIVERSE
D1=pd.Timestamp("2024-01-01T00:00:00Z");D2=pd.Timestamp("2025-01-01T00:00:00Z")
MIN_BREADTH_N=8
MIN_DEV_EXEC=300

FILTERS=(
 "BASE",
 "BTC_EMA_BEAR",
 "BTC_R30_NEG",
 "BREADTH50",
 "BREADTH60",
 "BREADTH70",
 "MKT_R30_NEG",
 "BTC_R30_NEG_B50",
 "BTC_EMA_BEAR_B50",
 "BTC_R30_NEG_MKT_R30_NEG",
)

def ema(c,n):
 return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()

def daily_state(path):
 t,o,h,l,c=ex.load(path)
 dt,do,dh,dl,dc,st,en=ip.rs(t,o,h,l,c,96)
 if len(dt)<80:return None
 e20=ema(dc,20);e50=ema(dc,50)
 r30=np.full(len(dc),np.nan)
 r30[30:]=dc[30:]/dc[:-30]-1.0
 valid=np.isfinite(e20)&np.isfinite(e50)
 bear=valid&(e20<e50)&(dc<e20)
 return {"close_time":dt+DAY,"bear":bear,"r30":r30,"e20":e20,"e50":e50,"close":dc}

def build_states(data):
 fs=[p for p in sorted(glob.glob(data+"/**/*.csv.gz",recursive=True)) if base.sym(p) in UNIVERSE]
 states={}
 for p in fs:
  s=base.sym(p)
  if s in states:raise RuntimeError(f"duplicate symbol file {s}")
  q=daily_state(p)
  if q is not None:states[s]=q
 if len(states)<MIN_BREADTH_N:raise RuntimeError(f"insufficient states {len(states)}")
 return states

def feature_at(states,ts):
 vals=[];r30=[]
 for s,x in states.items():
  j=np.searchsorted(x["close_time"],ts,side="right")-1
  if j<0:continue
  if np.isfinite(x["r30"][j]):
   vals.append(bool(x["bear"][j]));r30.append(float(x["r30"][j]))
 btc=states.get("BTCUSDT");btc_bear=False;btc_r30=np.nan
 if btc is not None:
  j=np.searchsorted(btc["close_time"],ts,side="right")-1
  if j>=0:
   btc_bear=bool(btc["bear"][j])
   if np.isfinite(btc["r30"][j]):btc_r30=float(btc["r30"][j])
 if len(vals)<MIN_BREADTH_N:return np.nan,np.nan,btc_bear,btc_r30,len(vals)
 return float(np.mean(vals)),float(np.median(r30)),btc_bear,btc_r30,len(vals)

def attach(d,states):
 cache={}
 rows=[]
 for ts in sorted(d.entry_time.unique()):
  cache[int(ts)]=feature_at(states,int(ts))
 for _,r in d.iterrows():
  b,m,bb,br,n=cache[int(r.entry_time)]
  rows.append((b,m,int(bb),br,n))
 z=d.copy()
 z[["daily_breadth","mkt_r30","btc_daily_bear","btc_r30","daily_n"]]=pd.DataFrame(rows,index=z.index)
 return z

def mask(z,name):
 if name=="BASE":return pd.Series(True,index=z.index)
 if name=="BTC_EMA_BEAR":return z.btc_daily_bear==1
 if name=="BTC_R30_NEG":return z.btc_r30<0
 if name=="BREADTH50":return z.daily_breadth>=.50
 if name=="BREADTH60":return z.daily_breadth>=.60
 if name=="BREADTH70":return z.daily_breadth>=.70
 if name=="MKT_R30_NEG":return z.mkt_r30<0
 if name=="BTC_R30_NEG_B50":return (z.btc_r30<0)&(z.daily_breadth>=.50)
 if name=="BTC_EMA_BEAR_B50":return (z.btc_daily_bear==1)&(z.daily_breadth>=.50)
 if name=="BTC_R30_NEG_MKT_R30_NEG":return (z.btc_r30<0)&(z.mkt_r30<0)
 raise RuntimeError(name)

def split(z,lab):
 if lab=="DEV":return z[z.dt<D1]
 if lab=="VALID":return z[(z.dt>=D1)&(z.dt<D2)]
 if lab=="EVAL":return z[z.dt>=D2]
 raise RuntimeError(lab)

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--data",required=True);ap.add_argument("--out",default="macro_regime_v5");a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(a.input,compression="gzip");d=d[d.hours==16].copy()
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);d["year"]=d.dt.dt.year
 states=build_states(a.data);z=attach(d,states)
 z.to_csv(out/"ledger_with_daily_features.csv.gz",index=False,compression="gzip")

 rows=[]
 masks={name:mask(z,name).fillna(False) for name in FILTERS}
 for name,m in masks.items():
  for lab in ("DEV","VALID","EVAL"):
   x=split(z,lab);mm=m.loc[x.index];y=x[mm]
   sel=rob.select_exec(y);met,_=rob.account(sel)
   rows.append({"filter":name,"split":lab,"raw_n":len(x),"pass_n":len(y),"pass_pct":100*len(y)/len(x) if len(x) else np.nan,
                "exec_n":len(sel),"avg_breadth":y.daily_breadth.mean() if len(y) else np.nan,
                "avg_btc_r30":y.btc_r30.mean() if len(y) else np.nan,**met})
 s=pd.DataFrame(rows);s.to_csv(out/"summary.csv",index=False)

 dev=s[(s.split=="DEV")&(s.exec_n>=MIN_DEV_EXEC)].copy()
 dev=dev.sort_values(["return_pct","mdd_pct","exec_n"],ascending=[False,True,False])
 if not len(dev):raise RuntimeError("no eligible DEV filter")
 best=str(dev.iloc[0]["filter"])
 pd.DataFrame([{"selected_filter":best,"min_dev_exec":MIN_DEV_EXEC}]).to_csv(out/"selected_dev.csv",index=False)

 yr=[]
 bm=masks[best]
 for y,x in z.groupby("year"):
  xx=x[bm.loc[x.index]]
  sel=rob.select_exec(xx);met,_=rob.account(sel)
  yr.append({"year":int(y),"raw_n":len(x),"pass_n":len(xx),"pass_pct":100*len(xx)/len(x) if len(x) else np.nan,"exec_n":len(sel),**met})
 ydf=pd.DataFrame(yr);ydf.to_csv(out/"selected_yearly.csv",index=False)

 lines=["# Short Continuation Daily Macro Regime V5","",
 "Frozen candidate: 16h / cap6 / 20bp. Only last fully completed UTC daily bars are used.",
 "Candidate filters: BTC daily EMA20/50 bear, BTC 30d return<0, 18-symbol daily bearish breadth 50/60/70, median market 30d return<0, and small logical combinations.",
 f"DEV selection requires >= {MIN_DEV_EXEC} executable trades. VALID/EVAL are not used for selection.","",
 "| Filter | Split | Pass | ExecN | Return | MDD | PF20 | MaxLS |",
 "|---|---|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["filter","split"]).iterrows():
  lines.append(f"| {r['filter']} | {r.split} | {f(r.pass_pct,1)}% | {int(r.exec_n)} | {f(r.return_pct,2)}% | {f(r.mdd_pct,2)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 lines+=["",f"## DEV-selected: {best}","",
 "| Year | Pass | ExecN | Return | MDD | PF20 | MaxLS |","|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in ydf.iterrows():
  lines.append(f"| {int(r.year)} | {f(r.pass_pct,1)}% | {int(r.exec_n)} | {f(r.return_pct,2)}% | {f(r.mdd_pct,2)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 (out/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
