"""Offline limiter contracts. All execution uses a synthetic local CLI."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

import source_only  # noqa: F401
from agentic import ValidationError
from agentic.token_limiter import AttemptJournal, digest
from agentic.reservation_estimator import estimate_reservation
from agentic.providers.codex_token_limiter import CodexTokenLimiter, _CodexEvents


# Recorded JSONL shapes with identities/counters replaced by synthetic values.
# No prompts, project usage measurements or raw logs are shipped.
EVENTS = [dict(type='thread.started', thread_id='synthetic-thread'),
          dict(type='turn.started'),
          dict(type='turn.completed', usage=dict(input_tokens=70,
               cached_input_tokens=60, output_tokens=30))]


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='awf315-fake-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.script = self.root / 'fake_cli.py'
        self.script.write_text('import sys\n', encoding='utf-8')
        self.binding = dict(project_id='synthetic-project', ticket_id='SYN-315',
                            run_id='synthetic-run', dispatch_id='synthetic-dispatch',
                            session_id='synthetic-session', role='worker')
        self.reservation = dict(binding=self.binding, reservation_id='synthetic-reservation',
                                reservation_tokens=1000, policy_hash='a' * 64,
                                operating_hash='b' * 64)
        self.journal = AttemptJournal(self.root / 'host' / 'attempts.sqlite3')
        self.runtime = dict(cli_version='synthetic-1', platform='synthetic-os',
                            effective_config_sha256='e' * 64)
        self.receipt_enabled = False
        self.receipt_change = None
        self.host = mock.Mock()
        self.host.observe_reservation.side_effect = lambda binding, identity: deepcopy(self.reservation)
        self.host.observe_runtime.side_effect = lambda launch: dict(deepcopy(self.runtime),
                                                              applied_launch_sha256=digest(launch))
        self.host.observe_qualification.side_effect = self.qualification
        self.limiter = CodexTokenLimiter(self.journal, self.host,
                      command=(sys.executable, '-B', str(self.script)),
                      project_root=self.root, clock=lambda: 100)

    def qualification(self, context):
        if not self.receipt_enabled:
            return None
        receipt = dict(version=1, status='QUALIFIED', qualified_for=deepcopy(context),
                       receipt_id='synthetic-receipt', evidence_sha256='c' * 64,
                       expires_at=200, strict_total_tokens=True)
        if self.receipt_change:
            self.receipt_change(receipt)
        return receipt

    def prepare(self):
        return self.limiter.prepare(self.binding, reservation_id='synthetic-reservation',
                 attempt_id=str(uuid.uuid4()), model='synthetic-model', effort='high')

    def fake(self, events=EVENTS, suffix=''):
        payload = b'\n'.join(json.dumps(event).encode() for event in events) + b'\n'
        self.script.write_text('import sys,time\n'
            'assert "features.rollout_budget.enforce_limit=true" in sys.argv\n'
            'assert "features.rollout_budget.limit_tokens=1000" in sys.argv\n'
            'assert "features.rollout_budget.sampling_token_weight=1.0" in sys.argv\n'
            'assert "features.rollout_budget.prefill_token_weight=1.0" in sys.argv\n'
            'sys.stdin.buffer.read()\n'
            'data=' + repr(payload) + '\n'
            'for byte in data:\n sys.stdout.buffer.write(bytes([byte])); sys.stdout.buffer.flush()\n'
            + suffix, encoding='utf-8')

    def test_unqualified_preparation_is_durable_and_cannot_launch(self):
        plan = self.prepare()
        self.assertFalse(plan['limits_enforced'])
        self.assertEqual(plan, AttemptJournal(self.journal.path).read(plan['attempt_id'])['plan'])
        with mock.patch('agentic.providers.codex_token_limiter.run_bounded') as launch:
            with self.assertRaisesRegex(ValidationError, 'qualification'):
                self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
        launch.assert_not_called()

    def test_invalid_reservation_is_rejected_before_journal_creation(self):
        for field, value in (('reservation_tokens', 0), ('reservation_tokens', True),
                             ('policy_hash', 'invalid'), ('reservation_id', 'other')):
            with self.subTest(field=field, value=value):
                original = deepcopy(self.reservation)
                self.reservation[field] = value
                with self.assertRaises(ValidationError):
                    self.prepare()
                self.reservation = original
        self.assertFalse(self.journal.path.exists())

    def test_receipts_must_match_exact_runtime_settings_and_expiry(self):
        self.receipt_enabled = True
        changes = [lambda r: r.update(expires_at=100),
                   lambda r: r.update(strict_total_tokens=False),
                   lambda r: r['qualified_for'].update(cli_version='other'),
                   lambda r: r['qualified_for'].update(platform='other'),
                   lambda r: r['qualified_for']['settings'].update(
                       **{'features.rollout_budget.limit_tokens': 999}),
                   lambda r: r['qualified_for']['settings'].update(
                       **{'features.rollout_budget.sampling_token_weight': True}),
                   lambda r: r.update(evidence_sha256='bad')]
        for change in changes:
            with self.subTest(change=change):
                self.receipt_change = change
                # Invalid receipts refuse preparation rather than silently qualify.
                with self.assertRaises(ValidationError):
                    self.prepare()

    def test_attempt_run_and_reservation_identities_cannot_be_reused(self):
        self.prepare()
        with self.assertRaisesRegex(ValidationError, 'identity'):
            self.prepare()

    def test_qualified_fake_cli_drives_native_settings_and_finishes_once(self):
        self.fake()
        self.receipt_enabled = True
        plan = self.prepare()
        result = self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
        self.assertEqual(100, result['actual_tokens'])
        self.assertEqual(100, result['lower_bound_tokens'])
        self.assertTrue(result['limits_enforced'])
        self.assertEqual(result, self.limiter.finish(plan['attempt_id'], success=True))
        with self.assertRaises(ValidationError):
            self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)

    def test_executable_or_version_change_prevents_launch(self):
        self.receipt_enabled = True
        plan = self.prepare()
        self.runtime['cli_version'] = 'changed'
        with self.assertRaises(ValidationError):
            self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
        self.runtime['cli_version'] = 'synthetic-1'
        self.script.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValidationError):
            self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)

    def test_interrupted_fake_preserves_lower_bound_but_unknown_total(self):
        self.fake(EVENTS + [dict(type='turn.started')], 'raise SystemExit(3)\n')
        self.receipt_enabled = True
        plan = self.prepare()
        result = self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
        self.assertIsNone(result['actual_tokens'])
        self.assertEqual(100, result['lower_bound_tokens'])

    def test_recorded_terminal_tails_never_settle_a_completed_prefix(self):
        prefix = b'\n'.join(json.dumps(event).encode() for event in EVENTS) + b'\n'
        cases = [('idle-failure', b'', 1), ('idle-cancel', b'', -9),
                 ('cancelled-idle', b'', 'cancel'), ('unobserved-stream', b'', None),
                 ('mid-turn-failure', b'{"type":"turn.started"}\n', 1),
                 ('started', b'{"type":"turn.started"}\n', 0),
                 ('truncated', b'{"type":"turn.st', 0),
                 ('clean', b'', 0)]
        for name, tail, returncode in cases:
            with self.subTest(tail=name):
                self.set_new_identity()
                self.receipt_enabled = True
                plan = self.prepare()
                def replay(*args, **kwargs):
                    kwargs['stream_sink']('stdout', prefix + tail)
                    if returncode == 'cancel':
                        raise ValidationError('SUBPROCESS_CANCELLED: synthetic idle cancellation')
                    if returncode is None:
                        raise ValidationError('SUBPROCESS_STDOUT_LIMIT: synthetic incomplete stream')
                    return mock.Mock(returncode=returncode)
                with mock.patch('agentic.providers.codex_token_limiter.run_bounded', side_effect=replay):
                    result = self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
                self.assertEqual(100, result['lower_bound_tokens'])
                if name in ('clean', 'idle-failure', 'idle-cancel', 'cancelled-idle'):
                    self.assertEqual(100, result['actual_tokens'])
                    self.assertEqual(0, result['retained_reservation_tokens'])
                else:
                    self.assertIsNone(result['actual_tokens'])
                    self.assertEqual(1000, result['retained_reservation_tokens'])

    def test_malformed_truncated_missing_and_duplicate_usage_are_unknown(self):
        cases = [[], EVENTS[:-1], EVENTS + [EVENTS[-1]],
                 EVENTS + [dict(type='turn.started'), dict(type='turn.failed')],
                 EVENTS + [dict(type='turn.completed', usage={'input_tokens': True})]]
        for events in cases:
            with self.subTest(events=events):
                self.set_new_identity()
                self.fake(events)
                self.receipt_enabled = True
                plan = self.prepare()
                result = self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
                self.assertIsNone(result['actual_tokens'])

    def set_new_identity(self):
        self.binding['run_id'] = str(uuid.uuid4())
        self.reservation['binding'] = deepcopy(self.binding)
        self.reservation['reservation_id'] = 'synthetic-reservation'
        # Each independent test attempt uses a separate protected journal.
        self.journal = AttemptJournal(self.root / str(uuid.uuid4()) / 'attempts.sqlite3')
        self.limiter.journal = self.journal

    def test_stop_is_durable_and_never_asserts_remote_termination(self):
        plan = self.prepare()
        receipt = self.limiter.stop(plan['attempt_id'], reason='OWNER_STOP')
        self.assertFalse(receipt['remote_termination_verified'])
        restarted = CodexTokenLimiter(AttemptJournal(self.journal.path), self.host,
                          command=(sys.executable, '-B', str(self.script)),
                          project_root=self.root, clock=lambda: 100)
        self.assertIsNone(restarted.finish(plan['attempt_id'], success=False)['actual_tokens'])
        with self.assertRaises(ValidationError):
            restarted.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)

    def test_idle_owner_stop_keeps_completed_total(self):
        plan, decoder = self.decoder()
        decoder.feed('stdout', b'\n'.join(json.dumps(event).encode() for event in EVENTS) + b'\n')
        decoder.end()
        self.limiter.stop(plan['attempt_id'], reason='OWNER_STOP')
        result = self.limiter.finish(plan['attempt_id'], success=False)
        self.assertEqual(100, result['actual_tokens'])
        self.assertEqual(0, result['retained_reservation_tokens'])
        self.assertFalse(result['success'])

    def test_sequence_replay_is_idempotent_gaps_and_resets_are_unknown(self):
        plan = self.prepare()
        self.journal.claim(plan['attempt_id'])
        observation = dict(sequence=1, event_id='d' * 64, counter_tokens=100,
                           complete=True, unknown=False)
        first = self.limiter.observe(plan['attempt_id'], observation)
        self.assertEqual(first, self.limiter.observe(plan['attempt_id'], observation))
        for sequence, counter in ((3, 200), (4, 2)):
            self.limiter.observe(plan['attempt_id'], dict(observation, sequence=sequence,
                        event_id=hashlib.sha256(str(sequence).encode()).hexdigest(), counter_tokens=counter))
        result = self.limiter.finish(plan['attempt_id'], success=True)
        self.assertIsNone(result['actual_tokens'])
        self.assertEqual(200, result['lower_bound_tokens'])

    def test_restart_cannot_relaunch_claimed_attempt_or_refund_unknown(self):
        plan = self.prepare()
        self.journal.claim(plan['attempt_id'])
        with self.assertRaises(ValidationError):
            AttemptJournal(self.journal.path).claim(plan['attempt_id'])
        result = self.limiter.finish(plan['attempt_id'], success=False)
        self.assertIsNone(result['actual_tokens'])
        self.assertEqual(plan['reservation_tokens'], result['retained_reservation_tokens'])

    def test_rough_estimator_is_positive_versioned_and_has_no_authority(self):
        low = estimate_reservation(minutes=1, difficulty='low')
        high = estimate_reservation(minutes=2, difficulty='high')
        self.assertGreater(low['reservation_tokens'], 0)
        self.assertGreater(high['reservation_tokens'], low['reservation_tokens'])
        self.assertTrue(high['rough'])
        self.assertFalse(high['limits_enforced'])
        self.assertIn('generic', high['rate_source'])
        for minutes, difficulty in ((0, 'low'), (True, 'low'), (1, 'unknown'), (float('inf'), 'high')):
            with self.subTest(minutes=minutes, difficulty=difficulty), self.assertRaises(ValidationError):
                estimate_reservation(minutes=minutes, difficulty=difficulty)

    def decoder(self):
        plan = self.prepare()
        self.journal.claim(plan['attempt_id'])
        return plan, _CodexEvents(self.limiter, plan['attempt_id'])

    def test_decoder_fragmentation_and_coalescing_preserve_exact_total(self):
        plan, decoder = self.decoder()
        raw = b'\n'.join(json.dumps(event).encode() for event in EVENTS) + b'\n'
        for byte in raw:
            decoder.feed('stdout', bytes([byte]))
        decoder.feed('stderr', b'synthetic diagnostic')
        decoder.end()
        self.assertEqual(100, self.limiter.finish(plan['attempt_id'], success=True)['actual_tokens'])

    def test_truncated_malformed_duplicate_keys_and_overflow_poison_completed_prefix(self):
        for raw in (b'{', b'not-json\n', b'\xff\n', b'{}\n',
                    b'{"type":"turn.started","type":"turn.completed"}\n',
                    b'x' * 65_537, b'{"type":"turn.completed","usage":{"input_tokens":9223372036854775808,"output_tokens":1}}\n'):
            with self.subTest(raw=raw[:50]):
                self.set_new_identity()
                plan, decoder = self.decoder()
                decoder.feed('stdout', b'\n'.join(json.dumps(event).encode() for event in EVENTS) + b'\n')
                decoder.feed('stdout', raw)
                decoder.end()
                outcome = self.limiter.finish(plan['attempt_id'], success=True)
                self.assertIsNone(outcome['actual_tokens'])
                self.assertEqual(100, outcome['lower_bound_tokens'])

    def test_duplicate_completion_does_not_double_count_lower_bound(self):
        plan, decoder = self.decoder()
        decoder.feed('stdout', b'\n'.join(json.dumps(event).encode() for event in EVENTS + [EVENTS[-1]]) + b'\n')
        result = self.limiter.finish(plan['attempt_id'], success=True)
        self.assertEqual(100, result['lower_bound_tokens'])
        self.assertIsNone(result['actual_tokens'])

    def test_observed_overrun_stops_and_retains_ceiling_violation(self):
        plan, decoder = self.decoder()
        events = deepcopy(EVENTS)
        events[-1]['usage']['input_tokens'] = 1001
        decoder.feed('stdout', b'\n'.join(json.dumps(event).encode() for event in events) + b'\n')
        result = self.limiter.finish(plan['attempt_id'], success=False)
        self.assertTrue(result['ceiling_violation'])
        self.assertEqual(1031, result['lower_bound_tokens'])
        self.assertEqual(1031, result['actual_tokens'])

    def test_concurrent_claim_has_exactly_one_winner(self):
        plan = self.prepare()
        def claim():
            try:
                AttemptJournal(self.journal.path).claim(plan['attempt_id'])
                return True
            except ValidationError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([False, True], sorted(pool.map(lambda _: claim(), range(2))))

    def test_stop_cancels_owned_fake_cli_and_final_usage_remains_unknown(self):
        # No PID lookup: run_bounded owns and reaps the fake process tree.
        self.fake(EVENTS + [dict(type='turn.started')], 'time.sleep(30)\n')
        self.receipt_enabled = True
        plan = self.prepare()
        result = []
        errors = []
        def run():
            try:
                result.append(self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root))
            except BaseException as exc:
                errors.append(exc)
        child = threading.Thread(target=run)
        child.start()
        try:
            deadline = time.monotonic() + 10
            while self.journal.read(plan['attempt_id'])['snapshot']['sequence'] < 4:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            self.limiter.stop(plan['attempt_id'], reason='OWNER_STOP')
        finally:
            child.join(35)
        self.assertFalse(child.is_alive())
        self.assertEqual([], errors)
        self.assertIsNone(result[0]['actual_tokens'])
        self.assertEqual(100, result[0]['lower_bound_tokens'])

    def test_effective_configuration_drift_refuses_before_launch(self):
        self.receipt_enabled = True
        plan = self.prepare()
        self.runtime['effective_config_sha256'] = 'f' * 64
        with mock.patch('agentic.providers.codex_token_limiter.run_bounded') as launch:
            with self.assertRaisesRegex(ValidationError, 'runtime changed'):
                self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
        launch.assert_not_called()

    def test_unapplied_settings_or_other_worktree_refuse_before_launch(self):
        self.receipt_enabled = True
        plan = self.prepare()
        other = self.root / 'other-project'
        other.mkdir()
        with self.assertRaisesRegex(ValidationError, 'project root'):
            self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=other)
        self.host.observe_runtime.side_effect = lambda launch: dict(self.runtime,
                                                        applied_launch_sha256='0' * 64)
        with self.assertRaisesRegex(ValidationError, 'settings observation'):
            self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)

    def test_saved_enforcement_flag_cannot_replace_a_current_receipt(self):
        plan = self.prepare()
        restored = self.journal.read(plan['attempt_id'])
        restored['plan']['limits_enforced'] = True
        self.assertIsNone(restored['plan']['qualification_receipt'])
        with mock.patch.object(self.journal, 'read', return_value=restored), \
                mock.patch('agentic.providers.codex_token_limiter.run_bounded') as launch:
            with self.assertRaisesRegex(ValidationError, 'qualification'):
                self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
