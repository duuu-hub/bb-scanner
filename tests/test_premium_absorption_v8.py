"""Premium archive integrity, exact availability and V8 economic causality."""
import hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import premium_index_archive as a
from scripts import premium_absorption_v8 as s

MONTH='2022-01';T=int(pd.Timestamp(MONTH+'-01',tz='UTC').timestamp()*1000)

def csv_row(ts,o,h,l,c):return f'{ts},{o},{h},{l},{c},0,{ts+a.BAR-1},0,0,0,0,0\n'
def zip_bytes(text):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('premium.csv',text)
    return b.getvalue()
def official_bytes():
    header='open_time,open,high,low,close,ignore,close_time,ignore,ignore,ignore,ignore,ignore\n'
    raw=zip_bytes(header+csv_row(T,-.0012,-.0007,-.0013,-.0008)+csv_row(T+a.BAR,.0001,.0003,-.0001,.0002))
    check=(hashlib.sha256(raw).hexdigest()+'  BTCUSDT-15m-2022-01.zip\n').encode();return raw,check

class ArchiveTests(unittest.TestCase):
    def setUp(self):a.INPUTS.clear()
    def test_signed_header_and_headerless_arrays_agree(self):
        header='open_time,open,high,low,close,ignore,close_time,ignore,ignore,ignore,ignore,ignore\n'
        text=csv_row(T,-.0012,-.0007,-.0013,-.0008)+csv_row(T+a.BAR,.0001,.0003,-.0001,.0002)
        x=a.parse_zip(zip_bytes(header+text),MONTH);y=a.parse_zip(zip_bytes(text),MONTH)
        for u,v in zip(x,y):np.testing.assert_array_equal(u,v)
        self.assertLess(x[3][0],0);self.assertEqual(x[0].dtype,np.dtype('int64'))
    def test_invalid_width_header_timestamp_geometry_and_values_fail(self):
        bad=['1,2,3\n','wrong,header,names,here,x,0,0,0,0,0,0,0\n',
             csv_row(T+1,0,.1,-.1,0),csv_row(T,0,-.1,.1,0),csv_row(T,0,1.1,-.1,0),
             csv_row(T,0,.1,-.1,float('nan')),csv_row(T,0,.1,-.1,0)+csv_row(T,0,.1,-.1,0)]
        for text in bad:
            with self.subTest(text=text),self.assertRaises((ValueError,pd.errors.EmptyDataError)):a.parse_zip(zip_bytes(text),MONTH)
    def test_original_checksum_npz_and_verified_cache_are_immutable(self):
        raw,check=official_bytes()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with patch.object(a,'_get',side_effect=[raw,check]) as get:first=a.load_month('BTCUSDT',MONTH,root/'cache',root/'ev')
            self.assertEqual(get.call_count,2)
            with patch.object(a,'_get',side_effect=AssertionError('immutable cache')):again=a.load_month('BTCUSDT',MONTH,root/'cache',root/'ev2')
            for u,v in zip(first,again):np.testing.assert_array_equal(u,v)
            paths=a.filenames('BTCUSDT',MONTH,root/'ev');m=json.loads(paths[3].read_text())
            self.assertEqual(paths[0].read_bytes(),raw);self.assertEqual(paths[1].read_bytes(),check)
            self.assertEqual(a.digest(paths[0]),m['official_checksum_sha256']);self.assertEqual(a.digest(paths[2]),m['npz_sha256'])
    def test_each_verified_cache_component_corruption_fails_without_redownload(self):
        for part in range(4):
            with self.subTest(part=part),tempfile.TemporaryDirectory() as td:
                root=Path(td);raw,check=official_bytes()
                with patch.object(a,'_get',side_effect=[raw,check]):a.load_month('BTCUSDT',MONTH,root/'c',root/'e')
                paths=a.filenames('BTCUSDT',MONTH,root/'c')
                if part==3:
                    m=json.loads(paths[3].read_text());m['source_url']='wrong';paths[3].write_text(json.dumps(m))
                else:paths[part].write_bytes(b'corrupt')
                with patch.object(a,'_get',side_effect=AssertionError('no replacement')),self.assertRaises(ValueError):a.load_month('BTCUSDT',MONTH,root/'c',root/'fail')
                self.assertEqual(a.INPUTS['BTCUSDT/'+MONTH]['status'],'CACHE_INTEGRITY_ERROR')
    def test_404_checksum_failure_and_transient_are_not_zero_imputed(self):
        raw,check=official_bytes()
        for effects,status,raises in [([None],'PREMIUM_DATA_GAP',False),([raw,None],'PREMIUM_DATA_GAP',False),([raw,b'bad'],'PREMIUM_DATA_GAP',False),([raw,RuntimeError('429')],'TRANSIENT_SOURCE_ERROR',True)]:
            with self.subTest(effects=effects),tempfile.TemporaryDirectory() as td:
                root=Path(td)
                with patch.object(a,'_get',side_effect=effects):
                    if raises:
                        with self.assertRaises(RuntimeError):a.load_month('BTCUSDT',MONTH,root/'c',root/'e')
                    else:self.assertIsNone(a.load_month('BTCUSDT',MONTH,root/'c',root/'e'))
                self.assertEqual(a.INPUTS['BTCUSDT/'+MONTH]['status'],status)
    def test_exact_alignment_missing_month_and_stage_cut(self):
        data=(np.array([T,T+2*a.BAR],np.int64),)+(np.array([-.001,-.0005]),)*4
        t=np.array([T,T+a.BAR,T+2*a.BAR],np.int64)
        f=a.align(t,data,{MONTH},T+2*a.BAR)
        np.testing.assert_array_equal(f['premium_available'],[True,False,False])
        self.assertTrue(np.isnan(f['premium_close'][1:]).all())
        self.assertFalse(a.align(t,data,set(),T+3*a.BAR)['premium_available'].any())
    def test_future_premium_cannot_change_prior_alignment(self):
        t=np.arange(T,T+8*a.BAR,a.BAR,dtype=np.int64);v=np.linspace(-.001,-.0003,8)
        data=(t.copy(),v.copy(),v.copy(),v.copy(),v.copy());old=a.align(t,data,{MONTH},T+8*a.BAR)
        changed=tuple(x.copy() for x in data);changed[-1][5:]=.9;new=a.align(t,changed,{MONTH},T+8*a.BAR)
        for key in old:np.testing.assert_array_equal(old[key][:5],new[key][:5])
    def test_load_symbol_includes_previous_month_but_never_future_stage(self):
        feb=int(pd.Timestamp('2022-02-01',tz='UTC').timestamp()*1000);calls=[]
        def get(symbol,month,cache,evidence):
            calls.append(month);ts=T if month==MONTH else feb
            return (np.array([ts],np.int64),)+(np.array([-.001]),)*4
        with patch.object(a,'load_month',side_effect=get):data,valid=a.load_symbol('BTCUSDT',np.array([T,feb+2*a.BAR]),feb,feb+3*a.BAR,'c','e')
        self.assertEqual(set(calls),{MONTH,'2022-02'});self.assertEqual(valid,{MONTH,'2022-02'})
        self.assertTrue((data[0]<feb+3*a.BAR).all())

