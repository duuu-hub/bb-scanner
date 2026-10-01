"""Economic timing, OLS reference, entry constraints and full calendar regressions."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import btc_factor_lag_v6 as s
from scripts import day_edge_lab as b
from scripts import relative_pullback_portfolio as account

def prices(n=1800,log_returns=None):
    rng=np.random.default_rng(46021)
    r=rng.normal(0,.001,n) if log_returns is None else log_returns
    c=100*np.exp(np.cumsum(r));t=np.arange(n,dtype=np.int64)*s.BAR
    return [t,c.copy(),c*1.003,c*.997,c.copy()]

def context(n=1800):
    rng=np.random.default_rng(46021);u=rng.normal(0,.001,n)
    r=1.4*u+rng.normal(0,.0003,n)
    raw,br=prices(n,r),prices(n,u);q=np.full(n,1_000_000.)
    f=s.features(raw,q,q*.6);btc=b.align_btc(raw[0],br[0],b.features(br,q))
    return raw,f,btc

def event(side=1,horizon=4,flow='PRICE_ONLY',n=100):
    c=np.full(n,100.);o=np.full(n,100.)
    c[20]=100.+side
    raw=[np.arange(n,dtype=np.int64)*s.BAR,o,c+3,c-3,c]
    f={k:np.full(n,np.nan) for k in ('beta4','beta16','r24','r216','log_gap4','log_gap16')}
    f.update(eligible=np.ones(n,dtype=bool),volume_multiple=np.full(n,2.),
             prior_atr=np.full(n,1.),buy_share=np.full(n,.6 if side==1 else .4),
             r1=np.full(n,side*.002))
    for h in (4,16):
        f[f'beta{h}']=np.full(n,1.5);f[f'r2{h}']=np.full(n,.5)
        f[f'log_gap{h}']=np.full(n,side*.03)
    btc={f'r{h}':np.full(n,side*.03) for h in (4,16)}
    cfg=next(p for p in s.configurations() if p['side']==side and p['horizon']==horizon
             and p['gap']==.01 and p['flow']==flow)
    return raw,f,btc,cfg

class FactorTimingTests(unittest.TestCase):
    def test_ols_beta_and_r2_match_demeaned_numpy_reference(self):
        raw,f,btc=context();out=s.factor_features(raw,f,btc);i=1200
        for h in (4,16):
            cut=i-h;sl=slice(cut-s.FIT_BARS+1,cut+1)
            r=np.log1p(f['r1'][sl]);u=np.log1p(btc['r1'][sl])
            rr=r-r.mean();uu=u-u.mean();cov=np.mean(rr*uu)
            self.assertAlmostEqual(out[f'beta{h}'][i],cov/np.mean(uu*uu),places=12)
            self.assertAlmostEqual(out[f'r2{h}'][i],cov*cov/(np.mean(rr*rr)*np.mean(uu*uu)),places=12)

    def test_impulse_returns_do_not_enter_prior_fit(self):
        raw,f,btc=context();old=s.factor_features(raw,f,btc);i=1200
        for h in (4,16):
            nf={k:v.copy() for k,v in f.items()};nb={k:v.copy() for k,v in btc.items()}
            nf['r1'][i-h+1:i+1]=.25;nb['r1'][i-h+1:i+1]=-.1
            new=s.factor_features(raw,nf,nb)
            self.assertEqual(old[f'beta{h}'][i],new[f'beta{h}'][i])
            self.assertEqual(old[f'r2{h}'][i],new[f'r2{h}'][i])

    def test_future_price_perturbation_does_not_change_features_or_fits(self):
        raw,f,btc=context();old=s.factor_features(raw,f,btc)
        newraw=[x.copy() for x in raw]
        for a in newraw[1:]:a[1300:]*=2
        q=np.full(len(raw[0]),1_000_000.)
        new=s.factor_features(newraw,s.features(newraw,q,q*.6),btc)
        for key in old:np.testing.assert_allclose(old[key][:1300],new[key][:1300],equal_nan=True)

    def test_missing_btc_return_invalidates_all_contaminated_windows(self):
        raw,f,btc=context();btc['r1'][800]=np.nan
        out=s.factor_features(raw,f,btc)
        for h in (4,16):
            self.assertTrue(np.isnan(out[f'beta{h}'][800+h:800+s.FIT_BARS+h]).all())
            self.assertTrue(np.isfinite(out[f'beta{h}'][800+s.FIT_BARS+h]))

    def test_coin_gap_restarts_full_fit_history(self):
        raw,f,btc=context();raw[0][800:]+=s.BAR
        q=np.full(len(raw[0]),1_000_000.);f=s.features(raw,q,q*.6)
        out=s.factor_features(raw,f,btc)
        for h in (4,16):
            self.assertTrue(np.isnan(out[f'beta{h}'][800:800+s.FIT_BARS+h]).all())
            self.assertTrue(np.isfinite(out[f'beta{h}'][800+s.FIT_BARS+h]))

    def test_zero_factor_variance_never_becomes_a_signal(self):
        raw,f,btc=context()
        btc['r1'][:]=0
        out=s.factor_features(raw,f,btc)
        self.assertTrue(np.isnan(out['beta4']).all())
        self.assertTrue(np.isnan(out['r24']).all())

    def test_current_volatility_and_turnover_are_excluded_from_prior_baselines(self):
        raw,f,btc=context();old=s.features(raw,np.ones(1800),np.ones(1800)*.6)
        newraw=[v.copy() for v in raw];newraw[2][1000]*=2;newraw[3][1000]*=.5
        q=np.ones(1800);q[1000]=100
        new=s.features(newraw,q,q*.6)
        self.assertEqual(old['prior_atr'][1000],new['prior_atr'][1000])
        self.assertAlmostEqual(new['volume_multiple'][1000],100.)

    def test_exact_timestamp_alignment_does_not_forward_fill_btc(self):
        t=np.array([0,s.BAR,2*s.BAR],np.int64);bt=np.array([0,2*s.BAR],np.int64)
        bf={f'r{h}':np.array([.1,.2]) for h in (1,4,16,96)}
        out=b.align_btc(t,bt,bf)
        self.assertTrue(np.isnan(out['r1'][1]));self.assertEqual(out['r1'][2],.2)

class LagSignalTests(unittest.TestCase):
    def test_sixteen_entries_and_ninety_six_unique_policies(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)
        self.assertEqual(set(p['hold'] for p in s.policies()),{24,96})

    def test_mirrored_lag_price_and_flow_confirmations(self):
        for side in (1,-1):
            for flow in ('PRICE_ONLY','FLOW55'):
                raw,f,btc,cfg=event(side=side,flow=flow)
                self.assertEqual(np.flatnonzero(s.mask(cfg,raw,f,btc)).tolist(),[20])

    def test_flow_confirmation_rejects_opposite_taker_pressure(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side=side,flow='FLOW55');f['buy_share'][:]=.4 if side==1 else .6
            self.assertFalse(s.mask(cfg,raw,f,btc).any())
            cfg=dict(cfg,flow='PRICE_ONLY');self.assertTrue(s.mask(cfg,raw,f,btc)[20])

    def test_overreacted_coin_cannot_qualify_as_lagger(self):
        raw,f,btc,cfg=event();f['log_gap4'][:]=-.01
        self.assertFalse(s.mask(cfg,raw,f,btc).any())

    def test_weak_wrong_or_missing_btc_impulse_is_rejected(self):
        for value in (.009,-.03,np.nan):
            raw,f,btc,cfg=event();btc['r4'][:]=value
            self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event(horizon=16);btc['r16'][:]=.019
        self.assertFalse(s.mask(cfg,raw,f,btc).any())

    def test_fit_quality_and_liquidity_are_hard_filters(self):
        for key,value in [('beta4',.49),('beta4',3.01),('r24',.099),('r24',np.nan),
                          ('volume_multiple',1.24),('eligible',False),('r1',-.002)]:
            raw,f,btc,cfg=event();f[key][:]=value
            self.assertFalse(s.mask(cfg,raw,f,btc).any(),key)

    def test_entry_gap_uses_next_open_without_future_ohlc(self):
        for side in (1,-1):
            raw,f,btc,cfg=event(side=side)
            raw[1][21]=raw[4][20]*(1+side*.006)
            rows,ex=s.intents('X',cfg,raw,f,btc,0,len(raw[0])*s.BAR)
            self.assertFalse(rows);self.assertEqual(ex['ENTRY_CATCHUP_GAP'],1)
            raw[1][21]=raw[4][20]*(1-side*.006)
            raw[2][21]=1e9;raw[3][21]=.001;raw[4][21]=1e8
            rows,ex=s.intents('X',cfg,raw,f,btc,0,len(raw[0])*s.BAR)
            self.assertEqual(len(rows),1);self.assertLess(rows[0]['known_entry_gap'],0)

    def test_actual_fill_sets_stop_risk_and_signal_priority(self):
        raw,f,btc,cfg=event();raw[1][21]=100.8
        rows,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR);tr=rows[0]
        self.assertEqual(tr['entry'],100.8);self.assertEqual(tr['sl'],98.8)
        self.assertAlmostEqual(tr['risk_pct'],2/100.8)
        self.assertAlmostEqual(tr['score'],.03*np.sqrt(.5)/(.01/1.01))
        self.assertEqual(tr['entry_time'],tr['signal_time']+s.BAR)
        self.assertEqual(tr['decision_time'],tr['entry_time'])

    def test_stop_floor_and_maximum_are_applied_to_actual_fill(self):
        raw,f,btc,cfg=event();f['prior_atr'][:]=.01
        rows,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR)
        self.assertAlmostEqual(rows[0]['risk_pct'],.005)
        f['prior_atr'][:]=5
        rows,ex=s.intents('X',cfg,raw,f,btc,0,100*s.BAR)
        self.assertFalse(rows);self.assertEqual(ex['STOP_ABOVE_8PCT'],1)

    def test_next_open_path_gap_and_split_entry_are_excluded(self):
        raw,f,btc,cfg=event();raw[0][21:]+=s.BAR
        rows,ex=s.intents('X',cfg,raw,f,btc,0,101*s.BAR)
        self.assertFalse(rows);self.assertEqual(ex['ENTRY_PATH_GAP'],1)
        raw,f,btc,cfg=event()
        rows,_=s.intents('X',cfg,raw,f,btc,0,21*s.BAR);self.assertFalse(rows)

    def test_cooldown_is_intent_based_not_future_exit_based(self):
        raw,f,btc,cfg=event(n=100)
        for i in (25,35,37):raw[4][i]=101.
        rows,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR)
        self.assertEqual([r['signal_time']//s.BAR for r in rows],[20,37])

    def test_policy_rows_uses_same_canonical_resolver_and_actual_tp_risk(self):
        raw,f,btc,cfg=event();p={**cfg,'hold':24,'exit_type':'TP3','policy':'P'}
        result=dict(status='RESOLVED',exit_time=23*s.BAR,exit=106.,reason='TP',gross_return=.06)
        with patch.object(s.canonical,'resolve',return_value=result) as resolver:
            rows,_,_=s.policy_rows('X',[p],raw,f,btc,0,100*s.BAR)
        tr=resolver.call_args.args[0]
        self.assertEqual(tr['tp'],106.);self.assertEqual(tr['max_hold_bars'],24)
        self.assertEqual(resolver.call_args.args[3],'TP2')
        self.assertAlmostEqual(rows[0]['net40_fraction'],.06-.002*2.06-.0002*2/96)

    def test_canonical_exclusion_is_retained_not_assigned_a_fake_loss(self):
        raw,f,btc,cfg=event();p={**cfg,'hold':24,'exit_type':'TP2','policy':'P'}
        with patch.object(s.canonical,'resolve',return_value=dict(status='EXIT_MISMATCH')):
            rows,counts,bad=s.policy_rows('X',[p],raw,f,btc,0,100*s.BAR)
        self.assertFalse(rows);self.assertEqual(bad[0]['status'],'EXIT_MISMATCH')
        self.assertEqual(counts['P/EXIT_MISMATCH'],1)

class SelectionTests(unittest.TestCase):
    def make_parts(self,parts,negative_price=False,negative_r=False,clustered=False):
        p=s.policies()[0];rows=[]
        for year in (2022,2023):
            start=int(pd.Timestamp(f'{year}-01-01',tz='Asia/Seoul').timestamp()*1000)
            for i in range(160):
                ts=start+i*s.BAR if clustered else start+(i//2)*s.DAY+(i%2)*s.BAR
                row={k:0 for k in s.COLUMNS}
                row.update(symbol=f'X{i%10}',variant=p['policy'],policy=p['policy'],entry_time=ts,
                           net40_fraction=-.001 if negative_price and year==2022 else .02,
                           net40_R=-.1 if negative_r and year==2023 else .5,split='DEV')
                rows.append(row)
        for i in range(8):
            folder=parts/str(i);folder.mkdir(parents=True)
            pd.DataFrame(rows if i==0 else [],columns=s.COLUMNS).to_csv(folder/'independent_candidates.csv.gz',index=False)
            (folder/'scan_meta.json').write_text(json.dumps(dict(complete=True,stage='DEV',counts={},coverage=[])))

    def test_selected_year_labels_follow_kst_not_utc_and_other_cells_remain(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make_parts(root/'parts');s.select(root/'parts',root/'out')
            d=pd.read_csv(root/'out/development_policy_cells.csv');observed=d[d.n>0].iloc[0]
            self.assertEqual(len(d),96);self.assertEqual((d.n==0).sum(),95)
            self.assertEqual(observed.year_2022_n,160);self.assertEqual(observed.year_2023_n,160)
            decision=json.loads((root/'out/selection.json').read_text())
            self.assertEqual(len(decision['policies']),1);self.assertEqual(decision['union_name'],'FACTOR_UNION')

    def test_positive_r_never_hides_negative_annual_price_mean(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make_parts(root/'parts',negative_price=True);s.select(root/'parts',root/'out')
            self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_positive_price_never_hides_negative_annual_r_mean(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make_parts(root/'parts',negative_r=True);s.select(root/'parts',root/'out')
            self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_clustered_entries_do_not_satisfy_yearly_date_frequency(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make_parts(root/'parts',clustered=True);s.select(root/'parts',root/'out')
            self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_empty_original_shards_preserve_all96_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts'
            for i in range(8):
                p=parts/str(i);p.mkdir(parents=True)
                pd.DataFrame(columns=s.COLUMNS).to_csv(p/'independent_candidates.csv.gz',index=False)
                (p/'scan_meta.json').write_text(json.dumps(dict(complete=True,stage='DEV',counts={},coverage=[])))
            s.select(parts,root/'out')
            table=pd.read_csv(root/'out/development_policy_cells.csv')
            self.assertEqual(len(table),96);self.assertTrue(table.n.eq(0).all())
            self.assertEqual(json.loads((root/'out/selection.json').read_text())['policies'],[])

class AllCalendarTests(unittest.TestCase):
    def sim(self,rows=(),end=3*s.DAY,all_days=True):
        n=end//s.BAR+1
        market=account.Market({r['symbol']:prices(n,np.zeros(n)) for r in rows})
        return account.simulate(pd.DataFrame(rows),market,0,end,20,True,all_kst_days=all_days)

    def trade(self,symbol,entry,exit,price=104.):
        return dict(symbol=symbol,entry_time=entry,exit_time=exit,entry=100.,exit=price,
                    sl=99.5,tp=104.,side=1,score=1.,reason='TP',max_hold_bars=24)

    def test_both_partial_edge_days_and_all_empty_days_count(self):
        r,_,daily,_=self.sim()
        self.assertEqual(r['calendar_days'],4);self.assertEqual(r['partial_calendar_days'],2)
        self.assertEqual(daily.covered_hours.tolist(),[15.,24.,24.,9.])
        self.assertEqual(r['flat_days_pct'],100.)
        self.assertEqual(daily.day.tolist(),['1970-01-01','1970-01-02','1970-01-03','1970-01-04'])

    def test_edge_day_profits_and_costs_are_retained_and_reconcile_final_equity(self):
        end=3*s.DAY;rows=[self.trade('FIRST',0,2*s.BAR),self.trade('LAST',end-2*s.BAR,end)]
        r,tr,d,_=self.sim(rows,end=end)
        self.assertGreater(d.return_pct.iloc[0],.7);self.assertGreater(d.return_pct.iloc[-1],.7)
        self.assertEqual(r['day_ge_0_7_pct'],50.)
        self.assertAlmostEqual(np.prod(1+d.return_pct/100),1+r['net_return_pct']/100)
        self.assertAlmostEqual(tr.net_pnl.sum(),r['net_return_pct']/100)

    def test_reporting_option_does_not_change_trades_cash_dd_risks_or_curve(self):
        end=3*s.DAY;rows=[self.trade('FIRST',0,2*s.BAR),self.trade('LAST',end-2*s.BAR,end)]
        a,at,ad,ac=self.sim(rows,end=end);o,ot,od,oc=self.sim(rows,end=end,all_days=False)
        pd.testing.assert_frame_equal(at,ot);pd.testing.assert_frame_equal(ac,oc)
        for key in ('net_return_pct','cagr_pct','mdd_15m_pct','pf','rejections','guard_triggers','trades'):
            self.assertEqual(a[key],o[key])
        self.assertEqual(o['calendar_days'],2);self.assertEqual(o['day_ge_0_7_pct'],0.)

    def test_every_day_after_permanent_halt_remains_in_denominator(self):
        rows=[self.trade('X'+str(i),0,s.BAR,price=80.) for i in range(4)]
        for row in rows:row['reason']='SL'
        rows.append(self.trade('BLOCKED',s.DAY,s.DAY+s.BAR))
        r,tr,d,_=self.sim(rows)
        self.assertIsNotNone(r['halt_time']);self.assertEqual(r['rejections']['halted'],1)
        self.assertEqual(r['calendar_days'],4);self.assertEqual(len(tr),4)
        self.assertTrue((d.return_pct.iloc[1:]==0).all())

    def test_exact_kst_midnight_end_adds_no_zero_duration_date(self):
        end=s.DAY-9*3_600_000
        r,_,d,_=self.sim(end=end)
        self.assertEqual(r['calendar_days'],1);self.assertEqual(d.covered_hours.iloc[0],15.)

if __name__=='__main__':unittest.main()
