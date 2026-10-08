"""Record actual stage completion with the registered branch/run identity guard."""
import argparse,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.research_run_checkpoint import gh_api,read_file,write_file,STATE_PATH,STATUS_PATH
BRANCH="research-daily-breakdown-intraday-v27"
EXPECTED={"validate","prepare_market","collect","preserve_cycle"}|{f"scan ({i})" for i in range(8)}|{f"accounts ({i})" for i in range(16)}

def snapshot(state,jobs,run,commit,now):
    if state.get("current_branch")!=BRANCH or state.get("current_run")!=run:return None
    stages=[j for j in jobs if j["name"]!="checkpoint"]
    ok={j["name"] for j in stages}==EXPECTED and all(
        j["status"]=="completed" and j["conclusion"]=="success" for j in stages)
    out=json.loads(json.dumps(state))
    phase="V27_384_ACCOUNT_RESULTS_READY_FOR_AUDIT" if ok else "V27_FAILED_OR_PARTIAL_AUDIT_PENDING"
    out.update(updated_at=now,phase=phase,latest_research_checkpoint=phase,
        next_action=f"Inspect terminal status, all 384 original account summaries, caps, delay cancellations, yearly returns and concentration outputs of {run}. V25 rejection remains. Preserve failures; no new run before audit.")
    out["current_execution"]=dict(version="V27",run_id=run,branch=BRANCH,code_commit=commit,
        plan_commit=state.get("current_execution",{}).get("plan_commit"),state=phase,
        workflow_status="FINAL_CHECKPOINT_JOB_IN_PROGRESS",observed_at=now,
        stage_jobs=[{k:j[k] for k in ("id","name","status","conclusion")} for j in stages],
        v25_rejection_retained=True,profitability_audited=False,account_daily_target_claim=False)
    out.setdefault("actual_state",{})["phase"]=phase
    return out

def self_test():
    state=dict(current_branch=BRANCH,current_run=1,previous_results=dict(v25=dict(survivors=0)))
    jobs=[dict(id=i,name=n,status="completed",conclusion="success") for i,n in enumerate(sorted(EXPECTED))]
    assert snapshot(state,jobs,2,"code","now") is None
    assert snapshot(dict(state,current_branch="newer"),jobs,1,"code","now") is None
    got=snapshot(state,jobs,1,"code","now")
    assert got["phase"]=="V27_384_ACCOUNT_RESULTS_READY_FOR_AUDIT" and got["previous_results"]==state["previous_results"]
    jobs[0]["conclusion"]="failure"
    assert snapshot(state,jobs,1,"code","now")["phase"]=="V27_FAILED_OR_PARTIAL_AUDIT_PENDING"
    print("V27_CHECKPOINT_IDENTITY_AND_NO_PROMOTION_PASS")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");a=ap.parse_args()
    if a.self_test:self_test();return
    if os.environ["GITHUB_REF_NAME"]!=BRANCH:raise ValueError("research-only checkpoint")
    repo=os.environ["GITHUB_REPOSITORY"];run=int(os.environ["GITHUB_RUN_ID"])
    now=datetime.now(timezone.utc).isoformat()
    jobs=gh_api(f"repos/{repo}/actions/runs/{run}/jobs?per_page=100")["jobs"]
    item,text=read_file(repo,STATE_PATH);out=snapshot(json.loads(text),jobs,run,os.environ["GITHUB_SHA"],now)
    if out is None:print("V27_CHECKPOINT_SKIPPED_NEWER_OR_UNREGISTERED_RUN");return
    write_file(repo,STATE_PATH,item,json.dumps(out,indent=2)+"\n",f"research(v27): actual stage checkpoint {run}, audit pending")
    _,fresh=read_file(repo,STATE_PATH)
    if json.loads(fresh).get("current_run")!=run or json.loads(fresh).get("current_branch")!=BRANCH:return
    item,text=read_file(repo,STATUS_PATH)
    text+=f"\n## V27 actual stage checkpoint — {now}\n\nRun {run}: {out['phase']}. Inspect final terminal status and all 384 account outputs. V25 rejection retained, economic audit pending, no daily-target claim.\n"
    write_file(repo,STATUS_PATH,item,text,f"research(v27): append actual stage checkpoint {run}")
    print("V27_FINAL_STAGE_CHECKPOINT_WRITTEN",run,out["phase"])

if __name__=="__main__":main()
