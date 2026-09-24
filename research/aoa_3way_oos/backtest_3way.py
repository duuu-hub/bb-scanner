#!/usr/bin/env python3
from __future__ import annotations

import json, math, hashlib, sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from research.aoa_market_context.analyze_market_context import (
    load_candles, attach, MARKET_FEATURES
)

BASE = ROOT / "research" / "aoa_3way_oos"
OUT = BASE / "output"
POLICY = ROOT / "research" / "aoa_market_context" / "aoa_policy_2019h2_2021_compact.csv"
BTC_DIR = ROOT / "market_data_store" / "bitget" / "15m" / "BTCUSDT"
ETH_DIR = ROOT / "market_data_store" / "bitget" / "15m" / "ETHUSDT"
OOS_START = pd.Timestamp("2022-01-01", tz="UTC")
COSTS = {"BASE_RT_012": 0.0012/2, "STRESS_RT_025": 0.0025/2}
ER_T = 0.193654
MAX_EXPOSURE = 1.0


def load_raw(symbol_dir: Path) -> pd.DataFrame:
    xs=[]
    for p in sorted(symbol_dir.glob("*.csv")):
        x=pd.read_csv(p)
        xs.append(x)
    z=pd.concat(xs,ignore_index=True)
    z["datetime_utc"]=pd.to_datetime(z["datetime_utc"],utc=True)
    for c in ["open","high","low","close","base_volume","quote_volume"]:
        z[c]=pd.to_numeric(z[c],errors="coerce")
    return z.dropna(subset=["open","high","low","close"]).drop_duplicates("timestamp_ms").sort_values("datetime_utc").reset_index(drop=True)


def fit_aoa_direction(candles: pd.DataFrame):
    pol=pd.read_csv(POLICY)
    ent=pol[pol["a"].isin(["E","FE"])].copy()
    j=attach(ent,candles,"t")
    j["y"]=(j["d"]=="L").astype(int)
    j=j[pd.to_datetime(j["t"],unit="s",utc=True)<OOS_START].copy()
    pipe=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=0.5))
    ])
    pipe.fit(j[MARKET_FEATURES],j["y"])
    co={k:float(v) for k,v in zip(MARKET_FEATURES,pipe.named_steps["lr"].coef_[0])}
    return pipe,j,dict(sorted(co.items(),key=lambda kv:abs(kv[1]),reverse=True))


def hybrid_daily_signal(btc: pd.DataFrame, eth: pd.DataFrame) -> pd.Series:
    def daily(x):
        d=x.set_index("datetime_utc").resample("1D",label="left",closed="left").agg(
            close=("close","last")
        ).dropna().reset_index()
        c=d["close"]
        d["ret30"]=c/c.shift(30)-1
        path=c.diff().abs().rolling(30,min_periods=15).sum()
        d["er30"]=(c-c.shift(30)).abs()/path.replace(0,np.nan)
        return d
    b=daily(btc).rename(columns={"close":"btc_close","ret30":"btc_ret30","er30":"btc_er30"})
    e=daily(eth).rename(columns={"close":"eth_close","ret30":"eth_ret30","er30":"eth_er30"})
    x=b.merge(e,on="datetime_utc",how="inner")
    bs=np.sign(x["btc_ret30"]); es=np.sign(x["eth_ret30"])
    agree=(bs==es)&(bs!=0)
    er=(x["btc_er30"]+x["eth_er30"])/2
    x["raw_dir"]=np.where(agree&(er>=ER_T),bs,0).astype(int)
    # Today's tradable direction comes only from yesterday's completed daily bar.
    x["dir"]=x["raw_dir"].shift(1).fillna(0).astype(int)
    daymap=dict(zip(x["datetime_utc"].dt.normalize(),x["dir"]))
    return btc["datetime_utc"].dt.normalize().map(daymap).fillna(0).astype(int)


