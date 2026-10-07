"""Final stage checkpoint with a branch/run identity guard; no economic promotion."""
import argparse,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.research_run_checkpoint import gh_api,read_file,write_file,STATE_PATH,STATUS_PATH

BRANCH="research-daily-breakdown-account-v26"
EXPECTED={"validate","prepare_dev","prepare_market","collect","preserve_cycle"}|{
    f"{name} ({i})" for name in ("gate_scan","accounts") for i in range(8)}

def snapshot(state,jobs,run,commit,now):
    if state.get("current_branch")!=BRANCH or state.get("current_run")!=run:return None
    stages=[j for j in jobs if j["name"]!="checkpoint"]
    ok={j["name"] for j in stages}==EXPECTED and all(
        j["status"]=="completed" and j["conclusion"]=="success" for j in stages)
    out=json.loads(json.dumps(state))
    phase="V26_ACCOUNT_DIAGNOSTIC_READY_FOR_AUDIT" if ok else "V26_FAILED_OR_PARTIAL_AUDIT_PENDING"
    out.update(updated_at=now,phase=phase,latest_research_checkpoint=phase,
        next_action=f"Fetch actual terminal status and all 64 scenario/cluster/arithmetic outputs of {run}. V25 rejection remains; V26 is exploratory only.")
    out["current_execution"]=dict(version="V26",run_id=run,branch=BRANCH,code_commit=commit,
        state=phase,workflow_status="FINAL_CHECKPOINT_JOB_IN_PROGRESS",observed_at=now,
        stage_jobs=[{k:j[k] for k in ("id","name","status","conclusion")} for j in stages],
        diagnostic_only=True,v25_rejection_retained=True,profitability_audited=False,account_daily_target_claim=False)
    out.setdefault("actual_state",{})["phase"]=phase
    return out

def self_test():
    state=dict(current_branch=BRANCH,current_run=1,previous_results=dict(v25=dict(survivors=0)))
    jobs=[dict(id=i,name=n,status="completed",conclusion="success") for i,n in enumerate(sorted(EXPECTED))]
    assert snapshot(state,jobs,2,"code","now") is None
    new=dict(state,current_branch="newer")
    assert snapshot(new,jobs,1,"code","now") is None
    got=snapshot(state,jobs,1,"code","now")
    assert got["phase"]=="V26_ACCOUNT_DIAGNOSTIC_READY_FOR_AUDIT" and got["previous_results"]==state["previous_results"]
    jobs[0]["conclusion"]="failure"
    assert snapshot(state,jobs,1,"code","now")["phase"]=="V26_FAILED_OR_PARTIAL_AUDIT_PENDING"
    assert "phase" not in state
    print("V26_CHECKPOINT_IDENTITY_AND_DIAGNOSTIC_STATUS_PASS")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");a=ap.parse_args()
    if a.self_test:self_test();return
    if os.environ["GITHUB_REF_NAME"]!=BRANCH:raise ValueError("research-only checkpoint")
    repo=os.environ["GITHUB_REPOSITORY"];run=int(os.environ["GITHUB_RUN_ID"])
    now=datetime.now(timezone.utc).isoformat()
    jobs=gh_api(f"repos/{repo}/actions/runs/{run}/jobs?per_page=100")["jobs"]
    item,text=read_file(repo,STATE_PATH);out=snapshot(json.loads(text),jobs,run,os.environ["GITHUB_SHA"],now)
    if out is None:print("V26_CHECKPOINT_SKIPPED_NEWER_OR_UNREGISTERED_RUN");return
    write_file(repo,STATE_PATH,item,json.dumps(out,indent=2)+"\n",f"research(v26): actual completed stage checkpoint {run}, audit pending")
    _,fresh=read_file(repo,STATE_PATH)
    if json.loads(fresh).get("current_run")!=run or json.loads(fresh).get("current_branch")!=BRANCH:
        print("V26_STATUS_APPEND_SKIPPED_NEWER_RUN");return
    item,text=read_file(repo,STATUS_PATH)
    text+=f"\n## V26 actual stage checkpoint — {now}\n\nRun [{run}](https://github.com/{repo}/actions/runs/{run}): {out['phase']}. Final checkpoint still completing; inspect final status and original account/cluster outputs. V25 remains rejected, V26 diagnostic only; no daily-target claim.\n"
    write_file(repo,STATUS_PATH,item,text,f"research(v26): append observed stage checkpoint {run}")
    print("V26_FINAL_STAGE_CHECKPOINT_WRITTEN",run,out["phase"])

if __name__=="__main__":main()
