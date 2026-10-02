import copy,json,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import breadth_expansion_v15 as b
from scripts import breadth_expansion_continuation_v15 as s
from scripts import audit_breakout_retest_v13 as audit

def fixture(side=1,h=16):
    n=130;i=110;t=s.base.START+30*s.DAY+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.);c=o.copy();high=o+.2;low=o-.2
    o[i]=100.;c[i]=101. if side==1 else 99.;high[i]=max(101.2,c[i]+.2);low[i]=min(98.8,c[i]-.2)
    o[i+1]=c[i];raw=(t,o,high,low,c)
    f=dict(eligible=np.ones(n,bool),prior_atr=np.ones(n),volume_multiple=np.full(n,1.5),
        r1=np.full(n,.01*side),r16=np.zeros(n),r96=np.zeros(n),buy_share=np.full(n,.6 if side==1 else .4),
        prevh4=np.full(n,100.),prevl4=np.full(n,100.),clv=np.full(n,.9 if side==1 else .1),
        structural_low=np.full(n,98.),structural_high=np.full(n,102.))
    f[f'r{h}'][i]=side*(.03 if h==16 else .06)
    for hh in (16,96):
        for kind in ('down','up'):
            f[kind+'_fraction'+str(hh)]=np.full(n,.1);f['previous_'+kind+'_fraction'+str(hh)]=np.full(n,.1)
            f[kind+str(hh)]=np.full(n,4.);f['previous_'+kind+str(hh)]=np.full(n,4.)
        f['n'+str(hh)]=np.full(n,40.);f['previous_n'+str(hh)]=np.full(n,40.)
    kind='up' if side==1 else 'down'
    f[kind+'_fraction'+str(h)][i]=.15;f['previous_'+kind+'_fraction'+str(h)][i]=.05
    f[kind+str(h)][i]=6;f['previous_'+kind+str(h)][i]=2
    f['open_time']=t.copy();f['previous_open_time']=t-s.BAR
    btc=dict(r1=np.zeros(n),close=np.full(n,100.))
    cfg=next(x for x in s.configurations() if x['side']==side and x['horizon']==h and x['pressure']==.10 and x['flow']=='PRICE_ONLY')
    return i,cfg,raw,f,btc

class SignalContract(unittest.TestCase):
    def get(self,side=1,h=16):return fixture(side,h)
    def rows(self,z):
        i,cfg,raw,f,btc=z
        return s.intents('XUSDT',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
    def test_fixed_grid(self):self.assertEqual((len(s.configurations()),len(s.policies()),set(s.HOLDS)),(16,96,{16,48}))
    def test_long_expansion_continuation(self):
        z=self.get();r,_=self.rows(z);self.assertEqual(len(r),1);self.assertEqual(r[0]['entry_time'],z[2][0][111]);self.assertGreater(r[0]['entry'],r[0]['sl'])
    def test_short_expansion_continuation(self):
        r,_=self.rows(self.get(-1));self.assertEqual(len(r),1);self.assertGreater(r[0]['sl'],r[0]['entry'])
    def test_24h_shock(self):self.assertEqual(len(self.rows(self.get(1,96))[0]),1)
    def test_current_coin_momentum_required(self):
        z=self.get();z[3]['r16'][110]=0;self.assertFalse(self.rows(z)[0])
    def test_breadth_expansion_required(self):
        z=self.get();z[3]['up_fraction16'][110]=.09;self.assertFalse(self.rows(z)[0])
    def test_exact_five_pp_boundary(self):
        z=self.get();z[3]['previous_up_fraction16'][110]=.05;z[3]['up_fraction16'][110]=.10;self.assertEqual(len(self.rows(z)[0]),1)
    def test_threshold_crossing_required(self):
        z=self.get();z[3]['previous_up_fraction16'][110]=.11;z[3]['up_fraction16'][110]=.20;self.assertFalse(self.rows(z)[0])
    def test_low_universe_missing_fraction_blocks(self):
        z=self.get();z[3]['up_fraction16'][110]=np.nan;self.assertFalse(self.rows(z)[0])
    def test_previous_universe_under_thirty_blocks(self):
        z=self.get();z[3]['previous_n16'][110]=29;self.assertFalse(self.rows(z)[0])
    def test_current_coin_bar_must_be_contiguous(self):
        z=self.get();z[2][0][110]-=s.BAR;self.assertFalse(self.rows(z)[0])
    def test_actual_entry_gap_blocks(self):
        z=self.get();z[2][0][111]+=s.BAR;r,e=self.rows(z);self.assertFalse(r);self.assertEqual(e['ENTRY_PATH_GAP'],1)
    def test_future_high_low_close_irrelevant_to_entry(self):
        z=self.get();first=self.rows(z)[0];z[2][2][111:]*=20;z[2][3][111:]*=.01;z[2][4][111:]*=3
        self.assertEqual(first,self.rows(z)[0])
    def test_favorable_gap_rejected(self):
        z=self.get();z[2][1][111]=102.;self.assertFalse(self.rows(z)[0])
    def test_adverse_gap_retained(self):
        z=self.get();z[2][1][111]=100.5;self.assertEqual(len(self.rows(z)[0]),1)
    def test_stop_floor(self):
        z=self.get();z[3]['structural_low'][110]=100.9;z[3]['prior_atr'][110]=.01
        r,_=self.rows(z);self.assertAlmostEqual(r[0]['risk_pct'],.005)
    def test_stop_above_sixpct_rejected(self):
        z=self.get();z[3]['structural_low'][110]=80.;self.assertFalse(self.rows(z)[0])
    def test_btc_guard(self):
        z=self.get();z[4]['r1'][110]=.021;self.assertFalse(self.rows(z)[0])
    def test_flow55_long_blocks_low_buy(self):
        z=self.get();z[1]['flow']='FLOW55';z[3]['buy_share'][110]=.5;self.assertFalse(self.rows(z)[0])
    def test_price_only_ignores_buy_share(self):
        z=self.get();z[3]['buy_share'][110]=.1;self.assertEqual(len(self.rows(z)[0]),1)
    def test_closed_local_break_required(self):
        z=self.get();z[3]['prevh4'][110]=102.;self.assertFalse(self.rows(z)[0])
    def test_trailing_policy_uses_canonical_resolver(self):
        z=self.get();seen=[]
        def resolver(tr,raw,f,exit_type,end):seen.append((exit_type,tr['entry_index']));return dict(status='DATA_GAP')
        with patch.object(s.canonical,'resolve',side_effect=resolver):
            p=[dict(**z[1],hold=16,exit_type='TRAIL',policy='fixture')];_,_,bad=s.policy_rows('XUSDT',p,z[2],z[3],z[4],z[2][0][0],z[2][0][-1]+s.BAR)
        self.assertEqual(seen,[('TRAIL',111)]);self.assertEqual(bad[0]['status'],'DATA_GAP')

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