@dataclass
class St:
    name: str
    side_cost: float
    equity: float = 1.0
    qty: float = 0.0
    avg_entry: float = math.nan
    last_mark: float = math.nan
    leg_start_equity: float = math.nan
    hold_bars: int = 0
    adverse_n: int = 0
    fav_n: int = 0
    reduce_n: int = 0
    last_leg_win: bool = True
    pending: dict|None = None
    turnover: float = 0.0
    fees: float = 0.0
    actions: dict = field(default_factory=lambda:{"ENTER":0,"ADD":0,"REDUCE":0,"EXIT":0,"FLIP":0})
    legs: list = field(default_factory=list)
    curve: list = field(default_factory=list)

    @property
    def side(self):
        return 1 if self.qty>0 else (-1 if self.qty<0 else 0)

    def exposure(self,px):
        return abs(self.qty)*px/max(self.equity,1e-12)

    def pnl_bps(self,px):
        if self.side==0 or not np.isfinite(self.avg_entry):
            return 0.0
        return self.side*(px/self.avg_entry-1.0)*10000.0

    def mark(self,px):
        if np.isfinite(self.last_mark):
            self.equity += self.qty*(px-self.last_mark)
        self.last_mark=px

    def _fee(self,notional):
        f=abs(notional)*self.side_cost
        self.equity-=f
        self.fees+=f
        self.turnover+=abs(notional)/max(self.equity+f,1e-12)

    def tranche_frac(self):
        if self.name=="AOA_CLONE":
            return 0.25 if self.last_leg_win else 0.1875
        return 0.25 if self.last_leg_win else 0.175

    def add(self,direction,px,fraction,kind):
        cap=max(0.0,MAX_EXPOSURE-self.exposure(px))
        frac=max(0.0,min(float(fraction),cap))
        if frac<=1e-9:return
        notional=frac*self.equity
        dq=direction*notional/px
        old=abs(self.qty); new=old+abs(dq)
        if old<=1e-15:
            self.avg_entry=px
            self.leg_start_equity=self.equity
            self.hold_bars=self.adverse_n=self.fav_n=self.reduce_n=0
        else:
            self.avg_entry=(old*self.avg_entry+abs(dq)*px)/new
        self._fee(notional)
        self.qty+=dq
        self.actions[kind]+=1

    def reduce(self,px,fraction):
        if self.side==0:return
        notional=min(abs(self.qty)*px,fraction*self.equity)
        if notional<=1e-9:return
        dq=-self.side*notional/px
        self._fee(notional); self.qty+=dq
        if abs(self.qty)<1e-12:self.qty=0.0
        self.actions["REDUCE"]+=1

    def close_leg(self,px,reason):
        if self.side==0:return None
        direction=self.side
        notional=abs(self.qty)*px
        self._fee(notional); self.qty=0.0
        r=(self.equity/self.leg_start_equity-1.0)*100 if self.leg_start_equity>0 else math.nan
        rec={"direction":"LONG" if direction>0 else "SHORT","return_pct":r,"bars":self.hold_bars,"reason":reason}
        self.legs.append(rec); self.last_leg_win=bool(r>0)
        self.avg_entry=math.nan; self.hold_bars=self.adverse_n=self.fav_n=self.reduce_n=0
        return direction

    def exec_pending(self,px):
        a=self.pending; self.pending=None
        if not a:return
        typ=a["type"]
        if typ=="ENTER":
            self.add(a["dir"],px,a.get("frac",self.tranche_frac()),"ENTER")
        elif typ=="ADD":
            self.add(self.side,px,a.get("frac",self.tranche_frac()),"ADD")
            if a.get("add_type")=="ADV":self.adverse_n+=1
            else:self.fav_n+=1
        elif typ=="REDUCE":
            self.reduce(px,a.get("frac",self.tranche_frac())); self.reduce_n+=1
        elif typ=="EXIT":
            self.close_leg(px,a.get("reason","EXIT")); self.actions["EXIT"]+=1
        elif typ=="FLIP":
            old=self.close_leg(px,a.get("reason","FLIP"))
            self.actions["FLIP"]+=1
            if old:
                self.add(-old,px,self.tranche_frac(),"ENTER")


def qualified_core(row):
    p=row["aoa_p_long"]; r=row["ret1h"]; z=row["bb_z20"]
    if p>=0.60 and r<=-50 and z<=-0.75:return 1
    if p<=0.40 and r>=50 and z>=0.75:return -1
    return 0


def decide_clone(st: St,row):
    if st.side==0:
        return {"type":"ENTER","dir":1 if row["aoa_p_long"]>=0.5 else -1}
    pnl=st.pnl_bps(row["close"]); s1=st.side*row["ret1h"]; ex=st.exposure(row["close"]); tr=st.tranche_frac()
    if (pnl>=60 and s1>=50 and st.hold_bars>=4) or pnl<=-500 or (st.hold_bars>=192 and pnl<0):
        return {"type":"FLIP","reason":"RULE_FLIP"}
    if pnl<=-300 and ex>tr+0.05:
        return {"type":"REDUCE","frac":tr}
    adv_level=-25-17.5*st.adverse_n
    if pnl<=adv_level and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"ADV"}
    fav_levels=[25,30,35]
    if st.fav_n<len(fav_levels) and pnl>=fav_levels[st.fav_n] and s1>0 and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"FAV"}
    red_level=40*(st.reduce_n+1)
    if pnl>=red_level and ex>tr+0.05:
        return {"type":"REDUCE","frac":tr}
    return None


