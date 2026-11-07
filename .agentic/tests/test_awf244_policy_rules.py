"""Accepted calendar policy at reader, production and diagnostic boundaries."""
from contextlib import closing, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError, operating as op
from agentic.canonical import canonical
from agentic.budgeting import fresh_settings, legacy_settings, resolve_settings
from agentic.continuous_controller import production_review_batching_cycle
from agentic.operating_calendar import effective_calendar, next_review_presentation
from agentic.protected_settings import effective_snapshot, require_budget_policy
from agentic.review_batching import ReviewBatchStore
from settings_pr_fixtures import publish_fixture_settings
import test_awf242_settings_pr as settings
import test_awf244_window_regressions as windows
import test_awf244_review_batching as batching


class PolicyReaderTests(unittest.TestCase):
    setUp = settings.SettingsPRTests.setUp
    git = settings.SettingsPRTests.git
    write = settings.SettingsPRTests.write
    publish = settings.SettingsPRTests.publish

    def show(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = op.main(['--root', str(self.root), *arguments])
        return code, output.getvalue()

    def test_normalized_defaults_never_mask_accepted_changes_in_either_direction(self):
        for enabled in (False, True):
            with self.subTest(accepted_enabled=enabled):
                accepted = deepcopy(self.value)
                accepted['operating_calendar']['review_batching']['enabled'] = enabled
                self.publish(accepted)
                working = deepcopy(self.value)
                working['operating_calendar'] = {} if not enabled else {'review_batching': {'enabled': False}}
                self.write(working)
                result = op.read_operating(self.root, self.gov)
                self.assertEqual(enabled, effective_calendar(result.config)['review_batching']['enabled'])

    def test_sources_describe_each_domain_and_show_both_mixed_cases(self):
        for legacy in ('budgeting', 'operating_calendar'):
            with self.subTest(legacy=legacy):
                value = deepcopy(self.value)
                value.pop(legacy)
                self.publish(value)
                code, output = self.show('show', '--json')
                self.assertEqual(0, code)
                sources = json.loads(output)['protected_settings']['sources']
                other = 'operating_calendar' if legacy == 'budgeting' else 'budgeting'
                self.assertEqual('legacy retained; default branch', sources[legacy])
                self.assertEqual('default branch', sources[other])
                code, output = self.show('show')
                self.assertEqual(0, code)
                self.assertIn(legacy + ': legacy retained; default branch', output)
                self.assertIn(other + ': default branch', output)

    def test_unverified_calendar_refuses_shared_protected_decision_check(self):
        self.git('remote', 'add', 'origin', 'https://example.invalid/policy.git')
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        planted = deepcopy(self.value)
        planted['operating_calendar']['review_batching']['enabled'] = False
        snapshot = effective_snapshot(self.root, self.gov, op.validate_operating(planted, self.gov))
        self.assertFalse(snapshot.provenance['protected_settings']['budget_admission'])
        with self.assertRaisesRegex(ValidationError, 'refused'):
            require_budget_policy(snapshot)

    def test_absent_default_file_accepts_defaults_or_legacy_for_each_domain(self):
        self.git('rm', '-q', op.CONFIG)
        self.git('commit', '-qm', 'Accepted absent operating file')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        for legacy in ((), ('budgeting',), ('operating_calendar',), ('budgeting', 'operating_calendar')):
            with self.subTest(legacy=legacy):
                working = deepcopy(self.value)
                working['budgeting'].update(accounting_enabled=True, enforcement_enabled=True)
                working['operating_calendar']['review_batching']['enabled'] = False
                working['operating_calendar']['timezone'] = 'UTC'
                for name in legacy:
                    working.pop(name)
                self.write(working)
                snapshot = op.read_operating(self.root, self.gov)
                report = snapshot.provenance['protected_settings']
                self.assertIn('absent', report['policy_observation'])
                self.assertTrue(report['budget_admission'])
                self.assertIsNone(report['budget_refusal'])
                require_budget_policy(snapshot)
                expected_budget = legacy_settings() if 'budgeting' in legacy else fresh_settings()
                self.assertEqual(expected_budget, resolve_settings(snapshot.config))
                self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))
                for name in ('budgeting', 'operating_calendar'):
                    self.assertEqual(name not in legacy, name in snapshot.config)
                code, output = self.show('show', '--json')
                self.assertEqual(0, code)
                shown = json.loads(output)
                self.assertTrue(shown['budget_admission'])
                self.assertEqual(effective_calendar({}), shown['effective_operating_calendar'])
                expected = {name: 'legacy retained' if name in legacy else 'fresh defaults'
                            for name in ('budgeting', 'operating_calendar', 'reporting')}
                self.assertEqual(expected, shown['protected_settings']['sources'])
                from agentic.reporting.settings import effective_reporting
                self.assertEqual({'mode': 'off', 'bindings': []}, effective_reporting(shown['configuration']))
                code, output = self.show('show')
                self.assertEqual(0, code)
                for name, source in expected.items():
                    self.assertIn(name + ': ' + source, output)

    def test_rejected_calendar_values_are_not_echoed_by_show_or_differences(self):
        sentinel = 'REJECTED_CALENDAR_SENTINEL'
        working = deepcopy(self.value)
        working['operating_calendar'] = {'timezone': sentinel}
        self.write(working)
        for flags in ((), ('--json',)):
            code, output = self.show('show', *flags)
            self.assertNotIn(sentinel, output)
            if code == 0:
                self.assertIn('operating_calendar', output)
            else:
                self.assertEqual(2, code)
        proposed = self.root / 'proposed.yaml'
        proposed.write_bytes(op._json(working))
        code, output = self.show('validate', '--file', str(proposed))
        self.assertEqual(2, code)
        self.assertNotIn(sentinel, output)
        self.publish(working)
        self.write(self.value)
        for flags in ((), ('--json',)):
            code, output = self.show('show', *flags)
            self.assertEqual(2, code)
            self.assertNotIn(sentinel, output)

    def test_duplicate_and_parse_errors_never_echo_source_at_output_paths(self):
        sentinel = 'REJECTED_YAML_SENTINEL'
        invalids = [
            ('operating_calendar:\n  ' + sentinel + ': 1\n  ' + sentinel + ': 2\n').encode(),
            ('operating_calendar: [' + sentinel + '\n').encode(),
        ]
        proposed = self.root / 'proposed.yaml'
        for raw in invalids:
            for route in ('proposed', 'working', 'default'):
                with self.subTest(raw=raw, route=route):
                    self.publish(self.value)
                    if route == 'proposed':
                        proposed.write_bytes(raw)
                        arguments = ('validate', '--file', str(proposed))
                    elif route == 'working':
                        (self.root / op.CONFIG).write_bytes(raw)
                        arguments = ('show', '--json')
                    else:
                        (self.root / op.CONFIG).write_bytes(raw)
                        self.git('add', op.CONFIG)
                        self.git('commit', '-qm', 'Invalid policy fixture')
                        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
                        self.write(self.value)
                        arguments = ('show', '--json')
                    code, output = self.show(*arguments)
                    self.assertEqual(2, code)
                    self.assertNotIn(sentinel, output)
                    if route == 'default':
                        self.assertIn('OPERATING_CONFIG.yaml', output)
                        self.assertIn('duplicate keys, malformed YAML or unsupported values', output)
                    report = op.inspect_operating(self.root, self.gov)
                    self.assertNotIn(sentinel, json.dumps(report))


