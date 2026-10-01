"""Causality, baselines, coverage and predeclared V4 screening."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import compression_expansion_v4 as s

def raw(n=3500):
    t=np.arange(n,dtype=np.int64)*s.BAR
    c=100+np.arange(n)*.002+np.sin(np.arange(n)/20)*.1
    return [t,c-.05,c+.2,c-.2,c.copy()]

def fixture(side=1):
    p=raw(400);q=np.ones(400)*1e6;f=s.features(p,q,q*.5)
    for key in ('eligible',):f[key][:]=False
    f['eligible'][200]=True
    c=p[4]
    p[1][200]=c[200]-side*.1
    f['compression'][200]=.4;f['volume_multiple'][200]=3
    f['buy_share'][200]=.6 if side==1 else .4
    f['clv'][200]=.9 if side==1 else .1
    f['r1'][200]=side*.005
    f['prevh16'][200]=c[200]-.1
    f['prevl16'][200]=c[200]+.1
    f['prior_ema50'][200]=c[200]-side
    f['prior_ema_slope'][200]=side*.1
    btc={'r16':np.full(400,side*.01)}
    cfg=dict(key='X',side=side,window=16,compression=.5,btc='ALIGN4H')
    return p,f,btc,cfg

class ExpansionTests(unittest.TestCase):
    def test_frozen_grid_has_96_unique_policies(self):
        self.assertEqual(len(s.configurations()),16)
        self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)

    def test_future_changes_cannot_modify_past_features(self):
        p=raw();q=np.ones(3500)*1e6;b=q*.5
        f=s.features(p,q,b)
        changed=[a.copy() for a in p];qq=q.copy();bb=b.copy()
        for a in changed[1:]:a[3300:]*=3
        qq[3300:]*=20;bb[3300:]*=5
        g=s.features(changed,qq,bb)
        for k in f:np.testing.assert_allclose(f[k][:3300],g[k][:3300],equal_nan=True)

    def test_breakout_candle_cannot_change_its_prior_baselines(self):
        p=raw(400);q=np.ones(400)*1e6
        f=s.features(p,q,q*.5)
        p[2][200]*=4;p[3][200]/=4;p[4][200]*=2;q[200]*=100
        g=s.features(p,q,q*.5)
        for k in ('compression','prior_atr','prior_ema50','prior_ema_slope','prevh16','prevh48','prevl48'):
            self.assertEqual(f[k][200],g[k][200])
        self.assertGreater(g['volume_multiple'][200],90)

    def test_gap_restarts_prior_rolling_baselines(self):
        p=raw(400);p[0][200:]+=s.BAR;q=np.ones(400)*1e6
        f=s.features(p,q,q*.5)
        self.assertTrue(np.isnan(f['compression'][200:297]).all())
        self.assertTrue(np.isnan(f['prior_ema50'][200:250]).all())
        self.assertTrue(np.isnan(f['prevh48'][200:248]).all())

    def test_long_and_short_have_mirrored_qualification(self):
        for side in (1,-1):
            p,f,b,cfg=fixture(side)
            self.assertEqual(np.flatnonzero(s.mask(cfg,p,f,b)).tolist(),[200])

    def test_wrong_flow_rejects_apparent_breakout(self):
        for side in (1,-1):
            p,f,b,cfg=fixture(side);f['buy_share'][200]=.5
            self.assertFalse(s.mask(cfg,p,f,b).any())

    def test_volume_without_prior_compression_does_not_qualify(self):
        p,f,b,cfg=fixture();f['compression'][200]=.8
        self.assertFalse(s.mask(cfg,p,f,b).any())

    def test_violent_shock_does_not_qualify_as_moderate_expansion(self):
        p,f,b,cfg=fixture();f['r1'][200]=.031
        self.assertFalse(s.mask(cfg,p,f,b).any())

    def test_btc_alignment_does_not_fill_missing_context_with_zero(self):
        p,f,b,cfg=fixture();b['r16'][200]=np.nan
        self.assertFalse(s.mask(cfg,p,f,b).any())
        cfg['btc']='ANY'
        self.assertTrue(s.mask(cfg,p,f,b)[200])

    def test_structural_stop_uses_prior_atr_and_actual_entry(self):
        p,f,b,cfg=fixture();p[1][201]=101.
        f['prior_atr'][200]=1.;f['low5'][200]=97.
        m=np.zeros(400,bool);m[200]=True
        with patch.object(s,'mask',return_value=m):
            entries,ex=s.intents('X',cfg,p,f,b,0,400*s.BAR)
        self.assertEqual(len(entries),1);self.assertFalse(ex)
        self.assertEqual(entries[0]['entry_time'],201*s.BAR)
        self.assertEqual(entries[0]['decision_time'],201*s.BAR)
        self.assertAlmostEqual(entries[0]['sl'],96.75)
        self.assertAlmostEqual(entries[0]['risk_pct'],(101-96.75)/101)

    def test_invalid_fill_side_stop_is_excluded(self):
        p,f,b,cfg=fixture();f['low5'][200]=200.;f['prior_atr'][200]=1.
        m=np.zeros(400,bool);m[200]=True
        with patch.object(s,'mask',return_value=m):
            entries,ex=s.intents('X',cfg,p,f,b,0,400*s.BAR)
        self.assertFalse(entries);self.assertEqual(ex['INVALID_STRUCTURAL_STOP'],1)

    def test_fixed_cooldown_is_not_exit_conditioned(self):
        p,f,b,cfg=fixture()
        for k in ('prior_atr','volume_multiple','compression','buy_share','prevh16','low5'):
            f[k][:]=f[k][200]
        f['low5'][:]=97.
        m=np.zeros(400,bool);m[[200,201,215,216,232]]=True
        with patch.object(s,'mask',return_value=m):
            entries,_=s.intents('X',cfg,p,f,b,0,400*s.BAR)
        self.assertEqual([t['entry_time'] for t in entries],[201*s.BAR,217*s.BAR,233*s.BAR])

    def test_empty_source_fails_before_any_btc_or_minute_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            with self.assertRaisesRegex(ValueError,'empty frozen source'):
                s.scan(d,d/'missing-btc',d/'out',d/'cache','DEV')

    def create_parts(self,root,cluster=False,incomplete=False):
        policy=s.policies()[0];rows=[]
        for year in (2022,2023):
            base=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp()*1000)
            for i in range(160):
                ts=base+i*s.BAR if cluster else base+(i//2)*s.DAY+(i%2)*s.BAR
                r={k:0 for k in s.COLUMNS}
                r.update(symbol=f'X{i%10}',variant=policy['policy'],policy=policy['policy'],
                         entry_time=ts,net40_fraction=.02,net40_R=.5,split='DEV')
                rows.append(r)
        for i in range(8):
            dest=root/f'part-{i}';dest.mkdir()
            pd.DataFrame(rows if i==0 else [],columns=s.COLUMNS).to_csv(dest/'independent_candidates.csv.gz',index=False,compression='gzip')
            (dest/'scan_meta.json').write_text(json.dumps(dict(stage='DEV',complete=not(incomplete and i==7),counts={},coverage=[])))

    def test_all_96_cells_including_zero_cells_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts)
            s.select(parts,root/'out')
            table=pd.read_csv(root/'out/development_policy_cells.csv')
            chosen=json.loads((root/'out/selection.json').read_text())
            self.assertEqual(len(table),96)
            self.assertEqual((table.n==0).sum(),95)
            self.assertEqual(len(chosen['policies']),1)
            self.assertEqual(chosen['policies'][0]['hold'],24)

    def test_many_clustered_trades_cannot_pass_daily_frequency_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts,cluster=True)
            s.select(parts,root/'out')
            self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_incomplete_part_cannot_enter_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);parts=root/'parts';parts.mkdir();self.create_parts(parts,incomplete=True)
            with self.assertRaisesRegex(ValueError,'incomplete development'):
                s.select(parts,root/'out')

if __name__=='__main__':unittest.main()

