from pathlib import Path
import sys, numpy as np, pandas as pd
sys.path.insert(0,"research")
import continuation_mining as cm
import continuation_execution_core as ex

OUT=Path("continuation_fast_results"); OUT.mkdir(exist_ok=True)
COST=0.20

def add_market_features(raw):
    d=pd.concat([cm.add_features(g) for _,g in raw.groupby("symbol",sort=False)],ignore_index=True)
    agg=d[["timestamp_ms","ret_1h","ret_4h"]].groupby("timestamp_ms").agg(
      breadth_1h=("ret_1h",lambda s:(s>0).mean()*100),
      breadth_4h=("ret_4h",lambda s:(s>0).mean()*100),
      market_med_1h=("ret_1h","median"),market_med_4h=("ret_4h","median")).reset_index()
    d=d.merge(agg,on="timestamp_ms",how="left")
    d["rel_vs_market_1h"]=d.ret_1h-d.market_med_1h
    d["rel_vs_market_4h"]=d.ret_4h-d.market_med_4h
    return d

def primary_labels(g, side):
    # Canonical semantics: signal close -> next 15m OPEN; entry candle eligible.
    n=len(g); out=np.full(n,"NO_ENTRY",dtype=object)
    ts=g.timestamp_ms.to_numpy(np.int64); op=g.open.to_numpy(float); hi=g.high.to_numpy(float); lo=g.low.to_numpy(float)
    for i in range(n-1):
        if ts[i+1]!=ts[i]+ex.BAR_MS: continue
        ep=op[i+1]; tp=ep*(1.015 if side=="LONG" else .985); sl=ep*(.9925 if side=="LONG" else 1.0075)
        end=np.searchsorted(ts,ts[i+1]+4*ex.BAR_MS,side="left")
        lab="TIMEOUT"
        for j in range(i+1,min(end,n)):
            ht=hi[j]>=tp if side=="LONG" else lo[j]<=tp
            hs=lo[j]<=sl if side=="LONG" else hi[j]>=sl
            if ht and hs: lab="AMBIG"; break
            if ht: lab="WIN"; break
            if hs: lab="LOSS"; break
        out[i]=lab
    return out

def apply_rule(df,rule):
    m=pd.Series(True,index=df.index)
    for token in rule.split(" & "):
        if "GE" in token: f,v=token.split("GE"); m &= df[f]>=float(v)
        else: f,v=token.split("LE"); m &= df[f]<=float(v)
    return m

def stats(d):
    net=d.gross_ret_pct-COST
    pos=net[net>0].sum(); neg=abs(net[net<0].sum())
    return dict(n=len(d),win_rate=float((net>0).mean()),expectancy_pct=float(net.mean()),
      pf=float(pos/neg) if neg else np.inf)

raw=cm.load("market_data_store/bitget/research_auto100_15m")
print("FAST raw",len(raw),flush=True)
d=add_market_features(raw)
long=(d.ret_1h>=1)&(d.ret_4h>0); short=(d.ret_1h<=-1)&(d.ret_4h<0)
cand=d[long|short].copy(); cand["direction"]=np.where(long.loc[cand.index],"LONG","SHORT")
labs=[]
for sym,g in d.groupby("symbol",sort=False):
    z=pd.DataFrame({"idx":g.index})
    z["LONG"]=primary_labels(g.reset_index(drop=True),"LONG")
    z["SHORT"]=primary_labels(g.reset_index(drop=True),"SHORT")
    labs.append(z)
lab=pd.concat(labs).set_index("idx")
cand["primary"]=np.where(cand.direction.eq("LONG"),lab.loc[cand.index,"LONG"],lab.loc[cand.index,"SHORT"])
cand=cand.sort_values("timestamp_ms")
times=np.sort(cand.timestamp_ms.unique()); split=times[int(len(times)*.70)]
tr=cand[cand.timestamp_ms<split]; te=cand[cand.timestamp_ms>=split]
q=tr[(tr.direction=="SHORT")&(~tr.primary.isin(["AMBIG","NO_ENTRY"]))]
sing=cm.lift_table(q,"primary",cm.FEATURES); combos=cm.combo_table(q,"primary",sing)
if combos.empty: raise SystemExit("no rule")
rule=combos.iloc[0].rules
print("FAST rule",rule,flush=True)
sig=te[(te.direction=="SHORT") & apply_rule(te,rule)].sort_values("timestamp_ms")
groups={s:g.sort_values("timestamp_ms").reset_index(drop=True) for s,g in raw.groupby("symbol")}
rows=[]; gaps=0
for r in sig.itertuples():
    g=groups[r.symbol]; base=int(np.searchsorted(g.timestamp_ms.to_numpy(),int(r.timestamp_ms)))
    rr=ex.replay_trade(g,base,"SHORT",5.,3.,24)
    if rr is None: gaps+=1; continue
    rows.append(dict(symbol=r.symbol,signal_ts=int(r.timestamp_ms),entry_ts=int(g.iloc[rr["entry_i"]].timestamp_ms),
      exit_ts=int(g.iloc[rr["exit_i"]].timestamp_ms),entry_px=rr["entry_px"],gross_ret_pct=rr["gross_ret_pct"],exit_reason=rr["exit_reason"]))
out=pd.DataFrame(rows)
out.to_csv(OUT/"trades.csv.gz",index=False,compression="gzip")
# Actual portfolio semantics: at most one live position per symbol. Signals arriving
# before that symbol's accepted trade exits are rejected chronologically.
accepted=[]; busy_until={}
for ix,r in out.sort_values(["entry_ts","signal_ts","symbol"]).iterrows():
    if int(r.entry_ts) <= busy_until.get(r.symbol,-1):
        continue
    accepted.append(ix); busy_until[r.symbol]=int(r.exit_ts)
port=out.loc[accepted].sort_values(["entry_ts","signal_ts"]).reset_index(drop=True)
port.to_csv(OUT/"trades_one_position_per_symbol.csv.gz",index=False,compression="gzip")
s=stats(out); ps=stats(port)
s.update(raw_signals=len(sig),gaps=gaps,ambiguous_15m=int((out.exit_reason=="BOTH_SL").sum()),
 one_symbol_n=ps["n"],one_symbol_win_rate=ps["win_rate"],one_symbol_expectancy_pct=ps["expectancy_pct"],
 one_symbol_pf=ps["pf"],one_symbol_ambiguous_15m=int((port.exit_reason=="BOTH_SL").sum()),rule=rule)
pd.DataFrame([s]).to_csv(OUT/"summary.csv",index=False)
print("FAST_RESULT",s,flush=True)
