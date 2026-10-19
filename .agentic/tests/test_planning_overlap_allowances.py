"""Exact authenticated overlap inputs for AWF-50 planning (Tier 3, FULL)."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_planning as fixtures
from agentic import planning
from agentic.canonical import canonical, fingerprint, sha256
from agentic.continuous_controller import validate_overlap_allowance_observation
from test_continuous_controller_planning import (
    NOW as APPROVED_AT, OWNER_CONFIG, allowance_request,
    allowance_observation, allowance_inventory,
)


class PlanningOverlapAllowanceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = fixtures.config(2)
        self.cfg['github']['repository_id'] = '101'
        self.items = [fixtures.ticket('AWF-101', 'src/shared.py',
                                     open_pr_files=['src/left.py']),
                      fixtures.ticket('AWF-102', 'src/shared.py',
                                     open_pr_files=['src/right.py'])]
        self.record = validate_overlap_allowance_observation(
            OWNER_CONFIG, allowance_request(), allowance_observation(),
            allowance_inventory(), now=APPROVED_AT,
            expected_repository_id='101', expected_cycle_id='cycle-17')
        self.context = {'repository_id': '101', 'cycle_id': 'cycle-17',
                        'pull_requests': deepcopy(allowance_request()['pull_requests'])}
        with patch.object(fixtures, 'NOW', APPROVED_AT):
            self.capacity = fixtures.capacity_kwargs(self.cfg, host_capacity=2, stream_capacity=2)
        # This trusted fixture retains independent controller output; it never
        # authenticates a record by reflecting candidate-supplied fields.
        self.authenticated = deepcopy(self.record)

    def verify(self, record, expected):
        self.assertEqual(self.context, expected)
        return deepcopy(self.authenticated)

    def plan(self, records=None, **options):
        now = options.pop('overlap_observed_at', APPROVED_AT)
        with patch.object(fixtures, 'NOW', now):
            capacity = fixtures.capacity_kwargs(self.cfg, host_capacity=2, stream_capacity=2)
        arguments = dict(capacity, overlap_allowances=(
            [deepcopy(self.record)] if records is None else records),
            expected_overlap_binding=deepcopy(self.context),
            verify_overlap_allowance=self.verify)
        arguments.update(options)
        return planning.plan_lanes(planning.build_backlog_graph(self.items, self.cfg),
                                   self.cfg, **arguments)

    def assert_closed(self, **options):
        result = self.plan(**options)
        self.assertEqual(1, result['lane_count'])
        self.assertEqual(1, len(result['lanes']))
        self.assertNotIn('overlap_allowances', result)

    def test_exact_allowance_splits_lanes_and_records_evidence(self):
        result = self.plan()
        self.assertEqual(2, result['lane_count'])
        self.assertEqual([['AWF-101'], ['AWF-102']],
                         [lane['ticket_keys'] for lane in result['lanes']])
        self.assertEqual([self.record], result['overlap_allowances'])
        self.assertEqual(self.record['allowance_id'], result['overlap_allowances'][0]['allowance_id'])
        self.assertEqual(self.record['allowance_sha256'], result['overlap_allowances'][0]['allowance_sha256'])

    def test_different_path_fails_closed(self):
        self.items[0]['write_paths'] = ['src/other.py']
        self.items[1]['write_paths'] = ['src/other.py']
        self.assert_closed()

    def test_ticket_subset_and_superset_fail_closed(self):
        for tickets in [['AWF-101'], ['AWF-101', 'AWF-102', 'AWF-103']]:
            with self.subTest(tickets=tickets):
                self.record['binding']['tickets'] = tickets
                self.assert_closed()
        # A valid pair cannot authorize a path attributed to a third ticket.
        self.record = deepcopy(self.authenticated)
        self.items.append(fixtures.ticket('AWF-103', 'src/shared.py'))
        self.assert_closed()

    def test_expired_and_not_yet_approved_fail_closed(self):
        for now in [self.record['expires_at'], '2026-10-15T07:54:59Z']:
            with self.subTest(now=now):
                self.assert_closed(overlap_observed_at=now)

    def test_repository_cycle_pr_head_and_files_must_match(self):
        for field in ['repository_id', 'cycle_id', 'number', 'head_sha', 'changed_files']:
            context = deepcopy(self.context)
            if field in ['repository_id', 'cycle_id']:
                context[field] = '999' if field == 'repository_id' else 'other-cycle'
            else:
                context['pull_requests'][0][field] = {
                    'number': 99, 'head_sha': '9' * 40,
                    'changed_files': ['src/other.py']}[field]
            with self.subTest(field=field):
                # The verifier trusts its controller source, independently of
                # the planner's current expected binding.
                self.assert_closed(expected_overlap_binding=context,
                                   verify_overlap_allowance=lambda *_: deepcopy(self.authenticated))

    def test_unauthenticated_malformed_unknown_fields_fail_closed_without_leaks(self):
        for record in [None, [], {}, {'binding': None},
                       dict(self.record, unknown=True),
                       dict(self.record, binding=dict(self.record['binding'], unknown=True)),
                       dict(self.record, approval=dict(self.record['approval'], unknown=True))]:
            with self.subTest(record=record):
                self.assert_closed(records=[record])
        self.assert_closed(verify_overlap_allowance=None)
        self.assert_closed(verify_overlap_allowance=lambda *_: {'status': 'VERIFIED'})
        self.assert_closed(expected_overlap_binding=None)
        self.assert_closed(records={'not': 'a list'})
        def failed_verifier(*_):
            raise RuntimeError('unavailable controller source')
        self.assert_closed(verify_overlap_allowance=failed_verifier)

    def test_uncovered_second_shared_path_still_serializes(self):
        for item in self.items:
            item['write_paths'].append('src/second.py')
        self.assert_closed()

    def test_recompute_preserves_binding_and_drops_expired_allowance(self):
        state = {}
        def recompute(now):
            # Refresh authenticated resource evidence, independently of the
            # overlap approval window.
            with patch.object(fixtures, 'NOW', now):
                capacity = fixtures.capacity_kwargs(self.cfg, host_capacity=2, stream_capacity=2)
            return planning.recompute_plan(
                self.items, self.cfg, state, now=now, trigger='files_changed',
                overlap_allowances=[self.record], expected_overlap_binding=self.context,
                verify_overlap_allowance=self.verify, **capacity)['record']
        first = recompute(APPROVED_AT)
        second = recompute('2026-10-15T08:01:00Z')
        for record in [first, second]:
            self.assertEqual(2, record['lane_count'])
            self.assertEqual([self.record], record['inputs']['overlap_allowances'])
            self.assertTrue(planning.verify_plan_record(record))
        expired = recompute(self.record['expires_at'])
        self.assertEqual(1, expired['lane_count'])
        self.assertNotIn('overlap_allowances', expired['inputs'])
        self.assertEqual([self.record], state['planning_history'][0]['inputs']['overlap_allowances'])

    def test_no_allowance_is_byte_identical_to_base_overlap_probe(self):
        cfg = fixtures.config(2)
        baseline = fixtures.plan(self.items, cfg, host_capacity=2, stream_capacity=2)
        self.assertEqual('f4808516e2ad0ab98c273b819c6157155d1cb34d88ca42fdb44f1100656964de',
                         sha256(canonical(baseline)))
        self.assertEqual(1, baseline['lane_count'])

    def test_authenticated_but_inexact_ticket_sets_still_fail_closed(self):
        original = deepcopy(self.record)
        for tickets in [['AWF-101'], ['AWF-101', 'AWF-102', 'AWF-103']]:
            with self.subTest(tickets=tickets):
                self.record = deepcopy(original)
                self.record['binding']['tickets'] = tickets
                self.record['allowance_sha256'] = fingerprint(
                    'controller-overlap-allowance-record',
                    {key: value for key, value in self.record.items() if key != 'allowance_sha256'})
                self.authenticated = deepcopy(self.record)
                self.assert_closed()

    def test_unknown_fields_fail_closed_even_with_resealed_authenticated_record(self):
        original = deepcopy(self.record)
        for target in ['record', 'binding', 'approval']:
            with self.subTest(target=target):
                self.record = deepcopy(original)
                node = self.record if target == 'record' else self.record[target]
                node['unknown'] = True
                self.record['allowance_sha256'] = fingerprint(
                    'controller-overlap-allowance-record',
                    {key: value for key, value in self.record.items() if key != 'allowance_sha256'})
                self.authenticated = deepcopy(self.record)
                self.assert_closed()


if __name__ == '__main__':
    unittest.main()
