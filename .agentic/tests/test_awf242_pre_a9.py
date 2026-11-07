"""Pre-A9: validated results and one protected recovery ceiling across modes."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch
from jsonschema.exceptions import ValidationError as SchemaError

import source_only
from agentic import ValidationError
from agentic.canonical import sha256
from agentic.model_routing import RoutingLedger, policy_from_config
from agentic.providers.github_review_host import HostDriver
from agentic.review_first_draft import run_first_draft
from agentic.review_loop import LoopStore, enroll, resume, resume_first_draft, tick
import test_awf242_pre_a3 as fixtures
import test_awf242_pre_a6 as pre_a6
import test_awf242_pre_a8 as pre_a8
import test_awf242_pre_a7 as pre_a7
from test_model_routing import request, capabilities
from test_reference_controller_adapter import _dispatch_payload


class ProductionPreA9Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    adapter = fixtures.ProductionPreA3Tests.adapter
    configured_adapter = pre_a6.ProductionPreA6Tests.configured_adapter
    dispatch = pre_a6.ProductionPreA6Tests.dispatch
    failed_adapter = pre_a8.ProductionPreA8Tests.failed_adapter
    failing_cli = pre_a7.ProductionPreA7Tests.failing_cli

    def malformed_cli(self, driver, content='{malformed'):
        executable = Path(sys.executable)
        policy = self.root / 'host-policy.json'
        policy.write_text('Fixture pinned policy')
        driver.c.update(executables={'codex': dict(path=str(executable), sha256=sha256(executable.read_bytes()))},
            _config_path=str(policy), config_hash=sha256(policy.read_bytes()), command_timeout_seconds=10)
        launches = []
        def command(name, args, **kwargs):
            launches.append(1)
            output = args[args.index('--output-last-message') + 1]
            events = [dict(type='thread.started', thread_id=str(uuid.uuid4())),
                dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
            program = ('import json,pathlib; pathlib.Path(' + repr(output) + ').write_text(' + repr(content) + '); '
                'events=' + repr(events) + '; [print(json.dumps(e),flush=True) for e in events]')
            return HostDriver.run(driver, name, ['-B', '-c', program], **kwargs)
        driver.run = command
        for target in ('review_qualification.validate_config_qualification', 'enforce_pinned_awf_skill'):
            mocked = patch('agentic.providers.github_review_host.' + target)
            mocked.start()
            self.addCleanup(mocked.stop)
        return launches

    def loop(self, driver):
        config = dict(driver.c, key='101:7', config_hash='f'*64,
            max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=1)
        store = LoopStore(driver.state)
        self.addCleanup(store.close)
        enroll(store, config, self.candidate)
        driver.snapshot = lambda: deepcopy(self.candidate)
        driver.files = lambda candidate: ['fixture.txt']
        driver.review = lambda candidate, findings, run_id, files: driver.agent(
            'critic', candidate, findings, run_id, files)
        return store, config

    def invalid_result(self, first_draft, enabled, enforcement, content):
        self.gov['project']['id'] = str(uuid.uuid4())
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        self.value['budgeting'].update(accounting_enabled=enabled, enforcement_enabled=enforcement)
        driver = self.review(state=str(uuid.uuid4()))
        launches = self.malformed_cli(driver, content)
        if first_draft:
            driver.verified_contract_text = lambda: 'Fixture scope'
            driver.first_draft_git_controls = lambda: {}
            driver.require_first_draft_git_controls = lambda expected: None
            driver.require_clean_first_draft_checkout = lambda: None
            config = dict(driver.c, key='101:0', pr=0, repository_id=101, config_hash='f'*64,
                max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=1)
            store = LoopStore(driver.state)
            self.addCleanup(store.close)
            with self.assertRaises((ValidationError, SchemaError)):
                run_first_draft(store, config, worker=lambda run: driver.first_draft_worker(run_id=run),
                    publisher=lambda receipt: self.fail('Invalid result cannot publish'),
                    observe_pr=lambda publication: self.fail('Invalid result cannot observe PR'))
        else:
            store, config = self.loop(driver)
            self.assertEqual(tick(store, config, driver)['phase'], 'PAUSED')
        paused = store.get(config['key'])
        run = driver.state / 'runs' / paused['inflight']['id']
        terminal = json.loads((run / 'execution-outcome.json').read_text())
        self.assertFalse(terminal['success'])
        self.assertEqual(terminal['failure_kind'], 'unknown')
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            if first_draft:
                resume_first_draft(store, config, paused['inflight']['id'])
            else:
                resume(store, config, self.candidate, paused['inflight']['id'])
        self.assertEqual(store.get(config['key'])['phase'], 'PAUSED')
        self.assertEqual(len(launches), 1)
        self.assertEqual(paused['agent_runs'], 1)
        self.assertEqual(paused['cycles'], 0)
        with driver.budgeting.operations.transaction('') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_resolutions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT success FROM execution_outcomes').fetchone()[0], 0)
        if enabled:
            with driver.budgeting._ledger()._transaction() as db:
                self.assertFalse(json.loads(db.execute('SELECT outcome FROM model_runs').fetchone()[0])['success'])
        else:
            self.assertFalse(driver.budgeting.database.path.exists())

    def test_invalid_results_cannot_resume_at_zero_cap_in_any_mode(self):
        for first in (False, True):
            # This CLI has no verified per-run token ceiling; enforced mode
            # refuses admission before any subprocess can run.
            for enabled, enforcement in ((False, False), (True, False)):
                for content in ('{malformed', '{}'):
                    with self.subTest(first_draft=first, accounting=enabled, enforcement=enforcement, content=content):
                        self.invalid_result(first, enabled, enforcement, content)

    def test_fresh_cap_three_requires_authorization_in_adapter_and_review(self):
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 3
        for enabled in (False, True):
            for host in ('adapter', 'review'):
                with self.subTest(accounting=enabled, host=host):
                    self.gov['project']['id'] = str(uuid.uuid4())
                    self.value['budgeting']['accounting_enabled'] = enabled
                    if host == 'adapter':
                        runner, adapters = self.configured_adapter(enabled)
                        with self.assertRaisesRegex(ValidationError, 'RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION'):
                            self.dispatch(adapters, dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10'))
                    else:
                        driver = self.review(state=str(uuid.uuid4()))
                        with self.assertRaisesRegex(ValidationError, 'RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION'):
                            self.agent(driver, 'critic')

    def test_malformed_results_consume_the_two_resolution_allowance(self):
        for first_draft in (False, True):
            for enabled in (False, True):
                with self.subTest(first_draft=first_draft, accounting=enabled):
                    self.gov['project']['id'] = str(uuid.uuid4())
                    self.value['budgeting']['accounting_enabled'] = enabled
                    driver = self.review(state=str(uuid.uuid4()))
                    launches = self.malformed_cli(driver)
                    if first_draft:
                        driver.verified_contract_text = lambda: 'Fixture scope'
                        driver.first_draft_git_controls = lambda: {}
                        driver.require_first_draft_git_controls = lambda expected: None
                        driver.require_clean_first_draft_checkout = lambda: None
                        config = dict(driver.c, key='101:0', pr=0, repository_id=101, config_hash='f'*64,
                            max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=1)
                        store = LoopStore(driver.state)
                        self.addCleanup(store.close)
                    else:
                        store, config = self.loop(driver)
                    for count in (1, 2, 3):
                        if first_draft:
                            with self.assertRaisesRegex(ValidationError, 'strict bounded JSON'):
                                run_first_draft(store, config, worker=lambda run: driver.first_draft_worker(run_id=run),
                                    publisher=lambda receipt: self.fail('Invalid worker cannot publish'),
                                    observe_pr=lambda publication: self.fail('Invalid worker cannot observe PR'))
                        else:
                            self.assertEqual(tick(store, config, driver)['phase'], 'PAUSED')
                        paused = store.get(config['key'])
                        recovery = (lambda: resume_first_draft(store, config, paused['inflight']['id'])) if first_draft else (
                            lambda: resume(store, config, self.candidate, paused['inflight']['id']))
                        if count < 3:
                            if enabled:
                                run = paused['inflight']['id']
                                if first_draft:
                                    run = json.loads((driver.state / 'runs' / run /
                                        'first-draft-attempt.json').read_text())['binding']['run_id']
                                driver.budgeting._ledger().resolve_failure(run, dict(verified=True,
                                    evidence_ref='fixture-terminal-proof-' + str(count), reason='Fixture recovery'))
                            recovery()
                        else:
                            with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                                recovery()
                        self.assertEqual(len(launches), count)
                        self.assertEqual(paused['cycles'], 0)
                    with driver.budgeting.operations.transaction('') as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM execution_resolutions').fetchone()[0], 2)

    def reduce_via_cli(self, ledger):
        config = deepcopy(self.gov)
        config['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        config = dict(project=config['project'], execution=dict(model_routing=config['execution']['model_routing']))
        paths = []
        for name, value in (('config', config), ('request', request(ticket_id='EX-10', role='critic',
                phase='critic', worker_context_id='UNKNOWN', worker_context_observed=False)), ('capabilities', capabilities())):
            path = self.root / (name + '.json')
            path.write_text(json.dumps(value))
            paths.extend(['--' + name, str(path)])
        result = subprocess.run([sys.executable, '-B', str(fixtures.ROOT / '.agentic/scripts/route_model.py'),
            'reserve', '--legacy-without-operating', '--project-root', str(self.root / 'installed'),
            '--ledger', str(ledger.path), *paths], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(ledger.retry_limit_history(self.gov['project']['id'])['retry_ceiling'], 0, result.stdout + result.stderr)

    def test_cli_reduction_blocks_off_resume_and_survives_ledger_corruption(self):
        driver = self.review()
        launches = self.failing_cli(driver)
        store, config = self.loop(driver)
        self.assertEqual(tick(store, config, driver)['phase'], 'PAUSED')
        paused = store.get(config['key'])
        ledger = driver.budgeting._ledger()
        self.reduce_via_cli(ledger)
        self.value['budgeting']['accounting_enabled'] = False
        restarted = self.review()
        config['_budgeting_context'] = restarted.budgeting
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            resume(store, config, self.candidate, paused['inflight']['id'])
        self.assertEqual(len(launches), 1)
        # The accepted reduction lives in operational state even if the optional
        # accounting source later becomes unreadable. It cannot be reseeded.
        ledger.path.write_bytes(b'corrupt optional accounting source')
        restarted = self.review()
        config['_budgeting_context'] = restarted.budgeting
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            resume(store, config, self.candidate, paused['inflight']['id'])
        self.assertEqual(store.get(config['key'])['phase'], 'PAUSED')

    def test_off_reduction_is_authoritative_for_routing_and_migration(self):
        _, _, _, record = self.failed_adapter(True)
        ledger = RoutingLedger(self.root / (self.gov['project']['id'] + '.sqlite'))
        self.value['budgeting']['accounting_enabled'] = False
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        reduced = self.review()
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            reduced.budgeting.operations.resolve(record['operational_binding'])
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 2
        result = ledger.reserve(self.gov['project']['id'], policy_from_config(self.gov), request(), capabilities())
        self.assertEqual(result['code'], 'RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION')
        self.assertEqual(result['authorized_retry_ceiling'], 0)
        ledger.migrate_operational_recovery(self.gov['project']['id'])
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            self.review().budgeting.operations.resolve(record['operational_binding'])


if __name__ == '__main__':
    unittest.main()
