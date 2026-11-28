"""Interrupted CLI telemetry must not turn a completed prefix into a total."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

import source_only  # noqa: F401
from agentic import ValidationError, operating as op
from agentic.budgeting import codex_jsonl_usage, codex_usage, fresh_settings
from agentic.canonical import sha256
from agentic.providers.github_review_host import HostDriver
from test_operating import governance
import test_awf242_pre_a3 as fixtures


class UsageTailTests(unittest.TestCase):
    def completed(self):
        return dict(type='turn.completed', usage=dict(input_tokens=70,
                    cached_input_tokens=60, output_tokens=30))

    def test_completed_prefix_then_interrupted_turn_is_unknown(self):
        self.assertIsNone(codex_usage([self.completed(), dict(type='turn.started')]))

    def test_failed_or_error_open_turn_is_unknown(self):
        for kind in ('turn.failed', 'error'):
            with self.subTest(kind=kind):
                self.assertIsNone(codex_usage([self.completed(), dict(type='turn.started'),
                                              dict(type=kind)]))

    def test_idle_error_does_not_discard_completed_usage(self):
        self.assertEqual(100, codex_usage([self.completed(), dict(type='error')]))

    def test_unmatched_starts_cannot_be_hidden_by_one_completion(self):
        self.assertIsNone(codex_usage([dict(type='turn.started'),
            dict(type='turn.started'), self.completed()]))

    def test_invalid_event_or_unclosed_item_tail_is_unknown(self):
        for tail in (None, {}, dict(type='unrecognized.tail'),
                     dict(type='item.started')):
            with self.subTest(tail=tail):
                self.assertIsNone(codex_usage([self.completed(), tail]))

    def test_completed_turn_then_thread_started_keeps_known_total(self):
        self.assertEqual(100, codex_usage([self.completed(), dict(type='thread.started')]))

    def test_completed_turns_keep_cache_as_subset(self):
        self.assertEqual(200, codex_usage([dict(type='turn.started'), self.completed(),
                                        dict(type='turn.started'), self.completed()]))

    def test_jsonl_requires_complete_transport_and_parseable_tail(self):
        raw = json.dumps(self.completed()).encode() + b'\n'
        for data, complete in ((raw, False), (raw+b'{', True), (raw+b'not-json\n', True)):
            with self.subTest(data=data, complete=complete):
                self.assertEqual(dict(actual_tokens=None, lower_bound_tokens=100),
                    codex_jsonl_usage(data, stream_complete=complete))
        self.assertEqual(dict(actual_tokens=None, lower_bound_tokens=0), codex_jsonl_usage(b''))
        self.assertEqual(100, codex_jsonl_usage(raw)['actual_tokens'])


class HostUsageTailTests(unittest.TestCase):
    review = fixtures.ProductionPreA3Tests.review
    summary = fixtures.ProductionPreA3Tests.summary

    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('awf315-tail-' + uuid.uuid4().hex)
        self.root.mkdir()
        (self.root / 'worker').mkdir()
        (self.root / 'critic').mkdir()
        environment = mock.patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'host-state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True)
        self.candidate = dict(repository_id=101, pr=7, head='a'*40, base='b'*40,
            head_ref='codex/EX-10-fixture', base_ref='main')

    def test_accounting_off_retention_matches_578f75ab(self):
        # Recorded baseline: outcome, error code and every retained byte.
        # Completed prefixes survive cancellation and unobserved transport loss.
        self.value['budgeting'] = fresh_settings()
        driver = self.review()
        config = self.root / 'host-policy.json'
        config.write_text('Fixture host policy')
        executable = Path(sys.executable)
        driver.c.update(executables={'codex': dict(path=str(executable),
            sha256=sha256(executable.read_bytes()))}, _config_path=str(config),
            config_hash=sha256(config.read_bytes()), command_timeout_seconds=10)
        events = [dict(type='thread.started', thread_id='11111111-1111-4111-8111-111111111111'),
                  dict(type='turn.started'),
                  dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
        prefix = b'\n'.join(json.dumps(e).encode() for e in events) + b'\n'
        cases = (('cancel-unfinished', b'{"type":"turn.st', 'SUBPROCESS_CANCELLED'),
                 ('cancel-completed', b'', 'SUBPROCESS_CANCELLED'),
                 ('transport', b'', 'SUBPROCESS_STDOUT_LIMIT'),
                 ('clean', b'', None))
        for name, tail, error in cases:
            with self.subTest(case=name):
                directory = self.root / name
                directory.mkdir()
                raw = prefix + tail
                def run(*args, **kwargs):
                    kwargs['stream_sink']('stdout', raw)
                    if error:
                        raise ValidationError(error + ': synthetic')
                    return SimpleNamespace(stdout=raw, stderr=b'', returncode=0)
                with mock.patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification'), \
                     mock.patch('agentic.providers.github_review_host.enforce_pinned_awf_skill'), \
                     mock.patch('agentic.providers.github_review_host.run_bounded', side_effect=run):
                    outcome, code = None, None
                    try:
                        outcome = HostDriver.run(driver, 'codex', ['exec'],
                            log=directory / 'codex.jsonl')
                    except ValidationError as exc:
                        code = str(exc).split(':', 1)[0]
                retained = {p.name: p.read_bytes() for p in directory.iterdir()}
                self.assertEqual((None if error else '', error, {'codex.jsonl': prefix}),
                                 (outcome, code, retained))
                self.assertFalse(getattr(driver, '_incomplete_codex_usage', {}))

    def test_real_host_failure_and_cancel_tail_accounting_and_pre_run(self):
        cases = (('cancel-mid-turn', b'{"type":"turn.started"}\n', True, None),
                 ('failure-mid-turn', b'{"type":"turn.started"}\n', False, None),
                 ('cancel-truncated', b'{"type":"turn.st', True, None),
                 ('failure-truncated', b'{"type":"turn.st', False, None),
                 ('failure-unparseable', b'not-json\n', False, None),
                 ('failure-idle', b'', False, 100),
                 ('cancel-idle', b'', True, 100))
        for enforced in (False, True):
            for name, tail, cancelled, expected in cases:
                with self.subTest(mode='pre_run' if enforced else 'accounting', case=name):
                    # Isolate operational failures and retained reservations.
                    self.setUp()
                    self.value['budgeting'].update(enforcement_enabled=enforced,
                        enforcement_mode='pre_run', max_tokens_per_ticket=4_000_000,
                        max_tokens_per_project=4_000_000)
                    driver = self.review()
                    config = self.root / 'host-policy.json'
                    config.write_text('Fixture host policy')
                    executable = Path(sys.executable)
                    driver.c.update(executables={'codex': dict(path=str(executable),
                        sha256=sha256(executable.read_bytes()))}, _config_path=str(config),
                        config_hash=sha256(config.read_bytes()), command_timeout_seconds=10,
                        agent_timeout_seconds=0.5 if cancelled else 10)
                    thread = str(uuid.uuid4())
                    events = [dict(type='thread.started', thread_id=thread),
                              dict(type='turn.started'),
                              dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
                    raw = b'\n'.join(json.dumps(e).encode() for e in events) + b'\n' + tail
                    program = ('import sys,time; sys.stdout.buffer.write(' + repr(raw) +
                        '); sys.stdout.buffer.flush(); ' +
                        ('time.sleep(30)' if cancelled else 'raise SystemExit(3)'))
                    driver.run = lambda name, args, **kw: HostDriver.run(driver, name,
                        ['-B', '-c', program], **kw)
                    run = str(uuid.uuid4())
                    with mock.patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification'), \
                         mock.patch('agentic.providers.github_review_host.enforce_pinned_awf_skill'):
                        with self.assertRaises(ValidationError):
                            driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
                    with driver.budgeting._ledger()._transaction() as db:
                        row = dict(db.execute('SELECT * FROM model_runs WHERE run_id=?', (run,)).fetchone())
                    self.assertEqual('settled', row['status'])
                    self.assertEqual(expected, row['actual_tokens'])
                    self.assertFalse(json.loads(row['outcome'])['success'])
                    if expected is None:
                        log = driver.state / 'runs' / run / 'codex.jsonl'
                        marker = log.with_name(log.name + '.usage-incomplete.json')
                        if marker.exists():
                            diagnostic = json.loads(marker.read_text())
                        elif log.exists():
                            diagnostic = codex_jsonl_usage(log.read_bytes())
                        else:
                            # Rejected machine evidence leaves no on-disk
                            # artifact; its counters remain diagnostic only.
                            diagnostic = driver._incomplete_codex_usage[str(log.absolute())]
                        self.assertEqual(dict(actual_tokens=None, lower_bound_tokens=100),
                            diagnostic)
                        if name == 'cancel-truncated':
                            self.assertFalse(log.exists())
                            self.assertFalse(marker.exists())
                        if name.startswith('failure-') and 'turn' not in name:
                            self.assertFalse(marker.exists())
                    summary = self.summary(driver)
                    self.assertEqual(int(expected is None), summary['unknown_runs'])
                    self.assertEqual(expected or 0, summary['known_tokens'])
                    if enforced:
                        self.assertEqual(1_100_000 if expected is None else 0,
                            summary['unresolved_reserved_tokens'])

    def test_unobserved_transport_is_unknown_after_host_restart(self):
        driver = self.review()
        config = self.root / 'host-policy.json'
        config.write_text('Fixture host policy')
        executable = Path(sys.executable)
        driver.c.update(executables={'codex': dict(path=str(executable),
            sha256=sha256(executable.read_bytes()))}, _config_path=str(config),
            config_hash=sha256(config.read_bytes()), command_timeout_seconds=10)
        driver.run = HostDriver.run.__get__(driver)
        events = [dict(type='thread.started', thread_id=str(uuid.uuid4())),
                  dict(type='turn.started'),
                  dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
        raw = b'\n'.join(json.dumps(e).encode() for e in events) + b'\n'
        def incomplete(*args, **kwargs):
            kwargs['stream_sink']('stdout', raw)
            raise ValidationError('SUBPROCESS_STDOUT_LIMIT: synthetic incomplete stream')
        run = str(uuid.uuid4())
        with mock.patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification'), \
             mock.patch('agentic.providers.github_review_host.enforce_pinned_awf_skill'), \
             mock.patch('agentic.providers.github_review_host.run_bounded', side_effect=incomplete):
            with self.assertRaisesRegex(ValidationError, 'SUBPROCESS_STDOUT_LIMIT'):
                driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
        directory = driver.state / 'runs' / run
        self.assertFalse((directory / 'codex.jsonl').exists())
        self.assertEqual(dict(actual_tokens=None, lower_bound_tokens=100),
            json.loads((directory / 'codex.jsonl.usage-incomplete.json').read_text()))
        admission = json.loads((directory / 'budget-admission.json').read_text())
        restarted = self.review()
        observed = restarted.observe_budget_outcome(admission, dict(session_id=None, success=False))
        self.assertIsNone(observed['actual_tokens'])


if __name__ == '__main__':
    unittest.main()
