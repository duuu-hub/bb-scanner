"""Behavioral audits for the actual import-safe 1D time-limit engine."""
from collections import Counter
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
import psar_1d_exec_helpers as helper
import psar_1d_short_timelimit as engine

PASS = []

# Exercise the current shared engine without replacing its one-pass implementation.
engine.atr_at_open = engine.prior_atr

def _single_limit(t,h,l,c,fs,fill,tp,sl,symbol,is_taker,days):
    q=Counter()
    out=engine.resolve_limits(t,h,l,c,fs,fill,tp,sl,symbol,is_taker,
                              limits=(days,),diagnostics=q)
    if days in out:return out[days]
    if q.get(f"partial_horizon_{days}d"):return ("partial_horizon",None,None)
    raise AssertionError(dict(q))

engine.replay_limit = _single_limit


def check(name, fn):
    fn()
    PASS.append(name)
    print("PASS", name, flush=True)


def equals(got, expected):
    assert got == expected, (got, expected)


def minute_case(bars, long, entry, expected, tp=None, sl=None):
    tp = (105 if long else 95) if tp is None else tp
    sl = (95 if long else 105) if sl is None else sl
    bars = list(bars)
    neutral = (bars[-1][1]+bars[-1][2])/2
    while len(bars) < 15:
        bars.append((len(bars)*60000, neutral, neutral))
    d = tuple(np.array([b[k] for b in bars], dtype=np.int64 if k == 0 else float)
              for k in range(3))
    with patch.object(helper, "_one_min", return_value=d):
        equals(helper._resolve_1m("TESTUSDT", 0, tp, sl, long, entry), expected)


def archive_case(text, expected=None, error=False):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("test.csv", text)
    helper._ONE_MIN_CACHE.clear()
    with patch.object(helper.urllib.request, "urlopen",
                      return_value=SimpleNamespace(read=lambda: z.getvalue())), \
         patch.object(helper.time, "sleep"):
        result = helper._one_min("TESTUSDT", 1609459200000)
    if error:
        equals(result[0], "data_gap")
    else:
        assert np.array_equal(result[0], expected)
    helper._ONE_MIN_CACHE.clear()


