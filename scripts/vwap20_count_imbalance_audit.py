import glob,os,pandas as pd,numpy as np
rows=[]
for p in glob.glob("data/**/*.csv.gz",recursive=True):
 s=os.path.basename(p).replace(".csv.gz","")
 try:d=pd.read_csv(p,usecols=["open_time","open","volume","quote_volume"]).sort_values("open_time").drop_duplicates("open_time")
 except:continue
 if len(d)<700:continue
 op=d.open.astype(float); vw=(d.quote_volume.astype(float).rolling(96).sum()/d.volume.astype(float).rolling(96).sum()).shift(1)
 dist=(op/vw-1)*100;r7=(op/op.shift(672)-1)*100
 raw=(dist<=-20)&(r7<=-5);ev=raw&~raw.shift(1,fill_value=False)
 first=pd.to_datetime(int(d.open_time.min()),unit="ms",utc=True)
 for i in np.where(ev.fillna(False))[0]:
  ts=int(d.open_time.iloc[i]);dt=pd.to_datetime(ts,unit="ms",utc=True)
  rows.append([s,dt.year,dt.strftime("%Y-%m-%d"),first.strftime("%Y-%m-%d"),(dt-first).days])
pd.DataFrame(rows,columns=["symbol","year","date","first_date","age_days"]).to_csv("raw.csv",index=False)
# coverage separately
cov=[]
for p in glob.glob("data/**/*.csv.gz",recursive=True):
 s=os.path.basename(p).replace(".csv.gz","")
 try:d=pd.read_csv(p,usecols=["open_time"])
 except:continue
 if len(d): 
  y=pd.to_datetime(d.open_time.astype("int64"),unit="ms",utc=True).dt.year
  for yr in sorted(y.unique()):cov.append([s,int(yr),int((y==yr).sum()),pd.to_datetime(int(d.open_time.min()),unit="ms",utc=True).strftime("%Y-%m-%d")])
pd.DataFrame(cov,columns=["symbol","year","bars","first_date"]).to_csv("coverage.csv",index=False)