def decide_core(st: St,row):
    sig=qualified_core(row); tr=st.tranche_frac()
    if st.side==0:
        return {"type":"ENTER","dir":sig} if sig else None
    pnl=st.pnl_bps(row["close"]); s1=st.side*row["ret1h"]; ex=st.exposure(row["close"])
    if sig==-st.side:
        return {"type":"EXIT","reason":"OPPOSITE_SIGNAL"}
    if pnl<=-200:
        return {"type":"EXIT","reason":"HARD_RISK"}
    if pnl>=80:
        return {"type":"EXIT","reason":"PROFIT_EXIT"}
    fav_levels=[25,50,75]
    if st.fav_n<len(fav_levels) and pnl>=fav_levels[st.fav_n] and s1>=50 and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"FAV"}
    if pnl>=40 and st.reduce_n==0 and ex>tr+0.05:
        return {"type":"REDUCE","frac":tr}
    adv_levels=[-25,-50,-75]
    if st.adverse_n<len(adv_levels) and pnl<=adv_levels[st.adverse_n] and s1>-100 and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"ADV"}
    return None


def decide_hybrid(st: St,row):
    sig=int(row["hybrid_dir"]); tr=st.tranche_frac()
    if st.side==0:
        return {"type":"ENTER","dir":sig} if sig else None
    pnl=st.pnl_bps(row["close"]); s1=st.side*row["ret1h"]; ex=st.exposure(row["close"])
    if sig==0:return {"type":"EXIT","reason":"CORE_FLAT"}
    if sig==-st.side:return {"type":"FLIP","reason":"CORE_FLIP"}
    if pnl<=-200:return {"type":"EXIT","reason":"HARD_RISK"}
    if pnl>=80:return {"type":"EXIT","reason":"PROFIT_EXIT"}
    fav_levels=[25,50,75]
    if st.fav_n<len(fav_levels) and pnl>=fav_levels[st.fav_n] and s1>=50 and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"FAV"}
    if pnl>=40 and st.reduce_n==0 and ex>tr+0.05:
        return {"type":"REDUCE","frac":tr}
    adv_levels=[-25,-50,-75]
    if st.adverse_n<len(adv_levels) and pnl<=adv_levels[st.adverse_n] and s1>-100 and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"ADV"}
    return None


def simulate(name,df,side_cost):
    st=St(name=name,side_cost=side_cost)
    dec={"AOA_CLONE":decide_clone,"AOA_CORE":decide_core,"AOA_HYBRID":decide_hybrid}[name]
    oos=df[df["datetime_utc"]>=OOS_START].copy().reset_index(drop=True)
    # Initial decision is taken from last completed pre-OOS bar.
    prev=df[df["datetime_utc"]<OOS_START].iloc[-1]
    if name=="AOA_CLONE": st.pending={"type":"ENTER","dir":1 if prev["aoa_p_long"]>=0.5 else -1}
    elif name=="AOA_CORE":
        q=qualified_core(prev); st.pending={"type":"ENTER","dir":q} if q else None
    else:
        q=int(prev["hybrid_dir"]); st.pending={"type":"ENTER","dir":q} if q else None
    st.last_mark=float(prev["close"])

    for _,r in oos.iterrows():
        op=float(r["open"]); cl=float(r["close"])
        st.mark(op); st.exec_pending(op)
        st.mark(cl)
        if st.side!=0: st.hold_bars+=1
        st.curve.append({"ts":r["datetime_utc"],"equity":st.equity,"exposure":st.exposure(cl),"side":st.side})
        st.pending=dec(st,r)

    if st.side!=0:
        px=float(oos.iloc[-1]["close"]); st.close_leg(px,"FINAL_CLOSE"); st.actions["EXIT"]+=1
        st.curve[-1]["equity"]=st.equity; st.curve[-1]["exposure"]=0.0; st.curve[-1]["side"]=0
    return st


