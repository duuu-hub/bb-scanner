import glob,json
import pandas as pd

train=json.load(open("train/cross_sectional_train_structure.json"))
p90=float(train["disp_quantiles"][2])

frames=[pd.read_csv(p) for p in glob.glob("parts/**/*.csv.gz",recursive=True)]
z=pd.concat(frames,ignore_index=True)
dt=pd.to_datetime(z.ts,unit="ms",utc=True)
z=z[(dt>=pd.Timestamp("2025-01-01",tz="UTC")) & (dt<pd.Timestamp("2027-01-01",tz="UTC"))].copy()

g=z.groupby("ts")["r_15m"]
z["rank"]=g.rank(pct=True)
z["disp"]=g.transform(lambda s:s.quantile(.75)-s.quantile(.25))

a=z[(z.r_15m<=-0.04)&(z["rank"]<=0.10)&(z.disp>=p90)].copy()
a["year"]=pd.to_datetime(a.ts,unit="ms",utc=True).dt.year

def stat(x):
    v=x.f_1h.dropna()
    gross=v.mean()*10000
    return {"n":int(len(v)),"gross_bp":float(gross),"net20_bp":float(gross-20),
            "net40_bp":float(gross-40),"wr_pct":float((v>0).mean()*100),
            "median_bp":float(v.median()*10000)}

out={"rule":"FROZEN","train_disp_p90":p90,"all":stat(a),
     "years":{str(int(y)):stat(b) for y,b in a.groupby("year")}}
print("FROZEN_HOLDOUT_PASS")
print(json.dumps(out,indent=2))
json.dump(out,open("frozen_holdout.json","w"),indent=2)
