"""V5 economic event, causality, annual screening and source integrity."""
import hashlib,io,json,tempfile,unittest,urllib.error,zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import liquidity_sweep_v5 as s
from scripts import official_minute_provenance as minute

def raw(n=400):
    t=np.arange(n,dtype=np.int64)*s.BAR
    c=100+np.arange(n)*.002+np.sin(np.arange(n)/20)*.1
    return [t,c-.05,c+.2,c-.2,c.copy()]

def fixture(side=-1):
    p=raw();q=np.ones(400)*1e6;f=s.features(p,q,q*.5)
    f['eligible'][:]=False;f['eligible'][200]=True
    for key in ('prevh48','prevh96'):f[key][200]=101.
    for key in ('prevl48','prevl96'):f[key][200]=99.
    f['prior_atr'][200]=1.;f['volume_multiple'][200]=2.
    if side==-1:
        p[1][200]=100.8;p[2][200]=102.;p[3][200]=100.;p[4][200]=100.2
        f['upper_wick'][200]=.6;f['buy_share'][200]=.6
    else:
        p[1][200]=99.2;p[2][200]=100.;p[3][200]=98.;p[4][200]=99.8
        f['lower_wick'][200]=.6;f['buy_share'][200]=.4
    cfg=dict(key='X',side=side,window=48,flow=.55,btc='ALIGN4H')
    return p,f,{'r16':np.full(400,side*.01)},cfg

def zip_fixture(header=False,gap=False,bad_geometry=False,n=15):
    start=int(pd.Timestamp('2022-01-01',tz='UTC').timestamp()*1000);lines=[]
    if header:lines.append('open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore')
    for i in range(n):
        t=start+(i+(1 if gap and i>=5 else 0))*60000
        h=99 if bad_geometry else 101
        lines.append(f'{t},100,{h},99,100,1,{t+59999},100,1,0.5,50,0')
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('XUSDT-1m-2022-01.csv','\n'.join(lines))
    rawzip=bio.getvalue();checksum=(hashlib.sha256(rawzip).hexdigest()+'  XUSDT-1m-2022-01.zip').encode()
    return start,rawzip,checksum