class ProductionPolicyTests(unittest.TestCase):
    setUp = windows.WindowRegressions.setUp
    observe_ready = windows.WindowRegressions.observe_ready
    observe_candidate = windows.WindowRegressions.observe_candidate
    deliver = windows.WindowRegressions.deliver
    readback = windows.WindowRegressions.readback
    intent_states = windows.WindowRegressions.intent_states
    cycle = windows.WindowRegressions.cycle

    def supplied(self, enabled):
        value = op.read_operating(self.owner.root, self.owner.gov).config
        value['operating_calendar']['review_batching']['enabled'] = enabled
        return replace(op.validate_operating(value, self.owner.gov),
            _provenance_json=canonical({'path': str(self.owner.root / op.CONFIG)}))

    def batch(self, snapshot):
        self.now = '2026-11-07T10:00:00Z'
        return production_review_batching_cycle(self.store,
            repository_root=self.owner.root, config=self.owner.gov, operating_snapshot=snapshot,
            calendar=effective_calendar(snapshot.config), binding=self.binding, clock=lambda: self.now,
            observe_owner_ready=self.observe_ready, observe_review_candidate=self.observe_candidate,
            deliver_review_batch=self.deliver, observe_review_delivery=self.readback)

    def unverify(self):
        from types import SimpleNamespace
        repository = SimpleNamespace(root=self.owner.root)
        settings.SettingsPRTests.git(repository, 'remote', 'add', 'origin', 'https://example.invalid/policy.git')
        settings.SettingsPRTests.git(repository, 'symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        value = self.supplied(False).config
        (self.owner.root / op.CONFIG).write_bytes(op._json(value))

    def test_direct_batching_ignores_supplied_calendar_in_both_directions(self):
        from settings_pr_fixtures import publish_fixture_settings
        for accepted_enabled in (True, False):
            with self.subTest(accepted_enabled=accepted_enabled):
                accepted = self.supplied(accepted_enabled).config
                publish_fixture_settings(self, self.owner.root, self.owner.gov, accepted)
                result = self.batch(self.supplied(not accepted_enabled))
                self.assertEqual([], result['errors'])
                self.assertEqual(0 if accepted_enabled else 1, len(self.delivered))

    def test_full_controller_ignores_supplied_snapshot(self):
        forged = self.supplied(False)
        real_read = op.read_operating
        first = True
        def read(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                return forged
            return real_read(*args, **kwargs)
        with patch.object(op, 'read_operating', side_effect=read):
            result = self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual([], result['errors'])
        self.assertTrue(result['operating_calendar']['review_batching']['enabled'])
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())

    def test_absent_default_file_allows_controller_and_batching_on_defaults(self):
        working = self.supplied(False).config
        working['budgeting'].update(accounting_enabled=True, enforcement_enabled=True)
        working['operating_calendar']['timezone'] = 'UTC'
        settings.SettingsPRTests.git(self.owner, 'rm', '-q', op.CONFIG)
        settings.SettingsPRTests.git(self.owner, '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Accepted absent operating file')
        settings.SettingsPRTests.git(self.owner, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        (self.owner.root / op.CONFIG).write_bytes(op._json(working))
        supplied = replace(op.validate_operating(working, self.owner.gov),
            _provenance_json=canonical({'path': str(self.owner.root / op.CONFIG)}))
        snapshot = op.read_operating(self.owner.root, self.owner.gov)
        self.assertEqual(fresh_settings(), resolve_settings(snapshot.config))
        self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))
        result = self.batch(supplied)
        self.assertEqual([], result['errors'])
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        result = self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual([], result['errors'])
        self.assertEqual(effective_calendar({}), result['operating_calendar'])
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())

    def test_unverified_planted_calendar_refuses_before_any_decision(self):
        forged = self.supplied(False)
        self.unverify()
        ReviewBatchStore(self.store)
        with self.assertRaisesRegex(ValidationError, 'refused'):
            self.batch(forged)
        self.assertEqual([], self.events)
        self.assertEqual([], self.intent_states())
        with self.assertRaisesRegex(ValidationError, 'refused'):
            self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual([], self.events)

    def test_post_fence_unverified_policy_cancels_without_sending(self):
        original = ReviewBatchStore.begin_delivery
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)
            self.unverify()
            return cancel
        with patch.object(ReviewBatchStore, 'begin_delivery', fence):
            result = self.cycle('2026-11-06T16:00:00Z')
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assertTrue(any('refused' in error.get('reason', '') for error in result['errors']))
        with self.store.connection() as db:
            self.assertEqual(0, db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0])


