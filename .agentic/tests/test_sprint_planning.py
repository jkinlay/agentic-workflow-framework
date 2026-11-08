"""Stage 1 milestone proposals: consistency is never owner acceptance."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.canonical import canonical, fingerprint
from agentic import sprint_planning as sprint
import test_planning as legacy


def authority(key, dependencies=(), duration=10):
    return dict(key=key, contract_sha256='a' * 64, requirements_sha256='b' * 64,
                scope=['read', 'write'], acceptance_criteria=['AC1', 'AC2'],
                write_paths=[f'src/{key}.py'], dependencies=list(dependencies),
                generator_dependencies=[], risk_tier=3, closure_standard='FULL',
                duration_minutes=duration, status='open')


def inputs():
    tickets = [authority('T-1'), authority('T-2', ['T-1'])]
    pipeline = dict(pipeline_id='product', entry_stage='read',
                    essential_stages=['read', 'write'],
                    terminal_artifacts=[dict(id='result', producer_stage='write')],
                    connections=[dict(source='read', target='write')], stages=[])
    for stage, key in [('read', 'T-1'), ('write', 'T-2')]:
        pipeline['stages'].append(dict(
            id=stage, ticket_key=key, scope_refs=[stage],
            acceptance_criteria=['AC1'], kind='execution',
            implementation=dict(kind='real', reference=f'src/{key}.py:{stage}',
                                sha256='c' * 64)))
    cfg = legacy.config()
    source = legacy.planning.recompute_plan(
        [legacy.ticket('T-1', 'src/T-1.py'),
         legacy.ticket('T-2', 'src/T-2.py', declared_dependencies=['T-1'])],
        cfg, {}, now=legacy.NOW, trigger='project_start',
        **legacy.capacity_kwargs(cfg))['record']
    bindings = dict(project_id='project', repository_id='repository', scope_sha256='e' * 64,
                    accepted_settings_sha256='f' * 64, settings_source_commit='1' * 40)
    return dict(tickets=tickets, source_ticket_plan=source, pipeline=pipeline, bindings=bindings)


def proposal(context):
    tickets = context['tickets']
    calendar = sprint.resolve_calendar('2026-03-16', review_time='16:00',
                                       review_time_provenance='assumed')
    interval = sprint.sprint_interval(calendar, 1)
    return dict(format=sprint.FORMAT, schema_version=1, record_id='proposal-1',
                created_at='2026-03-15T12:00:00Z', predecessor=None,
                project_binding={**context['bindings'],
                                 'contracts': [dict(ticket_key=t['key'],
                                                    contract_sha256=t['contract_sha256'],
                                                    requirements_sha256=t['requirements_sha256'])
                                               for t in tickets]},
                planning_binding=dict(source_ticket_plan_sha256=sprint.source_plan_digest(context['source_ticket_plan']),
                                      structural_graph_sha256=sprint.structural_graph_digest(tickets)),
                calendar=calendar, planned_finish='2026-03-29', pipeline=deepcopy(context['pipeline']),
                slices=[], milestones=[dict(
                    id='m1', ordinal=1, **interval, goal='Read and write a real result',
                    targets=[dict(ticket_key='T-2', slice_id=None)], prerequisites=['T-1', 'T-2'],
                    pipeline_runs=[dict(stage_ids=['read', 'write'], artifacts=[dict(
                        id='result', producer_stage='write', reference='artifacts/result.json',
                        demonstrate='Open and inspect the result', completion_predicates=['Result matches AC1'])])],
                    completion_predicates=['Real run succeeds'])])


class CalendarTests(unittest.TestCase):
    def test_anchor_resolved_once_and_supplied_provenance(self):
        value = sprint.resolve_calendar('2026-03-17', review_time='16:00', review_time_provenance='assumed')
        self.assertEqual('2026-03-23', value['resolved_anchor'])
        self.assertEqual('assumed', value['anchor_provenance'])
        self.assertEqual('assumed', value['review_time_provenance'])
        self.assertEqual('2026-04-03', sprint.sprint_interval(value, 1)['review_date'])
        self.assertEqual(value, deepcopy(value))
        supplied = sprint.resolve_calendar('2026-03-17', first_sprint_start='2026-03-18',
                                          review_time='09:30', review_time_provenance='supplied')
        self.assertEqual('supplied', supplied['anchor_provenance'])
        self.assertEqual('2026-03-18', supplied['resolved_anchor'])

    def test_local_dates_across_both_uk_clock_changes(self):
        for start, end, review, utc, hours in [
            ('2026-03-23', '2026-04-06', '2026-04-03', '2026-04-03T15:00:00Z', 335),
            ('2026-10-19', '2026-11-02', '2026-10-30', '2026-10-30T16:00:00Z', 337),
        ]:
            with self.subTest(start=start):
                cal = sprint.resolve_calendar(start, review_time='16:00', review_time_provenance='supplied')
                interval = sprint.sprint_interval(cal, 1)
                self.assertEqual((start, end, review, utc), tuple(interval[k] for k in
                                 ('start_date', 'end_date', 'review_date', 'review_at')))
                left = sprint.local_datetime_utc(start, '00:00', 'Europe/London')
                right = sprint.local_datetime_utc(end, '00:00', 'Europe/London')
                self.assertEqual(hours, (right - left).total_seconds() / 3600)

    def test_review_is_last_occurrence_and_finish_is_exclusive(self):
        cal = sprint.resolve_calendar('2026-03-16', review_time='16:00', review_time_provenance='supplied')
        self.assertEqual('2026-03-27', sprint.sprint_interval(cal, 1)['review_date'])
        self.assertEqual(1, len(sprint.sprints_through(cal, '2026-03-30')))
        self.assertEqual(2, len(sprint.sprints_through(cal, '2026-03-31')))

    def test_invalid_calendar_combinations_fail_closed(self):
        for options in [dict(length_days=True), dict(length_days=0), dict(timezone='Missing/Zone'),
                        dict(review_weekday='Funday'), dict(first_sprint_start='2026-02-30'),
                        dict(length_days=1), dict(review_time='24:00'),
                        dict(review_time_provenance='unknown')]:
            with self.subTest(options=options), self.assertRaises(sprint.SprintPlanningError):
                sprint.resolve_calendar('2026-03-16', **{
                    'review_time': '16:00', 'review_time_provenance': 'supplied', **options})

    def test_overflow_and_dst_gap_or_fold_are_refused(self):
        for day in ['2026-03-29', '2026-10-25']:
            with self.subTest(day=day), self.assertRaisesRegex(sprint.SprintPlanningError, 'AMBIGUOUS_LOCAL_TIME|NONEXISTENT_LOCAL_TIME'):
                sprint.local_datetime_utc(day, '01:30', 'Europe/London')
        with self.assertRaises(sprint.SprintPlanningError):
            sprint.resolve_calendar('9999-12-31', review_time='16:00', review_time_provenance='supplied')

    def test_short_recurring_calendar_rejects_a_later_sprint_without_review(self):
        cal = sprint.resolve_calendar('2026-03-18', first_sprint_start='2026-03-18',
                                      length_days=3, review_time='16:00', review_time_provenance='supplied')
        self.assertEqual('2026-03-20', sprint.sprint_interval(cal, 1)['review_date'])
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'NO_REVIEW_DATE'):
            sprint.sprints_through(cal, '2026-03-24')


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.context = inputs()
        self.record = proposal(self.context)

    def seal(self, record=None):
        return sprint.seal_milestone_plan(record or self.record, **self.context)

    def reject(self, code, edit):
        edit(self.record)
        with self.assertRaises(sprint.SprintPlanningError) as caught:
            self.seal()
        self.assertEqual(code, caught.exception.reason)

    def test_round_trip_domain_seal_and_no_acceptance(self):
        before = deepcopy(self.record)
        record = self.seal()
        self.assertEqual(before, self.record)
        self.assertEqual(record, sprint.validate_milestone_plan(record, **self.context))
        self.assertEqual(canonical(record) + b'\n', sprint.serialize_milestone_plan(record, **self.context))
        self.assertNotEqual(fingerprint('ticket-plan', self.record), record['plan_sha256'])
        self.assertNotIn('accepted', record)
        record['accepted'] = True
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'UNKNOWN_FIELD'):
            sprint.validate_milestone_plan(record, **self.context)

    def test_digest_tamper_or_external_binding_change(self):
        record = self.seal()
        record['milestones'][0]['goal'] = 'A different goal'
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'SEAL_MISMATCH'):
            sprint.validate_milestone_plan(record, **self.context)
        for field in ['source_ticket_plan', 'bindings', 'pipeline']:
            changed = deepcopy(self.context)
            changed[field][next(iter(changed[field]))] = 'changed'
            with self.subTest(field=field), self.assertRaises(sprint.SprintPlanningError):
                sprint.validate_milestone_plan(self.seal(), **changed)

    def test_structural_graph_ignores_progress_but_binds_authority(self):
        original = sprint.structural_graph_digest(self.context['tickets'])
        changed = deepcopy(self.context['tickets'])
        changed[0]['status'] = 'merged'
        self.assertEqual(original, sprint.structural_graph_digest(changed))
        for field, value in [('write_paths', ['src/other.py']), ('scope', ['other']),
                             ('dependencies', ['T-2']), ('generator_dependencies', ['T-2']),
                             ('contract_sha256', '0' * 64)]:
            changed = deepcopy(self.context['tickets'])
            changed[1][field] = value
            with self.subTest(field=field):
                try:
                    digest = sprint.structural_graph_digest(changed)
                except sprint.SprintPlanningError:
                    continue
                self.assertNotEqual(original, digest)

    def test_exact_calendar_coverage_and_prerequisite_closure(self):
        self.reject('PREREQUISITE_CLOSURE', lambda r: r['milestones'][0].update(prerequisites=['T-2']))
        self.record = proposal(self.context)
        self.reject('MILESTONE_COVERAGE', lambda r: r.update(planned_finish='2026-04-14'))
        self.record = proposal(self.context)
        self.reject('CALENDAR_MISMATCH', lambda r: r['milestones'][0].update(review_at='2026-03-27T15:00:00Z'))

    def test_closed_shapes_at_every_level(self):
        objects = [lambda r: r, lambda r: r['calendar'], lambda r: r['planning_binding'],
                   lambda r: r['project_binding'], lambda r: r['milestones'][0],
                   lambda r: r['milestones'][0]['targets'][0],
                   lambda r: r['milestones'][0]['pipeline_runs'][0],
                   lambda r: r['milestones'][0]['pipeline_runs'][0]['artifacts'][0],
                   lambda r: r['pipeline']['stages'][0]['implementation']]
        for select in objects:
            self.record = proposal(self.context)
            with self.subTest(select=select):
                self.reject('UNKNOWN_FIELD', lambda r: select(r).update(unknown=True))

    def test_foundation_mock_disconnected_and_unknown_are_distinct(self):
        cases = [('FOUNDATION_ONLY', lambda p: [s.update(kind='foundation') for s in p['stages']]),
                 ('MOCK_ONLY', lambda p: p['stages'][1]['implementation'].update(kind='mock')),
                 ('UNKNOWN_IMPLEMENTATION_PROVENANCE', lambda p: p['stages'][1]['implementation'].update(kind='unknown')),
                 ('DISCONNECTED_PIPELINE', lambda p: p.update(connections=[]))]
        for reason, change in cases:
            with self.subTest(reason=reason):
                self.context = inputs()
                change(self.context['pipeline'])
                self.record = proposal(self.context)
                self.reject(reason, lambda r: None)

    def test_missing_terminal_extra_disconnected_stage_and_scope_provenance(self):
        self.reject('TERMINAL_ARTIFACT', lambda r: r['milestones'][0]['pipeline_runs'][0]['artifacts'][0].update(id='other'))
        self.record = proposal(self.context)
        self.reject('DISCONNECTED_PIPELINE', lambda r: r['milestones'][0]['pipeline_runs'][0].update(stage_ids=['write']))
        self.context['pipeline']['stages'][1]['scope_refs'] = ['unknown']
        self.record = proposal(self.context)
        self.reject('UNKNOWN_SCOPE', lambda r: None)

    def test_predecessor_requires_identity_and_digest_together(self):
        self.reject('MISSING_REQUIRED_FIELD', lambda r: r.update(predecessor={'record_id': 'old'}))

    def test_successor_and_multiple_sprints_are_closed_and_deterministic(self):
        first = self.seal()
        self.record.update(record_id='proposal-2', planned_finish='2026-04-14',
                           predecessor=dict(record_id=first['record_id'], plan_sha256=first['plan_sha256']))
        for ordinal in (2, 3):
            later = deepcopy(self.record['milestones'][0])
            later.update(id=f'm{ordinal}', ordinal=ordinal, **sprint.sprint_interval(self.record['calendar'], ordinal))
            self.record['milestones'].append(later)
        sealed = self.seal()
        self.assertEqual(3, len(sealed['milestones']))
        self.assertEqual(sealed, self.seal())

    def test_missing_real_implementation_provenance_has_specific_cause(self):
        for field, value in [('reference', ''), ('sha256', None)]:
            with self.subTest(field=field):
                self.context = inputs()
                self.context['pipeline']['stages'][1]['implementation'][field] = value
                self.record = proposal(self.context)
                self.reject('UNKNOWN_IMPLEMENTATION_PROVENANCE', lambda r: None)

    def test_extra_stage_cannot_hang_off_the_real_route(self):
        stage = deepcopy(self.context['pipeline']['stages'][0])
        stage['id'] = 'unused'
        self.context['pipeline']['stages'].append(stage)
        self.context['pipeline']['connections'].append(dict(source='read', target='unused'))
        self.record = proposal(self.context)
        self.record['milestones'][0]['pipeline_runs'][0]['stage_ids'].append('unused')
        self.reject('DISCONNECTED_PIPELINE', lambda r: None)

    def test_source_plan_must_already_be_sealed(self):
        for source in ({'tickets': []}, {**self.context['source_ticket_plan'], 'plan_sha256': '0' * 64}):
            with self.subTest(source=source), self.assertRaisesRegex(sprint.SprintPlanningError, 'SOURCE_PLAN_SEAL'):
                sprint.source_plan_digest(source)

    def test_malformed_references_have_named_refusals(self):
        edits = [lambda r: r['slices'].append({'parent_ticket': []}),
                 lambda r: r['milestones'][0]['targets'][0].update(ticket_key=[]),
                 lambda r: r['pipeline']['stages'][0].update(ticket_key={}),
                 lambda r: r['pipeline']['connections'][0].update(source=[]),
                 lambda r: r['pipeline']['terminal_artifacts'][0].update(producer_stage={})]
        for edit in edits:
            self.record = proposal(self.context)
            edit(self.record)
            with self.subTest(edit=edit), self.assertRaises(sprint.SprintPlanningError):
                self.seal()


class SliceTests(unittest.TestCase):
    def setUp(self):
        self.context = inputs()
        self.record = proposal(self.context)
        parent = self.context['tickets'][1]
        self.slice = dict(id='slice-1', parent_ticket='T-2', contract_sha256='a' * 64,
                          requirements_sha256='b' * 64, acceptance_criteria=['AC1'],
                          included_scope=['write'], remaining_scope=['read'],
                          dependencies=['T-1'], generator_dependencies=[], risk_tier=3,
                          closure_standard='FULL', write_paths=parent['write_paths'],
                          completion_predicates=['Real result produced'], artifact_refs=['result'])
        self.record['slices'] = [self.slice]
        self.record['milestones'][0]['targets'][0]['slice_id'] = 'slice-1'

    def test_valid_slice_remains_parent_work(self):
        sealed = sprint.seal_milestone_plan(self.record, **self.context)
        self.assertEqual(['T-1', 'T-2'], sealed['milestones'][0]['prerequisites'])
        self.assertNotIn('dispatchable', sealed['slices'][0])

    def test_slice_cannot_widen_authority_or_hide_remaining_scope(self):
        for field, value, reason in [
            ('write_paths', ['src/other.py'], 'SLICE_WRITE_PATHS'),
            ('write_paths', ['src/T-2.py/**'], 'INVALID_PATH'),
            ('acceptance_criteria', ['AC9'], 'UNKNOWN_CRITERION'),
            ('included_scope', ['unknown'], 'UNKNOWN_SCOPE'),
            ('remaining_scope', [], 'SLICE_SCOPE_PARTITION'),
            ('contract_sha256', '0' * 64, 'SLICE_PARENT_BINDING'),
            ('dependencies', [], 'SLICE_INHERITANCE'),
            ('risk_tier', 1, 'SLICE_INHERITANCE'),
            ('closure_standard', 'DECLARED_LIMITATIONS', 'SLICE_INHERITANCE'),
            ('parent_ticket', 'T-9', 'UNKNOWN_TICKET')]:
            with self.subTest(field=field):
                record = deepcopy(self.record)
                record['slices'][0][field] = value
                with self.assertRaises(sprint.SprintPlanningError) as caught:
                    sprint.seal_milestone_plan(record, **self.context)
                self.assertEqual(reason, caught.exception.reason)

    def test_slice_cannot_demonstrate_parent_scope_outside_slice(self):
        self.slice.update(included_scope=['read'], remaining_scope=['write'])
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'SLICE_PIPELINE_SCOPE'):
            sprint.seal_milestone_plan(self.record, **self.context)


class RankingTests(unittest.TestCase):
    def test_earliest_closure_and_restricted_critical_score_then_existing_tie(self):
        tickets = [authority('T-1', duration=2), authority('T-2', ['T-1'], 3),
                   authority('T-3', duration=100), authority('T-4', duration=3)]
        milestones = [dict(ordinal=1, ticket_keys=['T-2', 'T-4']), dict(ordinal=2, ticket_keys=['T-1', 'T-3'])]
        effective, scores = sprint.milestone_priorities(tickets, milestones)
        self.assertEqual({'T-1': 1, 'T-2': 1, 'T-3': 2, 'T-4': 1}, effective)
        self.assertEqual(5, scores[1]['T-1'])
        self.assertEqual(3, scores[1]['T-2'])
        self.assertNotIn('T-3', scores[1])
        tie = {'T-1': (2, 'T-1'), 'T-2': (1, 'T-2'), 'T-3': (0, 'T-3'), 'T-4': (0, 'T-4')}
        ranked = sorted(tie, key=lambda k: sprint.milestone_rank_key(k, effective, scores, tie[k]))
        self.assertEqual(['T-1', 'T-4', 'T-2', 'T-3'], ranked)

    def test_generator_prerequisites_unknown_cycle_and_unassigned(self):
        tickets = [authority('T-1'), authority('T-2')]
        tickets[1]['generator_dependencies'] = ['T-1']
        self.assertEqual(['T-1', 'T-2'], sprint.prerequisite_closure(tickets, ['T-2']))
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'UNKNOWN_TICKET'):
            sprint.prerequisite_closure(tickets, ['T-9'])
        tickets[0]['dependencies'] = ['T-2']
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'DEPENDENCY_CYCLE'):
            sprint.prerequisite_closure(tickets, ['T-1'])
        with self.assertRaisesRegex(sprint.SprintPlanningError, 'UNASSIGNED_TICKET'):
            sprint.milestone_rank_key('T-9', {}, {}, ('T-9',))

    def test_deep_closure_uses_iterative_graph_walks(self):
        tickets = [authority(f'T-{i}', [] if i == 1 else [f'T-{i-1}'], 1) for i in range(1, 1102)]
        closure = sprint.prerequisite_closure(tickets, ['T-1101'])
        self.assertEqual(1101, len(closure))
        effective, scores = sprint.milestone_priorities(tickets, [dict(ordinal=1, ticket_keys=['T-1101'])])
        self.assertEqual(1101, scores[1]['T-1'])
        self.assertEqual(1, effective['T-1'])


if __name__ == '__main__':
    unittest.main()
