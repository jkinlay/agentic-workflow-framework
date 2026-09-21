"""Synthetic end-to-end cycles and adversarial host adapter checks; no live agents."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.canonical import sha256
from agentic.review_loop import LoopStore, enroll, pause, resume, tick, validate_review
from agentic.providers.github_review_host import HostDriver, load_config, protected, safe_path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = {'repository_id':12,'pr':7,'head':'a'*40,'base':'b'*40,'head_ref':'codex/test','base_ref':'main'}
FINDING = {'id':'F1','severity':'MAJOR','status':'OPEN','file':'src/a.py','message':'Missing error handling','evidence':'','basis':'criterion:AC1'}


class FakeDriver:
    def __init__(self):
        self.current = deepcopy(CANDIDATE)
        self.calls = []
        self.check = 'PASS'
        self.reports = []

    def snapshot(self):
        return deepcopy(self.current)

    def files(self, candidate):
        return ['src/a.py']

    def review(self, candidate, findings, run_id, files):
        self.calls.append(('critic',run_id,candidate['head']))
        if self.reports:
            return self.reports.pop(0)
        if not findings:
            findings = [deepcopy(FINDING)]
            verdict = 'CHANGES_REQUESTED'
        else:
            for finding in findings:
                finding.update(status='RESOLVED',evidence='Inspected current fix and test in src/a.py')
            verdict = 'APPROVE'
        return {'candidate':deepcopy(candidate),'verdict':verdict,'reviewed_files':files,'findings':findings,'summary':'Full review completed'}

    def amend(self, candidate, findings, run_id):
        self.calls.append(('worker',run_id,candidate['head']))
        self.current = {**candidate,'head':('c' if candidate['head'][0] != 'c' else 'd')*40}
        return deepcopy(self.current)

    def ci(self, candidate):
        return self.check


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LoopStore(self.temp.name)
        self.addCleanup(self.store.close)
        self.config = {'key':'12:7','repository':'fixture/project','config_hash':'f'*64,
            'max_amendment_cycles':3,'max_ci_wait_ticks':2,'max_agent_runs':10}
        self.driver = FakeDriver()
        enroll(self.store,self.config,self.driver.snapshot())

    def step(self):
        return tick(self.store,self.config,self.driver)

    def test_full_automatic_review_change_rereview_ci_cycle(self):
        self.assertEqual([self.step()['phase'] for _ in range(4)], ['AMEND','REVIEW','WAIT_CI','READY_FOR_FINAL_GATE'])
        self.assertEqual([x[0] for x in self.driver.calls],['critic','worker','critic'])
        self.assertEqual(len({x[1] for x in self.driver.calls}),3)
        state = self.store.get('12:7')
        self.assertEqual(state['findings'][0]['id'],'F1')
        self.assertEqual(state['findings'][0]['status'],'RESOLVED')
        self.assertEqual(state['cycles'],1)
        self.assertEqual(state['agent_runs'],3)

    def test_repeated_scheduler_tick_is_idempotent_at_ready(self):
        for _ in range(4): self.step()
        count = len(self.driver.calls)
        for _ in range(3): self.assertEqual(self.step()['phase'],'READY_FOR_FINAL_GATE')
        self.assertEqual(len(self.driver.calls),count)

    def test_second_enrollment_cannot_claim_same_branch(self):
        with self.assertRaises(sqlite3.IntegrityError):
            enroll(self.store,{**self.config,'key':'12:8'},self.driver.snapshot())

    def test_second_process_cannot_own_active_host_lock(self):
        with self.store.lock():
            with self.assertRaises(ValidationError):
                with self.store.lock(): pass

    def test_interrupted_amendment_is_not_replayed(self):
        self.step()
        def unknown(*args):
            self.driver.current['head'] = 'e'*40
            raise TimeoutError('push outcome uncertain')
        self.driver.amend = unknown
        state = self.step()
        self.assertEqual(state['phase'],'PAUSED')
        self.assertIsNotNone(state['inflight'])
        self.assertEqual(state['cycles'],1)
        for _ in range(3): self.assertEqual(self.step()['phase'],'PAUSED')
        self.assertEqual(len(self.driver.calls),1)

    def test_abandoned_intent_after_process_crash_pauses(self):
        state = self.store.get('12:7')
        state['inflight'] = {'id':'crashed','phase':'REVIEW'}
        self.store.save(state)
        self.assertEqual(self.step()['phase'],'PAUSED')
        self.assertEqual(self.driver.calls,[])

    def test_stale_critic_result_cannot_advance(self):
        original = self.driver.review
        def stale(*args):
            report = original(*args)
            self.driver.current['base'] = 'e'*40
            return report
        self.driver.review = stale
        self.assertEqual(self.step()['phase'],'PAUSED')

    def test_base_change_automatically_invalidates_ready_review(self):
        for _ in range(4): self.step()
        self.driver.current['base'] = 'e'*40
        state = self.step()
        self.assertEqual(state['phase'],'WAIT_CI')
        self.assertEqual(state['last_review']['candidate']['base'],'e'*40)

    def test_external_head_change_breaks_single_writer_ownership(self):
        self.driver.current['head'] = 'e'*40
        self.assertEqual(self.step()['phase'],'PAUSED')
        self.assertEqual(self.driver.calls,[])

    def test_expired_review_is_replaced_by_fresh_context(self):
        for _ in range(4): self.step()
        state = self.store.get('12:7')
        state['last_review']['completed_at'] = '2000-01-01T00:00:00Z'
        self.store.save(state)
        count = len(self.driver.calls)
        self.assertEqual(self.step()['phase'],'WAIT_CI')
        self.assertEqual(len(self.driver.calls),count+1)

    def test_config_change_pauses_before_agent(self):
        self.config['config_hash'] = 'changed'
        self.assertEqual(self.step()['phase'],'PAUSED')
        self.assertEqual(self.driver.calls,[])

    def test_no_progress_amendment_pauses(self):
        self.step()
        self.driver.amend = lambda candidate,*_: candidate
        self.assertEqual(self.step()['phase'],'PAUSED')

    def test_amendment_budget_is_durable_and_not_reset_by_resume(self):
        self.config['max_amendment_cycles'] = 1
        self.step(); self.step()
        self.driver.review = lambda candidate,findings,run,files: {'candidate':candidate,'verdict':'CHANGES_REQUESTED','reviewed_files':files,'findings':findings,'summary':'Still broken'}
        self.step()
        self.assertEqual(self.step()['phase'],'PAUSED')
        with self.assertRaises(ValidationError):
            resume(self.store,self.config,self.driver.snapshot(),'none')

    def test_agent_budget_cannot_be_reset_by_transient_failure(self):
        self.config['max_agent_runs'] = 1
        self.driver.review = lambda *args: (_ for _ in ()).throw(RuntimeError('transient'))
        state = self.step()
        resume(self.store,self.config,self.driver.snapshot(),state['inflight']['id'])
        self.assertIn('agent-run budget',self.step()['reason'])

    def test_pause_resume_restarts_full_review_with_same_ledger(self):
        self.step()
        pause(self.store,'12:7','Operator pause')
        state = resume(self.store,self.config,self.driver.snapshot(),'none')
        self.assertEqual(state['phase'],'REVIEW')
        self.assertEqual(state['findings'][0]['id'],'F1')

    def test_wrong_reconciliation_identity_is_rejected(self):
        pause(self.store,'12:7','pause')
        with self.assertRaises(ValidationError):
            resume(self.store,self.config,self.driver.snapshot(),'wrong')

    def test_missing_ci_exhausts_wait_budget(self):
        for _ in range(3): self.step()
        self.driver.check = 'WAIT'
        self.assertEqual([self.step()['phase'] for _ in range(3)],['WAIT_CI','WAIT_CI','PAUSED'])

    def test_failed_ci_automatically_requests_bounded_amendment(self):
        for _ in range(3): self.step()
        self.driver.check = 'FAIL'
        state = self.step()
        self.assertEqual(state['phase'],'AMEND')
        self.assertEqual(state['findings'][-1]['id'],'AWF-CI')
        self.driver.check = 'PASS'
        self.assertEqual([self.step()['phase'] for _ in range(3)],['REVIEW','WAIT_CI','READY_FOR_FINAL_GATE'])

    def test_ci_snapshot_race_cannot_mark_ready(self):
        for _ in range(3): self.step()
        def moved(candidate):
            self.driver.current['head'] = 'e'*40
            return 'PASS'
        self.driver.ci = moved
        self.assertEqual(self.step()['phase'],'PAUSED')

    def test_closed_pr_stops_without_action(self):
        self.driver.current = None
        self.assertEqual(self.step()['phase'],'CLOSED')
        self.assertEqual(self.driver.calls,[])

    def test_ledger_rejects_dropped_downgraded_fabricated_or_disputed_resolution(self):
        good = {'candidate':CANDIDATE,'verdict':'CHANGES_REQUESTED','reviewed_files':['src/a.py'],'findings':[deepcopy(FINDING)],'summary':'Reviewed'}
        bad = []
        for key,value in [('severity','MINOR'),('message','Different issue'),('status','RESOLVED')]:
            record = deepcopy(good); record['findings'][0][key] = value; bad.append(record)
        record = deepcopy(good); record['findings'] = []; bad.append(record)
        record = deepcopy(good); record['verdict'] = 'APPROVE'; record['findings'][0]['status']='DISPUTED'; bad.append(record)
        record = deepcopy(good); record['reviewed_files'] = []; bad.append(record)
        record = deepcopy(good); record['candidate']['head'] = 'f'*40; bad.append(record)
        for record in bad:
            with self.subTest(record=record), self.assertRaises(ValidationError):
                validate_review(record,CANDIDATE,[FINDING],['src/a.py'])


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ['runtime','state','worker','critic']:
            (self.base/name).mkdir()
        contract = self.base/'state/contract.md'; contract.write_text('Approved fixture contract')
        self.path = self.base/'state/config.json'
        self.value = json.loads((ROOT/'.agentic/review-loop/host-config.example.json').read_text())
        self.value.update(repository='fixture/project',repository_id=12,pr=7,head_branch='codex/test',
            state_dir=str(self.base/'state'),worker_checkout=str(self.base/'worker'),critic_checkout=str(self.base/'critic'),
            contract_path=str(contract),contract_sha256=sha256(contract.read_bytes()),runtime_manifest_sha256='f'*64,
            models={'worker':'fixture-worker','critic':'fixture-critic'},allowed_paths=['src/a.py'],
            required_checks=[{'name':'test','app_id':1,'workflow_path':'.github/workflows/ci.yml','workflow_sha256':sha256(b'workflow\n')}],
            qualification={'operator':'fixture','evidence':'Synthetic only','sandbox_verified':True,'credentials_isolated':True,'branch_owned':True,'single_host_database':True})
        executable = {'path':sys.executable,'sha256':sha256(Path(sys.executable).read_bytes())}
        self.value['executables'] = {x:deepcopy(executable) for x in ['git','gh','codex']}

    def config(self):
        self.path.write_text(json.dumps(self.value))
        return load_config(self.path,self.base/'runtime')

    def local_git_driver(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git executable unavailable for local repository integration')
        def command(*args):
            return subprocess.run([git,*args],text=True,encoding='utf-8',capture_output=True,check=True).stdout.strip()
        worker = self.base/'worker'; critic=self.base/'critic'; remote=self.base/'remote.git'
        command('init','--initial-branch=main',str(worker))
        command('-C',str(worker),'config','user.name','AWF fixture')
        command('-C',str(worker),'config','user.email','fixture@example.invalid')
        command('-C',str(worker),'config','commit.gpgsign','false')
        (worker/'src').mkdir(); (worker/'src/a.py').write_text('value = 1\n')
        command('-C',str(worker),'add','src/a.py'); command('-C',str(worker),'commit','-m','fixture baseline')
        base=command('-C',str(worker),'rev-parse','HEAD')
        command('-C',str(worker),'checkout','-b','codex/test')
        (worker/'src/a.py').write_text('value = 2\n')
        command('-C',str(worker),'commit','-am','fixture candidate')
        head=command('-C',str(worker),'rev-parse','HEAD')
        command('init','--bare',str(remote))
        command('-C',str(worker),'remote','add','origin',str(remote))
        command('-C',str(worker),'push','origin','main','codex/test')
        command('clone','--branch','codex/test',str(remote),str(critic))
        self.value['executables']['git']={'path':git,'sha256':sha256(Path(git).read_bytes())}
        config=self.config(); driver=HostDriver(config,ROOT)
        driver.url=str(remote)
        real_git=driver.git
        def local_git(checkout,*args):
            if args[0] in {'fetch','push'}:
                args=('-c','protocol.file.allow=always',*args)
            return real_git(checkout,*args)
        driver.git=local_git  # Explicit local transport seam; production forbids it.
        candidate={**CANDIDATE,'head':head,'base':base}
        driver.snapshot=lambda: {**candidate,'head':command('--git-dir',str(remote),'rev-parse','refs/heads/codex/test')}
        def agent(role,current,findings,run_id,files=None):
            (worker/'src/a.py').write_text('value = 3\n')
            return {'candidate':current,'outcome':'CHANGED','summary':'Synthetic file edit; no model called'}
        driver.agent=agent
        return driver,candidate,command

    def test_real_local_git_amendment_commits_and_pushes_one_child(self):
        driver,candidate,command=self.local_git_driver()
        amended=driver.amend(candidate,[FINDING],'fixture-local')
        self.assertNotEqual(amended['head'],candidate['head'])
        self.assertEqual(driver.snapshot(),amended)
        self.assertEqual(command('-C',str(driver.worker),'rev-parse','HEAD^'),candidate['head'])
        self.assertEqual(command('-C',str(driver.worker),'status','--porcelain'),'')

    def test_real_local_git_scope_escape_cannot_publish(self):
        driver,candidate,command=self.local_git_driver()
        agent=driver.agent
        def escaped(*args):
            (driver.worker/'outside.txt').write_text('out of scope')
            return agent(*args)
        driver.agent=escaped
        with self.assertRaisesRegex(ValidationError,'outside exact enrolled scope'): driver.amend(candidate,[FINDING],'fixture-escape')
        self.assertEqual(driver.snapshot(),candidate)
        self.assertEqual(command('-C',str(driver.worker),'rev-parse','HEAD'),candidate['head'])

    def test_real_local_git_worker_commit_cannot_be_published(self):
        driver,candidate,command=self.local_git_driver()
        agent=driver.agent
        def committed(*args):
            result=agent(*args)
            command('-C',str(driver.worker),'commit','-am','unauthorized worker commit')
            return result
        driver.agent=committed
        with self.assertRaisesRegex(ValidationError,'changed Git HEAD/branch'): driver.amend(candidate,[FINDING],'fixture-commit')
        self.assertEqual(driver.snapshot(),candidate)

    def test_real_local_git_worker_cannot_redirect_push(self):
        driver,candidate,command=self.local_git_driver()
        agent=driver.agent
        def redirected(*args):
            result=agent(*args)
            command('-C',str(driver.worker),'config','remote.origin.pushurl','https://example.invalid/other.git')
            return result
        driver.agent=redirected
        with self.assertRaisesRegex(ValidationError,'changed Git configuration'): driver.amend(candidate,[FINDING],'fixture-config')
        self.assertEqual(driver.snapshot(),candidate)

    def test_unconfigured_example_cannot_launch(self):
        path = ROOT/'.agentic/review-loop/host-config.example.json'
        with self.assertRaises(ValidationError): load_config(path,ROOT)

    def test_existing_findings_seed_enrollment_and_cannot_be_omitted(self):
        self.value['initial_findings'] = [deepcopy(FINDING)]
        config = self.config()
        store = LoopStore(self.base/'state')
        try:
            state = enroll(store,config,CANDIDATE)
            self.assertEqual(state['findings'],[FINDING])
            report = {'candidate':CANDIDATE,'verdict':'APPROVE','reviewed_files':['src/a.py'],'findings':[],'summary':'Missed history'}
            driver = FakeDriver(); driver.reports=[report]
            self.assertEqual(tick(store,config,driver)['phase'],'PAUSED')
        finally:
            store.close()

    def test_config_rejects_scope_escapes_unqualified_host_and_overlap(self):
        original = deepcopy(self.value)
        cases = [('allowed_paths',['../secret']),('allowed_paths',['.github/workflows/ci.yml']),('allowed_paths',['src/AGENTS.md']),
            ('worker_checkout',str(self.base/'state')),('max_amendment_cycles',1000),('automation_default',False)]
        for key,value in cases:
            self.value = deepcopy(original); self.value[key] = value
            with self.subTest(key=key,value=value),self.assertRaises(ValidationError): self.config()
        self.value = deepcopy(original); self.value['qualification']['sandbox_verified']=False
        with self.assertRaises(ValidationError): self.config()

    def test_executable_and_contract_pin_tampering_rejected(self):
        self.value['executables']['codex']['sha256'] = '0'*64
        with self.assertRaises(ValidationError): self.config()
        self.value['executables']['codex']['sha256'] = sha256(Path(sys.executable).read_bytes())
        Path(self.value['contract_path']).write_text('changed')
        with self.assertRaises(ValidationError): self.config()

    def test_real_bounded_subprocess_and_nonzero_failure(self):
        driver = HostDriver(self.config(),self.base/'runtime')
        self.assertEqual(driver.run('git',['-c','print("fixture")']).strip(),'fixture')
        with self.assertRaises(ValidationError): driver.run('git',['-c','raise SystemExit(3)'])

    def test_codex_invocation_uses_fresh_sandboxed_context_and_structured_output(self):
        driver = HostDriver(self.config(),ROOT)
        captured = []
        def run(name,args,**kwargs):
            captured.append(args)
            output = Path(args[args.index('--output-last-message')+1])
            output.write_text(json.dumps({'candidate':CANDIDATE,'verdict':'APPROVE','reviewed_files':['src/a.py'],'findings':[],'summary':'Fixture only'}))
            return ''
        driver.run = run
        driver.agent('critic',CANDIDATE,[],'fixture-review',['src/a.py'])
        command = captured[0]
        self.assertIn('--ephemeral',command)
        self.assertIn('read-only',command)
        self.assertIn('approval_policy="never"',command)
        self.assertNotIn('resume',command)
        self.assertFalse(any('bypass' in x for x in command))

    def test_github_snapshot_rejects_fork_retarget_and_wrong_identity(self):
        driver = HostDriver(self.config(),ROOT)
        pr = {'number':7,'state':'open','head':{'sha':'a'*40,'ref':'codex/test','repo':{'id':12}},'base':{'sha':'b'*40,'ref':'main','repo':{'id':12}}}
        driver.api = lambda _: deepcopy(pr)
        self.assertEqual(driver.snapshot(),CANDIDATE)
        for parent,key,value in [('head','repo',{'id':99}),('base','ref','other'),('base','repo',{'id':99})]:
            original = deepcopy(pr); pr[parent][key]=value
            with self.assertRaises(ValidationError): driver.snapshot()
            pr=original

    def test_ci_pins_current_head_app_and_workflow_provenance(self):
        driver = HostDriver(self.config(),ROOT)
        checks = {'total_count':1,'check_runs':[{'name':'test','app':{'id':1},'head_sha':'a'*40,'details_url':'https://github.com/fixture/project/actions/runs/10/job/11','status':'completed','conclusion':'success'}]}
        run = {'head_sha':'a'*40,'path':'.github/workflows/ci.yml','repository':{'id':12},'event':'pull_request'}
        driver.api = lambda suffix: deepcopy(run if suffix.startswith('actions/') else checks)
        driver.run = lambda *args,**kwargs: 'workflow\n'
        self.assertEqual(driver.ci(CANDIDATE),'PASS')
        checks['check_runs'][0]['head_sha']='e'*40
        self.assertEqual(driver.ci(CANDIDATE),'WAIT')
        checks['check_runs'][0]['head_sha']='a'*40
        run['path']='.github/workflows/untrusted.yml'
        with self.assertRaises(ValidationError): driver.ci(CANDIDATE)
        run['path']='.github/workflows/ci.yml'; checks['total_count']=101
        with self.assertRaises(ValidationError): driver.ci(CANDIDATE)

    def test_ci_workflow_pin_and_failed_result(self):
        driver = HostDriver(self.config(),ROOT)
        checks = {'total_count':1,'check_runs':[{'name':'test','app':{'id':1},'head_sha':'a'*40,'details_url':'https://github.com/fixture/project/actions/runs/10','status':'completed','conclusion':'failure'}]}
        run = {'head_sha':'a'*40,'path':'.github/workflows/ci.yml','repository':{'id':12},'event':'push'}
        driver.api=lambda suffix: deepcopy(run if suffix.startswith('actions/') else checks)
        driver.run=lambda *args,**kwargs: 'workflow\n'
        self.assertEqual(driver.ci(CANDIDATE),'FAIL')
        driver.run=lambda *args,**kwargs: 'changed\n'
        with self.assertRaises(ValidationError): driver.ci(CANDIDATE)

    def test_protected_paths_and_unsafe_names(self):
        for value in ['AGENTS.md','src/AGENTS.md','.agentic/a','x/.codex/a','.github/workflows/ci.yml','scripts/bootstrap_project.py']:
            self.assertTrue(protected(value))
        for value in ['../a','/a','-a','C:/a','a\\b','a\nsecret']:
            self.assertFalse(safe_path(value))
