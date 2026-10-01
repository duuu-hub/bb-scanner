import argparse,glob,json,os,time,sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import scripts.external_breakout_replay as m

# Pre-registered reconstruction from public observable characteristics only:
# breakout family, ~1:1 realized win/loss, ~59m average holding.
CONFIGS=(
{"name":"BA3_A_DONCHIAN12_R1","group":"BREAKOUT_ALGO_PROXY","family":"donchian","n":12,"buf":.1,"sl":1.,"r":1.},
{"name":"BA3_B_DONCHIAN24_R1","group":"BREAKOUT_ALGO_PROXY","family":"donchian","n":24,"buf":.1,"sl":1.,"r":1.},
{"name":"BA3_C_DONCHIAN48_R1","group":"BREAKOUT_ALGO_PROXY","family":"donchian","n":48,"buf":.1,"sl":1.,"r":1.},
{"name":"BA3_D_EMA50_DON24_R1","group":"BREAKOUT_ALGO_PROXY","family":"ema","n":24,"buf":.1,"sl":1.,"r":1.},
{"name":"BA3_E_COMPRESS12_R1","group":"BREAKOUT_ALGO_PROXY","family":"compress","n":12,"buf":.1,"sl":1.,"r":1.,"max_range_atr":2.},
{"name":"BA3_F_SQUEEZE20_R1","group":"BREAKOUT_ALGO_PROXY","family":"squeeze","n":20,"buf":.1,"sl":1.,"r":1.,"hist":100,"q":.25},
)
m.CONFIGS=CONFIGS
m.MAXH=16  # 4h cap; public account average hold was about 59m, exact exit logic unknown.

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError("bad shard")
 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 if not allf:raise RuntimeError("no data")
 fs=[p for i,p in enumerate(allf) if i%a.shards==a.shard];rows=[];cnt=defaultdict(int);started=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} files={len(fs)} total={len(allf)}",flush=True)
 for z,p in enumerate(fs,1):
  s=m.sym(p)
  if s=="BNXUSDT":cnt["excluded_BNXUSDT"]+=1;continue
  d=m.load(p)
  try:
   for aa,bb in m.segments(d[0]):
    if bb-aa<640:continue
    rr,cc=m.evaluate(s,*tuple(x[aa:bb] for x in d));rows.extend(rr)
    for k,v in cc.items():cnt[k]+=v
  finally:m.CACHE.clear()
  print(f"PROGRESS {z}/{len(fs)} {s} trades={len(rows)} elapsed_min={(time.time()-started)/60:.1f}",flush=True)
 cols=["config","group","family","symbol","signal_time","entry_time","exit_time","side","trigger","entry","exit","reason","hold_min","risk_pct","gross_return","gross_r","net20_return","net20_r","net40_return","net40_r"]
 pd.DataFrame(rows,columns=cols).to_csv(f"breakout_algo3_trades_shard_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={"definition":{"source_data_run":m.SOURCE_DATA_RUN,"workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),"tf":"1h","entry_ttl":"1h","max_hold":"4h","costs":"20/40bp round trip","reconstruction":"public characteristics only; proprietary strategy unknown","target_shape":"breakout; realized win/loss about 1:1; public average hold about 59m","chronology":"same canonical Binance 1m authority as Fortune/Blue replay","position":"one position per symbol/config"},"configs":CONFIGS,"rows":len(rows),"counters":dict(cnt)}
 Path(f"breakout_algo3_meta_shard_{a.shard}.json").write_text(json.dumps(meta,indent=2))
 print(json.dumps({"rows":len(rows),"counters":dict(cnt)},indent=2))
if __name__=="__main__":main()
