"""Causal flow-cascade entry, exit, selection and frozen-source tests."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import aggressive_flow_cascade_v11 as s

def event(side=1,flow='CURRENT65',n=100,indices=(20,)):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.);c=np.full(n,100.);h=np.full(n,101.);l=np.full(n,99.)
    f=dict(eligible=np.ones(n,bool),volume_multiple=np.full(n,3.5),prior_atr=np.ones(n),
        buy_share=np.full(n,.5),r1=np.zeros(n),clv=np.full(n,.5),
        prior_buy_share3=np.full(n,.6 if side==1 else .4),
        prior_long_count3=np.full(n,3.),prior_short_count3=np.full(n,3.))
    for w in (16,48):
        f[f'prevh{w}']=np.full(n,101.);f[f'prevl{w}']=np.full(n,99.)
    for i in indices:
        c[i]=102. if side==1 else 98.;o[i]=100.;h[i]=102.2 if side==1 else 100.2;l[i]=99.8 if side==1 else 97.8
        f['r1'][i]=side*.04;f['clv'][i]=.9 if side==1 else .1
        f['buy_share'][i]=.70 if side==1 else .30
    raw=[t,o,h,l,c];btc=dict(close=np.full(n,20000.),r1=np.full(n,side*.002))
    cfg=next(x for x in s.configurations() if x['side']==side and x['threshold']==.015 and x['lookback']==16 and x['flow']==flow)
    return raw,f,btc,cfg

def history_fixture(n=3200):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR
    c=100*np.exp(np.arange(n)*1e-6);o=c.copy();h=c*1.001;l=c*.999;q=np.full(n,1e6);buy=q*.6
    return [t,o,h,l,c],q,buy

class FeatureTests(unittest.TestCase):
    def test_grid_is_sixteen_by_two_by_three(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)
        self.assertEqual(set(p['exit_type'] for p in s.policies()),{'TP15','TP25','TRAIL'})
        self.assertEqual(set(p['hold'] for p in s.policies()),{8,24})

    def test_breakout_atr_and_prior_flow_exclude_current(self):
        raw,q,buy=history_fixture();old=s.features(raw,q,buy);i=3100
        nr=[x.copy() for x in raw];nq=q.copy();nb=buy.copy()
        nr[2][i]*=2;nr[3][i]*=.5;nq[i]*=10;nb[i]=nq[i]
        new=s.features(nr,nq,nb)
        for key in ('prior_atr','prevh16','prevl16','prevh48','prevl48','prior_buy_share3','prior_long_count3'):
            self.assertEqual(old[key][i],new[key][i],key)
        self.assertAlmostEqual(new['volume_multiple'][i],10*old['volume_multiple'][i])

    def test_future_mutation_cannot_change_earlier_features(self):
        raw,q,buy=history_fixture();old=s.features(raw,q,buy);nr=[x.copy() for x in raw]
        for x in nr[1:]:x[3150:]*=3
        nq=q.copy();nq[3150:]*=5;nb=buy.copy();nb[3150:]=0
        new=s.features(nr,nq,nb)
        for key in old:np.testing.assert_allclose(old[key][:3150],new[key][:3150],equal_nan=True)

    def test_coin_gap_restarts_every_prior_window(self):
        raw,q,buy=history_fixture();raw[0][3100:]+=s.BAR;f=s.features(raw,q,buy)
        self.assertTrue(np.isnan(f['prevh48'][3100:3148]).all())
        self.assertTrue(np.isnan(f['prior_buy_share3'][3100:3103]).all())
        self.assertTrue(np.isfinite(f['prevh48'][3148]))

    def test_exact_btc_alignment_never_interpolates(self):
        t=np.array([0,s.BAR,2*s.BAR]);bt=np.array([0,2*s.BAR]);c=np.array([100.,102.]);br=[bt,c,c,c,c]
        z=s.align_context(t,br);self.assertTrue(np.isnan(z['close'][1]));self.assertTrue(np.isnan(z['r1'][2]))
        bt=np.array([0,s.BAR,2*s.BAR]);c=np.array([100.,101.,102.]);br=[bt,c,c,c,c]
        z=s.align_context(t,br);self.assertAlmostEqual(z['r1'][2],102/101-1)

class SignalExitTests(unittest.TestCase):
    def test_mirrored_current_and_persistent_flow_signals(self):
        for side in (1,-1):
            for flow in ('CURRENT65','PERSIST55'):
                raw,f,btc,cfg=event(side,flow);self.assertEqual(np.flatnonzero(s.mask(cfg,raw,f,btc)).tolist(),[20])

    def test_shock_relative_btc_volume_breakout_and_clv_are_hard(self):
        mutations=[('eligible',False),('volume_multiple',2.99),('r1',.0149),('clv',.79)]
        for key,value in mutations:
            raw,f,btc,cfg=event();f[key][20]=value
            self.assertFalse(s.mask(cfg,raw,f,btc).any(),key)
        raw,f,btc,cfg=event();btc['r1'][20]=.016;self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event();btc['r1'][20]=.031;self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event();f['prevh16'][20]=102.1;self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event();f['r1'][20]=.081;self.assertFalse(s.mask(cfg,raw,f,btc).any())

    def test_persistent_flow_uses_three_prior_bars_and_current_separately(self):
        raw,f,btc,cfg=event(1,'PERSIST55')
        for key,value in [('buy_share',.599),('prior_buy_share3',.549),('prior_long_count3',1.)]:
            g={k:v.copy() for k,v in f.items()};g[key][20]=value;self.assertFalse(s.mask(cfg,raw,g,btc).any(),key)
        cfg=dict(cfg,flow='CURRENT65');g={k:v.copy() for k,v in f.items()};g['prior_buy_share3'][20]=0;g['prior_long_count3'][20]=0
        self.assertTrue(s.mask(cfg,raw,g,btc)[20])

    def test_next_open_gap_actual_fill_stop_and_future_ohlc(self):
        raw,f,btc,cfg=event();raw[1][21]=raw[4][20]*1.006
        rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['ENTRY_CATCHUP_GAP'],1)
        raw,f,btc,cfg=event();raw[1][21]=101.5;raw[2][21]=1e9;raw[3][21]=.0001;raw[4][21]=1e8
        rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR);tr=rows[0]
        self.assertEqual(tr['entry'],101.5);self.assertAlmostEqual(tr['sl'],100.0);self.assertAlmostEqual(tr['risk_pct'],1.5/101.5)
        self.assertEqual(tr['baseline_last_bar_open_time'],raw[0][19]);self.assertLess(tr['baseline_last_bar_open_time'],tr['decision_time'])

    def test_stop_floor_wide_invalid_and_path_gap_counted(self):
        raw,f,btc,cfg=event();f['prior_atr'][:]=.001
        rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR);self.assertAlmostEqual(rows[0]['risk_pct'],.0075)
        for value,reason in [(5.,'STOP_ABOVE_6PCT'),(np.nan,'INVALID_ATR')]:
            raw,f,btc,cfg=event();f['prior_atr'][:]=value;rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertFalse(rows);self.assertEqual(bad[reason],1)
        raw,f,btc,cfg=event();raw[0][21:]+=s.BAR;rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+101*s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['ENTRY_PATH_GAP'],1)

    def test_intent_cooldown_is_not_exit_conditioned(self):
        raw,f,btc,cfg=event(indices=(20,30,37));rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertEqual([x['signal_time'] for x in rows],[raw[0][20],raw[0][37]])

    def test_tp15_tp25_and_trail_use_shared_resolver(self):
        raw,f,btc,cfg=event();result=dict(status='RESOLVED',exit_time=raw[0][23],exit=105.,reason='TP',gross_return=.05)
        for kind,target in [('TP15',102.25),('TP25',103.75),('TRAIL',103.75)]:
            p=dict(cfg,hold=8,exit_type=kind,policy='P')
            with patch.object(s.canonical,'resolve',return_value=result) as resolver:
                rows,_,_=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertAlmostEqual(resolver.call_args.args[0]['tp'],target)
            self.assertEqual(resolver.call_args.args[3],'TRAIL' if kind=='TRAIL' else 'TP2')
            self.assertEqual(len(rows),1)

    def test_authoritative_exclusion_is_not_invented_loss(self):
        raw,f,btc,cfg=event();p=dict(cfg,hold=8,exit_type='TP15',policy='P')
        with patch.object(s.canonical,'resolve',return_value=dict(status='DATA_GAP')):
            rows,counts,bad=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertFalse(rows);self.assertEqual(counts['P/DATA_GAP'],1);self.assertEqual(bad[0]['status'],'DATA_GAP')

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
            self.assertEqual(out['union_name'],'AGGRESSIVE_FLOW_CASCADE_UNION')

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
        ctx=json.loads(s.CONTEXT.read_text());p=s.ROOT/'research/aggressive-flow-cascade-v11/FROZEN_INPUT_HASHES.json';old=json.loads(p.read_text())['expected_csv_sha256']
        self.assertEqual(len(ctx['expected_market_sha256']),856);self.assertEqual(len(old),256);self.assertEqual(s.digest(p),ctx['baseline_sha256'])

    def test_synthetic_eight_file_shard_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);s.smoke(p);r=json.loads((p/'smoke.json').read_text())
            self.assertEqual(r['scans'],8);self.assertTrue(r['all96_cells_preserved']);self.assertFalse(r['market_profitability_claim'])

if __name__=='__main__':unittest.main()
