"""Pre-A6 regressions through production hosts and operational resume."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import unittest
import uuid
from unittest.mock import patch

import source_only
from agentic import ValidationError, operating as op
from agentic.canonical import sha256
from agentic.providers.github_review_host import HostDriver
from agentic.review_loop import LoopStore, enroll, resume, tick
import test_awf242_pre_a3 as fixtures
from test_reference_controller_adapter import _dispatch_payload, FakeClock


class FixedContextCli(fixtures.ControllerCli):
    thread = None

    def launch(self, argv, **kwargs):
        events = [dict(type='thread.started', thread_id=self.thread or str(uuid.uuid4()))]
        if self.usage:
            events.append(dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30)))
        program = ('import json; events=' + repr(events) +
                   '; [print(json.dumps(e),flush=True) for e in events]; raise SystemExit(' + str(self.exit_code) + ')')
        return fixtures.ADAPTER['_SubprocessRunner'].launch(
            self, [sys.executable, '-B', '-c', program], **kwargs)


class ProductionPreA6Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    adapter = fixtures.ProductionPreA3Tests.adapter

    def configured_adapter(self, enabled, *, thread=None):
        if not hasattr(self, 'adapter_config'):
            self.adapter_config, _, _ = self.adapter()
        config = self.adapter_config
        config['github']['project_id'] = self.gov['project']['id']
        config['codex']['roles']['critic'].update(model=self.value['critic']['model'],
            reasoning_effort=self.value['critic']['reasoning_effort'])
        runner = FixedContextCli(record_dir=config['codex']['run_record_directory'])
        runner.thread = thread
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        value = deepcopy(self.value)
        value['budgeting']['accounting_enabled'] = enabled
        adapters['dispatch_ticket'].configure_budgeting(path=self.root / (self.gov['project']['id'] + '.sqlite'),
            governance=self.gov, operating=op.validate_operating(value, self.gov))
        return runner, adapters

    def dispatch(self, adapters, payload):
        receipt = adapters['dispatch_ticket'](payload)
        deadline = time.monotonic() + 10
        while not receipt['terminal'] and time.monotonic() < deadline:
            receipt = adapters['observe_dispatch'](payload)
            time.sleep(0.02)
        self.assertEqual(receipt['dispatch_state'], 'TERMINAL')
        self.assertTrue(receipt['terminal'])
        return receipt['session_id']

    def test_adapter_worker_host_review_all_modes_and_roles(self):
        for worker_mode in (False, True):
            for review_mode in (False, True):
                for role in ('critic', 'specialist'):
                    with self.subTest(worker=worker_mode, reviewer=review_mode, role=role):
                        ticket = 'EX-' + str(100 + int(worker_mode)*4 + int(review_mode)*2 + (role == 'specialist'))
                        self.gov['project']['id'] = 'project-' + ticket
                        _, adapters = self.configured_adapter(worker_mode)
                        payload = dict(_dispatch_payload(str(uuid.uuid4())), ticket=ticket)
                        context = self.dispatch(adapters, payload)
                        self.value['budgeting']['accounting_enabled'] = review_mode
                        driver = self.review(ticket=ticket, state=str(uuid.uuid4()))
                        self.agent(driver, role)
                        driver.thread = context
                        with self.assertRaisesRegex(ValidationError, 'independent|quarantined|Usage observation'):
                            self.agent(driver, role)

    def test_host_worker_adapter_critic_all_modes(self):
        for worker_mode in (False, True):
            for review_mode in (False, True):
                with self.subTest(worker=worker_mode, reviewer=review_mode):
                    ticket = 'EX-' + str(200 + int(worker_mode)*2 + int(review_mode))
                    self.gov['project']['id'] = 'project-' + ticket
                    self.value['budgeting']['accounting_enabled'] = worker_mode
                    driver = self.review(ticket=ticket, state=str(uuid.uuid4()))
                    context = driver.codex_run_id(self.agent(driver, 'worker'))
                    runner, adapters = self.configured_adapter(review_mode)
                    self.dispatch(adapters, dict(_dispatch_payload(str(uuid.uuid4()), role='critic'), ticket=ticket))
                    runner.thread = context
                    payload = dict(_dispatch_payload(str(uuid.uuid4()), role='critic'), ticket=ticket)
                    with self.assertRaisesRegex(ValidationError, 'independent|quarantined|Usage observation'):
                        try:
                            self.dispatch(adapters, payload)
                        finally:
                            record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
                            runner.observe(record, timeout=10)  # Reap even a refused startup context.

    def test_shared_worker_identity_unreadable_fails_closed(self):
        _, adapters = self.configured_adapter(False)
        self.dispatch(adapters, dict(_dispatch_payload('shared-worker'), ticket='EX-10'))
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review(state='other-host')
        # Corrupt the actual shared operational evidence, never substitute an observer.
        files = list((self.root / 'host-state').rglob('*execution*.sqlite*'))
        self.assertTrue(files, 'Shared protected execution identity evidence must exist in OFF')
        files[0].write_bytes(b'Fixture unreadable database')
        with self.assertRaisesRegex(ValidationError, 'independence unknown.*shared execution evidence unavailable'):
            self.agent(driver, 'critic')
        self.assertFalse(driver.budgeting.database.path.exists())

    def test_adapter_review_unreadable_shared_evidence_fails_before_launch(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        self.agent(driver, 'worker')
        driver.budgeting.operations.path.write_bytes(b'Fixture unreadable database')
        runner, adapters = self.configured_adapter(False)
        with self.assertRaisesRegex(ValidationError, 'independence unknown.*shared execution evidence unavailable'):
            self.dispatch(adapters, dict(_dispatch_payload('unreadable', role='critic'), ticket='EX-10'))
        record = json.loads((runner._budget_record_dir / 'unreadable.json').read_text())
        self.assertNotIn('pid', record)

    def test_shared_worker_unknown_context_fails_closed(self):
        config, runner, _ = self.adapter()
        runner.emit_thread, runner.exit_code, runner.usage = False, 1, False
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        self.value['budgeting']['accounting_enabled'] = False
        adapters['dispatch_ticket'].configure_budgeting(path=None, governance=self.gov,
            operating=op.validate_operating(self.value, self.gov))
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            self.dispatch(adapters, dict(_dispatch_payload('unknown-worker'), ticket='EX-10'))
        driver = self.review(state='other-host')
        with self.assertRaisesRegex(ValidationError, 'independence unknown.*context unavailable'):
            self.agent(driver, 'critic')

    def test_enforcement_still_refuses_unverified_host_limits(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        self.agent(driver, 'worker')
        self.value['budgeting'].update(accounting_enabled=True, enforcement_enabled=True)
        driver = self.review(state='enforced-review')
        with self.assertRaisesRegex(ValidationError, 'observed host token limits'):
            self.agent(driver, 'critic')
        runner, adapters = self.configured_adapter(True)
        with self.assertRaisesRegex(ValidationError, 'observed host token limits'):
            self.dispatch(adapters, dict(_dispatch_payload('enforced', role='critic'), ticket='EX-10'))
        record = json.loads((runner._budget_record_dir / 'enforced.json').read_text())
        self.assertNotIn('pid', record)
        self.assertFalse(driver.budgeting.database.path.exists())

    def test_off_failure_resume_second_launch_and_bounded_retries(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        config = dict(driver.c, key='101:7', config_hash='f'*64,
            max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=10)
        store = LoopStore(driver.state)
        self.addCleanup(store.close)
        enroll(store, config, self.candidate)
        driver.snapshot = lambda: deepcopy(self.candidate)
        driver.files = lambda candidate: ['fixture.txt']
        # Keep production agent construction/admission/run/outcome. Only the
        # external review collection is omitted, since each command fails.
        driver.review = lambda candidate, findings, run_id, files: driver.agent(
            'critic', candidate, findings, run_id, files)
        executable = Path(sys.executable)
        policy = self.root / 'host-policy.json'
        policy.write_text('Fixture pinned policy')
        driver.c.update(executables={'codex': dict(path=str(executable), sha256=sha256(executable.read_bytes()))},
            _config_path=str(policy), config_hash=sha256(policy.read_bytes()), command_timeout_seconds=10)
        launches = []
        def command(name, args, **kwargs):
            launches.append(1)
            events = [dict(type='thread.started', thread_id=str(uuid.uuid4())),
                dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
            program = ('import json; events=' + repr(events) +
                '; [print(json.dumps(e),flush=True) for e in events]; raise SystemExit(1)')
            return HostDriver.run(driver, name, ['-B', '-c', program], **kwargs)
        driver.run = command
        with patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification'), \
             patch('agentic.providers.github_review_host.enforce_pinned_awf_skill'):
            first = tick(store, config, driver)
            self.assertEqual(first['phase'], 'PAUSED')
            self.assertEqual(len(launches), 1)
            with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
                self.agent(driver, 'critic')
            self.assertEqual(len(launches), 1)
            with self.assertRaisesRegex(ValidationError, 'reconcile'):
                resume(store, config, self.candidate, 'wrong-run')
            for count in (2, 3):
                paused = store.get(config['key'])
                resumed = resume(store, config, self.candidate, paused['inflight']['id'])
                self.assertEqual(resumed['phase'], 'REVIEW')
                paused = tick(store, config, driver)
                self.assertEqual(paused['phase'], 'PAUSED')
                self.assertEqual(len(launches), count)
            with self.assertRaisesRegex(ValidationError, 'retry|recovery'):
                resume(store, config, self.candidate, paused['inflight']['id'])
        self.assertFalse(driver.budgeting.database.path.exists())
        self.assertFalse(driver.budgeting.registry.path.exists())

    def test_adapter_off_failure_reconcile_and_second_launch_without_usage(self):
        runner, adapters = self.configured_adapter(False)
        runner.exit_code, runner.usage = 1, False
        payload = _dispatch_payload('failed-off')
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            self.dispatch(adapters, payload)
        runner.exit_code = 0
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.dispatch(adapters, _dispatch_payload('unresolved-off'))
        result = adapters['observe_dispatch'](dict(payload, reconcile_nonce='operator-reconciled',
            reconcile_at=payload['begun_at']))
        self.assertEqual(result['status'], 'RECONCILED')
        self.assertEqual(result['dispatch_state'], 'FAILED')
        self.dispatch(adapters, _dispatch_payload('retry-off'))
        self.assertFalse((self.root / (self.gov['project']['id'] + '.sqlite')).exists())

    def test_adapter_off_reconciliation_cannot_reset_retry_limit(self):
        runner, adapters = self.configured_adapter(False)
        runner.exit_code, runner.usage = 1, False
        for count in range(3):
            payload = _dispatch_payload('failed-off-' + str(count))
            with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
                self.dispatch(adapters, payload)
            reconciliation = dict(payload, reconcile_nonce='operator-' + str(count), reconcile_at=payload['begun_at'])
            if count < 2:
                self.assertEqual(adapters['observe_dispatch'](reconciliation)['status'], 'RECONCILED')
                # Re-observing the same resolution consumes no fresh authority.
                self.assertEqual(adapters['observe_dispatch'](reconciliation)['status'], 'RECONCILED')
            else:
                with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                    adapters['observe_dispatch'](reconciliation)
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.dispatch(adapters, _dispatch_payload('over-cap-off'))


if __name__ == '__main__':
    unittest.main()
