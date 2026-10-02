import copy,json,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import breadth_pullback_v16 as b
from scripts import breadth_pullback_reclaim_v16 as s
from scripts import audit_breakout_retest_v13 as audit

def fixture(side=1,h=16,depth=.5):
    n=150;e,touch,reclaim=100,103,105
    t=s.base.START+30*s.DAY+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.);c=o.copy();high=o+.2;low=o-.2
    o[e]=100.;c[e]=101. if side==1 else 99.
    high[e]=101.2 if side==1 else 100.2;low[e]=99.8 if side==1 else 98.8
    if side==1:
        o[e+1:]=100.8;c[e+1:]=100.8;high[e+1:]=101.;low[e+1:]=100.6
        o[touch]=100.8;c[touch]=100.6;high[touch]=100.9;low[touch]=100.4
        high[104]=100.6;low[104]=100.2
        o[reclaim]=100.5;c[reclaim]=101.;high[reclaim]=101.1;low[reclaim]=100.4
    else:
        o[e+1:]=99.2;c[e+1:]=99.2;high[e+1:]=99.4;low[e+1:]=99.
        o[touch]=99.2;c[touch]=99.4;high[touch]=99.6;low[touch]=99.1
        high[104]=99.8;low[104]=99.4
        o[reclaim]=99.5;c[reclaim]=99.;high[reclaim]=99.6;low[reclaim]=98.9
    o[reclaim+1]=c[reclaim];raw=(t,o,high,low,c)
    f=dict(eligible=np.ones(n,bool),prior_atr=np.ones(n),volume_multiple=np.full(n,1.1),
        r1=np.zeros(n),r16=np.zeros(n),r96=np.zeros(n),buy_share=np.full(n,.5),
        prevh4=np.full(n,100.),prevl4=np.full(n,100.),clv=np.full(n,.5),
        structural_low=np.full(n,98.),structural_high=np.full(n,102.))
    f['volume_multiple'][e]=1.5;f[f'r{h}'][e]=side*(.03 if h==16 else .06)
    f['r1'][e]=side*.01;f['r1'][reclaim]=side*.01
    f['clv'][e]=.9 if side==1 else .1;f['clv'][reclaim]=.9 if side==1 else .1
    for hh in (16,96):
        for kind in ('down','up'):
            f[kind+'_fraction'+str(hh)]=np.full(n,.05);f['previous_'+kind+'_fraction'+str(hh)]=np.full(n,.05)
            f[kind+str(hh)]=np.full(n,2.);f['previous_'+kind+str(hh)]=np.full(n,2.)
        f['n'+str(hh)]=np.full(n,40.);f['previous_n'+str(hh)]=np.full(n,40.)
    kind='up' if side==1 else 'down'
    f[kind+'_fraction'+str(h)][e]=.15;f['previous_'+kind+'_fraction'+str(h)][e]=.05
    f[kind+str(h)][e]=6;f['previous_'+kind+str(h)][e]=2
    f['open_time']=t.copy();f['previous_open_time']=t-s.BAR
    btc=dict(r1=np.zeros(n),close=np.full(n,100.))
    cfg=next(x for x in s.configurations() if x['side']==side and x['horizon']==h and x['pressure']==.10 and x['depth']==depth)
    return e,touch,reclaim,cfg,raw,f,btc

