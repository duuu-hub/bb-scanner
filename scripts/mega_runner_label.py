#!/usr/bin/env python3
"""Label forward mega-runners without leaking future information into precursor features."""
import argparse, csv, json
from pathlib import Path
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",default="research/mega_runner/data/binance_spot_1d"); ap.add_argument("--out",default="research/mega_runner/labels.csv"); ap.add_argument("--horizon-days",type=int,default=365); a=ap.parse_args()
    out=[]; H=a.horizon_days
    for p in Path(a.data).glob("*USDT.csv"):
        rows=list(csv.DictReader(p.open()))
        lo=[float(x["low"]) for x in rows]; hi=[float(x["high"]) for x in rows]
        best=0; bi=bj=None
        # rolling forward horizon; intentionally simple/reference implementation
        for i in range(len(rows)):
            if lo[i]<=0: continue
            j=min(len(rows),i+H+1)
            if i+1>=j: continue
            mx=max(hi[i+1:j]); k=i+1+hi[i+1:j].index(mx)
            ret=(mx/lo[i]-1)*100
            if ret>best: best,bi,bj=ret,i,k
        if bi is not None:
            out.append({"symbol":p.stem,"max_forward_pct":round(best,2),"low_time":rows[bi]["open_time"],"peak_time":rows[bj]["open_time"],"mega_500":best>=500,"mega_1000":best>=1000,"mega_2000":best>=2000,"mega_5000":best>=5000})
    out.sort(key=lambda x:x["max_forward_pct"],reverse=True)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    with open(a.out,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=out[0].keys() if out else ["symbol"]); w.writeheader(); w.writerows(out)
    print(json.dumps({"symbols":len(out),"mega_2000":sum(x["mega_2000"] for x in out)},indent=2))
if __name__=="__main__": main()
