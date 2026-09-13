"""Reproduce the host loop schemas and deliberately unqualified config example."""
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parents[1] / '.agentic/review-loop'


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def write(name,value):
    (ROOT / name).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8',newline='\n')


def main():
    string = {'type':'string'}
    candidate = obj({'repository_id':{'type':'integer'},'pr':{'type':'integer'},'head':string,'base':string,'head_ref':string,'base_ref':string})
    finding = obj({'id':string,'severity':{'type':'string','enum':['BLOCKER','MAJOR','MINOR']},
        'status':{'type':'string','enum':['OPEN','DISPUTED','RESOLVED']},'file':string,'message':string,'evidence':string})
    critic = obj({'candidate':candidate,'verdict':{'type':'string','enum':['APPROVE','CHANGES_REQUESTED','BLOCKED']},
        'reviewed_files':{'type':'array','items':string},'findings':{'type':'array','items':finding},'summary':string})
    worker = obj({'candidate':candidate,'outcome':{'type':'string','enum':['CHANGED','NO_CHANGE','BLOCKED']},'summary':string})
    write('critic-result.schema.json',critic)
    write('worker-result.schema.json',worker)
    write('host-config.example.json',{'version':1,'automation_default':True,'github_host':'github.com','repository':'CHANGE_ME/CHANGE_ME',
        'repository_id':0,'pr':0,'head_branch':'codex/CHANGE_ME','base_branch':'main',
        'state_dir':'C:/awf-state','worker_checkout':'C:/awf-worker','critic_checkout':'C:/awf-critic',
        'contract_path':'C:/awf-state/contract.md','contract_sha256':'CHANGE_ME','runtime_manifest_sha256':'CHANGE_ME',
        'executables':{k:{'path':'CHANGE_ME','sha256':'CHANGE_ME'} for k in ['git','gh','codex']},
        'models':{'worker':'CHANGE_ME','critic':'CHANGE_ME'},'allowed_paths':['src/CHANGE_ME.py'],'initial_findings':[],
        'required_checks':[{'name':'CHANGE_ME','app_id':0,'workflow_path':'.github/workflows/ci.yml','workflow_sha256':'CHANGE_ME'}],
        'max_amendment_cycles':3,'max_ci_wait_ticks':24,'max_agent_runs':10,'command_timeout_seconds':120,'agent_timeout_seconds':1800,'max_review_age_seconds':3600,
        'qualification':{'operator':'CHANGE_ME','evidence':'CHANGE_ME','sandbox_verified':False,'credentials_isolated':False,'branch_owned':False,'single_host_database':False}})


if __name__ == '__main__':
    main()
