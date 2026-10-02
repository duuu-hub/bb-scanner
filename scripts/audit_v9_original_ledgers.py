"""Audit immutable saved V9 bytes/financial arithmetic; no market data rerun."""
from __future__ import annotations
import argparse,base64,hashlib,json,subprocess
from pathlib import Path
import numpy as np
import pandas as pd

def sha(data):return hashlib.sha256(data).hexdigest()

def run(inputs,cells_path,out):
    context=json.loads(inputs.read_text());out.mkdir(parents=True,exist_ok=True)
    assert sha(cells_path.read_bytes())==context['cells_sha256']
    frames,checked=[],[]
    assert {r['shard'] for r in context['files']}==set(range(8))
    for rec in context['files']:
        path=out/f'original-dev-{rec["shard"]}.csv.gz'
        result=subprocess.run(['gh','api','repos/duuu-hub/bb-scanner/git/blobs/'+rec['blob_sha']],check=True,capture_output=True,text=True)
        d=json.loads(result.stdout);assert d['sha']==rec['blob_sha'] and d['encoding']=='base64'
        data=base64.b64decode(''.join(d['content'].split()),validate=True)
        assert len(data)==rec['bytes'] and sha(data)==rec['sha256'];path.write_bytes(data)
        frame=pd.read_csv(path);frames.append(frame);checked.append(dict(shard=rec['shard'],sha256=rec['sha256'],bytes=len(data),rows=len(frame)))
    x=pd.concat(frames,ignore_index=True);cells=pd.read_csv(cells_path)
    assert len(x)==223218 and len(cells)==96 and not x.duplicated(['symbol','variant','entry_time']).any()
    ratio=x['exit']/x.entry;gross=x.side*(ratio-1)
    assert np.allclose(gross,x.gross_return,atol=1e-10,rtol=0)
    slippage=np.where(x.reason.eq('SL'),x.side*(ratio-ratio*(1-x.side*.001)),0.)
    actual_ratio=ratio*np.where(x.reason.eq('SL'),1-x.side*.001,1.)
    fees=.002*(1+actual_ratio);fund=.0002*(x.exit_time-x.entry_time)/86400000
    net=x.side*(actual_ratio-1)-fees-fund
    stop_ratio=x.sl/x.entry*(1-x.side*.001)
    risk=x.side*(1-stop_ratio)+.002*(1+stop_ratio)+.0002*x.max_hold_bars/96
    assert np.all(risk>0) and np.allclose(net,x.net40_fraction,atol=1e-10,rtol=0)
    assert np.allclose(net/risk,x.net40_R,atol=1e-10,rtol=0)
    assert np.allclose(x.risk_pct,abs(x.entry-x.sl)/x.entry,atol=1e-10,rtol=0)
    assert x.risk_pct.between(.0075-1e-10,.08+1e-10).all()
    assert x.max_hold_bars.isin([24,96]).all() and ((x.exit_time-x.entry_time)>0).all()
    x['gross_bp_rebuilt']=gross*10000;x['fee_bp_rebuilt']=fees*10000
    x['slip_bp_rebuilt']=slippage*10000;x['fund_bp_rebuilt']=fund*10000
    x['kday']=((x.entry_time+9*3600000)//86400000).astype('int64')
    x['kyear']=pd.to_datetime(x.entry_time,unit='ms',utc=True).dt.tz_convert('Asia/Seoul').dt.year
    maximum_error=0.;decomposition=[]
    for r in cells.to_dict('records'):
        g=x[x.variant.eq(r['policy'])];assert len(g)==r['n'] and g.symbol.nunique()==r['symbols']
        if not len(g):continue
        pn=g.net40_fraction;gain=pn[pn>0].sum();loss=-pn[pn<0].sum()
        rebuilt=dict(net40_mean_bp=pn.mean()*10000,net40_R_mean=g.net40_R.mean(),net40_pf=gain/loss,win_pct=(pn>0).mean()*100)
        contrib=g.groupby('symbol').net40_fraction.sum().clip(lower=0)
        rebuilt['top_symbol_share_pct']=contrib.max()/contrib.sum()*100 if contrib.sum()>0 else 100.
        for year,z in g.groupby('kyear'):
            rebuilt.update({f'year_{year}_net40_R':z.net40_R.mean(),f'year_{year}_net40_mean_bp':z.net40_fraction.mean()*10000,
                f'year_{year}_day_R':z.groupby('kday').net40_R.mean().mean(),f'year_{year}_days':z.kday.nunique(),f'year_{year}_n':len(z)})
        for key,val in rebuilt.items():
            assert key in r and np.isfinite(r[key]) and np.isclose(val,r[key],atol=1e-9,rtol=0),(r['policy'],key,val,r.get(key))
            maximum_error=max(maximum_error,abs(val-r[key]))
        decomposition.append(dict(policy=r['policy'],n=len(g),gross_mean_bp=float(g.gross_bp_rebuilt.mean()),
            fees_mean_bp=float(g.fee_bp_rebuilt.mean()),slip_mean_bp=float(g.slip_bp_rebuilt.mean()),
            funding_mean_bp=float(g.fund_bp_rebuilt.mean()),net40_mean_bp=float(pn.mean()*10000),
            cost_drag_bp=float((g.fee_bp_rebuilt+g.slip_bp_rebuilt+g.fund_bp_rebuilt).mean())))
    report=dict(status='V9_ORIGINAL_BYTE_LEDGER_ARITHMETIC_AUDIT_PASS',source_run=context['source_run'],source_code=context['source_code'],
        source_evidence_commit=context['evidence_commit'],original_ledgers=checked,cells=96,parameterized_outcomes=len(x),
        maximum_saved_stat_absolute_error=maximum_error,cost_decomposition=decomposition,
        gross_positive_cells=sum(r['gross_mean_bp']>0 for r in decomposition),
        net40_positive_cells=sum(r['net40_mean_bp']>0 for r in decomposition),
        gross_mean_bp_range=[min(r['gross_mean_bp'] for r in decomposition),max(r['gross_mean_bp'] for r in decomposition)],
        cost_drag_bp_range=[min(r['cost_drag_bp'] for r in decomposition),max(r['cost_drag_bp'] for r in decomposition)],
        note='Downloaded exact immutable saved original ledgers; independent gross/fees/slippage/funding/risk,all96 means/PF/year/date/concentration rebuilt. No market scan,selection change or new discovery. Not executable account trades.')
    (out/'ORIGINAL_LEDGER_AUDIT.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print('V9_ORIGINAL_BYTE_LEDGER_ARITHMETIC_AUDIT_PASS',json.dumps({k:report[k] for k in ('cells','parameterized_outcomes','gross_positive_cells','net40_positive_cells','gross_mean_bp_range','cost_drag_bp_range')}),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--inputs',type=Path,required=True);ap.add_argument('--cells',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();run(a.inputs,a.cells,a.out)
