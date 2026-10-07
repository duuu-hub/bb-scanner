import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from scripts import daily_channel_breakout_v25 as v25


def fixture(side=1, days=48):
    n=days*96;t=v25.prior.base.START+np.arange(n,dtype=np.int64)*v25.BAR
    c=np.full(n,100.);o=c.copy();h=np.full(n,101.);l=np.full(n,99.)
    i=36*96-1;c[i]=103. if side==1 else 97.
    h[i]=max(h[i],c[i]+1);l[i]=min(l[i],c[i]-1)
    o[i+1]=106. if side==1 else 94.
    h[i+1]=max(h[i+1],o[i+1]+1);l[i+1]=min(l[i+1],o[i+1]-1)
    raw=(t,o,h,l,c);q=np.full(n,1_000_000.);f=v25.features(raw,q,q*.5)
    btc=dict(close=c.copy(),r1=np.zeros(n),daily_r20=np.full(n,side*.1))
    cfg=next(p for p in v25.configurations() if p['side']==side and p['channel_days']==5
             and p['btc_regime']=='ANY' and p['stop_atr']==1.5)
    return i,cfg,raw,q,f,btc


class DailyChannelV25Tests(unittest.TestCase):
    def seeds(self, parts):
        i,cfg,raw,q,f,btc=parts
        rows,counts=v25.intents('XUSDT',cfg,raw,f,btc,v25.prior.base.START,int(raw[0][-1]+v25.BAR))
        return rows,counts

    def test_frozen_grid_holds_and_targets(self):
        self.assertEqual(len(v25.configurations()),16);self.assertEqual(len(v25.policies()),96)
        self.assertEqual(set(p['hold'] for p in v25.policies()),{192,672})
        self.assertEqual(set(p['exit_type'] for p in v25.policies()),{'R10','R20','R40'})

    def test_complete_utc_day_has_exact_96_bars(self):
        i,cfg,raw,q,f,btc=fixture();d=f['_daily']
        self.assertTrue(d.valid.all());self.assertEqual(len(d),48)
        self.assertEqual(int(d.iloc[35].bar_index),i)

    def test_incomplete_last_day_cannot_signal(self):
        i,cfg,raw,q,f,btc=fixture();short=tuple(a[:i] for a in raw)
        d=v25.complete_days(short);self.assertFalse(d.valid.iloc[-1])
        self.assertTrue(pd.isna(d.upper5.iloc[-1]))

    def test_partial_first_day_cannot_seed_rolls(self):
        i,cfg,raw,q,f,btc=fixture();d=v25.complete_days(tuple(a[7:] for a in raw))
        self.assertFalse(d.valid.iloc[0]);self.assertTrue(pd.isna(d.atr20.iloc[21]))
        self.assertTrue(np.isfinite(d.atr20.iloc[22]))

    def test_missing_day_restarts_rolling_history(self):
        i,cfg,raw,q,f,btc=fixture(days=64)
        keep=(raw[0]//v25.DAY)!=(raw[0][0]//v25.DAY+30)
        d=v25.complete_days(tuple(a[keep] for a in raw));by_day=d.set_index('day')
        self.assertTrue(pd.isna(by_day.loc[raw[0][0]//v25.DAY+51,'atr20']))
        self.assertTrue(np.isfinite(by_day.loc[raw[0][0]//v25.DAY+52,'atr20']))

    def test_missing_bar_invalidates_day_and_channels(self):
        i,cfg,raw,q,f,btc=fixture();keep=np.ones(len(q),bool);keep[34*96+10]=False
        rr=tuple(a[keep] for a in raw);ff=v25.features(rr,q[keep],q[keep]*.5)
        self.assertFalse(ff['_daily'].valid.iloc[34])
        self.assertEqual(self.seeds((i,cfg,rr,q[keep],ff,{k:a[keep] for k,a in btc.items()}))[0],[])

    def test_duplicate_timestamp_is_rejected(self):
        i,cfg,raw,q,f,btc=fixture();t=raw[0].copy();t[1]=t[0]
        with self.assertRaises(ValueError):v25.complete_days((t,*raw[1:]))

    def test_signal_next_utc_open_and_prior_only_atr_channel(self):
        i,cfg,raw,q,f,btc=fixture();rows,_=self.seeds((i,cfg,raw,q,f,btc))
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual(row['entry_time']%v25.DAY,0)
        self.assertEqual(row['entry_time'],int(raw[0][i]+v25.BAR))
        self.assertEqual(row['channel_level'],101.)
        self.assertAlmostEqual(row['daily_atr20'],2.)
        self.assertLess(row['channel_input_cutoff'],row['decision_time'])

    def test_current_day_high_cannot_raise_breakout_channel_or_atr(self):
        i,cfg,raw,q,f,btc=fixture();changed=tuple(a.copy() for a in raw);changed[2][35*96:i+1]=300.
        ff=v25.features(changed,q,q*.5);rows,_=self.seeds((i,cfg,changed,q,ff,btc))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['channel_level'],101.)
        self.assertAlmostEqual(rows[0]['daily_atr20'],2.)

    def test_short_breakout_symmetry(self):
        rows,_=self.seeds(fixture(-1));self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['side'],-1);self.assertEqual(rows[0]['channel_level'],99.)
        self.assertEqual(rows[0]['sl'],rows[0]['entry']+3.)

    def test_future_ohlc_perturbation_cannot_change_old_intent(self):
        parts=fixture();i,cfg,raw,q,f,btc=parts;before=self.seeds(parts)[0]
        changed=tuple(a.copy() for a in raw)
        for k in range(1,5):changed[k][i+2:]*=2
        ff=v25.features(changed,q,q*.5);after=self.seeds((i,cfg,changed,q,ff,btc))[0]
        self.assertEqual(before[0],after[0])

    def test_actual_adverse_open_gap_is_retained_in_stop_and_risk(self):
        rows,_=self.seeds(fixture());row=rows[0]
        self.assertEqual(row['entry'],106.);self.assertEqual(row['sl'],103.)
        self.assertAlmostEqual(row['risk_pct'],3/106)

    def test_breakout_onset_blocks_continued_same_direction(self):
        i,cfg,raw,q,f,btc=fixture();rr=tuple(a.copy() for a in raw)
        rr[4][i+96]=110.;rr[2][i+96]=111.
        ff=v25.features(rr,q,q*.5);rows,_=self.seeds((i,cfg,rr,q,ff,btc))
        self.assertEqual(len(rows),1)

    def test_btc_alignment_rejects_opposed_or_unavailable_regime(self):
        i,cfg,raw,q,f,btc=fixture();cfg=dict(cfg,btc_regime='ALIGN20')
        for val in (-.1,np.nan):
            bb=dict(btc,daily_r20=np.full(len(q),val));rows,counts=self.seeds((i,cfg,raw,q,f,bb))
            self.assertEqual(rows,[])
            self.assertEqual(sum(counts.values()),1)

    def test_btc_regime_matches_exact_daily_time_without_fill(self):
        i,cfg,raw,q,f,btc=fixture();out=v25.align_context(raw[0],raw,f)
        self.assertAlmostEqual(out['daily_r20'][i],.03)
        self.assertTrue(np.isnan(out['daily_r20'][i-1]))
        changed=tuple(a[:-96].copy() for a in raw);changed_f=v25.features(changed,q[:-96],q[:-96]*.5)
        out2=v25.align_context(raw[0],changed,changed_f)
        self.assertTrue(np.isnan(out2['daily_r20'][-1]))

    def test_stop_cap_is_an_explicit_exclusion(self):
        i,cfg,raw,q,f,btc=fixture();f['_daily'].loc[35,'atr20']=30.
        rows,counts=self.seeds((i,cfg,raw,q,f,btc));self.assertEqual(rows,[])
        self.assertEqual(counts['STOP_ABOVE_25PCT'],1)

    def test_liquidity_and_observed_age_are_causal(self):
        i,cfg,raw,q,f,btc=fixture();qq=np.full(len(q),100.)
        ff=v25.features(raw,qq,qq*.5);self.assertEqual(self.seeds((i,cfg,raw,qq,ff,btc))[0],[])
        self.assertFalse(f['eligible'][29*96]);self.assertTrue(f['eligible'][30*96])

    def test_engine_globals_restore_even_on_failure(self):
        before={k:getattr(v25.ENGINE,k) for k in ['CONTEXT','policies','align_context','load','features']}
        with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
            with v25.bound_engine():
                self.assertEqual(v25.ENGINE.CONTEXT,v25.CONTEXT)
                raise RuntimeError('synthetic failure')
        self.assertEqual(before,{k:getattr(v25.ENGINE,k) for k in before})

    def test_policy_target_uses_actual_risk_and_all_registered_holds(self):
        i,cfg,raw,q,f,btc=fixture();chosen=[p for p in v25.policies() if p['key']==cfg['key']]
        seen=[]
        def result(trade,*args):
            seen.append(trade.copy())
            return dict(status='RESOLVED',exit_time=trade['entry_time']+v25.BAR,
                        exit=trade['entry'],reason='TIME',gross_return=0.)
        with patch.object(v25.ENGINE.canonical,'resolve',side_effect=result):
            rows,counts,bad=v25.policy_rows('XUSDT',chosen,raw,f,btc,v25.prior.base.START,int(raw[0][-1]+v25.BAR))
        self.assertEqual(len(rows),6);self.assertEqual(bad,[])
        for trade,p in zip(seen,chosen):
            multiple={'R10':1,'R20':2,'R40':4}[p['exit_type']]
            self.assertAlmostEqual(trade['tp'],106+multiple*3)
            self.assertEqual(trade['max_hold_bars'],p['hold'])

    def test_real_canonical_timeout_and_gap_contract_for_week_hold(self):
        i,cfg,raw,q,f,btc=fixture();row=self.seeds((i,cfg,raw,q,f,btc))[0][0]
        trade=dict(row,tp=1000.,max_hold_bars=672,sl=1.)
        result=v25.ENGINE.canonical.resolve(trade,raw,f,'TP2',int(raw[0][-1]+v25.BAR))
        self.assertEqual(result['status'],'RESOLVED');self.assertEqual(result['reason'],'TIME')
        self.assertEqual(result['exit_time'],trade['entry_time']+7*v25.DAY)
        changed=tuple(a.copy() for a in raw);changed[0][i+30:]+=v25.BAR
        self.assertEqual(v25.ENGINE.canonical.resolve(trade,changed,f,'TP2',int(changed[0][-1]+v25.BAR))['status'],'DATA_GAP')
