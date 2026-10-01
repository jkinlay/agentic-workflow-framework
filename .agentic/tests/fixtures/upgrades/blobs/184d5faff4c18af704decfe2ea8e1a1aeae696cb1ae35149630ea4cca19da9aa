"""Regression checks for routine autonomy, honest handoffs and non-silent exits."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from agentic import ValidationError
from agentic.authorization import make_request
from agentic.canonical import load, fingerprint
from agentic.contracts import Contracts
from agentic.gates import evaluate
from agentic.interaction import (ROUTINE, decide_action, gate_handoff, loop_next_step,
    next_step, render_markdown, status_report)
from agentic.lifecycle import definition
from agentic.review_loop import LoopStore

ROOT=Path(__file__).resolve().parents[2]
NOW='2026-09-09T12:00:00Z'


class AutonomyTests(unittest.TestCase):
    def test_all_routine_in_scope_actions_continue_without_prompt(self):
        for action in ROUTINE:
            with self.subTest(action=action):
                result=decide_action(action,'assigned ticket',scope_authorized=True)
                self.assertEqual(result['decision'],'CONTINUE')
                self.assertFalse(result['prompt_user'])
                self.assertIsNone(result['next_step']['authorization']['phrase'])
                self.assertFalse(result['execution_authority'])

    def test_existing_explicit_authorization_is_not_requested_again(self):
        result=decide_action('change_governance','requested AWF upgrade',scope_authorized=True,already_authorized=True)
        self.assertEqual(result['decision'],'CONTINUE')

    def test_routine_word_cannot_expand_scope(self):
        with self.assertRaises(ValidationError):
            decide_action('edit','another repository',scope_authorized=False)

    def test_unknown_and_fabricated_permission_flags_are_rejected(self):
        with self.assertRaises(ValidationError): decide_action('whatever','target',scope_authorized=True)
        with self.assertRaises(ValidationError): decide_action('edit','target',scope_authorized='yes')

    def test_new_nonroutine_request_requires_concrete_reason_and_source(self):
        with self.assertRaises(ValidationError): decide_action('deploy','production',scope_authorized=True)
        auth={'required':True,'reason':'Production deployment has not been authorized.',
            'source':'Owner-approved production deployment policy','phrase':'Authorize deployment of build 42 to staging only.'}
        result=decide_action('deploy','staging build 42',scope_authorized=True,authorization=auth)
        self.assertTrue(result['prompt_user'])
        self.assertIn(auth['phrase'],render_markdown(result))

    def test_technical_failure_does_not_invent_owner_authorization(self):
        result=decide_action('test','assigned scope',scope_authorized=True,prerequisites_met=False)
        self.assertEqual(result['decision'],'RECONCILE')
        self.assertFalse(result['prompt_user'])

    def test_platform_denial_requires_native_control_when_available(self):
        auth={'required':True,'reason':'The host requires approval to access the named protected directory.',
            'source':'Native sandbox approval policy','phrase':None}
        result=decide_action('inspect','the protected directory',scope_authorized=True,
            platform_permitted=False,platform_approval=auth)
        self.assertEqual(result['decision'],'PLATFORM_APPROVAL_REQUIRED')
        self.assertIn('native approval control',render_markdown(result))
        auth['phrase']='Approve bypass'
        with self.assertRaises(ValidationError):
            decide_action('inspect','directory',scope_authorized=True,platform_permitted=False,platform_approval=auth)

    def test_status_requires_action_owner_trigger_and_honest_authorization_state(self):
        for field in ['action','owner','trigger']:
            data={'state':'WAITING','summary':'CI running','action':'Observe CI','owner':'controller','trigger':'next observation'}
            data[field]=''
            with self.assertRaises(ValidationError): status_report(**data)
        with self.assertRaises(ValidationError):
            status_report('NEEDS_AUTHORIZATION','Needs a decision','Obtain decision')

    def test_completion_has_a_next_step_but_does_not_claim_more_execution(self):
        report=status_report('COMPLETE','Requested release delivered','Use the verified ZIP and its migration guide.','user','when adopting the release')
        self.assertFalse(report['next_step']['continue_independent_work'])
        self.assertIn('Next:',render_markdown(report))

    def test_phrase_fence_cannot_be_broken_by_embedded_backticks(self):
        auth={'required':True,'reason':'New scope','source':'owner scope policy','phrase':'Approve literal ``` in document X only.'}
        report=status_report('NEEDS_AUTHORIZATION','Concrete change prepared','Approve scope',authorization=auth)
        self.assertIn('````text',render_markdown(report))


class LoopProgressTests(unittest.TestCase):
    def test_every_phase_and_unknown_state_has_actionable_next_step(self):
        for phase in ['REVIEW','AMEND','WAIT_CI','READY_FOR_FINAL_GATE','CLOSED','PAUSED','unexpected']:
            state={'phase':phase,'reason':'Temporary environment issue','inflight':None}
            step=loop_next_step(state)
            self.assertTrue(step['action'] and step['owner'] and step['trigger'])
            self.assertFalse(step['authorization']['required'])
            self.assertIsNone(step['authorization']['phrase'])

    def test_paused_unknown_run_requires_reconciliation_not_blind_retry_or_approval(self):
        step=loop_next_step({'phase':'PAUSED','reason':'Uncertain push','inflight':{'id':'run-123'}})
        self.assertIn('--reconciled-run run-123',step['action'])
        self.assertIn('surviving processes',step['action'])
        self.assertFalse(step['authorization']['required'])

    def test_inflight_status_does_not_tell_controller_to_start_duplicate(self):
        step=loop_next_step({'phase':'AMEND','inflight':{'id':'run-123'}})
        self.assertIn('do not dispatch a duplicate',step['action'])

    def test_exhausted_budget_is_not_misreported_as_routine_resume(self):
        step=loop_next_step({'phase':'PAUSED','reason':'Amendment cycle budget exhausted','inflight':None})
        self.assertIn('do not reset counters',step['action'])
        self.assertNotIn('--reconciled-run none',step['action'])

    def test_state_commit_persists_readable_next_step_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store=LoopStore(directory)
            try:
                state={'key':'12:7','owner':'fixture:branch','phase':'WAIT_CI','reason':'Checks running','inflight':None}
                store.save(state,new=True)
                loaded=store.get('12:7')
                report=Path(loaded['status_report_path'])
                self.assertTrue(report.is_file())
                content=report.read_text(encoding='utf-8')
                self.assertIn('Recheck required current-head CI',content)
                self.assertIn('Recorded at:',content)
                self.assertIn('New AWF authorization requested: none',content)
            finally: store.close()

    def test_scheduler_failure_retains_next_step_instead_of_silent_exit(self):
        scripts=str(ROOT/'.agentic/scripts')
        sys.path.insert(0,scripts)
        try:
            spec=importlib.util.spec_from_file_location('awf_scheduled_tick',ROOT/'.agentic/scripts/scheduled_tick.py')
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        finally: sys.path.remove(scripts)
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'broken.json'; config.write_text('{}')
            def failing(_):
                print(json.dumps({'status':'REJECTED','reason':'Missing qualification','next_step':next_step('Complete host qualification first.')}))
                return 2
            with redirect_stdout(io.StringIO()):
                code=module.run(config,invoke=failing)
            self.assertEqual(code,2)
            files=list((Path(directory)/'.awf-status').glob('*.md'))
            self.assertEqual(len(files),1)
            self.assertIn('Complete host qualification first.',files[0].read_text())

    def test_scheduler_exception_and_malformed_report_are_persisted(self):
        scripts=str(ROOT/'.agentic/scripts'); sys.path.insert(0,scripts)
        try:
            spec=importlib.util.spec_from_file_location('awf_scheduled_errors',ROOT/'.agentic/scripts/scheduled_tick.py')
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        finally: sys.path.remove(scripts)
        def crashed(_): raise RuntimeError('Unexpected fixture failure')
        def malformed(_):
            print(json.dumps({'status':'FAILED','next_step':{}}))
            return 0
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'missing-config.json'
            for invoke in [crashed,malformed]:
                with redirect_stdout(io.StringIO()): code=module.run(config,invoke=invoke)
                self.assertEqual(code,2)
                files=list((Path(directory)/'.awf-status').glob('*.md'))
                self.assertEqual(len(files),1)
                self.assertIn('Next:',files[0].read_text())

    def test_scheduler_failure_after_success_output_cannot_retain_success(self):
        scripts=str(ROOT/'.agentic/scripts'); sys.path.insert(0,scripts)
        try:
            spec=importlib.util.spec_from_file_location('awf_scheduled_late_failure',ROOT/'.agentic/scripts/scheduled_tick.py')
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        finally: sys.path.remove(scripts)
        for crash in [True,False]:
            def late_failure(_):
                print(json.dumps(status_report('COMPLETE','Everything completed','Use the result.')))
                if crash: raise RuntimeError('Finalization failed after report')
                return 2
            with tempfile.TemporaryDirectory() as directory:
                with redirect_stdout(io.StringIO()): code=module.run(Path(directory)/'config.json',invoke=late_failure)
                self.assertEqual(code,2)
                report=next((Path(directory)/'.awf-status').glob('*.md')).read_text()
                self.assertIn('REJECTED',report)
                self.assertNotIn('Everything completed',report)
                self.assertIn('Next:',report)
                if crash: self.assertIn('Finalization failed after report',report)


class GateHandoffTests(unittest.TestCase):
    def setUp(self):
        self.contracts=Contracts(ROOT/'.agentic/schemas')
        self.config=load(ROOT/'.agentic/examples/PROJECT_CONFIG.yaml')
        self.gate=evaluate(self.config,definition(),load(ROOT/'.agentic/examples/evidence-bundle.json'),self.contracts,NOW)
        self.request=make_request(self.gate,self.contracts,NOW)

    def test_exact_existing_phrase_is_rendered_with_its_evidence_limitations(self):
        report=gate_handoff(self.gate,self.request,self.config,self.contracts,NOW)
        self.assertEqual(report['next_step']['authorization']['phrase'],self.request['expected_text'])
        self.assertIn(self.request['expected_text'],render_markdown(report))
        self.assertFalse(report['live_source_verified'])
        self.assertFalse(report['execution_authority'])

    def test_stale_or_different_candidate_cannot_produce_approval_phrase(self):
        with self.assertRaises(ValidationError): gate_handoff(self.gate,self.request,self.config,self.contracts,'2030-01-01T00:00:00Z')
        altered=deepcopy(self.request); altered['candidate']['head_sha']='f'*40
        with self.assertRaises(ValidationError): gate_handoff(self.gate,altered,self.config,self.contracts,NOW)

    def test_rewritten_phrase_or_superseded_policy_cannot_be_presented(self):
        altered=deepcopy(self.request); altered['expected_text']='Approve everything'
        with self.assertRaises(ValidationError): gate_handoff(self.gate,altered,self.config,self.contracts,NOW)
        config=deepcopy(self.config); config['project']['name']='Different policy'
        with self.assertRaises(ValidationError): gate_handoff(self.gate,self.request,config,self.contracts,NOW)

    def test_jointly_rebound_candidate_cannot_escape_current_repository_policy(self):
        for field,value in [('host','https://other.example'),('repository','other/repository'),('target_base_branch','other-branch'),('merge_method','merge')]:
            gate=deepcopy(self.gate); gate['candidate'][field]=value
            request=make_request(gate,self.contracts,NOW)
            with self.subTest(field=field),self.assertRaises(ValidationError):
                gate_handoff(gate,request,self.config,self.contracts,NOW)
            gate['binding']['candidate_id']=fingerprint('candidate',gate['candidate'])
            request=make_request(gate,self.contracts,NOW)
            with self.subTest(field=field,rebound=True),self.assertRaises(ValidationError):
                gate_handoff(gate,request,self.config,self.contracts,NOW)
