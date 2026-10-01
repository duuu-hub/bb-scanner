"""Runtime audit of the pinned pre-break engine. No network or market selection."""
import argparse, ast, contextlib, hashlib, io, json, pathlib, sys, tempfile, types, zipfile
import numpy as np
import pandas as pd

def load_engine(path):
    source=pathlib.Path(path).read_text()
    if "numba" not in sys.modules:
        try:
            __import__("numba")
        except ModuleNotFoundError:
            # Local smoke audit needs identical Python semantics, not acceleration.
            module=types.ModuleType("numba")
            module.njit=lambda *args,**kwargs: lambda fn:fn
            sys.modules["numba"]=module
    tree=ast.parse(source)
    cutoff=next(i for i,node in enumerate(tree.body)
                if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="ap" for t in node.targets))
    ns={"__name__":"prebreak_runtime_audit"}
    exec(compile(ast.Module(body=tree.body[:cutoff],type_ignores=[]),str(path),"exec"),ns)
    return source,ns

@contextlib.contextmanager
def patch(ns,**values):
    old={k:ns[k] for k in values}
    ns.update(values)
    try: yield
    finally: ns.update(old)

def chronology(ns,long,rows,expected,entry=True):
    # Rows are deviations from a neutral price; short cases mirror all prices.
    tp,sl=(105.,95.) if long else (95.,105.)
    bars=[(200-h,200-l) if not long else (h,l) for h,l in rows]
    if not long: bars=[(l,h) for h,l in bars]
    neutral=sum(bars[-1])/2
    while len(bars)<15: bars.append((neutral,neutral))
    t=np.arange(15,dtype=np.int64)*60000
    h=np.array([x[0] for x in bars]);l=np.array([x[1] for x in bars])
    with patch(ns,_one_min=lambda *a:(t,h,l)):
        got=ns["_resolve_1m"]("TEST",0,tp,sl,long,100. if entry else None)
    assert got==expected,(long,rows,got,expected)

def fixture(ns,long=True,family="ATR",open_price=None,regimes=None,levels=None,
            fill_high=104.5,fill_low=100.,future_delta=0.,outcome="win",gap_through=False):
    n=105
    rt=np.arange(n,dtype=np.int64)*3600000
    ro=np.full(n,100.);rh=np.full(n,101.);rl=np.full(n,99.);rc=np.full(n,100.)
    sar=np.full(n,105. if long else 95.)
    bull=np.full(n,not long)
    if regimes is not None:bull[:]=regimes
    if levels is not None:sar[:]=levels
    if open_price is not None:ro[100:]=np.asarray(open_price)[100:] if np.ndim(open_price) else open_price
    if future_delta:
        rh[100:]+=future_delta;rl[100:]-=future_delta;rc[100:]+=future_delta/2
    t=np.arange(n*4,dtype=np.int64)*900000
    o=np.repeat(ro,4);h=np.full(len(t),101.);l=np.full(len(t),99.);c=o.copy()
    h[400:404]=fill_high if long else 200-fill_low
    l[400:404]=fill_low if long else 200-fill_high
    if gap_through:
        assert long
        o[400]=100.;h[400]=102.;l[400]=99.;c[400]=102.
        o[401:404]=103.5;h[401:404]=104.5;l[401:404]=103.5;c[401:404]=104.
    books=[];exits=[]
    old_book=ns["_book"]
    def book(q,result,fill,tp,sl,risk):
        books.append((result,fill,tp,sl,risk))
        old_book(q,result,fill,tp,sl,risk)
    def first_exit(hh,ll,start,tp,sl,b):
        exits.append((start,tp,sl,b))
        return (0,outcome=="win",outcome=="loss") if outcome in ("win","loss") else (-1,False,False)
    values=dict(resample=lambda *a:(rt,ro,rh,rl,rc),
        psar_open_projection=lambda *a:(sar,bull),
        ENTRY_ATR=(1.,) if family=="ATR" else (),
        ENTRY_PCT=(1.,) if family=="PCT" else (),
        SL_BUFFER_ATR=(.5,),TP_ATR=(.5,),TP_PCT=(.5,),
        _book=book,_first_exit=first_exit,_resolve_1m=lambda *a:"continue")
    with patch(ns,**values):
        out=ns["evaluate"](t,o,h,l,c,4,"TEST")
    return out,books,exits

def archive(ns,rows,expected,header=False):
    zbytes=io.BytesIO()
    text=("open_time,open,high,low,close\n" if header else "")+"\n".join(",".join(map(str,r)) for r in rows)
    with zipfile.ZipFile(zbytes,"w") as z:z.writestr("TEST.csv",text)
    class Response:
        def read(self):return zbytes.getvalue()
    old_url=ns["urllib"].request.urlopen;old_sleep=ns["time"].sleep
    ns["urllib"].request.urlopen=lambda *a,**k:Response()
    ns["time"].sleep=lambda *a:None
    ns["_ONE_MIN_CACHE"].clear()
    ts=1704067200000
    try:
        try:got=ns["_one_min"]("TESTUSDT",ts)
        except RuntimeError:
            assert expected=="error";return
        assert expected!="error"
        if expected=="gap":assert got[0]=="data_gap"
        else:assert got[0].tolist()==[r[0] for r in rows] and got[1][0]==101.
    finally:
        ns["urllib"].request.urlopen=old_url;ns["time"].sleep=old_sleep
        ns["_ONE_MIN_CACHE"].clear()

