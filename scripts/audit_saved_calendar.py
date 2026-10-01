"""Reporting-only reconstruction from frozen account curves; no price replay."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd
DAY=86400000
OFFSET=9*3600000

def rebuild(curve,trades,start,end):
    assert end>start
    assert not curve.time.duplicated().any()
    assert curve.time.iloc[0]==start and curve.time.iloc[-1]==end
    assert np.all(np.diff(curve.time)==900000)
    assert (curve.equity>0).all()
    marks=curve.set_index('time')
    used=curve[curve.time<end]
    active=used.groupby((used.time+OFFSET)//DAY).positions.max().gt(0).to_dict()
    entries=trades.groupby((trades.entry_time+OFFSET)//DAY).size().to_dict() if len(trades) else {}
    rows=[]
    for day in range((start+OFFSET)//DAY,(end-1+OFFSET)//DAY+1):
        a=max(start,day*DAY-OFFSET);z=min(end,(day+1)*DAY-OFFSET)
        eq0=float(marks.loc[a,'equity_pre_entry'])
        eq1=float(marks.loc[z,'equity']) if z==end else float(marks.loc[z,'equity_pre_entry'])
        rows.append(dict(day=pd.Timestamp(day*DAY,unit='ms',tz='UTC').strftime('%Y-%m-%d'),
                         start_equity=eq0,end_equity=eq1,return_pct=100*(eq1/eq0-1),
                         entries=int(entries.get(day,0)),active=bool(active.get(day,False) or entries.get(day,0)),
                         partial_day=z-a!=DAY,covered_hours=(z-a)/3600000))
    d=pd.DataFrame(rows)
    assert np.isclose(d.covered_hours.sum(),(end-start)/3600000)
    assert np.isclose(np.prod(1+d.return_pct/100),curve.equity.iloc[-1]/curve.equity_pre_entry.iloc[0],atol=1e-10)
    return d

def audit(source,out):
    summaries=json.loads((source/'summary.json').read_text())
    out.mkdir(parents=True,exist_ok=True);results=[];checks=[]
    for r in summaries:
        name=f'{r["split"]}__{r["variant"]}__{r["cost_bps"]}bp__{"guarded" if r["guarded"] else "diagnostic"}'
        path=source/'details'/name
        curve=pd.read_csv(path/'curve.csv.gz')
        try:trades=pd.read_csv(path/'trades.csv.gz')
        except pd.errors.EmptyDataError:trades=pd.DataFrame()
        old=pd.read_csv(path/'daily.csv');start,end=map(int,curve.time.iloc[[0,-1]])
        d=rebuild(curve,trades,start,end)
        complete=d[~d.partial_day].reset_index(drop=True)
        assert old.day.tolist()==complete.day.tolist()
        for col in ('start_equity','end_equity','return_pct'):
            np.testing.assert_allclose(old[col],complete[col],atol=1e-10)
        np.testing.assert_array_equal(old.entries,complete.entries)
        np.testing.assert_array_equal(old.active,complete.active)
        final=float(curve.equity.iloc[-1])
        assert np.isclose(final,1+r['net_return_pct']/100,atol=1e-10)
        if len(trades):assert np.isclose(trades.net_pnl.sum(),final-1,atol=1e-10)
        new={**r,'scenario':name,'calendar_days_original':r['calendar_days'],
             'original_calendar_scope':'COMPLETE_KST_DATES_ONLY',
             'calendar_scope':'ALL_INTERSECTED_KST_DATES','calendar_days':len(d),
             'partial_calendar_days':int(d.partial_day.sum())}
        ret=d.return_pct.to_numpy()
        for key,val in dict(daily_mean_pct=float(ret.mean()),
                daily_geometric_pct=float(100*(final**(1/len(d))-1)),
                day_ge_0_7_pct=float((ret>=.7).mean()*100),day_ge_2_pct=float((ret>=2).mean()*100),
                loss_days_pct=float((ret<0).mean()*100),no_entry_days_pct=float((d.entries==0).mean()*100),
                flat_days_pct=float((~d.active).mean()*100),worst_day_pct=float(ret.min()),best_day_pct=float(ret.max())).items():
            new[key+'_original']=r.get(key);new[key]=val
        new.update(day_ge_0_7_count=int((ret>=.7).sum()),day_ge_2_count=int((ret>=2).sum()))
        dest=out/'details'/name;dest.mkdir(parents=True,exist_ok=True)
        d.to_csv(dest/'daily_all_kst_dates.csv',index=False)
        checks.append(dict(scenario=name,all_checks_passed=True,original_complete_dates_exactly_match=True,
            final_cash_and_trades_unchanged=True,daily_product_equals_final_cash=True,
            start=start,end=end,
            original_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path/'curve.csv.gz',path/'trades.csv.gz',path/'daily.csv')},
            corrected_daily_sha256=hashlib.sha256((dest/'daily_all_kst_dates.csv').read_bytes()).hexdigest()))
        results.append(new)
        print('SAVED_CALENDAR_AUDIT',name,len(d),'dates',new['day_ge_0_7_count'],'goal0.7 days',new['day_ge_0_7_pct'],flush=True)
    (out/'summary.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in results]).to_csv(out/'summary.csv',index=False)
    (out/'audit.json').write_text(json.dumps(dict(kind='REPORTING_ONLY_NO_MARKET_RERUN',scenarios=checks),indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('source','out'):p.add_argument('--'+k,type=Path,required=True)
    args=p.parse_args();audit(args.source,args.out)
