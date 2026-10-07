"""Record finished research stages without overwriting a newer workstream."""
from __future__ import annotations
import argparse
import base64
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone

CENTRAL_BRANCH = "research-strategy-continuation"
STATE_PATH = "research/CONTINUATION.json"
STATUS_PATH = "research/RESEARCH_STATUS.md"


def terminal_snapshot(state, jobs, run_id, branch, commit, now):
    if state.get("current_branch") != branch or state.get("current_run") != run_id:
        return None
    other_jobs=[j for j in jobs if j['name'] != 'checkpoint']
    required=[j for j in other_jobs if j['name'] in {'validate','selection','preserve_cycle'}
              or j['name'].startswith('development (')]
    successful=(len(required)==11 and all(j['status']=='completed' and j['conclusion']=='success' for j in required)
                and all(j['status']=='completed' and j['conclusion'] in {'success','skipped'} for j in other_jobs))
    phase='V25_RESULT_READY_FOR_AUDIT' if successful else 'V25_FAILED_OR_PARTIAL_AUDIT_PENDING'
    out=json.loads(json.dumps(state))
    out.update(updated_at=now,phase=phase,latest_research_checkpoint=phase,
               current_run_url=f'https://github.com/{os.environ.get("GITHUB_REPOSITORY","duuu-hub/bb-scanner")}/actions/runs/{run_id}',
               next_action=f'Fetch actual terminal status of run {run_id}, audit original eight shard ledgers and all 96 cells; preserve rejection or freeze candidates. Do not duplicate or claim profitability from job success.')
    out['current_execution']=dict(version='V25',run_id=run_id,branch=branch,code_commit=commit,
        state='RESULT_READY_FOR_AUDIT' if successful else 'FAILED_OR_PARTIAL_STAGES',
        workflow_status='FINAL_CHECKPOINT_JOB_IN_PROGRESS',stages_observed_at=now,
        stage_jobs=[dict(id=j['id'],name=j['name'],status=j['status'],conclusion=j['conclusion']) for j in other_jobs],
        profitability_audited=False,account_daily_target_claim=False)
    out.setdefault('actual_state',{})['phase']=phase
    return out


def gh_api(path, payload=None):
    command=['gh','api',path]
    if payload is None:
        return json.loads(subprocess.check_output(command,text=True))
    with tempfile.NamedTemporaryFile(mode='w',suffix='.json',encoding='utf-8') as file:
        json.dump(payload,file);file.flush()
        return json.loads(subprocess.check_output(command+['--method','PUT','--input',file.name],text=True))


def read_file(repo,path):
    item=gh_api(f'repos/{repo}/contents/{path}?ref={CENTRAL_BRANCH}')
    return item,base64.b64decode(item['content']).decode('utf-8')


def write_file(repo,path,item,text,message):
    return gh_api(f'repos/{repo}/contents/{path}',dict(branch=CENTRAL_BRANCH,sha=item['sha'],message=message,
                  content=base64.b64encode(text.encode('utf-8')).decode('ascii')))


def self_test():
    state={'current_branch':'research-daily-channel-breakout-v25','current_run':1,'previous_results':{'v24_survivors':0}}
    jobs=[dict(name=n,id=i,status='completed',conclusion='success') for i,n in enumerate(
        ['validate']+[f'development ({i})' for i in range(8)]+['selection','preserve_cycle'])]
    jobs += [dict(name='gate',id=12,status='completed',conclusion='skipped'),
             dict(name='accounts',id=13,status='completed',conclusion='skipped')]
    got=terminal_snapshot(state,jobs,1,state['current_branch'],'code','time')
    assert got['phase']=='V25_RESULT_READY_FOR_AUDIT' and got['previous_results']==state['previous_results']
    assert got['current_execution']['profitability_audited'] is False
    assert terminal_snapshot(state,jobs,2,state['current_branch'],'code','time') is None
    assert terminal_snapshot(state,jobs,1,'different-branch','code','time') is None
    bad=json.loads(json.dumps(jobs));bad[2]['conclusion']='failure'
    assert terminal_snapshot(state,bad,1,state['current_branch'],'code','time')['phase']=='V25_FAILED_OR_PARTIAL_AUDIT_PENDING'
    assert state.get('phase') is None
    print('RESEARCH_CHECKPOINT_CONCURRENCY_AND_STATUS_SELFTEST_PASS')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    if args.self_test:
        self_test();return
    repo=os.environ['GITHUB_REPOSITORY'];run_id=int(os.environ['GITHUB_RUN_ID'])
    branch=os.environ['GITHUB_REF_NAME'];commit=os.environ['GITHUB_SHA']
    if branch!='research-daily-channel-breakout-v25':raise ValueError('checkpoint is V25 research-only')
    jobs=gh_api(f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100')['jobs']
    now=datetime.now(timezone.utc).isoformat()
    item,text=read_file(repo,STATE_PATH)
    out=terminal_snapshot(json.loads(text),jobs,run_id,branch,commit,now)
    if out is None:
        print('CHECKPOINT_SKIPPED_NEWER_OR_UNREGISTERED_RUN');return
    write_file(repo,STATE_PATH,item,json.dumps(out,indent=2)+'\n',f'research(v25): record completed stages for {run_id}, audit pending')
    # Read again before the separate narrative write, and refuse a newer workstream.
    _,fresh=read_file(repo,STATE_PATH)
    if json.loads(fresh).get('current_run')!=run_id or json.loads(fresh).get('current_branch')!=branch:
        print('STATUS_APPEND_SKIPPED_NEWER_RUN');return
    item,text=read_file(repo,STATUS_PATH)
    text+=f'\n## V25 Actions stage checkpoint — {now}\n\nRun [{run_id}](https://github.com/{repo}/actions/runs/{run_id}) on `{branch}` / `{commit}`: {out["phase"]}. All listed stage states are from actual Actions jobs. The final checkpoint job is still completing; audit the final run status and original 96-cell outputs before economic conclusions. No profitability or daily-target claim.\n'
    write_file(repo,STATUS_PATH,item,text,f'research(v25): append actual stage checkpoint {run_id}')
    print('RESEARCH_STAGE_CHECKPOINT_WRITTEN',run_id,out['phase'])


if __name__=='__main__':main()