def metrics(st: St):
    c=pd.DataFrame(st.curve)
    vals=c["equity"].pct_change().fillna(c["equity"].iloc[0]-1).to_numpy()
    total=(st.equity-1)*100
    days=(c["ts"].iloc[-1]-c["ts"].iloc[0]).total_seconds()/86400
    yrs=days/365.25
    cagr=((st.equity**(1/yrs)-1)*100) if st.equity>0 and yrs>0 else math.nan
    sd=np.std(vals)
    sharpe=np.mean(vals)/sd*math.sqrt(365.25*96) if sd>0 else math.nan
    eq=c["equity"].to_numpy(); dd=eq/np.maximum.accumulate(eq)-1
    lr=np.array([x["return_pct"] for x in st.legs],float)
    gp=lr[lr>0].sum(); gl=-lr[lr<0].sum(); pf=gp/gl if gl>0 else math.inf
    return {
        "return_pct":float(total),"cagr_pct":float(cagr),"sharpe":float(sharpe),
        "mdd_pct":float(dd.min()*100),"active_pct":float((c["exposure"]>1e-9).mean()*100),
        "avg_exposure_pct":float(c["exposure"].mean()*100),"max_exposure_pct":float(c["exposure"].max()*100),
        "turnover_equity_units":float(st.turnover),"fees_equity_units":float(st.fees),
        "legs":int(len(lr)),"win_rate_pct":float((lr>0).mean()*100) if len(lr) else math.nan,
        "profit_factor":float(pf),"actions":st.actions
    }


def yearly(st: St,name,cost_name):
    c=pd.DataFrame(st.curve).copy()
    c["r"]=c["equity"].pct_change().fillna(c["equity"].iloc[0]-1)
    c["year"]=c["ts"].dt.year
    rows=[]
    for y,g in c.groupby("year"):
        ret=(np.prod(1+g["r"].to_numpy())-1)*100
        eq=np.cumprod(1+g["r"].to_numpy()); dd=eq/np.maximum.accumulate(eq)-1
        rows.append({"strategy":name,"cost":cost_name,"year":int(y),"return_pct":float(ret),
                     "mdd_pct":float(dd.min()*100),"active_pct":float((g["exposure"]>1e-9).mean()*100)})
    return rows


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=load_candles()
    btc=load_raw(BTC_DIR); eth=load_raw(ETH_DIR)
    model,train,coef=fit_aoa_direction(candles)
    X=candles[MARKET_FEATURES]
    candles["aoa_p_long"]=model.predict_proba(X)[:,1]
    hsig=hybrid_daily_signal(btc,eth)
    hmap=dict(zip(btc["datetime_utc"],hsig))
    candles["hybrid_dir"]=candles["bar_start"].map(hmap).fillna(0).astype(int)
    candles["datetime_utc"]=candles["bar_start"]

    results=[]; yrows=[]; legs=[]; curves=[]
    for cost_name,side_cost in COSTS.items():
        for name in ["AOA_CLONE","AOA_CORE","AOA_HYBRID"]:
            st=simulate(name,candles,side_cost)
            row={"strategy":name,"cost":cost_name,**metrics(st)}
            results.append(row); yrows+=yearly(st,name,cost_name)
            for q in st.legs: legs.append({"strategy":name,"cost":cost_name,**q})
            cc=pd.DataFrame(st.curve); cc["strategy"]=name; cc["cost"]=cost_name; curves.append(cc)

    summary=pd.DataFrame(results)
    years=pd.DataFrame(yrows)
    pd.DataFrame(legs).to_csv(OUT/"legs.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(OUT/"equity_curves.csv.gz",index=False,compression="gzip")
    summary.to_csv(OUT/"summary.csv",index=False)
    years.to_csv(OUT/"yearly.csv",index=False)

    oos=btc[btc["datetime_utc"]>=OOS_START]
    bh=(oos["close"].iloc[-1]/oos["open"].iloc[0]-1)*100
    design=(BASE/"DESIGN_LOCK.md").read_bytes()
    meta={
        "oos_start":OOS_START.isoformat(),
        "oos_end":str(oos["datetime_utc"].iloc[-1]),
        "btc_rows":int(len(oos)),
        "aoa_direction_train_n":int(len(train)),
        "aoa_direction_coefficients":coef,
        "design_lock_sha256":hashlib.sha256(design).hexdigest(),
        "btc_buy_hold_pct_no_cost":float(bh),
        "costs":COSTS
    }
    (OUT/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")

    print("=== META ==="); print(json.dumps(meta,indent=2))
    print("\n=== SUMMARY ==="); print(summary.to_string(index=False))
    print("\n=== YEARLY ==="); print(years.to_string(index=False))


if __name__=="__main__":
    main()
