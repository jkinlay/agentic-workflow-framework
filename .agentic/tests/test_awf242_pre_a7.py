"""Pre-A7: retained operational evidence through production host construction."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import source_only
from source_only import skip_unless_source_repo
from owner_admission_fixtures import confirmed_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.canonical import sha256
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from agentic.providers.github_review_host import HostDriver
from agentic.review_first_draft import run_first_draft
from agentic.review_loop import LoopStore, resume_first_draft
import test_awf242_pre_a3 as fixtures
import test_awf242_pre_a6 as pre_a6
from test_reference_controller_adapter import _dispatch_payload, _ticket, FakeClock, NOW
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs


class ProductionPreA7Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    adapter = fixtures.ProductionPreA3Tests.adapter
    configured_adapter = pre_a6.ProductionPreA6Tests.configured_adapter
    dispatch = pre_a6.ProductionPreA6Tests.dispatch

    @skip_unless_source_repo("Requires source Git HEAD/tree for repository ownership and reservations", ".git")
    def test_controller_checkout_excluded_from_all_protected_state(self):
        config, runner, _ = self.adapter()
        store = ContinuousControllerStore(self.root / 'controller.sqlite', ['A'],
            worktree_roots=[self.root / 'worktree'])
        binding = dict(project_id='project-1', repository_id='101', scope_sha256='a'*64)
        inputs = planning_fixture_inputs([_ticket('tracked.txt')], store, 1, binding=binding, now=NOW)
        self.gov = inputs['planning_config']
        value = deepcopy(inputs['planning_operating_snapshot'].config)
        value['budgeting'] = dict(self.value['budgeting'], accounting_enabled=False)
        snapshot = confirmed_fixture_snapshot(self, self.root, value, self.gov)
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        config['codex']['roles']['writer']['reasoning_effort'] = 'high'
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        head, tree = [fixtures.subprocess.check_output(['git', '-C', str(fixtures.ROOT),
            'rev-parse', ref], text=True).strip() for ref in ('HEAD', 'HEAD^{tree}')]
        with tempfile.TemporaryDirectory(dir=fixtures.ROOT) as home, \
             patch.dict(os.environ, {'AWF_STATE_HOME': home}):
            result = production_controller_cycle(store, controller_config=dict(dispatch_enabled=True,
                auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
                host_capacity=1, **{**planning_resource_inputs(1), **inputs}, inventory_binding=binding,
                repository_root=fixtures.ROOT, repository_head_sha=head, repository_tree_sha=tree,
                publication_config=config,
                **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations',
                    'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
            # Reap a launch on the vulnerable implementation before the red assertion.
            for receipt in result['dispatch_receipts']:
                record = json.loads((runner._budget_record_dir / (receipt['dispatch_id'] + '.json')).read_text())
                runner.observe(record, timeout=10)
            self.assertEqual(result['dispatch_receipts'], [])
            self.assertTrue(result['errors'])
            self.assertFalse(list(Path(home).rglob('executions.sqlite3')))

    def test_deleted_identity_database_refuses_restart_in_every_mode(self):
        for enabled in (False, True):
            with self.subTest(accounting=enabled):
                self.gov['project']['id'] = 'deleted-' + str(enabled)
                _, adapters = self.configured_adapter(False)
                context = self.dispatch(adapters, dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10'))
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(state=str(uuid.uuid4()))
                self.agent(driver, 'critic')  # Fresh independent review works.
                backup = driver.budgeting.operations.path.read_bytes()
                driver.budgeting.operations.path.unlink()
                driver = self.review(state=str(uuid.uuid4()))
                driver.thread = context
                with self.assertRaisesRegex(ValidationError, 'shared execution evidence.*restore'):
                    self.agent(driver, 'critic')
                self.assertFalse(driver.budgeting.operations.path.exists())
                driver.budgeting.operations.path.write_bytes(backup)
                restored = self.review(state=str(uuid.uuid4()))
                self.agent(restored, 'critic')
                restored.thread = context
                with self.assertRaisesRegex(ValidationError, 'independent|quarantined|Usage observation'):
                    self.agent(restored, 'critic')

    def test_shared_failure_blocks_switching_hosts_in_every_mode(self):
        parent = self.root
        for origin in ('adapter', 'review'):
            for enabled, enforcement in ((False, False), (True, False), (True, True)):
                with self.subTest(origin=origin, accounting=enabled, enforcement=enforcement):
                    self.root = parent / (origin + str(enabled) + str(enforcement))
                    self.root.mkdir()
                    if hasattr(self, 'adapter_config'):
                        del self.adapter_config
                    self.gov['project']['id'] = 'failed-' + origin + str(enabled) + str(enforcement)
                    self.value['budgeting'].update(accounting_enabled=False, enforcement_enabled=False)
                    if origin == 'adapter':
                        runner, adapters = self.configured_adapter(False)
                        runner.exit_code = 1
                        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
                            self.dispatch(adapters, dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10'))
                        self.value['budgeting'].update(accounting_enabled=enabled, enforcement_enabled=enforcement)
                        driver = self.review(state=str(uuid.uuid4()))
                        for role in ('worker', 'critic', 'specialist'):
                            with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
                                self.agent(driver, role)
                        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
                            driver.qualification_agent('worker', str(uuid.uuid4()), {})
                    else:
                        driver = self.review(state=str(uuid.uuid4()))
                        self.failing_cli(driver)
                        with self.assertRaisesRegex(ValidationError, 'failed'):
                            self.agent(driver, 'worker')
                        self.value['budgeting']['enforcement_enabled'] = enforcement
                        runner, adapters = self.configured_adapter(enabled)
                        for role in ('writer', 'critic', 'probe'):
                            payload = dict(_dispatch_payload(str(uuid.uuid4()), role=role), ticket='EX-10')
                            try:
                                with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
                                    self.dispatch(adapters, payload)
                            finally:
                                record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
                                if 'pid' in record:
                                    runner.observe(record, timeout=10)
                            self.assertNotIn('pid', record)

    def failing_cli(self, driver):
        executable = Path(sys.executable)
        policy = self.root / 'host-policy.json'
        policy.write_text('Fixture pinned policy')
        driver.c.update(executables={'codex': dict(path=str(executable), sha256=sha256(executable.read_bytes()))},
            _config_path=str(policy), config_hash=sha256(policy.read_bytes()), command_timeout_seconds=10)
        launches = []
        def command(name, args, **kwargs):
            launches.append(1)
            program = ('import json; print(json.dumps(dict(type="thread.started",thread_id="' +
                str(uuid.uuid4()) + '")),flush=True); raise SystemExit(1)')
            return HostDriver.run(driver, name, ['-B', '-c', program], **kwargs)
        driver.run = command
        qualification = patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification')
        qualification.start()
        self.addCleanup(qualification.stop)
        skill = patch('agentic.providers.github_review_host.enforce_pinned_awf_skill')
        skill.start()
        self.addCleanup(skill.stop)
        return launches

    def test_real_failed_first_draft_resume_and_second_launch(self):
        self.failed_first_draft_recovery()

    def test_retained_first_draft_without_attempt_pointer_resumes(self):
        self.failed_first_draft_recovery(pre_pointer=True)

    def failed_first_draft_recovery(self, *, pre_pointer=False):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        driver.verified_contract_text = lambda: 'Fixture scope'
        driver.first_draft_git_controls = lambda: {}
        driver.require_first_draft_git_controls = lambda expected: None
        driver.require_clean_first_draft_checkout = lambda: None
        launches = self.failing_cli(driver)
        config = dict(driver.c, key='101:0', pr=0, repository_id=101, config_hash='f'*64,
            max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=10)
        store = LoopStore(driver.state)
        self.addCleanup(store.close)
        def attempt():
            return run_first_draft(store, config,
                worker=lambda run: driver.first_draft_worker(run_id=run),
                publisher=lambda receipt: self.fail('Failed worker cannot publish'),
                observe_pr=lambda publication: self.fail('Failed worker cannot observe PR'))
        for count in (1, 2, 3):
            with self.assertRaisesRegex(ValidationError, 'failed'):
                attempt()
            self.assertEqual(len(launches), count)
            paused = store.get(config['key'])
            self.assertEqual(paused['phase'], 'PAUSED')
            if pre_pointer:
                # Retain exactly the artifacts written by the pre-A7 host.
                (driver.state / 'runs' / paused['inflight']['id'] / 'first-draft-attempt.json').unlink()
            if count < 3:
                with self.assertRaisesRegex(ValidationError, 'reconcile'):
                    resume_first_draft(store, config, 'wrong-run')
                resumed = resume_first_draft(store, config, paused['inflight']['id'])
                self.assertTrue(resumed['resume_ready'])
            else:
                with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                    resume_first_draft(store, config, paused['inflight']['id'])
        self.assertFalse(driver.budgeting.database.path.exists())


if __name__ == '__main__':
    unittest.main()
