import argparse,glob,json,heapq
import pandas as pd, numpy as np
COSTS={"base":{"maker":0.04,"taker":0.12},"stress":{"maker":0.08,"taker":0.20},"severe":{"maker":0.12,"taker":0.30}}
def maxstreak(v):
 c=b=0
 for x in v:
  if x<0:c+=1;b=max(b,c)
  else:c=0
 return b
def sim(x,cost):
 x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
 eq=peak=1.;mdd=0.;openp={};heap=[];seq=0;closed=[];skips=0
 for ts,g in x.groupby("fill_ts",sort=True):
  ts=int(ts)
  while heap and heap[0][0]<=ts:
   et,_,sym=heapq.heappop(heap);z=openp.pop(sym,None)
   if z is None:continue
   net=z["pnl_pct"]-cost[z["order"]]
   pnl=z["notional"]*net/100.;eq+=pnl;closed.append((et,pnl,net,z["outcome"]))
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak*100 if peak else 0)
  if eq<=0:break
  elig=[]
  for r in g.itertuples(index=False):
   if r.symbol in openp:skips+=1;continue
   elig.append(r)
  slots=max(0,6-len(openp))
  elig=elig[:slots]
  if not elig:continue
  gross=sum(z["notional"] for z in openp.values());room=max(0,2.0*eq-gross)
  each=min(.30*eq,room/len(elig))
  if each<=0:continue
  for r in elig:
   if pd.isna(r.pnl_pct) or pd.isna(r.exit_ts):continue
   seq+=1;z={"notional":each,"exit_ts":int(r.exit_ts),"pnl_pct":float(r.pnl_pct),"order":str(r.order).lower(),"outcome":str(r.outcome)}
   openp[str(r.symbol)]=z;heapq.heappush(heap,(z["exit_ts"],seq,str(r.symbol)))
 while heap:
  et,_,sym=heapq.heappop(heap);z=openp.pop(sym,None)
  if z is None:continue
  net=z["pnl_pct"]-cost[z["order"]];pnl=z["notional"]*net/100.;eq+=pnl;closed.append((et,pnl,net,z["outcome"]))
  peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak*100 if peak else 0)
 nets=np.array([z[2] for z in closed]);gp=nets[nets>0].sum();gl=-nets[nets<0].sum()
 return {"return_pct":(eq-1)*100,"equity_multiple":eq,"mdd_pct":mdd,"pf_net":gp/gl if gl>0 else None,"expectancy_net_pct":nets.mean() if len(nets) else None,"trades":len(closed),"win_pct":100*sum(z[3]=="win" for z in closed)/len(closed) if closed else None,"max_losing_streak":maxstreak(nets),"skip_same_symbol":skips}
ap=argparse.ArgumentParser();ap.add_argument("--root",default="ledger");ap.add_argument("--out",default="portfolio.json");a=ap.parse_args()
fs=glob.glob(a.root+"/**/events_*.csv.gz",recursive=True);assert len(fs)==16,(len(fs),fs)
D=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
out={"definition":{"position_size_pct":30,"max_gross_exposure_pct":200,"max_open_positions":6,"same_symbol":"one","costs_roundtrip_pct":COSTS,"selection":"frozen B SHORT candidates; no retuning"},"variants":{}}
for v in sorted(D.variant.unique()):
 x=D[(D.variant==v)&D.outcome.isin(["win","loss"])].copy()
 out["variants"][v]={k:sim(x,c) for k,c in COSTS.items()}
 print("RESULT",v,json.dumps(out["variants"][v]),flush=True)
json.dump(out,open(a.out,"w"),indent=2);print("ANCHOR_PORTFOLIO_PASS",len(D),flush=True)