def input_case(ns,kind):
    t=np.arange(4,dtype=np.int64)*900000
    o=np.full(4,100.);h=o+1;l=o-1;c=o.copy()
    if kind=="duplicate":t[2]=t[1]
    if kind=="unaligned":t+=1
    if kind=="geometry":h[2]=98
    if kind=="nonfinite":l[2]=np.nan
    with tempfile.TemporaryDirectory() as td:
        p=pathlib.Path(td)/"TESTUSDT.csv.gz"
        pd.DataFrame(dict(open_time=t,open=o,high=h,low=l,close=c)).to_csv(p,index=False,compression="gzip")
        try:ns["load"](str(p))
        except RuntimeError:return
    raise AssertionError("invalid input accepted "+kind)

def assert_account(out):
    assert out
    for q in out.values():
        assert q["taker"]+q["maker"]==q["fills"]
        assert sum(q[k] for k in ("win","loss","data_gap","exit_mismatch","unresolved_eod"))==q["fills"]
        assert q["gross_loss_R"]==q["loss"]

def audit(source,ns):
    tests=[]
    def add(name,fn):tests.append((name,fn))
    def causal():
        x=np.arange(140.);h=100+4*np.sin(x/3)+2;l=h-4
        s,b=ns["psar_open_projection"](h,l)
        h2=h.copy();l2=l.copy();h2[110:]+=100;l2[110:]-=90
        s2,b2=ns["psar_open_projection"](h2,l2)
        assert np.array_equal(b[:111],b2[:111]) and np.allclose(s[:111],s2[:111],equal_nan=True)
    add("PSAR current/future HL cannot affect open projection",causal)
    def atr_causal():
        for long in (True,False):
            a=fixture(ns,long,future_delta=0)[1]
            b=fixture(ns,long,future_delta=70)[1]
            assert a and b and a[0][4]==b[0][4]==1.
    add("ATR risk uses prior closed hour",atr_causal)
    def resample():
        t=np.arange(8,dtype=np.int64)*900000;o=np.arange(10.,18.)
        r=ns["resample"](t,o,o+2,o-2,o+1,4)
        assert [x.tolist() for x in r]==[[0,3600000],[10.,14.],[15.,19.],[8.,12.],[14.,18.]]
    add("Resample exact 1H buckets",resample)
    def incomplete():
        t=np.arange(7,dtype=np.int64)*900000;o=np.full(7,100.)
        assert len(ns["resample"](t,o,o+1,o-1,o,4)[0])==1
        t2=t[1:5];o2=o[1:5]
        assert len(ns["resample"](t2,o2,o2+1,o2-1,o2,4)[0])==0
    add("Partial/misaligned hour discarded",incomplete)
    add("15m gaps split segments",lambda:assert_equal(ns["contiguous_segments"](np.array([0,900000,2700000,3600000])),[(0,2),(2,4)]))
    for kind in ("duplicate","unaligned","geometry","nonfinite"):
        add("Reject 15m "+kind,lambda kind=kind:input_case(ns,kind))
    base=1704067200000
    rows=[[base+i*60000,100,101,99,100] for i in range(3)]
    add("Official 1m headerless parser",lambda:archive(ns,rows,"ok"))
    add("Official 1m header parser",lambda:archive(ns,rows,"ok",True))
    add("Official 1m timestamp gap -> DATA_GAP",lambda:archive(ns,[rows[0],rows[2]],"gap"))
    add("Reject malformed 1m prices",lambda:archive(ns,[[base,100,98,99,100]],"error"))
    cases=[
        ("pre-entry SL ignored, later TP",[(101,99),(106,101)],"win",True),
        ("entry minute TP -> LOSS",[(106,99)],"loss",True),
        ("entry minute SL -> LOSS",[(101,94)],"loss",True),
        ("entry minute both -> LOSS",[(106,94)],"loss",True),
        ("established minute both -> LOSS",[(106,94)],"loss",False),
        ("entry then later SL",[(101,99),(101,94)],"loss",True),
        ("entry only -> CONTINUE",[(101,99)],"continue",True),
        ("entry not reached -> ENTRY_MISMATCH",[(99,98)],"entry_mismatch",True),
        ("exit not reached -> EXIT_MISMATCH",[(104,96)],"exit_mismatch",False),
    ]
    for name,bars,expected,entry in cases:
        def both(bars=bars,expected=expected,entry=entry):
            for long in (True,False):chronology(ns,long,list(bars),expected,entry)
        add("1m LONG/SHORT "+name,both)
    def gap():
        with patch(ns,_one_min=lambda *a:("data_gap","test")):
            assert ns["_resolve_1m"]("TEST",0,105,95,True)=="data_gap"
    add("Resolver propagates DATA_GAP",gap)
    def geometry():
        for long in (True,False):
            for family in ("ATR","PCT"):
                out,books,_=fixture(ns,long,family)
                assert books
                for _,fill,tp,sl,risk in books:
                    assert risk==abs(fill-sl)==1.
                    assert (sl<fill<105<tp) if long else (tp<95<fill<sl)
                assert_account(out)
                if "_approach_fill" in ns:
                    assert all(q["maker"]==0 and q["taker"]==q["fills"] for q in out.values())
    add("Actual engine fixed-fill SL and pre-break geometry",geometry)
    def already_crossed():
        for long in (True,False):
            out,books,_=fixture(ns,long,open_price=106 if long else 94)
            assert not out and not books
    add("Already past PSAR at 1H OPEN rejected",already_crossed)
    def approached():
        for long in (True,False):
            out,books,_=fixture(ns,long,fill_high=102.,fill_low=99.)
            assert not out and not books
            out,books,_=fixture(ns,long)
            assert len(books)==1
            if "_approach_fill" in ns:
                if long:
                    oo=[100.,103.5];hh=[102.,104.5];ll=[99.,103.5];e=103.;s=105.;expected=103.5
                else:
                    oo=[100.,96.5];hh=[101.,96.5];ll=[98.,96.];e=97.;s=95.;expected=96.5
                arrays=[np.array(x) for x in (oo,hh,ll)]
                assert ns["_approach_fill"](*arrays,0,2,e,s,long)==(1,expected,True)
                arrays[0][1]=106. if long else 94.
                arrays[1][1]=107. if long else 94.5
                arrays[2][1]=105.5 if long else 93.
                assert ns["_approach_fill"](*arrays,0,2,e,s,long) is None
                assert ns["_approach_fill"](*arrays,0,2,s,s,long) is None
    add("Approach must reach entry in intended direction",approached)
    def once():
        for long in (True,False):
            for family in ("ATR","PCT"):
                out,books,_=fixture(ns,long,family,open_price=104 if long else 96)
                assert len(books)==1 and sum(q["fills"] for q in out.values())==1
    add("One distance entry per continuous regime, both families",once)
    def reset():
        for family in ("ATR","PCT"):
            regimes=np.zeros(105,dtype=bool);regimes[101:103]=True
            levels=np.where(regimes,95.,105.)
            out,books,_=fixture(ns,True,family,open_price=np.where(regimes,95.5,104.),regimes=regimes,levels=levels)
            assert len(books)==3 and sum(q["fills"] for q in out.values())==3
    add("Regime reversal resets distance state",reset)
    def accounting():
        for outcome in ("win","loss","unresolved"):
            for long in (True,False):
                out,books,_=fixture(ns,long,outcome=outcome)
                assert_account(out)
                q=next(iter(out.values()))
                assert q[{"win":"win","loss":"loss","unresolved":"unresolved_eod"}[outcome]]==1
    add("Actual engine outcome/order accounting",accounting)
    def first_exit():
        h=np.array([101.,106.,108.]);l=np.array([99.,96.,94.])
        got=ns["_first_exit"](h,l,0,105.,95.,True)
        assert tuple(got)==(1,True,False)
        assert tuple(ns["_first_exit"](h,l,2,105.,95.,True))==(0,True,True)
    add("Earliest parent exit selected for 1m resolution",first_exit)
    assert len(tests)==30,len(tests)
    for name,test in tests:test()
    return [name for name,_ in tests]