class SignalContract(unittest.TestCase):
    def get(self,side=1,h=16,depth=.5):return fixture(side,h,depth)
    def rows(self,z):
        e,touch,reclaim,cfg,raw,f,btc=z
        return s.intents('XUSDT',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
    def test_fixed_grid(self):self.assertEqual((len(s.configurations()),len(s.policies()),set(s.HOLDS)),(16,96,{16,48}))
    def test_long_two_stage_reclaim(self):
        z=self.get();r,_=self.rows(z);self.assertEqual(len(r),1);self.assertEqual((r[0]['touch_index'],r[0]['reclaim_index'],r[0]['entry_index']),(103,105,106))
    def test_short_two_stage_reclaim(self):
        r,_=self.rows(self.get(-1));self.assertEqual(len(r),1);self.assertGreater(r[0]['sl'],r[0]['entry'])
    def test_96_bar_impulse(self):self.assertEqual(len(self.rows(self.get(1,96))[0]),1)
    def test_one_atr_pullback_grid(self):
        z=self.get(1,16,1.);z[4][3][103]=99.9;self.assertEqual(len(self.rows(z)[0]),1)
    def test_event_momentum_required(self):
        z=self.get();z[5]['r16'][100]=0;self.assertFalse(self.rows(z)[0])
    def test_breadth_crossing_required(self):
        z=self.get();z[5]['previous_up_fraction16'][100]=.11;self.assertFalse(self.rows(z)[0])
    def test_five_point_expansion_required(self):
        z=self.get();z[5]['up_fraction16'][100]=.09;self.assertFalse(self.rows(z)[0])
    def test_pullback_touch_required(self):
        z=self.get();z[4][3][101:111]=100.8;self.assertFalse(self.rows(z)[0])
    def test_same_bar_touch_and_reclaim_forbidden(self):
        z=self.get();z[4][3][101:105]=100.8;z[4][3][105]=100.4;self.assertFalse(self.rows(z)[0])
    def test_reclaim_must_be_after_touch(self):
        z=self.get();z[4][4][103]=101.;z[4][2][102]=100.6;z[5]['clv'][103]=.9
        r,_=self.rows(z);self.assertEqual(r[0]['reclaim_index'],105)
    def test_pre_reclaim_invalidation_blocks(self):
        z=self.get();z[4][3][104]=99.4;r,e=self.rows(z);self.assertFalse(r);self.assertEqual(e['PRE_RECLAIM_INVALIDATION'],1)
    def test_touch_after_ten_bars_is_ignored(self):
        z=self.get();z[4][3][101:111]=100.8;z[4][3][111]=100.4;self.assertFalse(self.rows(z)[0])
    def test_reclaim_after_twelve_bars_is_ignored(self):
        z=self.get();z[5]['clv'][105]=.5;z[4][4][105]=100.;z[4][1][113]=100.5;z[4][4][113]=101.;z[4][2][112]=100.6;z[5]['clv'][113]=.9
        self.assertFalse(self.rows(z)[0])
    def test_watch_path_gap_blocks(self):
        z=self.get();z[4][0][102]+=1;r,e=self.rows(z);self.assertFalse(r);self.assertGreater(e['WATCH_PATH_GAP'],0)
    def test_entry_path_gap_blocks(self):
        z=self.get();z[4][0][106]+=1;r,e=self.rows(z);self.assertFalse(r);self.assertEqual(e['ENTRY_PATH_GAP'],1)
    def test_future_bars_irrelevant_to_intent(self):
        z=self.get();first=self.rows(z)[0];z[4][2][107:]*=20;z[4][3][107:]*=.01;z[4][4][107:]*=3
        self.assertEqual(first,self.rows(z)[0])
    def test_favorable_entry_gap_rejected(self):
        z=self.get();z[4][1][106]=102.;self.assertFalse(self.rows(z)[0])
    def test_adverse_entry_gap_retained(self):
        z=self.get();z[4][1][106]=100.5;self.assertEqual(len(self.rows(z)[0]),1)
    def test_stop_floor(self):
        z=self.get();z[5]['prior_atr'][100]=.1;z[4][3][103]=100.95;z[4][1][106]=100.5
        r,_=self.rows(z);self.assertAlmostEqual(r[0]['risk_pct'],.005)
    def test_stop_above_six_percent_rejected(self):
        z=self.get();z[4][3][100]=80.;z[4][3][103]=90.
        self.assertFalse(self.rows(z)[0])
    def test_btc_reclaim_guard(self):
        z=self.get();z[6]['r1'][105]=.021;self.assertFalse(self.rows(z)[0])
    def test_reclaim_prior_bar_break_required(self):
        z=self.get();z[4][2][104]=101.1;self.assertFalse(self.rows(z)[0])
    def test_reclaim_volume_required(self):
        z=self.get();z[5]['volume_multiple'][105]=.99;self.assertFalse(self.rows(z)[0])
    def test_trailing_policy_uses_canonical_resolver(self):
        z=self.get();seen=[]
        def resolver(tr,raw,f,exit_type,end):seen.append((exit_type,tr['entry_index']));return dict(status='DATA_GAP')
        with patch.object(s.canonical,'resolve',side_effect=resolver):
            p=[dict(**z[3],hold=16,exit_type='TRAIL',policy='fixture')];_,_,bad=s.policy_rows('XUSDT',p,z[4],z[5],z[6],z[4][0][0],z[4][0][-1]+s.BAR)
        self.assertEqual(seen,[('TRAIL',106)]);self.assertEqual(bad[0]['status'],'DATA_GAP')

class CountsContract(unittest.TestCase):
    def table(self):
        times=np.arange(3,dtype=np.int64)*s.BAR
        f=pd.DataFrame(dict(open_time=times,n16=[40,40,29],down16=[20,16,2],up16=[0,0,0],n96=[40,40,29],down96=[20,16,2],up96=[0,0,0]))
        return times,f
    def test_exact_global_fraction(self):
        t,f=self.table();out=b.align(t,f);np.testing.assert_allclose(out['down_fraction16'][:2],[.5,.4]);self.assertTrue(np.isnan(out['down_fraction16'][2]))
    def test_missing_previous_not_filled(self):
        t,f=self.table();self.assertTrue(np.isnan(b.align(t[:1],f)['previous_down_fraction16'][0]))
    def test_no_nearest_future_match(self):
        t,f=self.table();out=b.align(t+s.BAR//2,f);self.assertTrue(np.isnan(out['down_fraction16']).all())
    def test_count_sums_not_average_of_shards(self):
        times=np.arange(3,dtype=np.int64)*s.BAR;r=(times,np.ones(3),np.ones(3),np.ones(3),np.ones(3))
        f=dict(eligible=np.ones(3,bool),r16=np.array([-.04,-.03,.04]),r96=np.array([-.08,-.1,.1]))
        x=b.counts(r,f,times);np.testing.assert_array_equal(x[:,1],[1,1,0]);np.testing.assert_array_equal(x[:,2],[0,0,1])
    def test_nan_coin_not_in_denominator(self):
        t,_=self.table();raw=(t,)*5;f=dict(eligible=np.ones(3,bool),r16=np.array([np.nan,-.1,.1]),r96=np.array([0.,np.nan,.1]))
        self.assertEqual(b.counts(raw,f,t)[0,0],0);self.assertEqual(b.counts(raw,f,t)[1,3],0)
    def test_impossible_counts_rejected(self):
        t,f=self.table();f.loc[0,'down16']=41
        with self.assertRaises(ValueError):b.validate_counts(f,t)
    def test_noninteger_counts_rejected(self):
        t,f=self.table();f=f.astype(float);f.loc[0,'n16']=40.5
        with self.assertRaises(ValueError):b.validate_counts(f,t)
    def test_time_geometry_rejected(self):
        t,f=self.table();f.loc[1,'open_time']+=1
        with self.assertRaises(ValueError):b.validate_counts(f,t)
    def test_feature_future_perturbation(self):
        t=s.base.START+np.arange(31*96)*s.BAR;n=len(t);c=np.exp(np.arange(n)*.0001);q=np.full(n,1e6)
        raw=(t,c.copy(),c*1.01,c*.99,c.copy());a=b.features(raw,q,q*.6);cut=30*96
        raw[2][cut:]*=5;raw[3][cut:]*=.1;raw[4][cut:]*=2;q[cut:]*=10;bb=b.features(raw,q,q*.6)
        for k in a:np.testing.assert_allclose(a[k][:cut],bb[k][:cut],equal_nan=True)

class GlobalArtifactContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.ctx=cls.root/'ctx.json';cls.times=b.grid('DEV')
        hashes={f'X{i}USDT':str(i)*64 for i in range(8)};cls.context=dict(baseline_sha256='a'*64,btc_sha256='b'*64,expected_market_sha256=hashes)
        cls.ctx.write_text(json.dumps(cls.context));cls.parts=cls.root/'parts'
        for i in range(8):
            p=cls.parts/str(i);p.mkdir(parents=True);f=pd.DataFrame({'open_time':cls.times})
            for k in b.COUNT_COLUMNS:f[k]=4 if k.startswith('n') else (2 if k.startswith('down') else 0)
            f.to_csv(p/'breadth_map.csv.gz',index=False,compression=dict(method='gzip',mtime=0))
            sym=f'X{i}USDT';check=dict(status='VERIFIED',shards=[i],baseline_sha256='a'*64,files=[dict(symbol=sym,sha256=hashes[sym])])
            (p/'verified_source.json').write_text(json.dumps(check))
            meta=dict(complete=True,stage='DEV',shard=i,source_data_run=36095439671,market_hashes={sym:hashes[sym]},source_files=1,
                baseline_sha256='a'*64,source_context_sha256=b.digest(cls.ctx),btc_sha256='b'*64,source_check_sha256=b.digest(p/'verified_source.json'),
                map_sha256=b.digest(p/'breadth_map.csv.gz'),rows=len(f),horizons={str(k):v for k,v in b.HORIZONS.items()},minimum_universe=30)
            # Synthetic eight coin records cannot each count four coins: use32 distinct original symbols in map/source metadata.
            catalogue={f'X{i}_{j}USDT':str(i)*64 for j in range(4)}
            meta['market_hashes']=catalogue;meta['source_files']=4;check['files']=[dict(symbol=k,sha256=v) for k,v in catalogue.items()]
            (p/'verified_source.json').write_text(json.dumps(check));meta['source_check_sha256']=b.digest(p/'verified_source.json')
            (p/'map_meta.json').write_text(json.dumps(meta))
        cls.context['expected_market_sha256']={f'X{i}_{j}USDT':str(i)*64 for i in range(8) for j in range(4)};cls.ctx.write_text(json.dumps(cls.context))
        for p in cls.parts.iterdir():
            m=json.loads((p/'map_meta.json').read_text());m['source_context_sha256']=b.digest(cls.ctx);(p/'map_meta.json').write_text(json.dumps(m))
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def copy_parts(self):
        p=self.root/self._testMethodName;shutil.copytree(self.parts,p);return p
    def test_eight_shard_reduction_and_verified_load(self):
        out=self.root/'good';f,m=b.reduce_maps(self.parts,out,'DEV',self.ctx);g,_=b.load_breadth(out,'DEV',self.ctx)
        self.assertEqual(m['minimum_universe'],30);self.assertEqual(len(m['market_hashes']),32);self.assertTrue((f.n16==32).all());pd.testing.assert_frame_equal(f,g)
    def test_missing_shard_fails_closed(self):
        p=self.copy_parts();shutil.rmtree(p/'7')
        with self.assertRaises(ValueError):b.reduce_maps(p,self.root/'bad1','DEV',self.ctx)
    def test_altered_map_fails_closed(self):
        p=self.copy_parts();path=p/'0'/'breadth_map.csv.gz';path.write_bytes(path.read_bytes()+b'x')
        with self.assertRaises(ValueError):b.reduce_maps(p,self.root/'bad2','DEV',self.ctx)
    def test_duplicate_shard_fails_closed(self):
        p=self.copy_parts();path=p/'7'/'map_meta.json';m=json.loads(path.read_text());m['shard']=0;path.write_text(json.dumps(m))
        with self.assertRaises(ValueError):b.reduce_maps(p,self.root/'bad3','DEV',self.ctx)
    def test_stage_leak_fails_closed(self):
        with self.assertRaises(ValueError):b.reduce_maps(self.parts,self.root/'bad4','GATE',self.ctx)
    def test_changed_context_fails_closed(self):
        cp=self.root/'altered.json';m=copy.deepcopy(self.context);m['expected_market_sha256']['X0_0USDT']='f'*64;cp.write_text(json.dumps(m))
        with self.assertRaises(ValueError):b.reduce_maps(self.parts,self.root/'bad5','DEV',cp)

class AuditArithmeticContract(unittest.TestCase):
    def test_source_check_inside_scan_output_real_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);s.smoke(root)
            for i in range(8):
                p=root/'scans'/str(i);m=json.loads((p/'scan_meta.json').read_text())
                self.assertTrue(m['complete']);self.assertEqual(m['source_check_sha256'],b.digest(p/'source_check.json'))
                self.assertEqual((root/f'check-{i}.json').read_bytes(),(p/'source_check.json').read_bytes())
    def test_independent_costs_and_R(self):
        entry=100.;ex=99.;fill=ex*.999;hold=16;duration=60;fee=.002*(1+fill/entry);fund=.0002*duration/1440
        net=fill/entry-1-fee-fund;reserve=1-99/100*.999+.002*(1+99/100*.999)+.0002*hold*15/1440
        f=pd.DataFrame([dict(entry=entry,exit=ex,side=1,sl=99,max_hold_bars=hold,entry_time=0,exit_time=duration*60000,
            reason='SL',status='RESOLVED',split='DEV',gross_return=-.01,net40_fraction=net,net40_R=net/reserve)])
        self.assertAlmostEqual(audit.row_arithmetic(f)[1][0],net)
        f['net40_fraction']=net+.001
        with self.assertRaises(AssertionError):audit.row_arithmetic(f)

if __name__=='__main__':unittest.main()
