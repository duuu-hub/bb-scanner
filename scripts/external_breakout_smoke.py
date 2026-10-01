import numpy as np
import scripts.external_breakout_replay as m

def patch(rows):
 old=m.w1m
 def f(symbol,ts):
  t=np.array([x[0] for x in rows],dtype=np.int64);o=np.array([x[1] for x in rows],float);h=np.array([x[2] for x in rows],float);l=np.array([x[3] for x in rows],float)
  while len(t)<15:
   nt=(t[-1]+60000) if len(t) else 0;t=np.r_[t,nt];o=np.r_[o,100.];h=np.r_[h,101.];l=np.r_[l,99.]
  return t[:15],o[:15],h[:15],l[:15]
 m.w1m=f;return old

old=patch([(0,100,101,99),(60000,100,106,99),(120000,100,101,94)])
assert m.dual_side("TEST",0,105,95)=="long";m.w1m=old
old=patch([(0,100,101,99),(60000,100,101,94),(120000,100,106,99)])
assert m.dual_side("TEST",0,105,95)=="short";m.w1m=old
old=patch([(0,100,106,94)])
assert m.dual_side("TEST",0,105,95)=="side_ambiguous";m.w1m=old
print("PASS dual_side")

old=patch([(0,100,106,99)])
assert m.stop_entry_bar("TEST",0,"long",100,105,95)=="loss";m.w1m=old
old=patch([(0,100,101,99),(60000,102,106,101)])
assert m.stop_entry_bar("TEST",0,"long",100,105,95)=="win";m.w1m=old
old=patch([(0,100,106,94)])
assert m.established("TEST",0,"long",105,95)=="loss";m.w1m=old
print("PASS chronology")

t=np.array([0,900000,1800000,2700000],dtype=np.int64);o=np.array([110.,100.,100.,100.]);h=np.array([111.,101.,101.,101.]);l=np.array([109.,99.,99.,99.])
x=m.entry("TEST",t,o,h,l,0,105.,95.)
assert x["side"]=="long" and x["fill"]==110.
print("PASS gap_fill")

rt=np.arange(200,dtype=np.int64)*3600000;rh=np.linspace(100,200,200);rl=rh-10;rc=rh-5;atr=np.ones(200)*5;ema=np.ones(200)*120;bbw=np.ones(200)*.1
cfg=[x for x in m.CONFIGS if x["name"]=="FORTUNE_N24"][0]
a=m.levels(cfg,120,rt,rh,rl,rc,atr,ema,bbw);rh2=rh.copy();rl2=rl.copy();rh2[120]=9999;rl2[120]=1
b=m.levels(cfg,120,rt,rh2,rl2,rc,atr,ema,bbw);assert a==b
print("PASS no_lookahead")

names=[x["name"] for x in m.CONFIGS]
assert names==["FORTUNE_N12","FORTUNE_N24","FORTUNE_N48","BLUE_A_DONCHIAN12","BLUE_B_DONCHIAN48","BLUE_C_COMPRESS12","BLUE_D_SQUEEZE20","BLUE_E_EMA50_DON24","BLUE_F_PREVDAY"]
assert all(x["sl"]==1. for x in m.CONFIGS)
assert all(x["r"]==3. for x in m.CONFIGS if x["group"]=="FORTUNE")
assert all(x["r"]==2. for x in m.CONFIGS if x["group"]=="BLUE_PROXY")
print("PASS frozen_configs")
print("ALL_EXTERNAL_BREAKOUT_SMOKE_PASS")
