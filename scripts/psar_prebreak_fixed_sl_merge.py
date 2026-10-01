"""Aggregate only the pinned fixed-SL scan, preserving raw artifacts and counters."""
import argparse, csv, glob, hashlib, json, math, pathlib

SOURCE_RUN=36811700963
ENGINE_SHA="6ef1f72760e468afed3f2386744fa6da9385def6"
DERIVED={"win_pct","gross_pf_actual_R","gross_expectancy_actual_R"}

def merge(data,allow_partial=False):
    assert data,"No shard artifacts"
    seen=set();agg={};files=0;reference=None
    for d in data:
        de=d["definition"];idx=de["shard_index"]
        assert idx not in seen,("duplicate shard",idx)
        seen.add(idx)
        assert de["engine_blob_sha"]==ENGINE_SHA,("wrong engine",idx,de["engine_blob_sha"])
        assert de["source_data_run"]=="36095439671" and de["input_files_total"]==855
        assert de["shard_count"]==64 and 0<=idx<64
        assert d["files"]==len(range(idx,855,64)),("unexpected file coverage",idx)
        assert not d["errors"],("shard errors",idx,d["errors"])
        sem={k:v for k,v in de.items() if k not in {"shard_index","workflow_commit_sha"}}
        if reference is None:reference=sem
        assert reference==sem,("semantic mismatch",idx)
        files+=d["files"]
        for key,v in d["summary"].items():
            assert v["taker"]+v["maker"]==v["fills"],("shard order accounting",idx,key)
            assert sum(v[k] for k in ("win","loss","data_gap","exit_mismatch","unresolved_eod"))==v["fills"],("shard outcomes",idx,key)
            q=agg.setdefault(key,{k:0 for k in v if k not in DERIVED})
            for k,value in v.items():
                if k not in DERIVED:q[k]+=value
    if not allow_partial:assert seen==set(range(64)) and files==855,("incomplete",sorted(set(range(64))-seen),files)
    rows=[]
    for key,q in agg.items():
        assert q["taker"]+q["maker"]==q["fills"]
        assert sum(q[k] for k in ("win","loss","data_gap","exit_mismatch","unresolved_eod"))==q["fills"]
        assert q["gross_loss_R"]==q["loss"]
        n=q["win"]+q["loss"]
        if not n:continue
        fields=key.split("|")
        wp=100*q["win"]/n
        pf=q["gross_profit_R"]/q["gross_loss_R"] if q["gross_loss_R"] else None
        ev=(q["gross_profit_R"]-q["gross_loss_R"])/n
        q.update(win_pct=wp,gross_pf_actual_R=pf,gross_expectancy_actual_R=ev)
        rows.append(dict(key=key,resolved_n=n,win_pct=wp,gross_pf_R=pf,gross_ev_R=ev,
            side=fields[3],family=fields[0][0],entry_distance=float(fields[0][1:]),
            sl_atr=float(fields[1][2:]),tp_distance=float(fields[2][2:]),
            unresolved_eod=q["unresolved_eod"],data_gap=q["data_gap"],
            entry_mismatch=q["entry_mismatch"],exit_mismatch=q["exit_mismatch"],
            raw_taker=q["taker"],raw_maker=q["maker"],
            cost_taker_fills=q["fills"]))
    # Zero-distance fills touch PSAR itself, violating strict pre-break geometry.
    eligible=[r for r in rows if r["entry_distance"]>0 and r["win_pct"]>=20]
    rank=lambda rs:sorted(rs,key=lambda r:(r["gross_pf_R"] or 0,r["resolved_n"]),reverse=True)
    eligible=rank(eligible)
    bands={}
    for lo,hi in ((20,30),(30,40),(40,101)):
        rs=rank([r for r in eligible if lo<=r["win_pct"]<hi])
        bands[str(lo)]={"range":[lo,hi],"count":len(rs),"gross_pf_ge_1":sum((r["gross_pf_R"] or 0)>=1 for r in rs),"top":rs[:10]}
    sides={side:{"count":sum(r["side"]==side for r in eligible),
        "gross_pf_ge_1":sum(r["side"]==side and (r["gross_pf_R"] or 0)>=1 for r in eligible),
        "top":[r for r in eligible if r["side"]==side][:10]} for side in ("LONG","SHORT")}
    neighborhoods={}
    for band in bands.values():
        if not band["top"]:continue
        r=band["top"][0];region={}
        for field in ("entry_distance","sl_atr","tp_distance"):
            grid=sorted({z[field] for z in rows if z["family"]==r["family"]})
            i=grid.index(r[field]);values=grid[max(0,i-1):i+2]
            neighbors=[z for z in rows if z["side"]==r["side"] and z["family"]==r["family"]
                and z[field] in values and all(z[q]==r[q] for q in ("entry_distance","sl_atr","tp_distance") if q!=field)]
            region[field]=neighbors
        neighborhoods[r["key"]]=region
    return {"source_run":SOURCE_RUN,"engine_blob_sha":ENGINE_SHA,"complete":len(seen)==64,
        "covered_shards":sorted(seen),"missing_shards":sorted(set(range(64))-seen),"covered_files":files,
        "definition":reference,"excluded_symbols":["BNXUSDT"],"summary":agg,"rows":rows,
        "eligible_win20_count":len(eligible),"eligible_gross_pf_ge_1":sum((r["gross_pf_R"] or 0)>=1 for r in eligible),
        "eligible_top":eligible[:30],"win_bands":bands,"sides":sides,"neighborhoods":neighborhoods,
        "limitations":["Gross actual-R PF; not price-return/capital-weighted net PF",
            "Raw signal outcomes allow same-symbol/regime overlap; not executable portfolio N",
            "No fees/slippage/funding, equity curve/MDD or holding-period cap",
            "Original intrabar maker label must be charged as taker",
            "Gap-through stop entry still requires ledger correction; current scan is diagnostic",
            "Zero-distance PSAR-touch controls excluded from strict pre-break candidates",
            "Unresolved end-of-data trades excluded from win/loss; holding-time and censoring audit required",
            "Full-period exploratory rankings are not independent holdout validation; no threshold retuning"]}

