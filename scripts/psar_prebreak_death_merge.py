import argparse,glob,json,os,csv
ap=argparse.ArgumentParser();ap.add_argument("--data",default="all");ap.add_argument("--out",default="psar_prebreak_death_merged.json");a=ap.parse_args()
paths=sorted(glob.glob(a.data+"/**/death_*.json",recursive=True))
if len(paths)!=64:raise RuntimeError(f"expected 64 shard jsons, got {len(paths)}")
docs=[json.load(open(p)) for p in paths]
idx=[d["definition"]["shard_index"] for d in docs]
if sorted(idx)!=list(range(64)):raise RuntimeError(f"shard coverage/duplicate failure {idx}")
if any(d["definition"]["shard_count"]!=64 for d in docs):raise RuntimeError("shard_count mismatch")
if any(d["definition"]["input_files_total"]!=855 for d in docs):raise RuntimeError("input total mismatch")
if sum(d["files"] for d in docs)!=855:raise RuntimeError(f"processed file accounting {sum(d['files'] for d in docs)}")
blobs={d["definition"]["source_engine_blob_sha"] for d in docs}
if blobs!={"f4b716e0ad4742c48c51f881126bbe73cf1c34b3"}:raise RuntimeError(f"source engine blob mismatch {blobs}")
scopes={d["definition"]["scope"] for d in docs}
if len(scopes)!=1:raise RuntimeError(f"scope mismatch {scopes}")
RAW=("fills","taker","maker","win","loss","collision_15m","resolved_1m","collision_1m_loss","data_gap","entry_mismatch","exit_mismatch","unresolved_eod","gross_profit_R","gross_loss_R","cost_R_20","net_sum_R_20","net_profit_R_20","net_loss_R_20","cost_R_40","net_sum_R_40","net_profit_R_40","net_loss_R_40")
agg={}
for d in docs:
    for k,v in d["summary"].items():
        q=agg.setdefault(k,{x:0 for x in RAW})
        for x in RAW:q[x]+=v.get(x,0)
for k,q in agg.items():
    if q["taker"]!=q["fills"] or q["maker"]!=0:raise RuntimeError(f"not all taker {k}: {q['fills']} {q['taker']} {q['maker']}")
    if q["win"]+q["loss"]+q["data_gap"]+q["exit_mismatch"]+q["unresolved_eod"]!=q["fills"]:raise RuntimeError(f"outcome accounting {k}")
    n=q["win"]+q["loss"]
    q["resolved"]=n
    q["win_pct"]=100*q["win"]/n if n else None
    q["gross_pf_actual_R"]=q["gross_profit_R"]/q["gross_loss_R"] if q["gross_loss_R"] else None
    q["gross_expectancy_actual_R"]=(q["gross_profit_R"]-q["gross_loss_R"])/n if n else None
    for bps in (20,40):
        q[f"net_pf_{bps}bp"]=q[f"net_profit_R_{bps}"]/q[f"net_loss_R_{bps}"] if q[f"net_loss_R_{bps}"] else None
        q[f"net_expectancy_R_{bps}bp"]=q[f"net_sum_R_{bps}"]/n if n else None
rows=[]
for k,q in agg.items():
    p,s,t,_=k.split("|")
    rows.append({"key":k,"entry_pct":float(p[1:]),"sl_atr":float(s[2:]),"tp_pct":float(t[2:]),**q})
rows.sort(key=lambda r:(-(r["net_pf_20bp"] if r["net_pf_20bp"] is not None else -1),-r["resolved"]))
meta={"source_engine_blob_sha":next(iter(blobs)),"source_data_run":"36095439671","input_files_total":855,"shards":64,"scope":next(iter(scopes)),"cost_policy":docs[0]["definition"]["costs"],"ledger_keys":docs[0]["definition"]["ledger_keys"]}
json.dump({"definition":meta,"summary":agg,"ranking_20bp":rows},open(a.out,"w"),indent=2)
csv_path=os.path.splitext(a.out)[0]+".csv"
with open(csv_path,"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
md_path=os.path.splitext(a.out)[0]+".md"
with open(md_path,"w") as f:
    f.write("# PSAR Pre-Break Death Test\n\n")
    f.write("All entries are taker. 20bp/40bp are total round-trip all-in cost assumptions. Funding excluded.\n\n")
    f.write("| key | N | win% | gross PF | gross EV(R) | net PF 20bp | net EV 20bp | net PF 40bp | net EV 40bp |\n")
    f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for r in rows:
        def ff(x):return "" if x is None else f"{x:.5f}"
        f.write(f"| {r['key']} | {r['resolved']} | {ff(r['win_pct'])} | {ff(r['gross_pf_actual_R'])} | {ff(r['gross_expectancy_actual_R'])} | {ff(r['net_pf_20bp'])} | {ff(r['net_expectancy_R_20bp'])} | {ff(r['net_pf_40bp'])} | {ff(r['net_expectancy_R_40bp'])} |\n")
print(json.dumps(meta,indent=2))
for r in rows:print(r["key"],"N",r["resolved"],"gross",r["gross_pf_actual_R"],"net20",r["net_pf_20bp"],"net40",r["net_pf_40bp"])
