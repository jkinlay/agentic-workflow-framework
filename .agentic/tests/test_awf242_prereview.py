"""AWF-242 auxiliary pre-review regressions; host observations are fixtures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import json
import tempfile
import unittest
import uuid

import source_only  # noqa: F401
from agentic import ValidationError
from agentic import operating as op
from agentic.budget_enrollment import EnrollmentRegistry
from agentic.budgeting import BudgetLedger, fresh_settings
from agentic.model_routing import RoutingLedger, policy_from_config
from test_awf242_budgeting import FixtureHost
from test_model_routing import capabilities, outcome, request
from test_operating import governance


class PreReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
                                       enforcement_enabled=True)
        self.host = FixtureHost()
        self.registry = EnrollmentRegistry(self.root / 'enrollment.sqlite', self.host)
        self.ledger = RoutingLedger(self.root / 'routing.sqlite')
        self.facade = BudgetLedger(self.ledger.path, self.registry)
        self.project = self.gov['project']['id']

    def enroll(self, ticket='EX-10'):
        run_id = str(uuid.uuid4())
        binding = dict(project_id=self.project, ticket_id=ticket, run_id=run_id,
                       dispatch_id='dispatch-' + run_id, session_id='session-' + run_id,
                       role='worker')
        self.host.enrollment = binding
        self.registry.enroll(binding)
        return binding

    def reserve(self, binding=None, **updates):
        return self.ledger.reserve(self.project, policy_from_config(self.gov),
            request(stream='A', **updates), capabilities(),
            operating=op.validate_operating(self.value, self.gov), governance=self.gov,
            budget_registry=self.registry, budget_binding=binding)

    def row(self, run_id):
        with self.ledger._transaction() as db:
            return dict(db.execute('SELECT * FROM model_runs WHERE run_id=?', (run_id,)).fetchone())

    def settlement_pair(self, first_interface, second_interface):
        bindings = [self.enroll(), self.enroll()]
        reservations = []
        for index, (interface, binding) in enumerate(zip((first_interface, second_interface), bindings)):
            if interface == 'facade':
                reservations.append(self.facade.reserve(binding, self.value['budgeting'], 100))
            else:
                reservations.append(self.reserve(binding, phase='phase-' + str(index), reservation_tokens=100))

        def settle(index):
            binding, reservation = bindings[index], reservations[index]
            if (first_interface, second_interface)[index] == 'facade':
                return self.facade.settle(binding, 100, 'shared-usage-observation')
            return self.ledger.settle(binding['run_id'],
                outcome(reservation, observation_id='shared-usage-observation'))
        return bindings, settle

    def test_facade_observation_cannot_charge_routed_run(self):
        bindings, settle = self.settlement_pair('facade', 'routing')
        settle(0)
        before = self.row(bindings[1]['run_id'])
        with self.assertRaisesRegex(ValidationError, 'observation already'):
            settle(1)
        self.assertEqual(self.row(bindings[1]['run_id']), before)
        self.assertEqual(self.facade.summary(self.project)['known_tokens'], 100)

    def test_routed_observation_cannot_charge_facade_run(self):
        bindings, settle = self.settlement_pair('routing', 'facade')
        settle(0)
        before = self.row(bindings[1]['run_id'])
        with self.assertRaisesRegex(ValidationError, 'observation already'):
            settle(1)
        self.assertEqual(self.row(bindings[1]['run_id']), before)
        self.assertEqual(self.facade.summary(self.project)['known_tokens'], 100)

    def test_routed_observation_cannot_charge_another_routed_run(self):
        bindings, settle = self.settlement_pair('routing', 'routing')
        settle(0)
        before = self.row(bindings[1]['run_id'])
        with self.assertRaisesRegex(ValidationError, 'observation already'):
            settle(1)
        self.assertEqual(self.row(bindings[1]['run_id']), before)
        self.assertEqual(self.facade.summary(self.project)['known_tokens'], 100)

    def test_concurrent_mixed_settlements_consume_observation_once(self):
        bindings, settle = self.settlement_pair('facade', 'routing')
        barrier = Barrier(2)

        def attempt(index):
            barrier.wait(timeout=10)
            try:
                settle(index)
                return 'settled'
            except ValidationError as exc:
                self.assertIn('observation already', str(exc))
                return 'refused'

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, range(2))), ['settled', 'refused'])
        summary = self.facade.summary(self.project)
        self.assertEqual(summary['known_tokens'], 100)
        self.assertEqual(summary['reserved_tokens'], 100)
        self.assertEqual(sum(self.row(binding['run_id'])['status'] == 'reserved'
                             for binding in bindings), 1)

    def test_independent_run_does_not_advance_enrolled_run_disposition(self):
        self.gov['execution']['model_routing']['budgets']['max_runs_per_ticket'] = 2
        self.gov['execution']['max_agent_runs_per_ticket'] = 2
        independent = self.reserve()
        self.ledger.settle(independent['run_id'], outcome(independent))
        first = self.reserve(self.enroll())
        self.assertEqual(first['ticket_runs_used'], 1)
        self.assertEqual(first['run_cap_signal'], 'OK')
        self.assertIsNone(first['disposition_request'])
        self.assertEqual(self.ledger.summary(self.project, 'EX-10')['disposition_requests'], [])
        self.ledger.settle(first['run_id'], outcome(first, observation_id='first-enrolled'))
        second = self.reserve(self.enroll())
        self.assertEqual(second['ticket_runs_used'], 2)
        self.assertEqual(second['run_cap_signal'], 'NEEDS_DISPOSITION')
        self.assertEqual(second['disposition_request']['runs_used'], 2)
        self.assertTrue(second['disposition_request']['owner_delivery_required'])
        self.assertEqual(self.facade.summary(self.project)['reservations'], 2)

    def test_accounting_only_run_count_excludes_independent_history(self):
        self.value['budgeting']['enforcement_enabled'] = False
        independent = self.reserve()
        self.ledger.settle(independent['run_id'], outcome(independent))
        first = self.reserve(self.enroll())
        self.assertEqual(first['ticket_runs_used'], 1)
        self.assertEqual(first['run_cap_signal'], 'OFF')
        self.assertIsNone(first['disposition_request'])

    def test_legacy_disposition_does_not_suppress_enrolled_disposition(self):
        self.gov['execution']['model_routing']['budgets']['max_runs_per_ticket'] = 2
        self.gov['execution']['max_agent_runs_per_ticket'] = 2
        settings = self.value.pop('budgeting')
        for index in range(2):
            legacy = self.reserve()
            self.assertEqual(legacy['ticket_runs_used'], index + 1)
            self.ledger.settle(legacy['run_id'], outcome(legacy))
        self.assertEqual(legacy['run_cap_signal'], 'NEEDS_DISPOSITION')
        self.assertEqual(legacy['disposition_request']['runs_used'], 2)
        with self.assertRaisesRegex(ValidationError, 'ticket run budget exhausted'):
            self.reserve()
        self.value['budgeting'] = settings
        for index in range(2):
            enrolled = self.reserve(self.enroll())
            if index == 1:
                self.assertIsNotNone(enrolled['disposition_request'])
                self.assertEqual(enrolled['disposition_request']['runs_used'], 2)
            self.ledger.settle(enrolled['run_id'], outcome(enrolled, observation_id='enrolled-' + str(index)))
        self.assertEqual(len(self.ledger.summary(self.project, 'EX-10')['disposition_requests']), 2)

    def unknown_run(self, estimate=None):
        self.value['budgeting']['enforcement_enabled'] = False
        reserved = self.reserve(self.enroll(), reservation_tokens=estimate)
        self.ledger.settle(reserved['run_id'], outcome(reserved, actual_tokens=None,
                                                     observation_id='unknown-usage'))
        return reserved

    def test_unknown_closed_usage_is_unknown_in_ticket_and_role_totals(self):
        reserved = self.unknown_run()
        before = self.row(reserved['run_id'])
        summary = self.ledger.summary(self.project, 'EX-10')
        self.assertIsNone(summary['tokens'])
        self.assertIsNone(summary['by_role']['worker']['tokens'])
        self.assertIsNone(summary['by_role']['worker']['actual_tokens'])
        self.assertIsNone(summary['by_role']['worker']['reserved_tokens'])
        self.assertEqual(self.row(reserved['run_id']), before)
        self.assertEqual(before['reserved_tokens'], 0)
        self.assertTrue(json.loads(before['request'])['_reservation_unknown'])

    def test_closed_unknown_run_is_not_unsettled(self):
        self.unknown_run()
        summary = self.ledger.summary(self.project, 'EX-10')
        self.assertEqual(summary['by_role']['worker']['unsettled_runs'], 0)
        self.assertEqual(summary['by_role']['worker']['closed_runs'], 1)
        self.assertEqual(summary['by_role']['worker']['unknown_runs'], 1)

    def test_known_reservation_does_not_replace_closed_unknown_usage(self):
        self.unknown_run(estimate=100)
        summary = self.ledger.summary(self.project, 'EX-10')
        self.assertIsNone(summary['tokens'])
        self.assertIsNone(summary['by_role']['worker']['actual_tokens'])
        self.assertEqual(summary['by_role']['worker']['reserved_tokens'], 100)

    def test_unknown_aggregate_stays_unknown_when_known_usage_is_added(self):
        self.unknown_run()
        known = self.reserve(self.enroll(), phase='known-phase')
        self.ledger.settle(known['run_id'], outcome(known, observation_id='known-usage'))
        summary = self.ledger.summary(self.project, 'EX-10')
        self.assertIsNone(summary['tokens'])
        self.assertIsNone(summary['by_role']['worker']['actual_tokens'])
        self.assertEqual(summary['by_role']['worker']['unknown_runs'], 1)

    def test_open_known_estimate_remains_an_estimate(self):
        self.reserve(self.enroll(), reservation_tokens=100)
        summary = self.ledger.summary(self.project, 'EX-10')
        self.assertEqual(summary['tokens'], 100)
        self.assertEqual(summary['by_role']['worker']['reserved_tokens'], 100)
        self.assertEqual(summary['by_role']['worker']['unsettled_runs'], 1)


if __name__ == '__main__':
    unittest.main()
