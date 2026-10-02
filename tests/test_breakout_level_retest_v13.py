"""Causal frozen breakout memory, closed retest, canonical fill and source contracts."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import breakout_level_retest_v13 as s

def event(side=1,participation='ANY',age=2,starts=(320,),n=1000):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.8);c=o.copy();h=np.full(n,101.);l=np.full(n,100.6)
    f=dict(eligible=np.ones(n,bool),volume_multiple=np.ones(n),prior_atr=np.ones(n),
           buy_share=np.full(n,.5),r1=np.zeros(n),clv=np.full(n,.5),
           prior_quote_mean=np.full(n,1e6),quote=np.full(n,.5e6))
    for w in (96,288):
        f[f'channel_high{w}']=np.full(n,101.);f[f'channel_low{w}']=np.full(n,99.)
    for b in starts:
        r=b+age
        o[b]=100.9;c[b]=102.;h[b]=102.3;l[b]=100.8
        f['volume_multiple'][b]=2.;f['quote'][b]=2e6;f['clv'][b]=.9;f['r1'][b]=.01
        if age>1:o[b+1]=102.;c[b+1]=102.1;h[b+1]=102.3;l[b+1]=101.4
        o[r]=101.1;c[r]=102.4;h[r]=102.6;l[r]=100.8;f['clv'][r]=.9
        o[r+1]=c[r+1]=102.4;h[r+1]=102.6;l[r+1]=102.2
    if side==-1:
        o,c,h,l=200-o,200-c,200-l,200-h
        f['clv']=1-f['clv'];f['r1']=-f['r1']
    raw=[t,o,h,l,c];btc=dict(close=np.full(n,20000.),r1=np.full(n,.001))
    cfg=next(x for x in s.configurations() if x['side']==side and x['window']==96 and x['wait']==8 and x['participation']==participation)
    return raw,f,btc,cfg

def seed(raw,f,btc,cfg):
    return s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+len(raw[0])*s.BAR)

def history_fixture(n=3400):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR
    c=100*np.exp(np.arange(n)*1e-6);q=np.full(n,1e6)
    return [t,c.copy(),c*1.001,c*.999,c],q,q*.6

class FeatureTests(unittest.TestCase):
    def test_fixed_grid_and_holds(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)
        self.assertEqual(set(p['hold'] for p in s.policies()),{48,192})
    def test_independent_prior_channel_quote_and_atr_cutoffs(self):
        raw,q,buy=history_fixture();i=3150;q[i]=1e9;raw[2][i]=1e6;f=s.features(raw,q,buy)
        for w in (96,288):
            self.assertEqual(f[f'channel_high{w}'][i],max(raw[2][i-w:i]))
            self.assertEqual(f[f'channel_low{w}'][i],min(raw[3][i-w:i]))
        self.assertEqual(f['prior_quote_mean'][i],np.mean(q[i-96:i]))
        tr=np.maximum(raw[2][1:i]-raw[3][1:i],np.maximum(abs(raw[2][1:i]-raw[4][:i-1]),abs(raw[3][1:i]-raw[4][:i-1])))
        expected=pd.Series(np.r_[np.nan,tr]).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
        self.assertAlmostEqual(f['prior_atr'][i],expected)
    def test_current_breakout_cannot_change_its_prior_level_baseline_or_atr(self):
        raw,q,buy=history_fixture();old=s.features(raw,q,buy);i=3150
        for z in raw[1:]:z[i]*=2
        q[i]*=10;buy[i]=q[i];new=s.features(raw,q,buy)
        for k in ('prior_atr','prior_quote_mean','channel_high96','channel_low96','channel_high288','channel_low288'):
            self.assertEqual(old[k][i],new[k][i],k)
    def test_future_perturbation_preserves_prefix_features_and_signal(self):
        raw,q,buy=history_fixture();old=s.features(raw,q,buy)
        for z in raw[1:]:z[3250:]*=3
        q[3250:]*=4;buy[3250:]=0;new=s.features(raw,q,buy)
        for k in old:np.testing.assert_allclose(old[k][:3250],new[k][:3250],equal_nan=True)
        raw,f,btc,cfg=event();before=seed(raw,f,btc,cfg)[0][0]
        for z in raw[2:]:z[323:]*=4
        after=seed(raw,f,btc,cfg)[0][0];self.assertEqual(before,after)
    def test_channel_and_volume_restart_after_gap(self):
        raw,q,buy=history_fixture();raw[0][3100:]+=s.BAR;f=s.features(raw,q,buy)
        for w in (96,288):
            self.assertTrue(np.isnan(f[f'channel_high{w}'][3100:3100+w]).all())
            self.assertTrue(np.isfinite(f[f'channel_high{w}'][3100+w]))
        self.assertTrue(np.isnan(f['prior_quote_mean'][3100:3196]).all())
    def test_btc_missing_endpoint_is_unavailable(self):
        t=np.array([0,s.BAR,2*s.BAR]);bt=np.array([0,2*s.BAR]);v=np.array([100.,102.])
        x=s.align_context(t,[bt,v,v,v,v]);self.assertTrue(np.isnan(x['close'][1]));self.assertTrue(np.isnan(x['r1'][2]))

class SnapshotTests(unittest.TestCase):
    def test_mirrored_sides_and_minimum_wait(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side);ev,_=s.retest_events(cfg,raw,f,btc)
            self.assertEqual([x['index'] for x in ev],[322]);self.assertEqual(ev[0]['breakout_index'],320)
            self.assertEqual(ev[0]['level'],101. if side==1 else 99.)
        raw,f,btc,cfg=event(age=1);self.assertFalse(s.retest_events(cfg,raw,f,btc)[0])
    def test_snapshot_ignores_new_breakout_and_keeps_original_level_atr_baseline(self):
        raw,f,btc,cfg=event();f['volume_multiple'][321]=3.;f['prior_atr'][321]=.5
        f['prior_quote_mean'][321]=2e6;f['channel_high96'][321]=101.5;f['clv'][321]=.9
        raw[1][321]=101.9;self.assertTrue(s.breakout_mask(cfg,raw,f,btc)[321])
        ev,_=s.retest_events(cfg,raw,f,btc)
        self.assertEqual(len(ev),1);self.assertEqual(ev[0]['level'],101.)
        self.assertEqual(ev[0]['breakout_atr'],1.);self.assertEqual(ev[0]['breakout_baseline_quote'],1e6)
    def test_maximum_wait_is_inclusive_and_expired_snapshot_cannot_signal(self):
        raw,f,btc,cfg=event(age=8);self.assertEqual(s.retest_events(cfg,raw,f,btc)[0][0]['index'],328)
        raw,f,btc,cfg=event(age=9);ev,count=s.retest_events(cfg,raw,f,btc)
        self.assertFalse(ev);self.assertEqual(count['SNAPSHOT_EXPIRED'],1)
    def test_failed_close_cancels_before_retest(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side);raw[4][321]=100.74 if side==1 else 99.26
            ev,count=s.retest_events(cfg,raw,f,btc);self.assertFalse(ev);self.assertEqual(count['SNAPSHOT_CANCEL_CLOSE'],1)
    def test_source_gap_clears_snapshot(self):
        raw,f,btc,cfg=event();raw[0][321:]+=s.BAR
        ev,count=s.retest_events(cfg,raw,f,btc);self.assertFalse(ev);self.assertEqual(count['SNAPSHOT_PATH_GAP'],1)
    def test_first_retest_consumes_snapshot_and_invalid_fill_cannot_retry(self):
        raw,f,btc,cfg=event();raw[1][323]=raw[4][322]*1.006
        raw[1][324]=101.1;raw[4][324]=103.;raw[2][324]=103.2;raw[3][324]=100.8;f['clv'][324]=.9
        ev,_=s.retest_events(cfg,raw,f,btc);self.assertEqual([e['index'] for e in ev],[322])
        rows,count=seed(raw,f,btc,cfg);self.assertFalse(rows);self.assertEqual(count['ENTRY_CATCHUP_GAP'],1)
    def test_lowvolume_arithmetic_mean_excludes_breakout_and_uses_frozen_baseline(self):
        raw,f,btc,cfg=event(participation='LOWVOL75');f['quote'][320]=100e6
        f['quote'][321]=.4e6;f['quote'][322]=1.1e6;f['prior_quote_mean'][322]=9e6
        ev,_=s.retest_events(cfg,raw,f,btc);self.assertAlmostEqual(ev[0]['pullback_volume_multiple'],.75)
        f['quote'][322]+=1.;self.assertFalse(s.retest_events(cfg,raw,f,btc)[0])
        cfg=dict(cfg,participation='ANY');self.assertEqual(len(s.retest_events(cfg,raw,f,btc)[0]),1)
    def test_retest_touch_recovery_body_clv_and_market_guards(self):
        for change in ('no_touch','no_recovery','wrong_body','clv','btc_missing','btc_shock','coin_shock','eligibility'):
            raw,f,btc,cfg=event()
            if change=='no_touch':raw[3][322]=101.26
            elif change=='no_recovery':raw[4][322]=raw[2][321]
            elif change=='wrong_body':raw[1][322]=103.
            elif change=='clv':f['clv'][322]=.59
            elif change=='btc_missing':btc['r1'][322]=np.nan
            elif change=='btc_shock':btc['r1'][322]=.016
            elif change=='coin_shock':f['r1'][322]=.081
            else:f['eligible'][322]=False
            self.assertFalse(s.retest_events(cfg,raw,f,btc)[0],change)
    def test_breakout_guards_are_checked_before_state_creation(self):
        for key,value in [('volume_multiple',1.249),('prior_atr',np.nan),('prior_quote_mean',0.),('eligible',False),('clv',.749),('r1',.081)]:
            raw,f,btc,cfg=event();f[key][320]=value;self.assertFalse(s.retest_events(cfg,raw,f,btc)[0],key)
        raw,f,btc,cfg=event();btc['r1'][320]=np.nan;self.assertFalse(s.retest_events(cfg,raw,f,btc)[0])
    def test_unknown_participation_fails_closed(self):
        raw,f,btc,cfg=event()
        with self.assertRaises(ValueError):s.retest_events(dict(cfg,participation='BAD'),raw,f,btc)

class FillExitTests(unittest.TestCase):
    def test_actual_fill_structural_stop_priority_and_time_cutoffs(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side);rows,_=seed(raw,f,btc,cfg);row=rows[0]
            entry=102.4 if side==1 else 97.6;stop=100.55 if side==1 else 99.45
            self.assertAlmostEqual(row['entry'],entry);self.assertAlmostEqual(row['sl'],stop)
            self.assertAlmostEqual(row['risk_pct'],1.85/entry);self.assertAlmostEqual(row['score'],2/(1.85/entry))
            self.assertEqual(row['channel_first_bar_open_time'],raw[0][224])
            self.assertEqual(row['channel_last_bar_open_time'],raw[0][319])
            self.assertEqual(row['decision_time'],raw[0][323]);self.assertEqual(row['entry_time'],raw[0][323])
    def test_adverse_fill_is_retained_favourable_catchup_and_wrong_side_are_excluded(self):
        raw,f,btc,cfg=event();raw[1][323]=101.8;self.assertEqual(seed(raw,f,btc,cfg)[0][0]['entry'],101.8)
        for price,reason in [(102.4*1.006,'ENTRY_CATCHUP_GAP'),(100.8,'RETEST_EDGE_WRONG_SIDE'),(float('nan'),'INVALID_ENTRY')]:
            raw,f,btc,cfg=event();raw[1][323]=price;rows,bad=seed(raw,f,btc,cfg)
            self.assertFalse(rows);self.assertEqual(bad[reason],1)
    def test_entry_timestamp_gap_and_split_cutoff(self):
        raw,f,btc,cfg=event();raw[0][323:]+=s.BAR;rows,bad=seed(raw,f,btc,cfg)
        self.assertFalse(rows);self.assertEqual(bad['ENTRY_PATH_GAP'],1)
        raw,f,btc,cfg=event();self.assertFalse(s.intents('X',cfg,raw,f,btc,s.base.START,raw[0][323])[0])
    def test_floor_and_maximum_stop_risk_use_actual_fill(self):
        raw,f,btc,cfg=event();ev,_=s.retest_events(cfg,raw,f,btc)
        ev[0]['retest_extreme']=102.39;ev[0]['breakout_atr']=.01
        with patch.object(s,'retest_events',return_value=(ev,{})):
            rows,_=seed(raw,f,btc,cfg);self.assertAlmostEqual(rows[0]['risk_pct'],.005)
        ev[0]['retest_extreme']=90.
        with patch.object(s,'retest_events',return_value=(ev,{})):
            rows,bad=seed(raw,f,btc,cfg);self.assertFalse(rows);self.assertEqual(bad['STOP_ABOVE_6PCT'],1)
    def test_cooldown_independent_of_exit(self):
        raw,f,btc,cfg=event(starts=(320,350,380));rows,_=seed(raw,f,btc,cfg)
        self.assertEqual([x['signal_time'] for x in rows],[raw[0][322],raw[0][382]])
    def test_tp_levels_and_independent_fee_funding_math(self):
        raw,f,btc,cfg=event();entry=102.4
        result=dict(status='RESOLVED',exit_time=raw[0][325],exit=105.,reason='TP',gross_return=105/entry-1)
        for kind,multiple in [('TP2',2),('TP3',3),('TRAIL',3)]:
            p=dict(cfg,hold=48,exit_type=kind,policy='P')
            with patch.object(s.canonical,'resolve',return_value=result) as resolver:
                rows,_,_=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+1000*s.BAR)
            self.assertAlmostEqual(resolver.call_args.args[0]['tp'],entry+multiple*1.85)
            ratio=105/entry;self.assertAlmostEqual(rows[0]['net40_fraction'],ratio-1-.002*(1+ratio)-.0002*30/1440)
            self.assertEqual(resolver.call_args.args[3],'TRAIL' if kind=='TRAIL' else 'TP2')
    def test_actual_entry_minute_tp_loses_and_later_chronology_is_honored(self):
        for side in (1,-1):
            for case,reason in [('entry_tp','SL'),('later_tp_first','TP'),('same_minute_both','SL')]:
                raw,f,btc,cfg=event(side);tr=seed(raw,f,btc,cfg)[0][0]
                tp=tr['entry']+side*2*abs(tr['entry']-tr['sl']);sl=tr['sl'];i=323 if case=='entry_tp' else 324
                raw[1][324]=raw[4][324]=tr['entry'];raw[2][324]=tr['entry']+.2;raw[3][324]=tr['entry']-.2
                raw[2][i]=max(tp,sl)+.2;raw[3][i]=min(tp,sl)-.2
                mt=raw[0][i]+np.arange(15,dtype=np.int64)*60000;mo=np.full(15,tr['entry']);mh=mo+.2;ml=mo-.2
                def touch_tp(k):
                    if side==1:mh[k]=tp+.2
                    else:ml[k]=tp-.2
                def touch_sl(k):
                    if side==1:ml[k]=sl-.2
                    else:mh[k]=sl+.2
                if case=='entry_tp':touch_tp(0)
                elif case=='later_tp_first':touch_tp(2);touch_sl(3)
                else:touch_tp(2);touch_sl(2)
                p=dict(cfg,hold=48,exit_type='TP2',policy='P')
                with patch.object(s.chronology.chronology,'w1m',return_value=(mt,mo,mh,ml)) as resolver:
                    rows,_,_=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+1000*s.BAR)
                resolver.assert_called_once();self.assertEqual(rows[0]['reason'],reason)
                self.assertAlmostEqual(rows[0]['exit'],sl if reason=='SL' else tp)
    def test_missing_minute_and_mismatched_entry_never_fabricate_pnl(self):
        raw,f,btc,cfg=event();p=dict(cfg,hold=48,exit_type='TP2',policy='P');raw[2][323]=107.
        mt=raw[0][323]+np.arange(15,dtype=np.int64)*60000
        for data,status in [(('data_gap','missing'),'DATA_GAP'),((mt,np.full(15,102.5),np.full(15,107.),np.full(15,102.2)),'ENTRY_MISMATCH')]:
            with patch.object(s.chronology.chronology,'w1m',return_value=data):
                rows,_,bad=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+1000*s.BAR)
            self.assertFalse(rows);self.assertEqual(bad[0]['status'],status)

class SelectionSourceTests(unittest.TestCase):
    def make_parts(self,root,negative=None,cluster=False,empty=False,alter=None):
        policy=s.policies()[0];rows=[]
        if not empty:
            for year,total in ((2021,80),(2022,120),(2023,120)):
                month=10 if year==2021 else 6;start=int(pd.Timestamp(f'{year}-{month:02d}-01',tz='Asia/Seoul').timestamp()*1000)
                for i in range(total):
                    row={k:0 for k in s.COLUMNS};row.update(symbol=f'X{i%10}',variant=policy['policy'],policy=policy['policy'],
                        entry_time=start+i*(s.BAR if cluster else s.DAY),net40_fraction=.02,net40_R=.5,split='DEV')
                    if negative:row[negative]=-.01
                    rows.append(row)
        hashes={f'X{i}':f'{i:064x}' for i in range(16)};context=dict(baseline_sha256='a'*64,btc_sha256='b'*64,expected_market_sha256=hashes)
        cp=root/'context.json';cp.write_text(json.dumps(context))
        for i in range(8):
            d=root/'parts'/str(i);d.mkdir(parents=True);ledger=d/'independent_candidates.csv.gz'
            pd.DataFrame(rows if i==0 else [],columns=s.COLUMNS).to_csv(ledger,index=False,compression='gzip')
            m=dict(complete=True,stage='DEV',shard=i,ledger_rows=len(rows) if i==0 else 0,ledger_sha256=s.digest(ledger),
                source_context_sha256=s.digest(cp),btc_sha256=context['btc_sha256'],baseline_sha256=context['baseline_sha256'],
                policies=s.policies(),market_hashes={f'X{k}':hashes[f'X{k}'] for k in (i,i+8)},counts={},coverage=[])
            if alter and i==0:alter(m)
            (d/'scan_meta.json').write_text(json.dumps(m))
        return cp

    def test_all96_including_zero_and_frozen_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cp=self.make_parts(root);s.select(root/'parts',root/'out',cp)
            out=json.loads((root/'out/selection.json').read_text());cells=pd.read_csv(root/'out/development_policy_cells.csv')
            self.assertEqual(len(cells),96);self.assertEqual((cells.n==0).sum(),95);self.assertEqual(len(out['policies']),1)
            self.assertEqual(out['union_name'],'BREAKOUT_LEVEL_RETEST_UNION')

    def test_negative_or_clustered_active_dates_fail(self):
        for negative,cluster in [('net40_fraction',False),('net40_R',False),(None,True)]:
            with self.subTest(negative=negative,cluster=cluster),tempfile.TemporaryDirectory() as td:
                root=Path(td);cp=self.make_parts(root,negative,cluster);s.select(root/'parts',root/'out',cp)
                self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_empty_preserves_all96_zero_cells(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cp=self.make_parts(root,empty=True);s.select(root/'parts',root/'out',cp)
            self.assertEqual(len(pd.read_csv(root/'out/development_policy_cells.csv')),96)

    def test_incomplete_duplicate_or_tampered_shards_fail_closed(self):
        for key,value in [('complete',False),('shard',1),('stage','GATE'),('ledger_rows',999),('ledger_sha256','bad'),('source_context_sha256','bad'),('policies',[])]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as td:
                root=Path(td);cp=self.make_parts(root,alter=lambda m:m.update({key:value}))
                with self.assertRaises(ValueError):s.select(root/'parts',root/'out',cp)

    def test_source_verifier_rejects_market_btc_and_catalogue_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'XUSDT.csv.gz';p.write_bytes(b'source');btc=root/'BTC.csv.gz';btc.write_bytes(b'btc')
            cp=root/'context.json';ct=dict(baseline_sha256='a'*64,btc_sha256=s.digest(btc),expected_market_sha256={'XUSDT':s.digest(p)});cp.write_text(json.dumps(ct))
            check=root/'check.json';check.write_text(json.dumps(dict(status='VERIFIED',shards=[0],baseline_sha256='a'*64,files=[dict(symbol='XUSDT',sha256=s.digest(p))])))
            s.verify_source(check,[p],btc,cp);p.write_bytes(b'changed')
            with self.assertRaises(ValueError):s.verify_source(check,[p],btc,cp)
            p.write_bytes(b'source');btc.write_bytes(b'changed')
            with self.assertRaises(ValueError):s.verify_source(check,[p],btc,cp)

    def test_real_frozen_catalogue_has856_and256_hashes(self):
        ctx=json.loads(s.CONTEXT.read_text());p=s.ROOT/'research/breakout-level-retest-v13/FROZEN_INPUT_HASHES.json';old=json.loads(p.read_text())['expected_csv_sha256']
        self.assertEqual(len(ctx['expected_market_sha256']),856);self.assertEqual(len(old),256);self.assertEqual(s.digest(p),ctx['baseline_sha256'])

    def test_synthetic_eight_file_shard_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);s.smoke(p);r=json.loads((p/'smoke.json').read_text())
            self.assertEqual(r['scans'],8);self.assertTrue(r['all96_cells_preserved']);self.assertFalse(r['market_profitability_claim'])

if __name__=='__main__':unittest.main()