def run():
    # Directly exercise the imported helper used by the engine, on both sides.
    cases = [
        ("long_preentry_tp_ignored", [(0,111,109),(60000,101,99),(120000,106,102)], True,100,"win"),
        ("short_preentry_tp_ignored", [(0,96,94),(60000,101,99),(120000,99,94)], False,100,"win"),
        ("long_entry_minute_tp_loss", [(0,106,99)],True,100,"loss"),
        ("short_entry_minute_tp_loss", [(0,101,94)],False,100,"loss"),
        ("long_entry_minute_sl_loss", [(0,101,94)],True,100,"loss"),
        ("short_entry_minute_sl_loss", [(0,106,99)],False,100,"loss"),
        ("long_entry_minute_both_loss", [(0,106,94)],True,100,"loss"),
        ("short_entry_minute_both_loss", [(0,106,94)],False,100,"loss"),
        ("long_gap_through_entry", [(0,99,98),(60000,106,101)],True,100,"win"),
        ("short_gap_through_entry", [(0,102,101),(60000,99,94)],False,100,"win"),
        ("long_later_tp", [(0,101,99),(60000,106,101)],True,100,"win"),
        ("short_later_tp", [(0,101,99),(60000,99,94)],False,100,"win"),
        ("long_later_sl", [(0,101,99),(60000,101,94)],True,100,"loss"),
        ("short_later_sl", [(0,101,99),(60000,106,99)],False,100,"loss"),
        ("long_preentry_tp_then_continue", [(0,106,102),(60000,101,99)],True,100,"continue"),
        ("short_preentry_tp_then_continue", [(0,98,94),(60000,101,99)],False,100,"continue"),
        ("long_unseen_entry_excluded", [(0,110,106),(60000,104,102)],True,100,"entry_mismatch"),
        ("short_unseen_entry_excluded", [(0,98,96)],False,100,"entry_mismatch"),
        ("long_established_both_loss", [(0,106,94)],True,None,"loss"),
        ("short_established_both_loss", [(0,106,94)],False,None,"loss"),
        ("long_established_tp_before_sl", [(0,106,99),(60000,101,94)],True,None,"win"),
        ("short_established_tp_before_sl", [(0,99,94),(60000,106,99)],False,None,"win"),
        ("long_established_sl_before_tp", [(0,101,94),(60000,106,99)],True,None,"loss"),
        ("short_established_sl_before_tp", [(0,106,99),(60000,99,94)],False,None,"loss"),
        ("long_unseen_exit_excluded", [(0,104,96)],True,None,"exit_mismatch"),
        ("short_unseen_exit_excluded", [(0,104,96)],False,None,"exit_mismatch"),
    ]
    for name, bars, long, entry, expected in cases:
        check(name, lambda b=bars, lo=long, en=entry, ex=expected: minute_case(b,lo,en,ex))
    def gap():
        with patch.object(helper, "_one_min", return_value=("data_gap","missing")):
            equals(helper._resolve_1m("TESTUSDT",0,95,105,False), "data_gap")
    check("explicit_data_gap_excluded", gap)
    for n in (0,14,16):
        def incomplete(n=n):
            t = np.arange(n,dtype=np.int64)*60000
            with patch.object(helper, "_one_min", return_value=(t,np.full(n,101.),np.full(n,99.))):
                # 16 candles include one outside the window and remain a valid 15m window.
                equals(helper._resolve_1m("TESTUSDT",0,95,105,False),
                       "exit_mismatch" if n == 16 else "data_gap")
        check(f"minute_window_count_{n}", incomplete)
    def internal_gap():
        t = np.arange(15,dtype=np.int64)*60000
        t[7] = t[6]
        with patch.object(helper,"_one_min",return_value=(t,np.full(15,101.),np.full(15,99.))):
            equals(helper._resolve_1m("TESTUSDT",0,95,105,False),"data_gap")
    check("internal_1m_duplicate_excluded", internal_gap)
    for field in ("high","low"):
        def mismatch(field=field):
            d = (np.arange(15)*60000,np.full(15,101.),np.full(15,99.))
            with patch.object(helper,"_one_min",return_value=d):
                equals(helper._resolve_1m("TESTUSDT",0,95,105,False,None,
                       102 if field == "high" else 101,
                       98 if field == "low" else 99), "source_mismatch")
        check("parent_1m_"+field+"_mismatch_excluded", mismatch)
    vt = np.array([1609459200000,1609459260000])
    good = "1609459200000,100,101,99\n1609459260000,100,101,99\n"
    check("official_csv_without_header", lambda: archive_case(good,vt))
    check("official_csv_with_header", lambda: archive_case("open_time,open,high,low\n"+good,vt))
    for name, bad in [
        ("non_numeric",good.replace(",101,",",oops,",1)),
        ("nonfinite",good.replace(",101,",",NaN,",1)),
        ("negative_price",good.replace(",99",",-1",1)),
        ("inverted_high_low",good.replace(",101,99",",98,99",1)),
        ("misaligned_timestamp",good.replace("1609459200000","1609459200001")),
        ("timestamp_duplicate",good.replace("1609459260000","1609459200000")),
        ("month_mismatch",good.replace("1609459","1612137")),
        ("empty_archive",""),
        ("malformed_schema","1609459200000,100\n"),
    ]:
        check("official_csv_"+name+"_excluded",lambda b=bad:archive_case(b,error=True))
    def unavailable():
        helper._ONE_MIN_CACHE.clear()
        with patch.object(helper.urllib.request,"urlopen",side_effect=OSError("missing")), \
             patch.object(helper.time,"sleep"):
            equals(helper._one_min("TESTUSDT",1609459200000)[0],"data_gap")
        helper._ONE_MIN_CACHE.clear()
    check("archive_download_failure_excluded",unavailable)
    def psar_causal():
        x=np.arange(150,dtype=float)
        base=100+4*np.sin(x/3)+2*np.sin(x/11)
        h,l=base+1.5,base-1.5
        p,b=helper.psar_open_projection(h,l)
        for i in range(100,150):
            hh,ll=h.copy(),l.copy()
            hh[i:]=10000
            ll[i:]=.01
            pp,bb=helper.psar_open_projection(hh,ll)
            assert p[i] == pp[i] and b[i] == bb[i]
    check("psar_current_and_future_bars_do_not_leak",psar_causal)
    def atr_causal():
        x=np.arange(150,dtype=float)
        h,l,c=102+x/100,98+x/100,100+x/100
        a=engine.atr_at_open(h,l,c)
        for i in range(100,150):
            hh,ll,cc=h.copy(),l.copy(),c.copy()
            hh[i:]=10000;ll[i:]=.01;cc[i:]=5000
            assert a[i] == engine.atr_at_open(hh,ll,cc)[i]
    check("atr_prior_closed_bars_only",atr_causal)
    def resampling():
        t=np.arange(2*96+1,dtype=np.int64)*900000
        o=np.arange(len(t),dtype=float)+100;h=o+2;l=o-2;c=o+1
        rt,ro,rh,rl,rc=helper.resample(t,o,h,l,c,96)
        assert rt.tolist()==[0,86400000]
        assert ro.tolist()==[100,196] and rc.tolist()==[196,292]
        assert rh.tolist()==[197,293] and rl.tolist()==[98,194]
    check("utc_daily_resample_and_partial_day_excluded",resampling)
    check("source_segments_split_at_gap",lambda:equals(helper.contiguous_segments(
          np.array([0,900000,2700000,3600000])),[(0,2),(2,4)]))
    def burnin():
        x=np.arange(180,dtype=float)
        base=100+4*np.sin(x/3)+2*np.sin(x/11)
        h,l=base+1.5,base-1.5
        p,b=helper.psar_open_projection(h,l)
        h[:2]+=25;l[:2]-=25
        pp,bb=helper.psar_open_projection(h,l)
        assert np.allclose(p[100:],pp[100:],rtol=0,atol=1e-12)
        assert np.array_equal(b[100:],bb[100:])
    check("psar_100_bar_burnin_sanity",burnin)
    n=14*96+2
    t=np.arange(n,dtype=np.int64)*900000
    h,l,c=np.full(n,101.),np.full(n,99.),np.full(n,100.)
    for days in engine.LIMITS:
        def force(days=days):
            cc=c.copy();cc[2+days*96-1]=97
            rr=engine.replay_limit(t,h,l,cc,2,100,90,110,"TESTUSDT",True,days)
            equals(rr,("time",97.,int(t[2]+days*86400000)))
        check(f"exact_{days}_day_deadline_last_15m_close",force)
    def partial():
        equals(engine.replay_limit(t[:-1],h[:-1],l[:-1],c[:-1],2,100,90,110,
                                   "TESTUSDT",True,14),("partial_horizon",None,None))
    check("data_end_short_horizon_excluded",partial)
    for touch,outcome,px in [("tp","win",90.),("sl","loss",110.)]:
        def earlier(touch=touch,outcome=outcome,px=px):
            hh,ll=h.copy(),l.copy()
            if touch=="tp":ll[3]=89
            else:hh[3]=111
            rr=[engine.replay_limit(t,hh,ll,c,2,100,90,110,"TESTUSDT",True,d)
                for d in engine.LIMITS]
            assert all(r==(outcome,px,int(t[3]+900000)) for r in rr)
        check("early_"+touch+"_identical_across_all_limits",earlier)
    def deadline_priority():
        ll=l.copy();ll[97]=89
        equals(engine.replay_limit(t,h,ll,c,2,100,90,110,"TESTUSDT",True,1)[0],"win")
        ll=l.copy();ll[98]=89
        equals(engine.replay_limit(t,h,ll,c,2,100,90,110,"TESTUSDT",True,1)[0],"time")
    check("tp_before_deadline_and_after_deadline_order",deadline_priority)
    def end_to_end(taker=False,invalid=False):
        n=115*96
        ts=1609459200000+np.arange(n,dtype=np.int64)*900000
        oo=np.full(n,100.);hh=np.full(n,101.);ll=np.full(n,99.);cc=np.full(n,100.)
        ix=100*96
        ps=110.
        if taker:
            oo[ix]=110.;hh[ix]=111.;ll[ix]=109.;cc[ix]=110.
            hh[ix+1]=111.;ll[ix+1]=108.;cc[ix+1]=109.
        elif invalid:
            ps=400.  # Large prior ATR makes the target marketable and TP negative.
            hh[:ix]=200.;ll[:ix]=1.
        else:
            hh[ix+2]=105.;ll[ix+2]=99.;cc[ix+2]=103.
        def projection(a,b):
            sar=np.full(len(a),ps);bull=np.ones(len(a),dtype=bool);bull[100]=False
            return sar,bull
        counts=Counter()
        with patch.object(engine,"psar_open_projection",side_effect=projection):
            rows=engine.one(ts,oo,hh,ll,cc,"TESTUSDT",counts)
        if invalid:
            assert rows==[] and counts["nonpositive_tp"]==1
            return
        z=pd.DataFrame(rows);engine.validate(z)
        assert len(z)==4 and set(z.order)==({"TAKER"} if taker else {"MAKER"})
        assert np.allclose(z.sl,112.4) and np.allclose(z.atr_open,2.)
        if taker:
            assert np.allclose(z.fill,110.) and np.allclose(z.tp,108.2)
            assert (z.outcome=="win").all() and (z.fill_ts==ts[ix]).all()
        else:
            assert np.allclose(z.fill,104.5) and np.allclose(z.tp,98.575)
            assert (z.fill_ts==ts[ix+2]).all() and (z.outcome=="time").all()
        assert np.allclose(z.pnl_pct,(z.fill-z.exit_price)/z.fill*100)
    check("favorable_open_taker_actual_fill_tp_frozen_sl",lambda:end_to_end(taker=True))
    check("resting_target_maker_same_day_fill_and_deadlines",lambda:end_to_end())
    check("nonpositive_tp_not_orderable_signal_excluded",lambda:end_to_end(invalid=True))
    def duplicate_inputs():
        equals(helper._symbol("/tmp/ETHUSDT.csv.gz"),"ETHUSDT")
        for b in ("ETH.csv.gz","../bad.csv","ETH-USDT.csv.gz"):
            try:helper._symbol(b)
            except RuntimeError:continue
            raise AssertionError(b)
    check("symbol_filename_integrity",duplicate_inputs)
    def malformed_15m():
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"ETHUSDT.csv.gz"
            base=pd.DataFrame(dict(open_time=[0,900000],open=[100,100],
                 high=[101,101],low=[99,99],close=[100,100]))
            for field,val in [("open_time",[0,0]),("open_time",[0,900001]),
                              ("high",[98,101]),("close",[np.nan,100]),
                              ("open",[-1,100])]:
                d=base.copy();d[field]=val;d.to_csv(p,index=False,compression="gzip")
                try:helper.load(p)
                except RuntimeError:continue
                raise AssertionError((field,val))
    check("malformed_15m_ohlc_and_timestamps_rejected",malformed_15m)
    assert len(PASS)>=40
    print("ALL_TIME_LIMIT_AUDITS_PASS",len(PASS))


if __name__=="__main__":
    run()
