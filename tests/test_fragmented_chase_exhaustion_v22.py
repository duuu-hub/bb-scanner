import unittest
from unittest import mock
import numpy as np

from scripts import day_edge_lab as base
from scripts import fragmented_chase_exhaustion_v22 as v22


class TestFragmentedChaseExhaustionV22(unittest.TestCase):
    def fixture(self, side=1):
        n=110;t=base.START+np.arange(n,dtype=np.int64)*base.BAR
        o=np.full(n,100.0);h=np.full(n,100.4);l=np.full(n,99.6);c=np.full(n,100.0)
        i=100
        if side==1:
            o[i]=100;c[i]=96;h[i]=100.2;l[i]=95.8
            o[i+1]=96;c[i+1]=98;h[i+1]=98.2;l[i+1]=95.7;o[i+2]=98
        else:
            o[i]=100;c[i]=104;h[i]=104.2;l[i]=99.8
            o[i+1]=104;c[i+1]=102;h[i+1]=104.3;l[i+1]=101.8;o[i+2]=102
        raw=(t,o,h,l,c)
        f=dict(eligible=np.ones(n,bool),prior_atr=np.full(n,2.0),buy_share=np.full(n,.5),
            volume_multiple=np.ones(n),trade_count_multiple=np.ones(n),fragmentation_ratio=np.ones(n),
            trade_count=np.full(n,1000.),average_trade_value=np.full(n,1000.),
            prior_average_trade_value=np.full(n,1000.),r1=np.zeros(n),clv=np.full(n,.5))
        f['volume_multiple'][i]=2;f['trade_count_multiple'][i]=2.5;f['fragmentation_ratio'][i]=.6
        f['trade_count'][i]=2500;f['average_trade_value'][i]=600
        f['buy_share'][i]=.3 if side==1 else .7
        btc=dict(close=np.full(n,50000.),r1=np.zeros(n))
        cfg=next(x for x in v22.configurations() if x['side']==side and x['shock_atr']==1.5 and x['fragment_ratio']==.65)
        return raw,f,btc,cfg,i

    def test_grid(self):
        self.assertEqual(len(v22.configurations()),16);self.assertEqual(len(v22.policies()),96)

    def test_btc_context_uses_source_loader_not_trade_count_validator(self):
        names=('CONTEXT','COLUMNS','LOG_PREFIX','configurations','policies','load',
            'features','intents','policy_rows','BTC_LOAD','BTC_FEATURES')
        original={name:getattr(v22.engine,name) for name in names}
        try:
            v22.bind_engine()
            self.assertIs(v22.engine.BTC_LOAD,v22.SOURCE_LOAD)
            self.assertIs(v22.engine.BTC_FEATURES,v22.SOURCE_FEATURES)
            self.assertIs(v22.engine.load,v22.load)
        finally:
            for name,value in original.items():
                setattr(v22.engine,name,value)

    def test_scan_forwards_v22_frozen_context_by_default(self):
        with mock.patch.object(v22.engine, "scan") as scan:
            v22.scan("data", "btc", "out", "cache", "DEV", "source-check")
        self.assertEqual(scan.call_args.kwargs["context_path"], v22.CONTEXT)

    def test_long_after_down_chase(self):
        raw,f,btc,cfg,i=self.fixture(1);self.assertTrue(v22.event_mask(cfg,raw,f)[i])
        rows,_=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['side'],1)
        self.assertEqual(rows[0]['entry_time'],raw[0][i+2])

    def test_short_after_up_chase(self):
        raw,f,btc,cfg,i=self.fixture(-1);self.assertTrue(v22.event_mask(cfg,raw,f)[i])
        rows,_=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['side'],-1)

    def test_fragmentation_is_mandatory(self):
        raw,f,_,cfg,i=self.fixture(1);f['fragmentation_ratio'][i]=.651
        self.assertFalse(v22.event_mask(cfg,raw,f)[i])

    def test_trade_count_explosion_is_mandatory(self):
        raw,f,_,cfg,i=self.fixture(1);f['trade_count_multiple'][i]=1.99
        self.assertFalse(v22.event_mask(cfg,raw,f)[i])

    def test_turnover_gate(self):
        raw,f,_,cfg,i=self.fixture(1);f['volume_multiple'][i]=1.49
        self.assertFalse(v22.event_mask(cfg,raw,f)[i])

    def test_taker_flow_gate(self):
        raw,f,_,cfg,i=self.fixture(1);f['buy_share'][i]=.401
        self.assertFalse(v22.event_mask(cfg,raw,f)[i])

    def test_outer_quartile_close(self):
        raw,f,_,cfg,i=self.fixture(1);raw[3][i]=94
        self.assertFalse(v22.event_mask(cfg,raw,f)[i])

    def test_confirmation_is_separate_closed_bar(self):
        raw,f,btc,cfg,i=self.fixture(1);raw[4][i+1]=96.5
        rows,counts=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertFalse(rows);self.assertEqual(counts['CONFIRMATION_FAILED'],1)

    def test_confirmation_adverse_extension_limit(self):
        raw,f,btc,cfg,i=self.fixture(1);raw[3][i+1]=95.59
        rows,_=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertFalse(rows)

    def test_favorable_gap_excluded(self):
        raw,f,btc,cfg,i=self.fixture(1);raw[1][i+2]=99
        rows,counts=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertFalse(rows);self.assertEqual(counts['ENTRY_CATCHUP_GAP'],1)

    def test_adverse_gap_retained(self):
        raw,f,btc,cfg,i=self.fixture(1);raw[1][i+2]=97
        rows,_=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertLess(rows[0]['known_entry_gap'],0)

    def test_gap_breaks_confirmation_path(self):
        raw,f,btc,cfg,i=self.fixture(1);raw[0][i+1]+=1
        rows,counts=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertFalse(rows);self.assertEqual(counts['CONFIRMATION_OR_ENTRY_PATH_GAP'],1)

    def test_future_perturbation_does_not_change_intent(self):
        raw,f,btc,cfg,i=self.fixture(1);a,_=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        raw[1][i+5]=500;raw[2][i+5]=600;raw[3][i+5]=1;raw[4][i+5]=400
        f['volume_multiple'][i+5]=999;f['fragmentation_ratio'][i+5]=0
        b,_=v22.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        keys=('entry_time','entry','sl','event_midpoint','confirmation_time')
        self.assertEqual([{k:r[k] for k in keys} for r in a],[{k:r[k] for k in keys} for r in b])

    def test_features_are_shifted_and_reset_on_gap(self):
        n=205;t=base.START+np.arange(n,dtype=np.int64)*base.BAR;t[150:]+=base.BAR
        close=np.full(n,100.);raw=(t,close.copy(),close*1.001,close*.999,close.copy())
        q=np.full(n,1e6);aux=np.column_stack((q*.5,np.full(n,1000.)))
        f=v22.features(raw,q,aux)
        self.assertTrue(np.isfinite(f['prior_average_trade_value'][96]))
        self.assertTrue(np.isnan(f['prior_average_trade_value'][150]))

    def test_noninteger_trade_count_rejected(self):
        n=100;t=base.START+np.arange(n,dtype=np.int64)*base.BAR;c=np.full(n,100.)
        raw=(t,c.copy(),c*1.001,c*.999,c.copy());q=np.full(n,1e6)
        aux=np.column_stack((q*.5,np.full(n,1000.)));aux[3,1]=1000.5
        with self.assertRaises(ValueError):v22.features(raw,q,aux)

    def test_zero_trade_bar_is_excluded_and_resets_rolling_baselines(self):
        n=205;t=base.START+np.arange(n,dtype=np.int64)*base.BAR;c=np.full(n,100.)
        raw=(t,c.copy(),c*1.001,c*.999,c.copy());q=np.full(n,1e6)
        aux=np.column_stack((q*.5,np.full(n,1000.)));aux[100,1]=0
        f=v22.features(raw,q,aux)
        self.assertTrue(np.isnan(f['average_trade_value'][100]))
        self.assertFalse(f['eligible'][100])
        self.assertTrue(np.isnan(f['prior_average_trade_value'][196]))
        self.assertTrue(np.isfinite(f['prior_average_trade_value'][197]))


if __name__=='__main__':unittest.main()
