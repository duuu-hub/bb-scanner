"""Past-only setup, mirrored release, canonical exits and failed-byte preservation."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import taker_absorption_release_v12 as s

def event(side=1,flow='PRICE_ONLY',n=100,indices=(20,)):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.);c=np.full(n,100.);h=np.full(n,101.);l=np.full(n,99.)
    f=dict(eligible=np.ones(n,bool),volume_multiple=np.full(n,2.),prior_atr=np.ones(n),
           buy_share=np.full(n,.5),r1=np.zeros(n),clv=np.full(n,.5))
    for w in (4,8):
        f[f'setup_high{w}']=np.full(n,101.);f[f'setup_low{w}']=np.full(n,99.)
        f[f'setup_buy_share{w}']=np.full(n,.3 if side==1 else .7)
        f[f'setup_volume_multiple{w}']=np.full(n,2.)
        f[f'setup_quote_sum{w}']=np.full(n,2e6*w)
        f[f'setup_taker_buy_quote_sum{w}']=f[f'setup_quote_sum{w}']*f[f'setup_buy_share{w}']
        f[f'setup_baseline_quote_mean{w}']=np.full(n,1e6)
        f[f'setup_atr{w}']=np.ones(n);f[f'setup_price_change{w}']=np.zeros(n)
        f[f'setup_buy_count{w}']=np.full(n,w if side==-1 else 0.)
        f[f'setup_sell_count{w}']=np.full(n,w if side==1 else 0.)
    for i in indices:
        c[i]=102. if side==1 else 98.;h[i]=max(c[i],o[i])+.2;l[i]=min(c[i],o[i])-.2
        f['r1'][i]=side*.02;f['clv'][i]=.9 if side==1 else .1
        f['buy_share'][i]=.65 if side==1 else .35
    raw=[t,o,h,l,c];btc=dict(close=np.full(n,20000.),r1=np.full(n,.001))
    cfg=next(x for x in s.configurations() if x['side']==side and x['window']==4 and x['share']==.6 and x['flow']==flow)
    return raw,f,btc,cfg

def history_fixture(n=3400):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR
    c=100*np.exp(np.arange(n)*1e-6);o=c.copy();h=c*1.001;l=c*.999;q=np.full(n,1e6);buy=q*.6
    return [t,o,h,l,c],q,buy

class FeatureTests(unittest.TestCase):
    def test_grid_sixteen_entries_ninety_six_policies(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)
        self.assertEqual(set(p['exit_type'] for p in s.policies()),{'TP2','TP3','TRAIL'})
        self.assertEqual(set(p['hold'] for p in s.policies()),{16,48})

    def test_independent_setup_window_volume_weighting_and_cutoffs(self):
        raw,q,buy=history_fixture();i=3150;w=4
        q[i-w:i]=[1e6,2e6,3e6,4e6];buy[i-w:i]=q[i-w:i]*[.1,.2,.3,.8]
        q[i]=100e6;buy[i]=100e6;f=s.features(raw,q,buy)
        expected=np.dot([1,2,3,4],[.1,.2,.3,.8])/10
        self.assertAlmostEqual(f['setup_buy_share4'][i],expected)
        self.assertNotAlmostEqual(expected,np.mean([.1,.2,.3,.8]))
        self.assertEqual(f['setup_quote_sum4'][i],10e6)
        self.assertEqual(f['setup_baseline_quote_mean4'][i],np.mean(q[i-w-96:i-w]))
        self.assertEqual(f['setup_volume_multiple4'][i],2.5)
        self.assertEqual(f['setup_high4'][i],max(raw[2][i-w:i]))
        self.assertEqual(f['setup_low4'][i],min(raw[3][i-w:i]))
        self.assertEqual(f['setup_atr4'][i],f['prior_atr'][i-w])
        self.assertAlmostEqual(f['setup_price_change4'][i],(raw[4][i-1]-raw[4][i-w-1])/f['prior_atr'][i-w])
        self.assertEqual(f['setup_sell_count4'][i],3);self.assertEqual(f['setup_buy_count4'][i],1)

    def test_release_bar_cannot_change_its_setup_or_prior_atr(self):
        raw,q,buy=history_fixture();old=s.features(raw,q,buy);i=3150
        nr=[x.copy() for x in raw];nq=q.copy();nb=buy.copy()
        nr[1][i]*=2;nr[2][i]*=3;nr[3][i]*=.5;nr[4][i]*=2;nq[i]*=9;nb[i]=nq[i]
        new=s.features(nr,nq,nb)
        for key in old:
            if key.startswith('setup_') or key=='prior_atr':self.assertEqual(old[key][i],new[key][i],key)

    def test_future_mutation_does_not_change_features_or_existing_intents(self):
        raw,q,buy=history_fixture();old=s.features(raw,q,buy);nr=[x.copy() for x in raw]
        for x in nr[1:]:x[3250:]*=3
        nq=q.copy();nq[3250:]*=5;nb=buy.copy();nb[3250:]=0;new=s.features(nr,nq,nb)
        for key in old:np.testing.assert_allclose(old[key][:3250],new[key][:3250],equal_nan=True)
        raw,f,btc,cfg=event();before=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)[0]
        for x in raw[1:]:x[22:]*=3
        after=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)[0]
        self.assertEqual(before,after)

    def test_gap_restarts_setup_and_pre_setup_volume(self):
        raw,q,buy=history_fixture();raw[0][3100:]+=s.BAR;f=s.features(raw,q,buy)
        self.assertTrue(np.isnan(f['setup_high8'][3100:3108]).all())
        self.assertTrue(np.isnan(f['setup_volume_multiple8'][3100:3204]).all())
        self.assertTrue(np.isfinite(f['setup_volume_multiple8'][3204]))

    def test_exact_btc_alignment_missing_prior_never_filled(self):
        t=np.array([0,s.BAR,2*s.BAR]);bt=np.array([0,2*s.BAR]);c=np.array([100.,102.]);br=[bt,c,c,c,c]
        z=s.align_context(t,br);self.assertTrue(np.isnan(z['close'][1]));self.assertTrue(np.isnan(z['r1'][2]))
        bt=np.array([0,s.BAR,2*s.BAR]);c=np.array([100.,101.,102.]);br=[bt,c,c,c,c]
        self.assertAlmostEqual(s.align_context(t,br)['r1'][2],102/101-1)

class SignalExitTests(unittest.TestCase):
    def test_both_sides_and_confirmation_modes_release_against_setup_flow(self):
        for side in (1,-1):
            for flow in ('PRICE_ONLY','FLOW55'):
                raw,f,btc,cfg=event(side,flow);self.assertEqual(np.flatnonzero(s.mask(cfg,raw,f,btc)).tolist(),[20])
                f['setup_buy_share4'][20]=.7 if side==1 else .3
                self.assertFalse(s.mask(cfg,raw,f,btc).any())

    def test_setup_volume_flatness_persistence_and_release_are_required(self):
        for key,value in [('eligible',False),('volume_multiple',1.249),('setup_volume_multiple4',1.499),
                          ('setup_price_change4',.501),('setup_sell_count4',2),('setup_atr4',np.nan),
                          ('clv',.749),('r1',.081),('setup_buy_share4',.401),('setup_high4',102.)]:
            raw,f,btc,cfg=event();f[key][20]=value;self.assertFalse(s.mask(cfg,raw,f,btc).any(),key)
        raw,f,btc,cfg=event();btc['r1'][20]=.016;self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event();btc['r1'][20]=np.nan;self.assertFalse(s.mask(cfg,raw,f,btc).any())

    def test_release_current_flow_is_separate_and_optional(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side,'FLOW55');f['buy_share'][20]=.549 if side==1 else .451
            self.assertFalse(s.mask(cfg,raw,f,btc).any())
            cfg=dict(cfg,flow='PRICE_ONLY');self.assertTrue(s.mask(cfg,raw,f,btc)[20])

    def test_next_open_structural_stop_actual_risk_and_priority(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side);raw[2][21]=1e9;raw[3][21]=.0001;raw[4][21]=1e8
            rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR);tr=rows[0]
            self.assertEqual(tr['entry'],100.);self.assertEqual(tr['sl'],98.75 if side==1 else 101.25)
            self.assertAlmostEqual(tr['risk_pct'],.0125)
            self.assertAlmostEqual(tr['score'],.4*np.sqrt(2)/.0125)
            self.assertEqual(tr['setup_first_bar_open_time'],raw[0][16])
            self.assertEqual(tr['setup_baseline_last_bar_open_time'],raw[0][15])
            self.assertEqual(tr['setup_last_bar_open_time'],raw[0][19])
            self.assertEqual(tr['entry_time'],raw[0][21]);self.assertEqual(tr['decision_time'],raw[0][21])

    def test_favorable_gap_wrong_side_wide_stop_and_path_exclusions(self):
        raw,f,btc,cfg=event();raw[1][21]=102*1.006
        rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['ENTRY_CATCHUP_GAP'],1)
        for edge,reason in [(100.,'SETUP_EDGE_WRONG_SIDE'),(90.,'STOP_ABOVE_6PCT')]:
            raw,f,btc,cfg=event();f['setup_low4'][20]=edge
            rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertFalse(rows);self.assertEqual(bad[reason],1)
        raw,f,btc,cfg=event();raw[0][21:]+=s.BAR
        rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+101*s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['ENTRY_PATH_GAP'],1)

    def test_stop_floor_and_cooldown_independent_of_exit(self):
        raw,f,btc,cfg=event(indices=(20,30,37));f['setup_low4'][:]=99.9;f['prior_atr'][:]=.01
        rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertEqual([x['signal_time'] for x in rows],[raw[0][20],raw[0][37]])
        self.assertAlmostEqual(rows[0]['risk_pct'],.005)

    def test_tp_two_three_trail_shared_resolver_and_independent_net(self):
        raw,f,btc,cfg=event();result=dict(status='RESOLVED',exit_time=raw[0][23],exit=103.,reason='TP',gross_return=.03)
        for kind,target in [('TP2',102.5),('TP3',103.75),('TRAIL',103.75)]:
            p=dict(cfg,hold=16,exit_type=kind,policy='P')
            with patch.object(s.canonical,'resolve',return_value=result) as resolver:
                rows,_,_=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertEqual(resolver.call_args.args[0]['tp'],target)
            self.assertEqual(resolver.call_args.args[3],'TRAIL' if kind=='TRAIL' else 'TP2')
            expected=.03-.002*(1+1.03)-.0002*30/1440
            self.assertAlmostEqual(rows[0]['net40_fraction'],expected)

    def test_authoritative_exclusions_remain_excluded(self):
        for status in ['DATA_GAP','ENTRY_MISMATCH','EXIT_MISMATCH']:
            raw,f,btc,cfg=event();p=dict(cfg,hold=16,exit_type='TP2',policy='P')
            with patch.object(s.canonical,'resolve',return_value=dict(status=status)):
                rows,counts,bad=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertFalse(rows);self.assertEqual(counts['P/'+status],1);self.assertEqual(bad[0]['status'],status)

    def test_actual_one_minute_entry_tp_and_established_collision_order(self):
        for kind,multiple in [('TP2',2),('TP3',3)]:
            for case,expected in [('entry_tp','SL'),('later_tp_first','TP'),('same_minute_both','SL')]:
                raw,f,btc,cfg=event();p=dict(cfg,hold=16,exit_type=kind,policy='P')
                entry_bar=21 if case=='entry_tp' else 22;tp=100+multiple*1.25
                raw[2][entry_bar]=tp+.2;raw[3][entry_bar]=99 if case=='entry_tp' else 98
                mt=raw[0][entry_bar]+np.arange(15,dtype=np.int64)*60000
                mo=np.full(15,100.);mh=np.full(15,101.);ml=np.full(15,99.)
                if case=='entry_tp':mh[0]=tp+.2
                elif case=='later_tp_first':mh[2]=tp+.2;ml[3]=98.
                else:mh[2]=tp+.2;ml[2]=98.
                with patch.object(s.chronology.chronology,'w1m',return_value=(mt,mo,mh,ml)) as minutes:
                    rows,_,_=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
                minutes.assert_called_once();self.assertEqual(rows[0]['reason'],expected)
                self.assertEqual(rows[0]['exit'],98.75 if expected=='SL' else tp)

    def test_actual_minute_entry_mismatch_and_gap_are_excluded(self):
        for data,status in [(('data_gap','missing'),'DATA_GAP'),('mismatch','ENTRY_MISMATCH')]:
            raw,f,btc,cfg=event();p=dict(cfg,hold=16,exit_type='TP2',policy='P');raw[2][21]=103.
            if data=='mismatch':
                mt=raw[0][21]+np.arange(15,dtype=np.int64)*60000
                data=(mt,np.full(15,100.1),np.full(15,103.),np.full(15,99.))
            with patch.object(s.chronology.chronology,'w1m',return_value=data):
                rows,_,bad=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertFalse(rows);self.assertEqual(bad[0]['status'],status)

class FailedOriginalTests(unittest.TestCase):
    def meta(self,raw,check):
        return dict(symbol='XUSDT',month='2022-06',status='DATA_GAP',original_zip_sha256=hashlib.sha256(raw).hexdigest(),
                    official_checksum_text_sha256=hashlib.sha256(check).hexdigest(),source_url='source',checksum_url='check')

    def test_malformed_bytes_retained_without_inventing_validity(self):
        raw,check=b'invalid zip bytes',b'published checksum';meta=self.meta(raw,check)
        with tempfile.TemporaryDirectory() as td,patch.object(s.official,'_get',side_effect=[raw,check]):
            p=Path(td);s.retain_failed_minute_original(meta,p/'cache',p/'out')
            self.assertEqual((p/'out'/meta['failed_raw_evidence_file']).read_bytes(),raw)
            self.assertEqual((p/'out'/meta['failed_checksum_evidence_file']).read_bytes(),check)
            self.assertTrue(meta['failed_raw_preserved']);self.assertEqual(meta['status'],'DATA_GAP')

    def test_tamper_failed_zip_and_checksum_rejected_with_bytes_saved(self):
        for downloads in [(b'tampered',b'check'),(b'raw',b'tampered')]:
            meta=self.meta(b'raw',b'check')
            with tempfile.TemporaryDirectory() as td,patch.object(s.official,'_get',side_effect=downloads):
                p=Path(td)
                with self.assertRaises(ValueError):s.retain_failed_minute_original(meta,p/'cache',p/'out')
                self.assertFalse(meta.get('failed_raw_preserved',False));self.assertTrue(list((p/'out').iterdir()))

    def test_canonical_gap_return_not_changed_by_retention(self):
        meta=self.meta(b'raw',b'check');key='XUSDT/2022-06'
        ts=int(pd.Timestamp('2022-06-01',tz='UTC').timestamp()*1000)
        with patch.dict(s.official.INPUTS,{key:meta},clear=True),patch.object(s.minute_audit,'audited_minutes',return_value=('data_gap','bad geometry')),patch.object(s,'retain_failed_minute_original') as preserve:
            self.assertEqual(s.audited_minutes('XUSDT',ts),('data_gap','bad geometry'));preserve.assert_called_once()

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
            self.assertEqual(out['union_name'],'TAKER_ABSORPTION_RELEASE_UNION')

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
        ctx=json.loads(s.CONTEXT.read_text());p=s.ROOT/'research/taker-absorption-release-v12/FROZEN_INPUT_HASHES.json';old=json.loads(p.read_text())['expected_csv_sha256']
        self.assertEqual(len(ctx['expected_market_sha256']),856);self.assertEqual(len(old),256);self.assertEqual(s.digest(p),ctx['baseline_sha256'])

    def test_synthetic_eight_file_shard_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);s.smoke(p);r=json.loads((p/'smoke.json').read_text())
            self.assertEqual(r['scans'],8);self.assertTrue(r['all96_cells_preserved']);self.assertFalse(r['market_profitability_claim'])

if __name__=='__main__':unittest.main()