class ProductionBoundaries(unittest.TestCase):
    setUp = ProductionPolicyTests.setUp
    observe_ready = ProductionPolicyTests.observe_ready
    observe_candidate = ProductionPolicyTests.observe_candidate
    deliver = ProductionPolicyTests.deliver
    readback = ProductionPolicyTests.readback
    intent_states = ProductionPolicyTests.intent_states
    cycle = ProductionPolicyTests.cycle
    supplied = ProductionPolicyTests.supplied

    def batch(self, **overrides):
        snapshot = self.supplied(True)
        arguments = dict(repository_root=self.owner.root, config=self.owner.gov,
            operating_snapshot=snapshot, calendar=effective_calendar(snapshot.config),
            binding=self.binding, clock=lambda: self.now,
            observe_owner_ready=self.observe_ready, observe_review_candidate=self.observe_candidate,
            deliver_review_batch=self.deliver, observe_review_delivery=self.readback)
        arguments.update(overrides)
        return production_review_batching_cycle(self.store, **arguments)

    def test_callback_cannot_replace_post_fence_calendar(self):
        self.now = '2026-11-06T16:59:59Z'
        calls = []
        def callback():
            with self.store.connection() as db:
                self.assertEqual('SENDING', db.execute(
                    'SELECT phase FROM review_presentation_phase_v1').fetchone()[0])
            calls.append(True)
            self.now = '2026-11-06T17:00:00Z'
            return effective_calendar({'operating_calendar': {'review_batching': {'enabled': False}}})
        result = self.batch(load_calendar=callback)
        self.assertEqual([True], calls)
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assertTrue(result['errors'])
        accepted = effective_calendar(op.read_operating(self.owner.root, self.owner.gov).config)
        self.assertEqual('2026-11-09T07:00:00Z', next_review_presentation(self.now, accepted)['presentation_at'])
        with self.store.connection() as db:
            self.assertEqual(0, db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0])

    def test_foreign_provenance_refuses_in_full_controller_and_direct_entry(self):
        foreign = self.owner.root.parent / 'foreign-policy'
        value = self.supplied(False).config
        publish_fixture_settings(self, foreign, self.owner.gov, value)
        forged = replace(op.validate_operating(value, self.owner.gov),
            _provenance_json=canonical({'path': str(foreign / op.CONFIG)}))
        real_read = op.read_operating
        first = True
        def read(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                return forged
            return real_read(*args, **kwargs)
        with patch.object(op, 'read_operating', side_effect=read):
            with self.assertRaisesRegex(ValidationError, 'provenance.*repository root'):
                self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual([], self.delivered)
        self.assertEqual([], self.events)
        self.now = '2026-11-07T10:00:00Z'
        with self.assertRaisesRegex(ValidationError, 'provenance.*repository root'):
            self.batch(operating_snapshot=forged)
        self.assertTrue(effective_calendar(real_read(self.owner.root, self.owner.gov).config)['review_batching']['enabled'])

    def test_callback_cannot_replace_post_fence_admission(self):
        self.now = '2026-11-06T16:00:00Z'
        def callback():
            ProductionPolicyTests.unverify(self)
            return effective_calendar({'operating_calendar': {'review_batching': {'enabled': False}}})
        result = self.batch(load_calendar=callback)
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assertTrue(any('refused' in error.get('reason', '') for error in result['errors']))
        with self.store.connection() as db:
            self.assertEqual(0, db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0])

    def test_provider_callbacks_can_begin_immediate_on_separate_connection(self):
        self.now = '2026-11-10T10:00:00Z'
        probes = []
        def observe(value, now):
            with closing(sqlite3.connect(self.store.path, timeout=0)) as db:
                try:
                    db.execute('BEGIN IMMEDIATE')
                    probes.append('unlocked')
                    db.rollback()
                except sqlite3.OperationalError:
                    probes.append('writer-locked')
            return self.observe_candidate(value, now)
        result = self.batch(observe_review_candidate=observe)
        self.assertEqual([], result['errors'])
        self.assertEqual(['unlocked'] * 3, probes)
        self.assertEqual(1, len(self.delivered))

    def test_never_fetched_full_defaults_admit_default_calendar_and_show_unverified(self):
        settings.SettingsPRTests.git(self.owner, 'remote', 'add', 'origin',
                                           'https://example.invalid/policy.git')
        settings.SettingsPRTests.git(self.owner, 'symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        settings.SettingsPRTests.git(self.owner, 'update-ref', '-d', 'refs/remotes/origin/main')
        snapshot = op.read_operating(self.owner.root, self.owner.gov)
        report = snapshot.provenance['protected_settings']
        self.assertTrue(report['budget_admission'])
        self.assertTrue(report['unverified'])
        self.assertIsNone(report['commit'])
        self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, op.main(['--root', str(self.owner.root), 'show', '--json']))
        shown = json.loads(output.getvalue())
        self.assertTrue(shown['protected_settings']['unverified'])
        self.assertIn('unverified', shown['protected_settings']['sources']['operating_calendar'])
        self.assertTrue(shown['budget_admission'])
        self.now = '2026-11-10T10:00:00Z'
        result = self.batch(operating_snapshot=snapshot)
        self.assertEqual([], result['errors'])
        self.assertEqual(1, len(self.delivered))