def assert_equal(a,b):assert a==b,(a,b)

def known_findings(ns):
    out,_,_=fixture(ns,True)
    q=next(iter(out.values()))
    finding={"intrabar_trigger_reported_as_maker":q["maker"]==1,
             "cost_policy":"All OPEN and intrabar approach fills must be charged as taker before cost selection",
             "zero_entry_distance":"PSAR-touch controls; excluded from strict pre-break candidates",
             "gap_through_entry":"15m OPEN gap correction present" if "_approach_fill" in ns else "Legacy parent fill requires straddling threshold"}
    # Demonstrate the gap-through omission rather than claiming a clean execution audit.
    result,books,_=fixture(ns,True,gap_through=True)
    finding["gap_through_counterexample_reproduced"]=not books
    return finding

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--engine",default="scripts/psar_1h_breakthrough_compare.py")
    ap.add_argument("--out",default="prebreak_runtime_audit.json");ap.add_argument("--require-trigger-fix",action="store_true");a=ap.parse_args()
    source,ns=load_engine(a.engine)
    names=audit(source,ns);print("DETAILED_CHECKS_PASS",len(names),flush=True)
    for repetition in range(10):
        audit(source,ns);print("CLEAN_REPEAT_PASS",repetition+1,flush=True)
    raw=source.encode()
    sha=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
    result={"engine_blob_sha":sha,"checks":names,"check_count":30,"consecutive_clean_suite_repetitions":10,
            "findings":known_findings(ns),"scope":"Gross geometry/accounting/causality and mocked official 1m semantics; not cost or live execution approval"}
    if a.require_trigger_fix:
        assert "_approach_fill" in ns
        assert not result["findings"]["intrabar_trigger_reported_as_maker"]
        assert not result["findings"]["gap_through_counterexample_reproduced"]
    pathlib.Path(a.out).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