class SweepTests(unittest.TestCase):
    def test_grid_is_frozen_and_unique(self):
        self.assertEqual(len(s.configurations()),16);self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)

    def test_sides_require_opposite_aggressive_flow(self):
        for side in (-1,1):
            p,f,b,c=fixture(side);self.assertEqual(np.flatnonzero(s.mask(c,p,f,b)).tolist(),[200])
            f['buy_share'][200]=.4 if side==-1 else .6
            self.assertFalse(s.mask(c,p,f,b).any())

    def test_price_must_reject_inside_known_range(self):
        p,f,b,c=fixture(-1);p[4][200]=101.1
        self.assertFalse(s.mask(c,p,f,b).any())
        p,f,b,c=fixture(1);p[4][200]=98.9
        self.assertFalse(s.mask(c,p,f,b).any())

    def test_sweep_has_prior_atr_minimum_distance(self):
        p,f,b,c=fixture(-1);p[2][200]=101.09
        self.assertFalse(s.mask(c,p,f,b).any())

    def test_flow_threshold_boundary_is_symmetric(self):
        p,f,b,c=fixture(1);f['buy_share'][200]=.45
        self.assertTrue(s.mask(c,p,f,b)[200])
        p,f,b,c=fixture(-1);f['buy_share'][200]=.55
        self.assertTrue(s.mask(c,p,f,b)[200])

    def test_wick_body_and_volume_must_agree(self):
        p,f,b,c=fixture(-1);f['upper_wick'][200]=.49
        self.assertFalse(s.mask(c,p,f,b).any())
        p,f,b,c=fixture(-1);p[4][200]=100.9
        self.assertFalse(s.mask(c,p,f,b).any())
        p,f,b,c=fixture(-1);f['volume_multiple'][200]=1.49
        self.assertFalse(s.mask(c,p,f,b).any())

    def test_btc_missing_fails_alignment_but_any_ignores_it(self):
        p,f,b,c=fixture();b['r16'][200]=np.nan
        self.assertFalse(s.mask(c,p,f,b).any());c['btc']='ANY'
        self.assertTrue(s.mask(c,p,f,b)[200])

    def test_prior_baselines_cannot_use_current_candle(self):
        p=raw();q=np.ones(400)*1e6;f=s.features(p,q,q*.5)
        p[2][200]*=4;p[3][200]/=4;p[4][200]*=2;q[200]*=100
        g=s.features(p,q,q*.5)
        for k in ('prior_atr','volume_multiple','prevh48','prevl48','prevh96','prevl96'):
            if k=='volume_multiple':self.assertGreater(g[k][200],99)
            else:self.assertEqual(f[k][200],g[k][200])

    def test_future_perturbation_preserves_every_past_feature(self):
        p=raw(3500);q=np.ones(3500)*1e6;f=s.features(p,q,q*.5)
        changed=[x.copy() for x in p];qq=q.copy()
        for x in changed[1:]:x[3300:]*=4
        qq[3300:]*=10
        # Buy-share is itself changed here only in the future.
        g=s.features(changed,qq,np.r_[q[:3300]*.5,qq[3300:]*.4])
        for k in f:np.testing.assert_allclose(f[k][:3300],g[k][:3300],equal_nan=True)

    def test_gaps_reset_range_and_volume_history(self):
        p=raw();p[0][200:]+=s.BAR;q=np.ones(400)*1e6;f=s.features(p,q,q*.5)
        self.assertTrue(np.isnan(f['prevh96'][200:296]).all())
        self.assertTrue(np.isnan(f['volume_multiple'][200:296]).all())
        self.assertTrue(np.isnan(f['prior_atr'][200:215]).all())

    def test_actual_entry_and_closed_sweep_anchored_sl(self):
        for side in (-1,1):
            p,f,b,c=fixture(side);p[1][201]=101.
            entries,ex=s.intents('XUSDT',c,p,f,b,0,400*s.BAR)
            self.assertEqual(len(entries),1);self.assertFalse(ex)
            e=entries[0];self.assertEqual(e['entry_time'],201*s.BAR)
            self.assertEqual(e['decision_time'],e['entry_time'])
            stop=97.75 if side==1 else 102.25
            self.assertAlmostEqual(e['sl'],stop)
            self.assertAlmostEqual(e['risk_pct'],abs(101-stop)/101)

    def test_invalid_gap_fill_is_not_a_valid_stop(self):
        p,f,b,c=fixture(-1);p[1][201]=104.
        entries,ex=s.intents('XUSDT',c,p,f,b,0,400*s.BAR)
        self.assertFalse(entries);self.assertEqual(ex['INVALID_STRUCTURAL_STOP'],1)

    def test_minimum_stop_and_above_eight_percent_exclusion(self):
        p,f,b,c=fixture(-1);p[1][201]=102.2
        entries,ex=s.intents('XUSDT',c,p,f,b,0,400*s.BAR)
        self.assertFalse(ex);self.assertAlmostEqual(entries[0]['risk_pct'],.005)
        p,f,b,c=fixture(-1);p[1][201]=90.
        entries,ex=s.intents('XUSDT',c,p,f,b,0,400*s.BAR)
        self.assertFalse(entries);self.assertEqual(ex['STOP_ABOVE_8PCT'],1)

    def test_cooldown_uses_intents_not_future_outcomes(self):
        p,f,b,c=fixture(-1);f['prior_atr'][:]=1.;f['volume_multiple'][:]=2.
        f['prevh48'][:]=101.;f['prevl48'][:]=99.
        m=np.zeros(400,bool);m[[200,201,215,216,232]]=True
        with patch.object(s,'mask',return_value=m):
            entries,_=s.intents('XUSDT',c,p,f,b,0,400*s.BAR)
        self.assertEqual([e['entry_time'] for e in entries],[201*s.BAR,217*s.BAR,233*s.BAR])

    def create_parts(self,root,negative_price=False,cluster=False,incomplete=False):
        p=s.policies()[0];rows=[]
        for year in (2022,2023):
            start=int(pd.Timestamp(f'{year}-01-01',tz='Asia/Seoul').timestamp()*1000)
            for i in range(160):
                ts=start+i*s.BAR if cluster else start+(i//2)*s.DAY+(i%2)*s.BAR
                r={k:0 for k in s.COLUMNS}
                r.update(symbol=f'X{i%10}',variant=p['policy'],policy=p['policy'],entry_time=ts,
                    net40_fraction=-.001 if negative_price and year==2022 else .02,net40_R=.5,split='DEV')
                rows.append(r)
        for i in range(8):
            dest=root/f'part-{i}';dest.mkdir()
            pd.DataFrame(rows if i==0 else [],columns=s.COLUMNS).to_csv(dest/'independent_candidates.csv.gz',index=False,compression='gzip')
            (dest/'scan_meta.json').write_text(json.dumps(dict(stage='DEV',complete=not(incomplete and i==7),counts={},coverage=[])))

    def test_all_cells_preserved_and_year_is_kst(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts)
            s.select(parts,root/'out');d=pd.read_csv(root/'out/development_policy_cells.csv')
            self.assertEqual(len(d),96);self.assertEqual((d.n==0).sum(),95)
            chosen=json.loads((root/'out/selection.json').read_text())
            self.assertEqual(len(chosen['policies']),1);self.assertEqual(chosen['union_name'],'SWEEP_UNION')
            observed=d[d.n>0].iloc[0]
            self.assertEqual(observed.year_2022_n,160);self.assertEqual(observed.year_2023_n,160)

    def test_positive_r_cannot_hide_negative_annual_price_return(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts,negative_price=True)
            s.select(parts,root/'out')
            self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_clustered_entry_days_cannot_satisfy_frequency(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts,cluster=True)
            s.select(parts,root/'out');self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_incomplete_parts_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts,incomplete=True)
            with self.assertRaisesRegex(ValueError,'incomplete development'):
                s.select(parts,root/'out')

    def test_empty_source_stops_before_minute_retrieval(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaisesRegex(ValueError,'empty frozen source'):
                s.scan(root,root/'none',root/'out',root/'cache','DEV')

class MinuteTests(unittest.TestCase):
    def setUp(self):minute.INPUTS.clear();s.chronology.chronology.CACHE.clear()
    def tearDown(self):s.chronology.chronology.CACHE.clear();s.SLICE_DIR=None
    def test_official_header_and_headerless_values_agree(self):
        a,x,_=zip_fixture(False);_,y,_=zip_fixture(True)
        for u,v in zip(minute.parse_zip(x,'2022-01'),minute.parse_zip(y,'2022-01')):
            np.testing.assert_array_equal(u,v)

    def test_original_zip_checksum_then_verified_cache(self):
        ts,data,checksum=zip_fixture(True)
        with tempfile.TemporaryDirectory() as td:
            with patch.object(minute,'_get',side_effect=[data,checksum]) as get:
                result=minute.load_month('XUSDT',ts,Path(td))
                self.assertEqual(len(result[0]),15);self.assertEqual(get.call_count,2)
            meta=minute.INPUTS['XUSDT/2022-01']
            self.assertTrue(meta['checksum_verified']);self.assertEqual(meta['original_zip_sha256'],meta['official_checksum_sha256'])
            with patch.object(minute,'_get',side_effect=AssertionError('must use verified cache')):
                again=minute.load_month('XUSDT',ts,Path(td))
            for a,b in zip(result,again):np.testing.assert_array_equal(a,b)

    def test_legacy_cache_without_original_checksum_is_not_trusted(self):
        ts,data,checksum=zip_fixture()
        with tempfile.TemporaryDirectory() as td:
            np.savez_compressed(Path(td)/'XUSDT-2022-01.npz',t=np.array([ts]),o=[1.],h=[1.],l=[1.])
            with patch.object(minute,'_get',side_effect=[data,checksum]):
                got=minute.load_month('XUSDT',ts,Path(td))
            self.assertEqual(len(got[0]),15);self.assertEqual(got[1][0],100.)

    def test_verified_cache_corruption_fails_without_replacement(self):
        ts,data,checksum=zip_fixture()
        with tempfile.TemporaryDirectory() as td:
            with patch.object(minute,'_get',side_effect=[data,checksum]):minute.load_month('XUSDT',ts,Path(td))
            (Path(td)/'XUSDT-2022-01.npz').write_bytes(b'changed')
            with patch.object(minute,'_get',side_effect=AssertionError('no replacement')):
                with self.assertRaisesRegex(ValueError,'cache mismatch'):minute.load_month('XUSDT',ts,Path(td))

    def test_checksum_mismatch_is_explicit_gap(self):
        ts,data,_=zip_fixture()
        with tempfile.TemporaryDirectory() as td:
            with patch.object(minute,'_get',side_effect=[data,b'0'*64]):
                result=minute.load_month('XUSDT',ts,Path(td))
            self.assertEqual(result[0],'data_gap');self.assertIn('checksum mismatch',result[1])

    def test_definitive_missing_archive_and_checksum_excluded(self):
        ts,data,_=zip_fixture()
        for responses in ([None],[data,None]):
            with tempfile.TemporaryDirectory() as td:
                with patch.object(minute,'_get',side_effect=responses):
                    result=minute.load_month('XUSDT',ts,Path(td))
                self.assertEqual(result[0],'data_gap')

    def test_gapped_or_bad_geometry_official_archive_excluded(self):
        for bad in ('gap','bad_geometry'):
            ts,data,checksum=zip_fixture(**{bad:True})
            with tempfile.TemporaryDirectory() as td:
                with patch.object(minute,'_get',side_effect=[data,checksum]):
                    result=minute.load_month('XUSDT',ts,Path(td))
                self.assertEqual(result[0],'data_gap')

    def test_exhausted_transient_error_is_not_fabricated_data_gap(self):
        with patch.object(minute.urllib.request,'urlopen',side_effect=urllib.error.URLError('temporary')),patch.object(minute.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'transient official'):minute._get('https://data.binance.vision/example')

    def test_exact_authoritative_slice_and_provenance_are_preserved(self):
        ts,data,checksum=zip_fixture()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);s.SLICE_DIR=root/'slices';s.SLICE_DIR.mkdir()
            s.chronology.MINUTE_CACHE_DIR=root/'cache';s.MINUTE_INPUTS.clear();s.MINUTE_SLICES.clear()
            with patch.object(minute,'_get',side_effect=[data,checksum]):
                result=s.audited_minutes('XUSDT',ts)
            rec=s.MINUTE_SLICES['XUSDT/'+str(ts)]
            self.assertEqual(rec['status'],'VALIDATED_SLICE');self.assertEqual(rec['bars'],15)
            self.assertEqual(rec['npz_sha256'],minute.digest(s.SLICE_DIR/rec['file']))
            self.assertTrue(s.MINUTE_INPUTS['XUSDT/2022-01']['checksum_verified'])

    def test_shared_canonical_resolver_keeps_entry_minute_tp_only_as_loss(self):
        ts,data,checksum=zip_fixture()
        with tempfile.TemporaryDirectory() as td:
            s.chronology.MINUTE_CACHE_DIR=Path(td);s.MINUTE_INPUTS.clear();s.MINUTE_SLICES.clear()
            parent=[np.array([ts],dtype=np.int64),np.array([100.]),np.array([101.]),np.array([99.]),np.array([100.])]
            tr=dict(symbol='XUSDT',entry_index=0,entry_time=ts,side=1,entry=100.,tp=101.,sl=98.,max_hold_bars=24)
            with patch.object(minute,'_get',side_effect=[data,checksum]),patch.object(s.chronology.chronology,'one_min',s.audited_minutes):
                result=s.canonical.resolve(tr,parent,{},'TP2',ts+s.DAY)
            self.assertEqual(result['status'],'RESOLVED');self.assertEqual(result['reason'],'SL')
            self.assertEqual(result['exit'],98.);self.assertEqual(result['exit_time'],ts+60000)

    def test_shared_canonical_resolver_excludes_checksum_gap(self):
        ts,data,_=zip_fixture()
        with tempfile.TemporaryDirectory() as td:
            s.chronology.MINUTE_CACHE_DIR=Path(td);s.MINUTE_INPUTS.clear();s.MINUTE_SLICES.clear()
            parent=[np.array([ts],dtype=np.int64),np.array([100.]),np.array([101.]),np.array([99.]),np.array([100.])]
            tr=dict(symbol='XUSDT',entry_index=0,entry_time=ts,side=1,entry=100.,tp=101.,sl=98.,max_hold_bars=24)
            with patch.object(minute,'_get',side_effect=[data,b'0'*64]),patch.object(s.chronology.chronology,'one_min',s.audited_minutes):
                result=s.canonical.resolve(tr,parent,{},'TP2',ts+s.DAY)
            self.assertEqual(result['status'],'DATA_GAP');self.assertNotIn('gross_return',result)

if __name__=='__main__':unittest.main()
