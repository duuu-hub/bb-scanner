"""Independent saved V13 ledger audit. This never reruns market signals."""
import argparse,base64,hashlib,io,json,os,sys,time,urllib.parse,urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

REPO='duuu-hub/bb-scanner'
REF='6f3766792984f15d2a1422f6672d7a06be8f3b7e'
ROOT='research/breakout-level-retest-v13/run-36992677948/complete-evidence'
CODE='869fbc6ca98c23880d9d64ffb71a322509b73d3d'
sha=lambda b:hashlib.sha256(b).hexdigest()

def api(endpoint):
    token=os.environ['GH_TOKEN']
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+'/'+endpoint,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'saved-v13-ledger-audit'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=90) as r:return json.load(r)
        except (urllib.error.URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(2*(attempt+1))

def get_file(path):
    result=api('contents/'+urllib.parse.quote(path,safe='/')+'?ref='+REF)
    if not result.get('content') or result.get('encoding')!='base64':result=api('git/blobs/'+result['sha'])
    if result['encoding']!='base64':raise ValueError('binary API content unavailable '+path)
    return base64.b64decode(result['content'])

def close(a,b,label,tol=1e-9):
    if not np.allclose(a,b,rtol=0,atol=tol,equal_nan=False):raise AssertionError(label)

def row_arithmetic(frame):
    entry,ex,side,stop,hold=[frame[k].to_numpy(float) for k in ('entry','exit','side','sl','max_hold_bars')]
    duration=(frame.exit_time.to_numpy(float)-frame.entry_time.to_numpy(float))/60000
    if (not np.isfinite(frame[['entry','exit','side','sl','max_hold_bars','entry_time','exit_time']]).all().all()
        or not frame.status.eq('RESOLVED').all() or not frame.split.eq('DEV').all()
        or np.any(~np.isin(side,[-1,1])) or np.any(entry<=0) or np.any(ex<=0)
        or np.any(duration<=0) or np.any(duration>hold*15)):raise AssertionError('saved trade geometry/stage')
    gross=side*(ex/entry-1);close(gross,frame.gross_return.to_numpy(float),'gross arithmetic')
    fill=np.where(frame.reason.eq('SL'),ex*(1-side*.001),ex);ratio=fill/entry
    fee=.002*(1+ratio);fund=.0002*duration/1440;net=side*(ratio-1)-fee-fund
    stop_ratio=stop/entry*(1-side*.001)
    reserve=side*(1-stop_ratio)+.002*(1+stop_ratio)+.0002*hold*15/1440
    if np.any(reserve<=0):raise AssertionError('nonpositive reserve')
    close(net,frame.net40_fraction.to_numpy(float),'net40 arithmetic')
    close(net/reserve,frame.net40_R.to_numpy(float),'R arithmetic')
    return gross,net,net/reserve,fee,fund,gross-side*(ratio-1)

def audit(out):
    out.mkdir(parents=True,exist_ok=True)
    manifest_bytes=get_file(ROOT+'/EVIDENCE_MANIFEST.json');manifest=json.loads(manifest_bytes)
    (out/'original_EVIDENCE_MANIFEST.json').write_bytes(manifest_bytes)
    if manifest['code_commit']!=CODE:raise AssertionError('code commit mismatch')
    files={f['file']:f for f in manifest['files']}
    tree=api('git/trees/'+REF+'?recursive=1')
    if tree['truncated']:raise AssertionError('truncated evidence tree')
    lookup={f['path']:f for f in tree['tree']}
    for f in files.values():
        if lookup[ROOT+'/'+f['file']]['size']!=f['bytes']:raise AssertionError('archive byte size mismatch')
    frames,proof,sources=[],[],{};prior=exclusions=minute_checks=0
    for i in range(8):
        relative=f'files/breakout-level-retest-v13-dev-{i}/'
        def original(name):
            b=get_file(ROOT+'/'+relative+name);f=files[relative+name]
            if len(b)!=f['bytes'] or sha(b)!=f['sha256']:raise AssertionError('original SHA/bytes '+relative+name)
            target=out/f'original-shard-{i}'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
            return b
        data=original('independent_candidates.csv.gz');m=json.loads(original('scan_meta.json'))
        source=json.loads(original('source_check.json'));bad=pd.read_csv(io.BytesIO(original('exclusions.csv')))
        frame=pd.read_csv(io.BytesIO(data),compression='gzip')
        if (not m['complete'] or m['stage']!='DEV' or m['shard']!=i or len(frame)!=m['ledger_rows']
            or sha(data)!=m['ledger_sha256'] or len(bad)!=m['exclusions'] or source['status']!='VERIFIED'
            or source['errors'] or source['shards']!=[i]):raise AssertionError('shard/source metadata')
        prior+=source['matched_prior_hashes'];exclusions+=len(bad);minute_checks+=m['minute_official_checksums_verified']
        for f in source['files']:
            if f['symbol'] in sources or m['market_hashes'][f['symbol']]!=f['sha256']:raise AssertionError('source hash/duplicate')
            sources[f['symbol']]=f['sha256']
        gross,net,risk,fee,fund,slip=row_arithmetic(frame)
        frame['_gross_bp']=gross*10000;frame['_net_bp']=net*10000;frame['_R']=risk
        frame['_drag_bp']=(gross-net)*10000;frame['_fee_bp']=fee*10000;frame['_fund_bp']=fund*10000;frame['_slip_bp']=slip*10000
        frames.append(frame);proof.append(dict(shard=i,rows=len(frame),sha256=sha(data),exclusions=len(bad)))
        print('V13_ORIGINAL_LEDGER_AUDITED',i,len(frame),sha(data),flush=True)
    x=pd.concat(frames,ignore_index=True)
    if len(sources)!=856 or prior!=256 or exclusions!=24 or len(x)!=145968 or x.duplicated(['symbol','variant','entry_time']).any():raise AssertionError('global coverage/trials')
    # Saved selection is a frozen development result, never reselected here.
    selected_root='files/breakout-level-retest-v13-selected/'
    cell_bytes=get_file(ROOT+'/'+selected_root+'development_policy_cells.csv')
    selection_bytes=get_file(ROOT+'/'+selected_root+'selection.json')
    for name,b in [('development_policy_cells.csv',cell_bytes),('selection.json',selection_bytes)]:
        if sha(b)!=files[selected_root+name]['sha256']:raise AssertionError('selection/cells hash')
        (out/name).write_bytes(b)
    cells=pd.read_csv(io.BytesIO(cell_bytes));selection=json.loads(selection_bytes)
    if len(cells)!=96 or selection['policies']:raise AssertionError('frozen selection count')
    detail=[];years=pd.to_datetime(x.entry_time,unit='ms',utc=True).dt.tz_convert('Asia/Seoul').dt.year
    x['_year']=years;x['_date']=(x.entry_time.to_numpy(np.int64)+9*3600000)//86400000
    for _,c in cells.iterrows():
        z=x[x.variant==c.policy]
        if len(z)!=c.n or z.symbol.nunique()!=c.symbols:raise AssertionError('policy count')
        close(z._net_bp.mean(),c.net40_mean_bp,'cell mean');close(z._R.mean(),c.net40_R_mean,'cell R')
        gain=z.loc[z._net_bp>0,'_net_bp'].sum();loss=-z.loc[z._net_bp<0,'_net_bp'].sum()
        close(gain/loss,c.net40_pf,'cell PF')
        for year,g in z.groupby('_year'):
            close(g._net_bp.mean(),c[f'year_{year}_net40_mean_bp'],'year price')
            close(g._R.mean(),c[f'year_{year}_net40_R'],'year R')
            close(g.groupby('_date')._R.mean().mean(),c[f'year_{year}_day_R'],'year equal-active-date R')
            if len(g)!=c[f'year_{year}_n'] or g._date.nunique()!=c[f'year_{year}_days']:raise AssertionError('year counts')
        detail.append(dict(policy=c.policy,n=len(z),gross_mean_bp=float(z._gross_bp.mean()),net40_mean_bp=float(z._net_bp.mean()),net40_R=float(z._R.mean()),
            cost_drag_bp=float(z._drag_bp.mean()),fee_bp=float(z._fee_bp.mean()),funding_bp=float(z._fund_bp.mean()),slip_bp=float(z._slip_bp.mean()),pf=float(c.net40_pf)))
    detail.sort(key=lambda r:r['net40_mean_bp'],reverse=True)
    report=dict(status='PASS',kind='independent-original8-sha-count-cost-R-all96-cell-arithmetic',run=36992677948,code_commit=CODE,evidence_commit=REF,
        rows=len(x),cells=96,selected=0,accounts=0,chronology_excluded=exclusions,source_files=len(sources),prior_hashes=prior,minute_checksum_records=minute_checks,
        archive_files=len(files),archive_bytes=sum(f['bytes'] for f in files.values()),all_tree_sizes_verified=True,
        positive_gross_cells=sum(c['gross_mean_bp']>0 for c in detail),positive_net40_cells=sum(c['net40_mean_bp']>0 for c in detail),
        cost_drag_bp_range=[min(c['cost_drag_bp'] for c in detail),max(c['cost_drag_bp'] for c in detail)],best_price_cell=detail[0],shards=proof,cells_detail=detail,
        limitations=['Overlapping diagnostics are not executable account trades.','All original ledgers/metadata/selected cells rehashed; remaining raw minute source bytes only archive tree size-checked.','No signal market rerun or clean-holdout claim.'])
    (out/'AUDIT.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    print('V13_SAVED_ORIGINAL_FULL_AUDIT_PASS',json.dumps({k:v for k,v in report.items() if k not in ('cells_detail','shards')}),flush=True)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();audit(a.out)