class AtomicObservationBindings(unittest.TestCase):
    setUp = batching.BatchingTests.setUp
    observe = batching.BatchingTests.observe
    enqueue = batching.BatchingTests.enqueue

    def test_claim_discards_observation_when_candidate_changes_during_read(self):
        self.enqueue()
        def observe(value):
            self.store.enqueue({**value, 'head_sha': 'e' * 40}, self.ready, {})
            return self.observe(value)
        self.assertIsNone(self.store.claim(self.now, {}, observe))
        with self.controller.connection() as db:
            self.assertEqual(['PENDING', 'WITHDRAWN'], sorted(row[0] for row in db.execute(
                'SELECT state FROM review_presentation_intent_v1')))
            self.assertEqual(0, db.execute('SELECT count(*) FROM review_presentation_outbox_v1').fetchone()[0])

    def test_recheck_refuses_if_claim_released_during_provider_read(self):
        self.enqueue()
        batch = self.store.claim(self.now, {}, self.observe)
        def observe(value):
            self.assertTrue(self.store.release_unsent(batch['batch_id'], self.now, {}))
            return self.observe(value)
        with self.assertRaisesRegex(ValidationError, 'changed|not claimed'):
            self.store.recheck(batch['batch_id'], self.now, {}, observe)
        with self.controller.connection() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM review_presentation_check_v1').fetchone()[0])
