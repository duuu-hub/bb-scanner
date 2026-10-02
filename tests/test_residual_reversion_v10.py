"""Independent prior-window OLS/AR1 references, causal exits and source integrity."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import residual_reversion_v10 as s

def context(n=3600):
    rng=np.random.default_rng(61002);x=np.cumsum(rng.normal(0,.002,n));e=np.zeros(n)
    for i in range(1,n):e[i]=.94*e[i-1]+rng.normal(0,.002)
    c=np.exp(1+1.4*x+e);bc=np.exp(10+x);t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR;q=np.full(n,1e6)
    raw=[t,c.copy(),c*1.003,c*.997,c.copy()];br=[t,bc.copy(),bc*1.003,bc*.997,bc.copy()]
    f=s.features(raw,q,q*.6);btc=s.align_context(t,br,s.features(br,q,q*.5))
    return raw,f,btc

def event(side=1,flow='PRICE_ONLY',n=100):
    t=s.base.START+np.arange(n,dtype=np.int64)*s.BAR;c=np.full(n,100.);o=c.copy();c[20]+=side
    raw=[t,o,np.maximum(c,o)+1,np.minimum(c,o)-1,c]
    f=dict(eligible=np.ones(n,bool),volume_multiple=np.full(n,2.),prior_atr=np.ones(n),atr=np.full(n,.01),
           buy_share=np.full(n,.6 if side==1 else .4),r1=np.full(n,side*.002))
    for w in s.FITS:
        for key,value in [('beta',1.5),('intercept',0.),('r2',.8),('sigma',.01),('rho',.94),
                          ('half_life',12.),('residual',-side*.04),('z',-side*4),('inward',side*.001),('equilibrium',100+side*5)]:
            f[key+str(w)]=np.full(n,value)
    btc=dict(r16=np.zeros(n),close=np.full(n,20000.))
    cfg=next(p for p in s.configurations() if p['side']==side and p['fit']==672 and p['z']==2 and p['flow']==flow)
    return raw,f,btc,cfg

class PriorModelTests(unittest.TestCase):
    def test_beta_intercept_sigma_r2_rho_half_life_against_direct_window(self):
        raw,f,btc=context();out=s.residual_features(raw,f,btc);i=2500
        for w in s.FITS:
            x=np.log(btc['close'][i-w:i]);y=np.log(raw[4][i-w:i]);u=x-x.mean();v=y-y.mean()
            beta=np.mean(u*v)/np.mean(u*u);alpha=y.mean()-beta*x.mean();e=y-alpha-beta*x
            a,b=e[:-1]-e[:-1].mean(),e[1:]-e[1:].mean();rho=np.mean(a*b)/np.mean(a*a)
            refs=dict(beta=beta,intercept=alpha,sigma=np.std(e),r2=np.corrcoef(x,y)[0,1]**2,
                      rho=rho,half_life=-np.log(2)/np.log(rho))
            for key,val in refs.items():self.assertAlmostEqual(out[key+str(w)][i],val,places=8,msg=key)

    def test_current_coin_and_btc_do_not_enter_training_moments(self):
        raw,f,btc=context();old=s.residual_features(raw,f,btc);i=2500
        nr=[a.copy() for a in raw];nb={k:a.copy() for k,a in btc.items()};nr[4][i]*=1.3;nb['close'][i]*=.8
        new=s.residual_features(nr,f,nb)
        for w in s.FITS:
            for name in ('beta','intercept','sigma','r2','rho','half_life'):
                self.assertEqual(old[name+str(w)][i],new[name+str(w)][i])
            self.assertNotEqual(old['z'+str(w)][i],new['z'+str(w)][i])

    def test_future_mutation_cannot_change_earlier_any_model_value(self):
        raw,f,btc=context();old=s.residual_features(raw,f,btc);nr=[a.copy() for a in raw]
        for a in nr[1:]:a[2700:]*=3
        nb={k:a.copy() for k,a in btc.items()};nb['close'][2700:]*=.2;new=s.residual_features(nr,f,nb)
        for key in old:np.testing.assert_allclose(old[key][:2700],new[key][:2700],equal_nan=True)

    def test_inward_change_uses_same_frozen_model_for_both_closes(self):
        raw,f,btc=context();out=s.residual_features(raw,f,btc);i=2500
        for w in s.FITS:
            beta=out['beta'+str(w)][i];expect=np.log(raw[4][i]/raw[4][i-1])-beta*np.log(btc['close'][i]/btc['close'][i-1])
            self.assertAlmostEqual(out['inward'+str(w)][i],expect,places=12)
            target=np.exp(out['intercept'+str(w)][i]+beta*np.log(btc['close'][i]))
            self.assertAlmostEqual(out['equilibrium'+str(w)][i],target,places=12)

    def test_missing_btc_invalidates_every_contaminated_training_window(self):
        raw,f,btc=context();k=1400;btc['close'][k]=np.nan;out=s.residual_features(raw,f,btc)
        for w in s.FITS:
            self.assertTrue(np.isnan(out['beta'+str(w)][k+1:k+w+1]).all())
            self.assertTrue(np.isfinite(out['beta'+str(w)][k+w+1]))
            self.assertTrue(np.isnan(out['z'+str(w)][k]))

    def test_coin_gap_restarts_the_full_prior_window(self):
        raw,f,btc=context();raw[0][1400:]+=s.BAR;out=s.residual_features(raw,f,btc)
        for w in s.FITS:
            self.assertTrue(np.isnan(out['beta'+str(w)][1400:1400+w]).all())
            self.assertTrue(np.isfinite(out['beta'+str(w)][1400+w]))

    def test_zero_factor_and_residual_variance_fail_closed(self):
        raw,f,btc=context();btc['close'][:]=20000;out=s.residual_features(raw,f,btc)
        self.assertTrue(np.isnan(out['beta672']).all())
        raw,f,btc=context();raw[4][:]=np.exp(1+1.4*np.log(btc['close']));out=s.residual_features(raw,f,btc)
        self.assertTrue(np.isnan(out['sigma672']).all())

    def test_exact_alignment_never_interpolates_missing_or_future_btc(self):
        t=np.array([0,s.BAR,2*s.BAR]);bt=np.array([0,2*s.BAR]);c=np.array([1.,2.]);br=[bt,c,c,c,c]
        bf={f'r{h}':np.zeros(2) for h in (1,4,16,96)};out=s.align_context(t,br,bf)
        self.assertTrue(np.isnan(out['close'][1]));np.testing.assert_equal(out['close'][[0,2]],[1.,2.])

    def test_current_turnover_atr_and_observed_days_are_causal(self):
        raw,f,btc=context();q=np.ones(len(raw[0]));old=s.features(raw,q,q*.6);nr=[a.copy() for a in raw]
        nr[2][2500]*=3;nr[3][2500]*=.1;q[2500]=100;new=s.features(nr,q,q*.6)
        self.assertEqual(old['prior_atr'][2500],new['prior_atr'][2500]);self.assertAlmostEqual(new['volume_multiple'][2500],100)

class EntryExitTests(unittest.TestCase):
    def test_full_grid_is_sixteen_by_two_by_three(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96);self.assertEqual(set(p['exit_type'] for p in s.policies()),{'MEAN','TP2','TRAIL'})

    def test_mirrored_residual_inward_confirmations(self):
        for side in (1,-1):
            for flow in ('PRICE_ONLY','FLOW55'):
                raw,f,btc,cfg=event(side,flow);self.assertEqual(np.flatnonzero(s.mask(cfg,raw,f,btc)).tolist(),[20])

    def test_quality_room_deviation_and_relaxation_are_hard_conditions(self):
        for key,value in [('eligible',False),('volume_multiple',.99),('beta672',.49),('beta672',3.01),
                          ('r2672',.49),('sigma672',np.nan),('half_life672',.99),('half_life672',96.01),
                          ('half_life672',np.nan),('z672',-1.99),('residual672',-.0149),('inward672',0.),('r1',.031)]:
            raw,f,btc,cfg=event();f[key][:]=value
            self.assertFalse(s.mask(cfg,raw,f,btc).any(),key)

    def test_common_shock_or_missing_btc_is_rejected(self):
        for v in (.031,-.031,np.nan):
            raw,f,btc,cfg=event();btc['r16'][:]=v;self.assertFalse(s.mask(cfg,raw,f,btc).any())

    def test_flow_filter_and_price_only(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side,'FLOW55');f['buy_share'][:]=.4 if side==1 else .6
            self.assertFalse(s.mask(cfg,raw,f,btc).any());cfg=dict(cfg,flow='PRICE_ONLY');self.assertTrue(s.mask(cfg,raw,f,btc)[20])

    def test_actual_fill_gap_ignores_future_entry_bar_ohlc(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side);raw[1][21]=raw[4][20]*(1+side*.006)
            rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertFalse(rows);self.assertEqual(bad['ENTRY_CATCHUP_GAP'],1)
            raw[1][21]=raw[4][20]*(1-side*.006);raw[2][21]=1e8;raw[3][21]=.00001;raw[4][21]=1e6
            rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR);self.assertEqual(len(rows),1)

    def test_actual_fill_risk_fit_cutoff_and_equilibrium_are_frozen(self):
        raw,f,btc,cfg=event();raw[1][21]=100.8
        rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR);tr=rows[0]
        self.assertEqual(tr['sl'],98.8);self.assertAlmostEqual(tr['risk_pct'],2/100.8)
        self.assertEqual(tr['equilibrium_price'],105);self.assertEqual(tr['fit_cutoff_time'],raw[0][20])
        self.assertLess(tr['fit_cutoff_time'],tr['decision_time']);self.assertEqual(tr['fit_last_bar_open_time'],raw[0][19])

    def test_stop_floor_maximum_invalid_atr_and_target_passed_are_counted(self):
        raw,f,btc,cfg=event();f['prior_atr'][:]=.001
        rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR);self.assertAlmostEqual(rows[0]['risk_pct'],.0075)
        for key,val,reason in [('prior_atr',5.,'STOP_ABOVE_8PCT'),('prior_atr',np.nan,'INVALID_ATR'),('equilibrium672',99.,'EQUILIBRIUM_TARGET_PASSED')]:
            raw,f,btc,cfg=event();f[key][:]=val
            rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertFalse(rows);self.assertEqual(bad[reason],1)

    def test_next_open_path_and_split_bounds(self):
        raw,f,btc,cfg=event();raw[0][21:]+=s.BAR
        rows,bad=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+101*s.BAR);self.assertFalse(rows);self.assertEqual(bad['ENTRY_PATH_GAP'],1)
        raw,f,btc,cfg=event();rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+21*s.BAR);self.assertFalse(rows)

    def test_cooldown_depends_on_intents_not_future_exits(self):
        raw,f,btc,cfg=event()
        for i in (30,50,52):raw[4][i]=101
        rows,_=s.intents('X',cfg,raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertEqual([r['signal_time'] for r in rows],[raw[0][20],raw[0][52]])

    def test_mean_and_tp2_use_explicit_shared_fixed_target_path(self):
        raw,f,btc,cfg=event();result=dict(status='RESOLVED',exit_time=raw[0][23],exit=105.,reason='TP',gross_return=.05)
        for kind,target in [('MEAN',105.),('TP2',104.),('TRAIL',104.)]:
            p=dict(cfg,hold=24,exit_type=kind,policy='P')
            with patch.object(s.canonical,'resolve',return_value=result) as resolver:
                rows,_,_=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
            self.assertEqual(resolver.call_args.args[0]['tp'],target)
            self.assertEqual(resolver.call_args.args[3],'TRAIL' if kind=='TRAIL' else 'TP2')
            self.assertAlmostEqual(rows[0]['net40_fraction'],.05-.002*2.05-.0002*2/96)

    def test_authoritative_exclusion_is_not_a_guessed_loss(self):
        raw,f,btc,cfg=event();p=dict(cfg,hold=24,exit_type='MEAN',policy='P')
        with patch.object(s.canonical,'resolve',return_value=dict(status='DATA_GAP')):
            rows,counts,bad=s.policy_rows('X',[p],raw,f,btc,s.base.START,s.base.START+100*s.BAR)
        self.assertFalse(rows);self.assertEqual(counts['P/DATA_GAP'],1);self.assertEqual(bad[0]['status'],'DATA_GAP')

class SelectionSourceTests(unittest.TestCase):
    def test_actual_synthetic_file_scan_and_selection_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);s.smoke(p);report=json.loads((p/'smoke.json').read_text())
            self.assertEqual(report['scans'],8);self.assertTrue(report['synthetic_only'])
            self.assertFalse(report['market_profitability_claim'])

    def make_parts(self,root,negative=None,cluster=False,empty=False,alter=None):
        policy=s.policies()[0];rows=[]
        if not empty:
            for year,total in ((2021,80),(2022,120),(2023,120)):
                month=10 if year==2021 else 6
                start=int(pd.Timestamp(f'{year}-{month:02d}-01',tz='Asia/Seoul').timestamp()*1000)
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

    def test_freeze_complete_selection_and_all96_including_zero_cells(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cp=self.make_parts(root);s.select(root/'parts',root/'out',cp)
            out=json.loads((root/'out/selection.json').read_text());self.assertEqual(len(out['policies']),1)
            cells=pd.read_csv(root/'out/development_policy_cells.csv');self.assertEqual(len(cells),96)
            self.assertEqual((cells.n==0).sum(),95);self.assertEqual(out['union_name'],'RESIDUAL_REVERSION_UNION')

    def test_nonpositive_price_r_or_clustered_dates_fail_selection(self):
        for negative,cluster in [('net40_fraction',False),('net40_R',False),(None,True)]:
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);cp=self.make_parts(root,negative,cluster);s.select(root/'parts',root/'out',cp)
                self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_empty_stage_is_recorded_as_all96_zero_cells(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cp=self.make_parts(root,empty=True);s.select(root/'parts',root/'out',cp)
            self.assertEqual(len(pd.read_csv(root/'out/development_policy_cells.csv')),96)
            self.assertEqual(json.loads((root/'out/selection.json').read_text())['status'],'NO_DEVELOPMENT_POLICY_SURVIVOR')

    def test_incomplete_duplicate_or_tampered_shard_fails_closed(self):
        for key,value in [('complete',False),('shard',1),('stage','GATE'),('ledger_rows',999),('ledger_sha256','bad'),
                          ('source_context_sha256','bad'),('btc_sha256','bad'),('policies',[])]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as td:
                root=Path(td);cp=self.make_parts(root,alter=lambda m:m.update({key:value}))
                with self.assertRaises(ValueError):s.select(root/'parts',root/'out',cp)

    def test_annual_price_R_and_2021_partial_year_are_required(self):
        r=dict(n=320,symbols=10,top_symbol_share_pct=10,net40_mean_bp=1,net40_R_mean=.1)
        for year,n,days in ((2021,40,20),(2022,80,60),(2023,80,60)):
            for k,v in [('n',n),('days',days),('net40_mean_bp',1),('net40_R',.1),('day_R',.1)]:r[f'year_{year}_{k}']=v
        self.assertFalse(s.development_rejections(r))
        for key in ('year_2021_net40_mean_bp','year_2022_net40_R','year_2023_day_R'):
            bad=dict(r);bad[key]=0;self.assertTrue(s.development_rejections(bad))

    def test_source_verifier_rejects_hash_btc_and_catalogue_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'XUSDT.csv.gz';p.write_bytes(b'source');btc=root/'BTC.csv.gz';btc.write_bytes(b'btc')
            cp=root/'context.json';ct=dict(baseline_sha256='a'*64,btc_sha256=s.digest(btc),expected_market_sha256={'XUSDT':s.digest(p)})
            cp.write_text(json.dumps(ct));check=root/'check.json';check.write_text(json.dumps(dict(status='VERIFIED',shards=[0],baseline_sha256='a'*64,files=[dict(symbol='XUSDT',sha256=s.digest(p))])))
            s.verify_source(check,[p],btc,cp);p.write_bytes(b'changed')
            with self.assertRaises(ValueError):s.verify_source(check,[p],btc,cp)
            p.write_bytes(b'source');btc.write_bytes(b'changed')
            with self.assertRaises(ValueError):s.verify_source(check,[p],btc,cp)

    def test_real_catalogue_contract_has856_hashes_and256_baseline_matches(self):
        ctx=json.loads(s.CONTEXT.read_text());baseline=s.ROOT/'research/residual-reversion-v10/FROZEN_INPUT_HASHES.json'
        expected=json.loads(baseline.read_text())['expected_csv_sha256']
        self.assertEqual(len(ctx['expected_market_sha256']),856);self.assertEqual(len(expected),256)
        self.assertEqual(s.digest(baseline),ctx['baseline_sha256'])
        self.assertTrue(all(ctx['expected_market_sha256'].get(k)==v for k,v in expected.items()))

if __name__=='__main__':unittest.main()
