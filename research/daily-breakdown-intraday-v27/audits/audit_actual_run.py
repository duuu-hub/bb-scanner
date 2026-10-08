"""Read-only post-run audit against immutable GitHub originals; no rule tuning."""
from pathlib import Path
import argparse,base64,collections,gzip,hashlib,io,json,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from scripts import daily_breakdown_intraday_v27 as frozen

def sha(b):return hashlib.sha256(b).hexdigest()
def git_sha(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def verified_bytes(r):
    b=base64.b64decode(r['content']) if r.get('encoding')=='base64' else r['content'].encode()
    assert git_sha(b)==r['blob_sha'],r['path']
    return b
def read(folder,name):return json.loads((folder/name).read_text())
def close(a,b):assert np.isclose(a,b,rtol=1e-9,atol=1e-10),(a,b)
def scenario(r):return f"{r['split']}__{r['policy']}__CAP{r['daily_cap']}__{r['cost_bps']}bp__{'guarded' if r['guarded'] else 'unguarded_diagnostic'}"
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--inputs',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    originals=read(a.inputs,'account-evidence.json');aggregate=read(a.inputs,'aggregate-evidence.json')
    trade_files=read(a.inputs,'primary-trades.json');day_files=read(a.inputs,'primary-days.json')
    source=read(a.inputs,'SCAN_SOURCE_AUDIT.json');reg=frozen.registry()
    terminal=read(a.inputs,'terminal.json');cycle=read(a.inputs,'cycle-evidence.json')
    assert terminal['id']==37761086287 and terminal['status']=='completed' and terminal['conclusion']=='success'
    expected_jobs={'validate','prepare_market','collect','preserve_cycle','checkpoint'}|{f'scan ({i})' for i in range(8)}|{f'accounts ({i})' for i in range(16)}
    assert len(terminal['jobs'])==29 and {j['name'] for j in terminal['jobs']}==expected_jobs
    assert all(j['status']=='completed' and j['conclusion']=='success' for j in terminal['jobs'])
    cycle_manifest=json.loads(verified_bytes(next(r for r in cycle if r['file']=='EVIDENCE_MANIFEST.json')))
    index_record=next(r for r in cycle if r['file']=='files/partition-index.json')
    cycle_index=json.loads(verified_bytes(index_record))
    assert cycle_manifest['run']=='37761086287' and cycle_manifest['code_commit']==terminal['head_sha']
    assert sha(verified_bytes(index_record))==next(f['sha256'] for f in cycle_manifest['files'] if f['original_path']=='partition-index.json')
    assert len(cycle_index)==24 and not any(r.get('status')=='PARTITION_MISSING' for r in cycle_index)
    assert source['all_checks_passed'] and source['original_source_files_per_period']==856
    by_probe=collections.defaultdict(dict)
    for r in originals:by_probe[r['probe']][r['kind']]=(r,json.loads(verified_bytes(r)))
    assert set(by_probe)==set(range(16))
    all_rows={};all_audits={};manifest_entries={};original_bytes=0;original_files=0
    for i,items in by_probe.items():
        assert set(items)=={'manifest','summary','audit','result_manifest'}
        manifest=items['manifest'][1]
        indexed=next(r for r in cycle_index if r['label']==f'account-probe-{i}')
        assert indexed['commit']==items['manifest'][0]['commit'] and indexed['original']==manifest
        assert manifest['run']=='37761086287' and manifest['code_commit']=='fbf329c27f4bff0d7f99febbbd4a14768ce2024a'
        files={x['original_path']:x for x in manifest['files']};manifest_entries[i]=files
        original_bytes+=sum(x['bytes'] for x in files.values());original_files+=len(files)
        for kind,name in [('summary','summary.json'),('audit','audit.json'),('result_manifest','result_manifest.json')]:
            assert sha(verified_bytes(items[kind][0]))==files[name]['sha256']
        m=items['result_manifest'][1]
        assert m['probe_index']==i and m['policy']==reg['policies'][i]['policy'] and m['scenarios']==24 and m['all_checks_passed'] and m['v25_rejection_retained']
        assert m['contract_sha256']==sha(frozen.REGISTRY.read_bytes())
        assert len(items['summary'][1])==len(items['audit'][1])==24
        for row in items['summary'][1]:
            assert row['probe_index']==i and row['policy']==reg['policies'][i]['policy']
            key=scenario(row);assert key not in all_rows;all_rows[key]=row
        for audit in items['audit'][1]:
            key=audit['scenario'];assert key not in all_audits
            assert audit['all_checks_passed'] and audit['quota_verified'] and audit['actual_max_hold_minutes']==1440
            for name,digest in audit['sha256'].items():assert files[f'details/{key}/{name}']['sha256']==digest
            all_audits[key]=audit
    assert len(all_rows)==len(all_audits)==384 and set(all_rows)==set(all_audits)
    actual={(r['policy'],r['split'],r['cost_bps'],r['guarded'],str(r['daily_cap'])) for r in all_rows.values()}
    expected={(p['policy'],s,c,g,cap) for p in reg['policies'] for s in ('DEV','GATE') for c in (20,40) for g in (True,False) for cap in ('UNLIMITED','1','2')}
    assert actual==expected
    agg={r['file']:verified_bytes(r) for r in aggregate}
    summaries=json.loads(agg['summary.json']);audits=json.loads(agg['audit.json']);clusters=json.loads(agg['cluster_diagnostics.json']);flags=json.loads(agg['material_screen.json']);decision=json.loads(agg['decision.json'])
    assert {scenario(r):r for r in summaries}==all_rows
    assert {r['scenario']:r for r in audits}==all_audits
    periods=pd.read_csv(io.BytesIO(agg['year_quarter.csv'])).to_dict('records')
    assert frozen.material_screen(summaries,periods,clusters)==flags
    assert len(flags)==24 and sum(f['further_audit_flag'] for f in flags)==decision['further_audit_flags']==0
    assert decision['qualified_candidates']==0 and not decision['account_daily_target_claim'] and decision['v25_rejection_retained']
    primary={k:r for k,r in all_rows.items() if r['guarded'] and r['cost_bps']==40 and str(r['daily_cap']) in ('1','2')}
    trades={r['scenario']:r for r in trade_files};days={r['scenario']:r for r in day_files}
    assert len(primary)==64 and set(primary)==set(trades)==set(days)
    primary_details=[];reason_counts=collections.Counter()
    for key,row in primary.items():
        tb=verified_bytes(trades[key]);db=verified_bytes(days[key]);i=row['probe_index']
        assert sha(tb)==all_audits[key]['sha256']['trades.csv.gz'] and sha(db)==all_audits[key]['sha256']['daily.csv']
        try:tr=pd.read_csv(io.BytesIO(gzip.decompress(tb)))
        except pd.errors.EmptyDataError:tr=pd.DataFrame()
        day=pd.read_csv(io.BytesIO(db));cap=int(row['daily_cap']);assert len(tr)==row['trades'] and len(day)==row['calendar_days']
        assert day.entries.le(cap).all() and int(day.entries.sum())==len(tr) and not day.day.duplicated().any()
        pnl=tr.net_pnl.to_numpy() if len(tr) else np.array([])
        close(pnl.sum(),row['net_return_pct']/100);close(np.prod(1+day.return_pct/100),1+row['net_return_pct']/100)
        close(day.return_pct.mean(),row['daily_mean_pct']);close((day.return_pct>=.7).mean()*100,row['day_ge_0_7_pct']);close((day.return_pct>=1).mean()*100,row['day_ge_1_pct']);close((day.return_pct>=2).mean()*100,row['day_ge_2_pct'])
        assert int(day.partial_day.sum())==row['partial_calendar_days']
        residual=share=None
        if len(tr):
            reason_counts.update(tr.reason)
            assert tr.hold_min.between(0,1440,inclusive='right').all()
            close(tr.hold_min.max(),row['max_hold_min']);assert np.allclose((tr.exit_time-tr.entry_time)/60000,tr.hold_min)
            assert (tr.notional/tr.entry_equity<=.300000001).all() and (tr.reserved_risk/tr.entry_equity<=.005000001).all()
            assert np.allclose(tr.entry_fee,tr.notional*.002) and np.allclose(tr.exit_fee,tr.notional/tr.entry*tr.exit*.002)
            assert np.allclose(tr.funding,tr.notional*.0002*tr.hold_min/1440)
            dates=pd.to_datetime((tr.entry_time+9*3600000)//86400000*86400000,unit='ms',utc=True).dt.strftime('%Y-%m-%d')
            counts=tr.groupby(dates).size();assert int(counts.max())<=cap
            observed=day.set_index('day').entries;assert all(int(observed.get(d,0))==n for d,n in counts.items())
            gain=float(pnl[pnl>0].sum());loss=float(-pnl[pnl<0].sum())
            if loss:close(gain/loss,row['pf'])
            else:assert row['pf'] is None
            close((pnl>0).mean()*100,row['win_pct'])
            grouped=tr.groupby(dates).net_pnl.sum();positive=grouped[grouped>0].sort_values(ascending=False,kind='stable')
            retained=tr[~dates.isin(positive.head(5).index)]
            residual=float(retained.net_pnl.sum())
            share=float(positive.head(5).sum()/positive.sum()) if len(positive) else None
            match=[c for c in clusters if c['scope']=='EXECUTABLE_ACCOUNT_REALIZED_PNL' and c['policy']==row['policy'] and c['split']==row['split'] and str(c['daily_cap'])==str(cap)]
            assert len(match)==1 and match[0]['n']==len(tr)
            if len(retained):close(retained.net_pnl.mean(),match[0]['after_remove_best5_dates_mean'])
            else:assert match[0]['after_remove_best5_dates_mean'] is None
            if share is not None:close(share,match[0]['top5_positive_date_share'])
            else:assert match[0]['top5_positive_date_share'] is None
        start,end=frozen.engine.source_helpers.interval(row['split'])
        close(day.covered_hours.sum(),(end-start)/3600000)
        dated=pd.to_datetime(day.day)
        for year,g in day.groupby(dated.dt.year):
            matches=[p for p in periods if p['scenario']==key and str(p['period'])==str(year)]
            assert len(matches)==1;close((np.prod(1+g.return_pct/100)-1)*100,matches[0]['net_return_pct'])
            assert int(g.partial_day.sum())==matches[0]['partial_days']
        primary_details.append(dict(scenario=key,trades=len(tr),maximum_hold_minutes=row['max_hold_min'],cap=cap,net_return_pct=row['net_return_pct'],daily_mean_pct=row['daily_mean_pct'],after_remove_best5_dates_residual_pnl=residual,top5_positive_date_share=share,all_direct_trade_calendar_checks_passed=True))
    table=pd.DataFrame(summaries);primary_table=table[table.guarded&(table.cost_bps==40)&table.daily_cap.astype(str).isin(['1','2'])];gate=primary_table[primary_table.split=='GATE'];dev=primary_table[primary_table.split=='DEV'];best=all_rows[scenario(gate.loc[gate.net_return_pct.idxmax()].to_dict())]
    range_of=lambda f,k:[float(f[k].min()),float(f[k].max())]
    report=dict(run_id=37761086287,run_head_commit='fbf329c27f4bff0d7f99febbbd4a14768ce2024a',all_checks_passed=True,scope='All 384 original summary/audit/archived detail hashes; direct original trades and calendars for all 64 primary guarded 40bp cap1/2 scenarios',source_audit=source,account_partitions=16,original_account_evidence_files=original_files,original_account_evidence_bytes=original_bytes,account_scenarios_verified=384,primary_trade_calendars_directly_recomputed=64,qualified_candidates=0,further_audit_flags=0,account_daily_target_claim=False,v25_rejection_retained=True,observed_history_only=True,findings=dict(primary_dev_return_pct_range=range_of(dev,'net_return_pct'),primary_gate_return_pct_range=range_of(gate,'net_return_pct'),primary_daily_mean_pct_range=range_of(primary_table,'daily_mean_pct'),gate_positive_accounts=int((gate.net_return_pct>0).sum()),gate_accounts=32,primary_accounts_positive_after_best5_date_removal=sum(d['after_remove_best5_dates_residual_pnl'] is not None and d['after_remove_best5_dates_residual_pnl']>0 for d in primary_details),gate_accounts_positive_after_best5_date_removal=sum(d['scenario'].startswith('GATE') and d['after_remove_best5_dates_residual_pnl'] is not None and d['after_remove_best5_dates_residual_pnl']>0 for d in primary_details),best_observed_primary_gate=best,failed_condition_counts=dict(collections.Counter(x for f in flags if f['daily_cap']!='UNLIMITED' for x in f['failed_conditions']))),primary_details=primary_details,limitations=['DEV and GATE were already observed; this is exploratory, not pristine OOS.','KST boundary dates and partial years retain explicit partial-day labels; GATE 2024 begins at KST 09:00 on January 1.','Best-date removal is arithmetic on original realized P&L, with no capital reallocation.','Entry stress uses exact 15m opens; it does not calibrate sub-15m execution or market impact.'])
    report['findings']['primary_trade_exit_reasons']=dict(reason_counts)
    report['findings']['primary_time_exit_share_pct']=100*reason_counts['TIME']/sum(reason_counts.values())
    report['terminal_execution']=terminal
    report['permanent_cycle_evidence']=dict(commit=terminal['evidence_head']['commit'],partition_refs_verified=24,cycle_evidence_files=len(cycle_manifest['files']),cycle_original_bytes=sum(f['bytes'] for f in cycle_manifest['files']),partial_or_missing_partitions=0)
    a.out.mkdir(parents=True,exist_ok=True);(a.out/'V27_ACTUAL_INTRADAY_AUDIT.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    primary_table.to_csv(a.out/'PRIMARY_40BP_GUARDED_64.csv',index=False)
    print('V27_ROOT_ORIGINAL_384_AND_DIRECT_PRIMARY64_AUDIT_PASS');print(json.dumps(report['findings'],indent=2))
if __name__=='__main__':main()