def report(result):
    lines=["# PSAR 1H Pre-Break Fixed-SL Scan","",
        "Source run: "+str(SOURCE_RUN),
        "Coverage: "+str(len(result["covered_shards"]))+"/64 shards, "+str(result["covered_files"])+"/855 files.",
        "","Gross diagnostic rankings; costs, portfolio constraints, and execution gaps are not validated.","",
        "| Win band | Side | Resolved signals | Win % | Gross PF (R) | Gross EV (R) | Entry distance | SL ATR | TP from PSAR |",
        "|---|---|---:|---:|---:|---:|---|---:|---|"]
    for name,band in result["win_bands"].items():
        if not band["top"]:continue
        r=band["top"][0];unit="ATR" if r["family"]=="E" else "%"
        label={"20":"20–30%","30":"30–40%","40":"40%+"}[name]
        lines.append("| "+f"{label} | {r['side']} | {r['resolved_n']:,} | {r['win_pct']:.3f} | {r['gross_pf_R']:.5f} | {r['gross_ev_R']:+.5f} | {r['entry_distance']:g}{unit} | {r['sl_atr']:g} | {r['tp_distance']:g}{unit}"+" |")
    lines+=["","This table is a scan comparison, not a practical or portfolio optimum.","","## Integrity and remaining work",""]
    lines+=["- "+x for x in result["limitations"]]
    return "\n".join(lines)+"\n"

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--data",default="all")
    ap.add_argument("--out",default="psar_prebreak_fixed_sl_merged.json")
    ap.add_argument("--allow-partial",action="store_true");a=ap.parse_args()
    fs=sorted(glob.glob(a.data+"/**/out_*.json",recursive=True))
    ds=[json.loads(pathlib.Path(f).read_text()) for f in fs]
    result=merge(ds,a.allow_partial)
    pathlib.Path(a.out).write_text(json.dumps(result,indent=2))
    pathlib.Path(a.out).with_suffix(".md").write_text(report(result))
    csv_path=pathlib.Path(a.out).with_suffix(".csv")
    with csv_path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(result["rows"][0]));w.writeheader();w.writerows(result["rows"])
    print(json.dumps({k:result[k] for k in ("complete","covered_shards","covered_files","eligible_win20_count","eligible_gross_pf_ge_1")},indent=2))
    for name,band in result["win_bands"].items():print("WIN_BAND",name,json.dumps(band["top"][:3]))
    for name,side in result["sides"].items():print("SIDE",name,json.dumps(side["top"][:3]))
if __name__=="__main__":main()
