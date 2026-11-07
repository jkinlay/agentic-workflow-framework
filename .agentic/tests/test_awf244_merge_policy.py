"""Missing working files cannot substitute defaults for accepted calendars."""
from copy import deepcopy
import unittest

import source_only
from agentic import ValidationError, operating as op
from agentic.operating_calendar import effective_calendar, next_review_presentation
from agentic.protected_settings import effective_snapshot, resolve_protected, refresh_default_policy
from agentic.providers.github_review_host import HostDriver
import test_awf242_settings_second_look as settings
import test_awf244_policy_rules as policy
from settings_pr_fixtures import publish_fixture_settings


class MissingWorkingCalendarTests(unittest.TestCase):
    setUp = settings.RoundThreeTests.setUp
    review_config = settings.RoundThreeTests.review_config

    def test_review_host_missing_file_checks_accepted_policy_for_both_domains(self):
        config = self.review_config()
        for budget in ('legacy', 'off', 'on'):
            with self.subTest(budget=budget):
                accepted = deepcopy(self.value)
                accepted['operating_calendar']['timezone'] = 'UTC'
                accepted['operating_calendar']['review_batching']['enabled'] = False
                if budget == 'legacy':
                    accepted.pop('budgeting')
                else:
                    accepted['budgeting'].update(accounting_enabled=budget == 'on',
                                                 enforcement_enabled=budget == 'on')
                self.fixture.publish(accepted)
                self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
                (self.root / op.CONFIG).unlink()
                resolved, report = resolve_protected(self.root, self.gov, {})
                self.assertEqual(accepted['operating_calendar'], resolved['operating_calendar'])
                self.assertEqual('default branch', report['sources']['operating_calendar'])
                if budget == 'legacy':
                    self.assertIsNone(HostDriver.from_project(config, self.root).budgeting)
                else:
                    with self.assertRaisesRegex(ValidationError, 'OPERATING_CONFIG.yaml is missing'):
                        HostDriver.from_project(config, self.root)
                supplied = op.validate_operating(self.value, self.gov)
                effective = effective_snapshot(self.root, self.gov, supplied)
                self.assertEqual(accepted['operating_calendar'], effective.config['operating_calendar'])
                self.assertFalse(next_review_presentation('2026-11-07T10:00:00Z',
                    effective_calendar(effective.config))['held'])


class MissingWorkingBatchTests(unittest.TestCase):
    setUp = policy.ProductionPolicyTests.setUp
    supplied = policy.ProductionPolicyTests.supplied
    batch = policy.ProductionPolicyTests.batch
    observe_ready = policy.ProductionPolicyTests.observe_ready
    observe_candidate = policy.ProductionPolicyTests.observe_candidate
    deliver = policy.ProductionPolicyTests.deliver
    readback = policy.ProductionPolicyTests.readback
    intent_states = policy.ProductionPolicyTests.intent_states

    def test_deleted_working_file_uses_accepted_hold_instead_of_supplied_defaults(self):
        supplied = self.supplied(False)
        accepted = self.supplied(True).config
        accepted['operating_calendar']['review_batching']['presentation_time'] = '09:00'
        publish_fixture_settings(self, self.owner.root, self.owner.gov, accepted)
        (self.owner.root / op.CONFIG).unlink()
        result = self.batch(supplied)
        self.assertEqual([], result['errors'])
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        snapshot = effective_snapshot(self.owner.root, self.owner.gov, supplied)
        self.assertEqual('2026-11-09T09:00:00Z', next_review_presentation(self.now,
            effective_calendar(snapshot.config))['presentation_at'])

    def test_deleted_working_file_refuses_send_when_accepted_calendar_releases_hold(self):
        supplied = self.supplied(True)
        accepted = self.supplied(False).config
        publish_fixture_settings(self, self.owner.root, self.owner.gov, accepted)
        (self.owner.root / op.CONFIG).unlink()
        result = self.batch(supplied)
        self.assertTrue(any('OPERATING_CONFIG.yaml is missing' in error.get('reason', '')
                            for error in result['errors']))
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        with self.store.connection() as db:
            self.assertEqual(0, db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0])
