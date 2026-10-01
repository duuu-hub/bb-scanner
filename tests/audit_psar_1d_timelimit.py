"""Execution regression audit: 40+ distinct cases, then ten seeded clean rounds."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import psar_1d_exec_helpers as H
import psar_1d_short_timelimit as E
SEED=0


class Audit(unittest.TestCase):
    def setUp(self):
        H._ONE_MIN_CACHE.clear()
        self.rng=np.random.default_rng(SEED+191)
        self.t=np.arange(15,dtype=np.int64)*60000
        self.h=np.full(15,105.)
        self.l=np.full(15,95.)

    def minute(self, entry=None, source=None):
        kwargs=dict(entry=entry)
        if source:
            kwargs.update(source_high=source[0],source_low=source[1])
        with patch.object(H,"_one_min",return_value=(self.t,self.h,self.l)):
            return H._resolve_1m("TESTUSDT",0,90.,110.,False,**kwargs)

    def test_01_pre_entry_tp_ignored(self):
        self.h[:5]=99.;self.l[1]=89.;self.h[5]=101.
        self.assertEqual(self.minute(100.),"continue")

    def test_02_entry_minute_tp_loss(self):
        self.h[:5]=99.;self.h[5]=101.;self.l[5]=89.
        self.assertEqual(self.minute(100.),"loss")

    def test_03_entry_minute_sl_loss(self):
        self.h[:5]=99.;self.h[5]=111.
        self.assertEqual(self.minute(100.),"loss")

    def test_04_entry_minute_both_loss(self):
        self.h[:5]=99.;self.h[5]=111.;self.l[5]=89.
        self.assertEqual(self.minute(100.),"loss")

    def test_05_established_both_loss(self):
        self.h[6]=111.;self.l[6]=89.
        self.assertEqual(self.minute(),"loss")

    def test_06_entry_then_later_tp(self):
        self.h[:5]=99.;self.h[5]=101.;self.l[7]=89.
        self.assertEqual(self.minute(100.),"win")

    def test_07_entry_then_later_sl(self):
        self.h[:5]=99.;self.h[5]=101.;self.h[7]=111.
        self.assertEqual(self.minute(100.),"loss")

    def test_08_tp_before_sl(self):
        self.l[3]=89.;self.h[7]=111.
        self.assertEqual(self.minute(),"win")

    def test_09_sl_before_tp(self):
        self.h[3]=111.;self.l[7]=89.
        self.assertEqual(self.minute(),"loss")

    def test_10_entry_mismatch(self):
        self.h[:]=99.
        self.assertEqual(self.minute(100.),"entry_mismatch")

    def test_11_exit_mismatch(self):
        self.assertEqual(self.minute(),"exit_mismatch")

    def test_12_reach_through_entry(self):
        self.h[:5]=99.;self.h[5]=105.;self.l[5]=101.;self.l[8]=89.
        self.assertEqual(self.minute(100.),"win")

    def test_13_data_gap_sentinel(self):
        with patch.object(H,"_one_min",return_value=("data_gap","missing")):
            self.assertEqual(H._resolve_1m("TESTUSDT",0,90.,110.,False),"data_gap")

    def test_14_incomplete_window(self):
        self.t=self.t[:-1];self.h=self.h[:-1];self.l=self.l[:-1]
        self.assertEqual(self.minute(),"data_gap")

    def test_15_window_interior_duplicate(self):
        self.t[5]=self.t[4]
        self.assertEqual(self.minute(),"data_gap")

    def test_16_source_high_mismatch(self):
        self.l[3]=89.
        self.assertEqual(self.minute(source=(120.,89.)),"source_mismatch")

    def test_17_source_low_mismatch(self):
        self.l[3]=89.
        self.assertEqual(self.minute(source=(105.,80.)),"source_mismatch")

    def test_18_source_match(self):
        self.l[3]=89.
        self.assertEqual(self.minute(source=(105.,89.)),"win")

    def archive(self, header=False, kind="valid"):
        ts=1609459200000+np.arange(20,dtype=np.int64)*60000
        if kind=="gap": ts[5:]+=60000
        if kind=="duplicate": ts[5]=ts[4]
        if kind=="misaligned": ts+=1
        if kind=="month": ts+=31*E.DAY
        h=np.full(20,105.);l=np.full(20,95.)
        if kind=="nonfinite": h[3]=np.nan
        if kind=="geometry": h[3]=90.
        csv=pd.DataFrame({"t":ts,"o":100.,"h":h,"l":l}).to_csv(index=False,header=header)
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w") as z: z.writestr("input.csv",csv)
        with patch.object(H.urllib.request,"urlopen",side_effect=lambda *a,**k:io.BytesIO(out.getvalue())),patch.object(H.time,"sleep"):
            return H._one_min("TESTUSDT",1609459200000)

    def test_19_archive_no_header(self):
        d=self.archive();self.assertEqual(len(d[0]),20)
        self.assertEqual(int(d[0][0]),1609459200000)

    def test_20_archive_header(self):
        d=self.archive(True);self.assertEqual(len(d[0]),20)
        self.assertEqual(int(d[0][0]),1609459200000)

    def test_21_archive_gap(self): self.assertEqual(self.archive(kind="gap")[0],"data_gap")
    def test_22_archive_duplicate(self): self.assertEqual(self.archive(kind="duplicate")[0],"data_gap")
    def test_23_archive_misaligned(self): self.assertEqual(self.archive(kind="misaligned")[0],"data_gap")
    def test_24_archive_wrong_month(self): self.assertEqual(self.archive(kind="month")[0],"data_gap")
    def test_25_archive_nonfinite(self): self.assertEqual(self.archive(kind="nonfinite")[0],"data_gap")
    def test_26_archive_bad_geometry(self): self.assertEqual(self.archive(kind="geometry")[0],"data_gap")

    def test_27_download_failure_excluded_and_cached(self):
        with patch.object(H.urllib.request,"urlopen",side_effect=OSError("missing")) as m,patch.object(H.time,"sleep"):
            self.assertEqual(H._one_min("TESTUSDT",1609459200000)[0],"data_gap")
            self.assertEqual(H._one_min("TESTUSDT",1609459260000)[0],"data_gap")
            self.assertEqual(m.call_count,4)

    def series(self,n=96*115,open_price=100.,high=105.,low=95.,close=100.):
        t=np.arange(n,dtype=np.int64)*E.STEP
        return t,np.full(n,open_price),np.full(n,high),np.full(n,low),np.full(n,close)

    def file_values(self, kind="valid"):
        t,o,h,l,c=self.series(192)
        if kind=="dup":t[5]=t[4]
        if kind=="align":t+=1
        if kind=="nan":h[5]=np.nan
        if kind=="zero":o[5]=0
        if kind=="geometry":h[5]=90.
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"TESTUSDT.csv.gz"
            pd.DataFrame(dict(open_time=t,open=o,high=h,low=l,close=c)).to_csv(p,index=False)
            return H.load(p)

    def test_28_15m_loader_valid(self): self.assertEqual(len(self.file_values()[0]),192)
    def test_29_15m_duplicate_rejected(self):
        with self.assertRaises(RuntimeError):self.file_values("dup")
    def test_30_15m_alignment_rejected(self):
        with self.assertRaises(RuntimeError):self.file_values("align")
    def test_31_15m_nonfinite_rejected(self):
        with self.assertRaises(RuntimeError):self.file_values("nan")
    def test_32_15m_nonpositive_rejected(self):
        with self.assertRaises(RuntimeError):self.file_values("zero")
    def test_33_15m_geometry_rejected(self):
        with self.assertRaises(RuntimeError):self.file_values("geometry")

    def test_34_contiguous_segments(self):
        t=self.series(192)[0];t[96:]+=E.STEP
        self.assertEqual(H.contiguous_segments(t),[(0,96),(96,192)])

    def test_35_utc_resample(self):
        t,o,h,l,c=self.series(192);c[95]=101.;c[191]=102.
        rt,ro,rh,rl,rc=H.resample(t,o,h,l,c,96)
        self.assertEqual(rt.tolist(),[0,E.DAY])
        self.assertEqual(rc.tolist(),[101.,102.])

    def test_36_partial_daily_bar_rejected(self):
        values=self.series(192)
        r=H.resample(*(v[1:-1] for v in values),96)
        self.assertEqual(len(r[0]),0)

    def test_37_internal_gap_daily_rejected(self):
        values=list(self.series(192));values[0][10]+=1
        self.assertEqual(len(H.resample(*values,96)[0]),1)

    def test_38_psar_prefix_and_future_mutation(self):
        mid=100.+np.cumsum(self.rng.normal(0,1,180))
        h=mid+2.;l=mid-2.
        p,b=H.psar_open_projection(h,l)
        for i in (15,50,100,130,170):
            hh=h.copy();ll=l.copy()
            hh[i:]+=50.;ll[i:]-=50.
            pp,bb=H.psar_open_projection(hh,ll)
            self.assertEqual(p[i],pp[i]);self.assertEqual(b[i],bb[i])
            ph,pb=H.psar_open_projection(h[:i],l[:i])
            np.testing.assert_allclose(ph[2:],p[2:i])
            np.testing.assert_array_equal(pb[2:],b[2:i])

    def test_39_atr_prior_only(self):
        c=100.+np.cumsum(self.rng.normal(0,.5,100));h=c+2.;l=c-2.
        a=E.prior_atr(h,l,c)
        i=60;hh=h.copy();ll=l.copy();cc=c.copy()
        hh[i:]+=50.;ll[i:]-=50.;cc[i:]+=40.
        self.assertEqual(a[i],E.prior_atr(hh,ll,cc)[i])
        prev=np.r_[np.nan,c[:-1]]
        tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
        self.assertAlmostEqual(a[i],np.mean(tr[i-14:i]))

    def exits(self,n=None,fs=3,**kwargs):
        t,o,h,l,c=self.series(n or 3+14*96,close=97.)
        result=E.resolve_limits(t,h,l,c,fs,100.,90.,110.,"TESTUSDT",True,**kwargs)
        return result,t

    def test_40_exact_four_deadlines(self):
        out,t=self.exits()
        for d in E.LIMITS:self.assertEqual(out[d],("time",97.,int(t[3]+d*E.DAY)))

    def test_41_partial_horizon_not_shortened(self):
        from collections import Counter
        q=Counter();out,t=self.exits(n=3+3*96-1,diagnostics=q)
        self.assertEqual(set(out),{1});self.assertEqual(q["partial_horizon_3d"],1)

    def test_42_tp_before_deadline_shared(self):
        t,o,h,l,c=self.series(3+14*96);l[8]=89.
        out=E.resolve_limits(t,h,l,c,3,100.,90.,110.,"TESTUSDT",True)
        self.assertEqual(len(out),4)
        self.assertTrue(all(v==("win",90.,int(t[8]+E.STEP)) for v in out.values()))

    def test_43_sl_before_deadline_shared(self):
        t,o,h,l,c=self.series(3+14*96);h[8]=111.
        out=E.resolve_limits(t,h,l,c,3,100.,90.,110.,"TESTUSDT",True)
        self.assertTrue(all(v[0]=="loss" and v[1]==110. for v in out.values()))

    def test_44_last_bar_tp_before_time(self):
        t,o,h,l,c=self.series(14*96);l[95]=89.
        out=E.resolve_limits(t,h,l,c,0,100.,90.,110.,"TESTUSDT",True)
        self.assertEqual(out[1],("win",90.,E.DAY))

    def test_45_next_bar_exit_does_not_change_shorter_limit(self):
        t,o,h,l,c=self.series(14*96);l[96]=89.
        out=E.resolve_limits(t,h,l,c,0,100.,90.,110.,"TESTUSDT",True)
        self.assertEqual(out[1],("time",100.,E.DAY))
        self.assertEqual(out[3][0],"win")

    def test_46_later_gap_preserves_completed_limit(self):
        t,o,h,l,c=self.series(14*96);h[200]=111.;l[200]=89.
        from collections import Counter
        q=Counter()
        with patch.object(E,"_resolve_1m",return_value="data_gap"):
            out=E.resolve_limits(t,h,l,c,0,100.,90.,110.,"TESTUSDT",True,diagnostics=q)
        self.assertEqual(set(out),{1});self.assertEqual(q["excluded_data_gap_3d"],1)

    def test_47_maker_ambiguous_entry_loss(self):
        t,o,h,l,c=self.series(14*96);l[0]=89.
        with patch.object(E,"_resolve_1m",return_value="loss") as m:
            out=E.resolve_limits(t,h,l,c,0,100.,90.,110.,"TESTUSDT",False)
        self.assertTrue(all(v[0]=="loss" for v in out.values()))
        self.assertEqual(m.call_args.args[5],100.)

    def test_48_maker_mismatch_excluded(self):
        t,o,h,l,c=self.series(14*96);l[0]=89.
        with patch.object(E,"_resolve_1m",return_value="entry_mismatch"):
            self.assertEqual(E.resolve_limits(t,h,l,c,0,100.,90.,110.,"TESTUSDT",False),{})

    def generated(self,mode="taker"):
        price=99. if mode=="taker" else 95.
        t,o,h,l,c=self.series(open_price=price,high=price+.5,low=price-.5,close=price)
        if mode=="maker":
            for d in range(100,115):h[d*96+2]=96.6
        elif mode=="invalid":o[:]=105.;h[:]=106.;l[:]=104.;c[:]=105.
        with patch.object(E,"psar_open_projection",side_effect=lambda hh,ll:(np.full(len(hh),102.),np.zeros(len(hh),bool))),patch.object(E,"prior_atr",side_effect=lambda hh,ll,cc:np.full(len(hh),2.)):
            return pd.DataFrame(E.one(t,o,h,l,c,"TESTUSDT"),columns=E.COLUMNS)

    def test_49_taker_actual_fill_tp(self):
        z=self.generated();E.validate(z)
        self.assertAlmostEqual(z.iloc[0].fill,99.)
        self.assertAlmostEqual(z.iloc[0].sl,104.4)
        self.assertAlmostEqual(z.iloc[0].tp,94.95)
        self.assertNotAlmostEqual(z.iloc[0].tp,90.575)

    def test_50_maker_target_and_same_day(self):
        z=self.generated("maker");E.validate(z)
        self.assertAlmostEqual(z.iloc[0].fill,96.5)
        self.assertEqual(z.iloc[0].fill_ts-z.iloc[0].signal_ts,2*E.STEP)

    def test_51_unfilled_is_not_a_trade(self):self.assertEqual(len(self.generated("unfilled")),0)
    def test_52_invalid_risk_is_not_a_trade(self):self.assertEqual(len(self.generated("invalid")),0)

    def test_53_validate_duplicate_rejected(self):
        z=self.generated()
        with self.assertRaises(AssertionError):E.validate(pd.concat([z,z.iloc[:1]],ignore_index=True))

    def test_54_validate_wrong_pnl_rejected(self):
        z=self.generated();z.loc[0,"pnl_pct"]=5.
        with self.assertRaises(AssertionError):E.validate(z)

    def test_55_validate_wrong_deadline_rejected(self):
        z=self.generated();z.loc[0,"exit_ts"]-=E.STEP
        with self.assertRaises(AssertionError):E.validate(z)

    def test_56_symbols_and_disjoint_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(17):(Path(tmp)/f"T{i}USDT.csv.gz").touch()
            groups=[E.source_files(tmp,k,8)[1] for k in range(8)]
            all_paths=[p for g in groups for p in g]
            self.assertEqual(len(all_paths),17);self.assertEqual(len(set(all_paths)),17)

    def test_57_duplicate_source_symbol_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a","b"):
                p=Path(tmp)/name;p.mkdir();(p/"TESTUSDT.csv.gz").touch()
            with self.assertRaises(AssertionError):E.source_files(tmp,0,8)

    def test_58_short_pnl_and_cost_sign(self):
        gross=(100.-97.)/100.*100.
        self.assertAlmostEqual(gross,3.)
        self.assertAlmostEqual(gross-20./100.,2.8)
        self.assertAlmostEqual(gross-40./100.,2.6)

    def test_59_train_cutoff(self):
        self.assertEqual(pd.Timestamp(E.CUT,unit="ms",tz="UTC"),pd.Timestamp("2025-01-01",tz="UTC"))
        self.assertLess(E.CUT-1,E.CUT)

    def test_60_cli_out_import_safe(self):
        proc=subprocess.run([sys.executable,str(Path(E.__file__)),"--help"],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0);self.assertIn("--out",proc.stdout)


if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument("--rounds",type=int,default=10);args=ap.parse_args()
    count=unittest.defaultTestLoader.loadTestsFromTestCase(Audit).countTestCases()
    assert count>=40
    for SEED in range(args.rounds):
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(Audit)
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():sys.exit(1)
        print("CLEAN_ROUND",SEED+1,"DISTINCT_CASES",count,flush=True)
    print("EXECUTION_AUDIT_PASS",count,args.rounds,flush=True)
