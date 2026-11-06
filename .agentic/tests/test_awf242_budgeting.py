"""AWF-242 contracts; fixture host observations are not live authentication."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

import source_only  # noqa: F401
from agentic import ValidationError
from agentic.budgeting import (BudgetLedger, fresh_settings, legacy_settings,
                               resolve_settings, settings_hash, transition_settings)
from agentic.budget_enrollment import EnrollmentRegistry


class FixtureHost:
    """Test-only adapter. Production must obtain these facts independently."""
    def __init__(self):
        self.owner = None
        self.enrollment = None

    def observe_owner_direction(self, project_id, prior_hash, changes):
        return self.owner

    def observe_enrollment(self, binding):
        return self.enrollment


class SettingsTests(unittest.TestCase):
    def test_missing_upgrade_fields_are_legacy_not_fresh(self):
        self.assertEqual(resolve_settings({}), legacy_settings())
        self.assertEqual(resolve_settings({}, fresh=True), fresh_settings())
        explicit = {'budgeting': fresh_settings()}
        self.assertEqual(resolve_settings(explicit), fresh_settings())
        with self.assertRaises(ValidationError):
            resolve_settings({'budgeting': None})

    def test_fresh_defaults_and_dormant_limit(self):
        value = fresh_settings()
        self.assertEqual(value, dict(accounting_enabled=False, enforcement_enabled=False,
                                    max_tokens_per_ticket=4_000_000,
                                    max_tokens_per_project=1_000_000_000))
        edited = transition_settings(value, {'max_tokens_per_ticket': 12})
        self.assertFalse(edited['accounting_enabled'])
        self.assertFalse(edited['enforcement_enabled'])
        self.assertEqual(value, fresh_settings())

    def test_enforcement_requires_accounting_and_accounting_off_disables_it(self):
        with self.assertRaises(ValidationError):
            transition_settings(fresh_settings(), {'enforcement_enabled': True})
        enabled = transition_settings(fresh_settings(), dict(accounting_enabled=True, enforcement_enabled=True))
        self.assertFalse(transition_settings(enabled, {'accounting_enabled': False})['enforcement_enabled'])
        with self.assertRaises(ValidationError):
            transition_settings(enabled, dict(accounting_enabled=False, enforcement_enabled=True))

    def test_closed_types_and_legacy_not_silently_off(self):
        for changes in ({'accounting_enabled': 1}, {'max_tokens_per_ticket': True},
                        {'max_tokens_per_project': 0}, {'owner': 'owner'}, {}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                transition_settings(fresh_settings(), changes)
        legacy = legacy_settings()
        self.assertTrue(legacy['accounting_enabled'])
        self.assertTrue(legacy['enforcement_enabled'])
        self.assertIsNone(legacy['max_tokens_per_project'])
        with self.assertRaises(ValidationError):
            transition_settings(legacy, {'accounting_enabled': False})


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = FixtureHost()
        self.registry = EnrollmentRegistry(self.root / 'enrollment.sqlite', self.host)
        self.ledger = BudgetLedger(self.root / 'budget.sqlite', self.registry)
        self.settings = transition_settings(fresh_settings(), dict(accounting_enabled=True,
            enforcement_enabled=True, max_tokens_per_ticket=100, max_tokens_per_project=150))

    def enroll(self, run='run-1', ticket='T-1', **updates):
        binding = dict(project_id='project', ticket_id=ticket, run_id=run,
                       dispatch_id='dispatch-' + run, session_id='session-' + run,
                       role='worker')
        binding.update(updates)
        self.host.enrollment = dict(binding)
        self.registry.enroll(binding)
        return binding

    def reserve(self, binding, tokens=80, settings=None):
        return self.ledger.reserve(binding, self.settings if settings is None else settings, tokens)

    def test_off_does_not_open_ledger_or_require_telemetry_or_identity(self):
        result = self.ledger.reserve(None, fresh_settings(), None)
        self.assertEqual(result['status'], 'OFF')
        self.assertFalse(self.ledger.path.exists())

    def test_independent_ultra_and_controller_are_excluded(self):
        binding = self.enroll()
        self.reserve(binding)
        independent = {**binding, 'run_id': 'independent-ultra', 'session_id': 'ultra'}
        self.assertEqual(self.reserve(independent)['status'], 'UNKNOWN')
        with self.assertRaises(ValidationError):
            self.enroll('controller', role='controller')
        self.assertEqual(self.ledger.summary('project')['reserved_tokens'], 80)

    def test_spoofed_enrollment_and_session_unknown(self):
        with self.assertRaises(ValidationError):
            self.registry.enroll(dict(project_id='project', ticket_id='T', run_id='forged',
                dispatch_id='dispatch', session_id='session', role='worker'))
        binding = self.enroll()
        self.assertEqual(self.reserve({**binding, 'session_id': None})['status'], 'UNKNOWN')

    def test_exact_duplicates_and_cross_ticket_rebinding_refused(self):
        binding = self.enroll()
        self.registry.enroll(binding)
        self.assertTrue(self.reserve(binding)['created'])
        self.assertFalse(self.reserve(binding)['created'])
        with self.assertRaises(ValidationError):
            self.reserve(binding, tokens=20)
        with self.assertRaises(ValidationError):
            self.enroll(ticket='other')
        self.assertEqual(self.ledger.summary('project')['reservations'], 1)

    def test_lifetime_cap_across_reopen_ticket_and_settings_changes(self):
        first = self.enroll()
        self.reserve(first)
        self.ledger.settle(first, 70, 'usage-1')
        reopened = BudgetLedger(self.ledger.path, self.registry)
        second = self.enroll('run-2', 'T-2')
        reopened.reserve(second, self.settings, 80)
        with self.assertRaises(ValidationError):
            self.reserve(self.enroll('run-3', 'T-3'), 1)
        self.assertEqual(reopened.summary('project')['known_tokens'], 70)

    def test_accounting_only_overruns_do_not_refuse_or_quarantine(self):
        accounting = {**self.settings, 'enforcement_enabled': False}
        first = self.enroll()
        self.reserve(first, 500, accounting)
        result = self.ledger.settle(first, 700, 'usage')
        self.assertFalse(result['budget_overrun'])
        self.reserve(self.enroll('run-2'), 500, accounting)

    def test_off_transition_preserves_late_settlement_and_unknown(self):
        first = self.enroll()
        self.reserve(first)
        self.ledger.reserve(None, fresh_settings(), None)
        self.ledger.settle(first, None, 'unknown-usage')
        summary = self.ledger.summary('project')
        self.assertEqual(summary['unknown_runs'], 1)
        self.assertEqual(summary['known_tokens'], 0)
        self.assertEqual(summary['unresolved_reserved_tokens'], 80)
        with self.assertRaises(ValidationError):
            self.reserve(self.enroll('run-2', 'T-2'), 1)
        with self.assertRaises(ValidationError):
            self.ledger.settle(first, 0, 'another-usage')

    def test_enabled_overrun_preserved_and_settlement_replay_checked(self):
        first = self.enroll()
        self.reserve(first)
        self.assertTrue(self.ledger.settle(first, 90, 'usage')['budget_overrun'])
        self.ledger.settle(first, 90, 'usage')
        with self.assertRaises(ValidationError):
            self.ledger.settle(first, 80, 'usage')
        with self.assertRaises(ValidationError):
            self.reserve(self.enroll('run-2', 'T-2'), 1)
        self.assertEqual(self.ledger.summary('project')['known_tokens'], 90)

    def test_atomic_concurrent_project_admission(self):
        bindings = [self.enroll('run-' + str(i), 'T-' + str(i)) for i in range(2)]
        def attempt(binding):
            try:
                self.reserve(binding)
                return 'reserved'
            except ValidationError:
                return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, bindings)), ['reserved', 'blocked'])

    def test_owner_direction_exact_binding_replay_and_reopen(self):
        current = fresh_settings()
        changes = {'accounting_enabled': True}
        self.host.owner = dict(observation_id='owner-event', actor_id='fixture-owner',
            project_id='project', prior_settings_hash=settings_hash(current), changes=changes)
        for key, wrong in [('project_id', 'other'), ('prior_settings_hash', '0' * 64),
                           ('changes', {'accounting_enabled': False})]:
            with self.subTest(key=key):
                original = self.host.owner
                self.host.owner = {**original, key: wrong}
                with self.assertRaises(ValidationError):
                    self.registry.admit_owner_direction('project', current, changes)
                self.host.owner = original
        result = self.registry.admit_owner_direction('project', current, changes)
        self.assertTrue(result['settings']['accounting_enabled'])
        reopened = EnrollmentRegistry(self.registry.path, self.host)
        with self.assertRaises(ValidationError):
            reopened.admit_owner_direction('project', current, changes)

    def test_caller_identity_and_malformed_mixed_changes_are_refused_before_consumption(self):
        current = fresh_settings()
        for changes in ({'actor_id': 'owner', 'accounting_enabled': True},
                        {'max_tokens_per_ticket': 123, 'enforcement_enabled': True}):
            with self.assertRaises(ValidationError):
                self.registry.admit_owner_direction('project', current, changes)
        self.assertFalse(self.registry.path.exists())
        self.host.owner = {'source': 'user', 'actor_id': 'owner'}
        with self.assertRaises(ValidationError):
            self.registry.admit_owner_direction('project', current, {'accounting_enabled': True})
        missing = EnrollmentRegistry(self.registry.path, None)
        with self.assertRaises(ValidationError):
            missing.admit_owner_direction('project', current, {'accounting_enabled': True})

    def test_enrollment_reopen_and_each_retry_must_enroll(self):
        first = self.enroll()
        reopened = EnrollmentRegistry(self.registry.path, self.host)
        self.assertTrue(reopened.enrolled(first))
        retry = {**first, 'run_id': 'retry'}
        self.assertFalse(reopened.enrolled(retry))
        self.assertEqual(self.reserve(retry)['status'], 'UNKNOWN')
        self.host.enrollment = retry
        reopened.enroll(retry)
        self.assertTrue(reopened.enrolled(retry))

    def test_duplicate_tool_usage_observation_cannot_charge_another_run(self):
        first = self.enroll()
        self.reserve(first, 40)
        second = self.enroll('run-2')
        self.reserve(second, 40)
        self.ledger.settle(first, 10, 'host-cumulative-tools-and-retries')
        with self.assertRaises(ValidationError):
            self.ledger.settle(second, 10, 'host-cumulative-tools-and-retries')
        self.assertEqual(self.ledger.summary('project')['known_tokens'], 10)
        self.assertEqual(self.ledger.summary('project')['reserved_tokens'], 40)

    def test_protected_state_rejects_worktree(self):
        inside = BudgetLedger(self.root / 'worker' / 'budget.sqlite', self.registry,
                              worktree_roots=[self.root / 'worker'])
        with self.assertRaises(ValidationError):
            inside.reserve(self.enroll(), self.settings, 10)
        self.assertFalse(inside.path.exists())


if __name__ == '__main__':
    unittest.main()