def event(side=1,lookback=1,btc_filter='ANY',n=100):
    c=np.full(n,100.);o=c.copy();c[20]=100+2*side
    raw=[np.arange(n,dtype=np.int64)*s.BAR,o,c+3,c-3,c]
    premium=np.zeros(n);premium[20-lookback]=-side*.0012;premium[20]=-side*.0008
    f=dict(eligible=np.ones(n,bool),volume_multiple=np.full(n,2.),prior_atr=np.full(n,1.),
      buy_share=np.full(n,.6 if side==1 else .4),r1=np.full(n,side*.002),
      premium_available=np.ones(n,bool),premium_open=premium.copy(),premium_high=premium.copy(),
      premium_low=premium.copy(),premium_close=premium)
    btc=dict(r16=np.full(n,side*.03))
    cfg=next(x for x in s.configurations() if x['side']==side and x['threshold']==.0005 and x['lookback']==lookback and x['btc']==btc_filter)
    return raw,f,btc,cfg

class EconomicTests(unittest.TestCase):
    def test_sixteen_entries_ninety_six_policies(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96);self.assertEqual(len({x['policy'] for x in s.policies()}),96)
    def test_mirrored_signed_dislocation_and_normalization(self):
        for side in (1,-1):
            for lookback in (1,4):
                raw,f,btc,cfg=event(side,lookback);self.assertEqual(np.flatnonzero(s.mask(cfg,raw,f,btc)).tolist(),[20])
                f['premium_close'][20]=side*.0008;self.assertFalse(s.mask(cfg,raw,f,btc).any())
    def test_normalization_magnitude_and_direction_are_hard_filters(self):
        raw,f,btc,cfg=event();f['premium_close'][20]=-.0011
        self.assertFalse(s.mask(cfg,raw,f,btc).any())
        f['premium_close'][20]=-.0013;self.assertFalse(s.mask(cfg,raw,f,btc).any())
    def test_missing_or_noncontiguous_premium_invalidates_signal(self):
        raw,f,btc,cfg=event();f['premium_available'][20]=False;self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event();raw[0][20:]+=s.BAR;self.assertFalse(s.mask(cfg,raw,f,btc).any())
    def test_flow_volume_price_and_btc_filters(self):
        for key,value in [('buy_share',.4),('volume_multiple',1.24),('r1',-.01),('eligible',False)]:
            raw,f,btc,cfg=event();f[key][:]=value;self.assertFalse(s.mask(cfg,raw,f,btc).any(),key)
        raw,f,btc,cfg=event(btc_filter='ALIGN4H');btc['r16'][:]=np.nan;self.assertFalse(s.mask(cfg,raw,f,btc).any())
        raw,f,btc,cfg=event();btc['r16'][:]=np.nan;self.assertTrue(s.mask(cfg,raw,f,btc)[20])
    def test_next_open_actual_risk_priority_and_future_ohlc_independence(self):
        raw,f,btc,cfg=event();raw[1][21]=101.9;rows,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR);r=rows[0]
        self.assertEqual(r['entry'],101.9);self.assertEqual(r['sl'],99.9);self.assertAlmostEqual(r['risk_pct'],2/101.9)
        self.assertAlmostEqual(r['score'],.0008*.0004*np.sqrt(2)/(1/102));self.assertEqual(r['entry_time'],r['decision_time'])
        raw[2][21:]*=100;raw[3][21:]*=.01;raw[4][21:]*=20;new,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR);self.assertEqual(r,new[0])
    def test_entry_gap_floor_overwide_stop_and_path_gap_are_explicit(self):
        raw,f,btc,cfg=event();raw[1][21]=raw[4][20]*1.006;rows,ex=s.intents('X',cfg,raw,f,btc,0,100*s.BAR)
        self.assertFalse(rows);self.assertEqual(ex['ENTRY_CATCHUP_GAP'],1)
        raw,f,btc,cfg=event();f['prior_atr'][:]=.001;rows,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR);self.assertAlmostEqual(rows[0]['risk_pct'],.005)
        f['prior_atr'][:]=5;rows,ex=s.intents('X',cfg,raw,f,btc,0,100*s.BAR);self.assertFalse(rows);self.assertEqual(ex['STOP_ABOVE_8PCT'],1)
        raw,f,btc,cfg=event();raw[0][21:]+=s.BAR;rows,ex=s.intents('X',cfg,raw,f,btc,0,101*s.BAR);self.assertFalse(rows);self.assertEqual(ex['ENTRY_PATH_GAP'],1)
    def test_fixed_intent_cooldown_not_future_exit(self):
        raw,f,btc,cfg=event(n=100)
        for i in (25,35,37):
            raw[4][i]=102.;f['premium_close'][i-1]=-.0012;f['premium_close'][i]=-.0008
        rows,_=s.intents('X',cfg,raw,f,btc,0,100*s.BAR);self.assertEqual([r['signal_time']//s.BAR for r in rows],[20,37])
    def test_shared_resolver_tp_and_exclusion(self):
        raw,f,btc,cfg=event();p=dict(cfg,hold=24,exit_type='TP3',policy='P')
        result=dict(status='RESOLVED',exit_time=23*s.BAR,exit=106.,reason='TP',gross_return=.06)
        with patch.object(s.canonical,'resolve',return_value=result) as resolver:rows,_,_=s.policy_rows('X',[p],raw,f,btc,0,100*s.BAR)
        self.assertEqual(resolver.call_args.args[0]['tp'],106.);self.assertAlmostEqual(rows[0]['net40_fraction'],.06-.002*2.06-.0002*2/96)
        with patch.object(s.canonical,'resolve',return_value=dict(status='ENTRY_MISMATCH')):rows,counts,bad=s.policy_rows('X',[p],raw,f,btc,0,100*s.BAR)
        self.assertFalse(rows);self.assertEqual(counts['P/ENTRY_MISMATCH'],1);self.assertEqual(bad[0]['status'],'ENTRY_MISMATCH')
    def test_current_and_future_price_cannot_change_prior_baselines(self):
        n=200;c=100+np.arange(n)*.01;t=np.arange(n,dtype=np.int64)*s.BAR;raw=[t,c.copy(),c+1,c-1,c.copy()];q=np.full(n,1e6)
        old=s.features(raw,q,q*.6);newraw=[x.copy() for x in raw];newraw[2][150:]*=2;newraw[3][150:]*=.5;nq=q.copy();nq[150:]*=100;new=s.features(newraw,nq,nq*.6)
        self.assertEqual(old['prior_atr'][150],new['prior_atr'][150]);self.assertAlmostEqual(new['volume_multiple'][150],100.)
        for key in old:np.testing.assert_allclose(old[key][:150],new[key][:150],equal_nan=True)

class SelectionTests(unittest.TestCase):
    def make(self,root,coverage=.95,bad_year=None,bad_kind=None,clustered=False,empty=False):
        p=s.policies()[0];rows=[]
        for year,n in ((2021,80),(2022,160),(2023,160)):
            start=int(pd.Timestamp(f'{year}-10-01' if year==2021 else f'{year}-01-01',tz='Asia/Seoul').timestamp()*1000)
            for i in range(n):
                ts=start+i*s.BAR if clustered else start+(i//2)*s.DAY+(i%2)*s.BAR
                row={k:0 for k in s.COLUMNS};price=-.001 if bad_year==year and bad_kind=='price' else .02;r=-.1 if bad_year==year and bad_kind in ('r','date') else .5
                row.update(symbol=f'X{i%10}',variant=p['policy'],policy=p['policy'],entry_time=ts,net40_fraction=price,net40_R=r,split='DEV');rows.append(row)
        for i in range(8):
            d=root/str(i);d.mkdir(parents=True);pd.DataFrame(rows if i==0 and not empty else [],columns=s.COLUMNS).to_csv(d/'independent_candidates.csv.gz',index=False)
            cov=[dict(symbol=f'C{i}',premium_year_coverage={str(y):dict(eligible=100,known=int(100*coverage)) for y in (2021,2022,2023)})]
            (d/'scan_meta.json').write_text(json.dumps(dict(complete=True,stage='DEV',counts={},coverage=cov)))
    def test_95percent_three_year_coverage_and_all96cells(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make(root/'p');s.select(root/'p',root/'o');d=json.loads((root/'o/selection.json').read_text());table=pd.read_csv(root/'o/development_policy_cells.csv')
            self.assertTrue(d['premium_coverage']['passed']);self.assertEqual(len(d['policies']),1);self.assertEqual(d['union_name'],'PREMIUM_ABSORPTION_UNION');self.assertEqual(len(table),96)
    def test_below_coverage_is_input_failure_not_alpha(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make(root/'p',coverage=.94);s.select(root/'p',root/'o');d=json.loads((root/'o/selection.json').read_text())
            self.assertEqual(d['status'],'SOURCE_COVERAGE_INSUFFICIENT');self.assertFalse(d['policies'])
    def test_partial2021_price_r_date_and_frequency_are_hard_gates(self):
        for bad_kind in ('price','r','date'):
            with self.subTest(kind=bad_kind),tempfile.TemporaryDirectory() as td:
                root=Path(td);self.make(root/'p',bad_year=2021,bad_kind=bad_kind);s.select(root/'p',root/'o');self.assertFalse(json.loads((root/'o/selection.json').read_text())['policies'])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make(root/'p',clustered=True);s.select(root/'p',root/'o');self.assertFalse(json.loads((root/'o/selection.json').read_text())['policies'])
    def test_2022_and_2023_price_and_r_still_hard_gates(self):
        for year in (2022,2023):
            for kind in ('price','r'):
                with self.subTest(year=year,kind=kind),tempfile.TemporaryDirectory() as td:
                    root=Path(td);self.make(root/'p',bad_year=year,bad_kind=kind);s.select(root/'p',root/'o');self.assertFalse(json.loads((root/'o/selection.json').read_text())['policies'])
    def test_duplicate_coverage_symbol_and_empty_cells(self):
        row=dict(symbol='X',premium_year_coverage={'2021':dict(eligible=100,known=95)})
        with self.assertRaises(ValueError):s.premium_coverage([dict(coverage=[row]),dict(coverage=[row])])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.make(root/'p',empty=True);s.select(root/'p',root/'o');self.assertTrue(pd.read_csv(root/'o/development_policy_cells.csv').n.eq(0).all())

if __name__=='__main__':unittest.main()
