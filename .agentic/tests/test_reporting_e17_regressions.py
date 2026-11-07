from test_reporting_fixtures import reporting_policy_snapshot
"""Terminal reporting ownership and input shapes through production entry points."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import source_only
from agentic.review_batching import ReviewBatchStore
import test_reporting_e6_regressions as e6
import test_reporting_e12_regressions as e12
import test_reporting_owner_admission as owner
from reporting_pr_fixtures import ReportingPRFixture as owner_fixture


class ExhaustionTests(unittest.TestCase):
    def probe(self, case, run):
        case.helper.listed = [deepcopy(case.helper.current)]
        case.network.latency = 0
        original = case.network.open

        def rejected(request, timeout):
            if request.get_method() == 'POST' and 'graph.microsoft.com' in request.full_url:
                case.network.posts.append(('teams', json.loads(request.data)))
                raise HTTPError(request.full_url, 400, 'Synthetic rejection', {}, io.BytesIO(b'{}'))
            return original(request, timeout)

        case.network.open = rejected
        for now in ('2026-11-11T10:15:01Z', '2026-11-11T10:16:01Z', '2026-11-11T10:17:01Z',
                    '2026-11-12T10:15:01Z', '2026-11-12T10:16:01Z', '2026-11-12T10:17:01Z'):
            e6.ControllerTests.restart(case)
            run(now)
        reports = [r for r in case.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual(1, len(reports))
        ids = reports[0]['delivery_ids']
        states = {d['platform']: d for d in case.status()['deliveries'] if d['delivery_id'] in ids}
        self.assertEqual('DELIVERED', states['slack']['status'])
        self.assertEqual('FAILED', states['teams']['status'])
        self.assertEqual(1, len(states['slack']['attempts']))
        self.assertEqual(2, len(states['teams']['attempts']))
        reviews = ReviewBatchStore(case.helper.store)
        for calendar, now in (({}, '2026-11-13T10:00:00Z'),
                              ({'review_batching': {'enabled': False}}, '2026-11-14T06:00:00Z'),
                              ({'review_batching': {'presentation_time': '06:00'}}, '2026-11-16T06:00:00Z')):
            self.assertIsNone(reviews.claim(now, calendar, lambda c: self.fail('Terminal intent re-observed'), max_members=1))
            view = reviews.view(now, calendar)
            self.assertEqual('EXHAUSTED', view['intents'][0]['state'])
            self.assertIsNone(view['intents'][0]['retry_at'])
            self.assertIsNone(reviews.enqueue_observed(case.helper.current,
                dict(candidate=case.helper.current, observed_at=now, expires_at=now.replace('00Z', '59Z'),
                     open=True, merged=False, final_gate_valid=True, readiness='READY_FOR_OWNER_AUTHORIZATION'),
                now, calendar, repository_id=str(case.helper.current['repository_id'])))

    def checked(self, result):
        self.assertEqual([], result['errors'])

    def test_controller_terminal_reporting_across_restart_and_calendar(self):
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.probe(case, lambda now: self.checked(case.cycle(now)))

    def test_cli_terminal_reporting_across_restart_and_calendar(self):
        e6.CliTests.probe(self, lambda c, n: None,
            lambda c, r, o: self.probe(c, lambda now: e12.E12CliTests.cycle(self, c, now)))

    def legacy_probe(self, case, run, retained):
        from agentic.reporting.runtime import ReviewReportingPort
        self.probe(case, run)
        now = '2026-11-13T10:15:01Z'
        case.clock.value = now
        reviews = ReviewBatchStore(case.helper.store)
        observe = lambda c: dict(candidate=c, observed_at=now, expires_at='2026-11-13T10:20:00Z',
            open=True, merged=False, final_gate_valid=True, readiness='READY_FOR_OWNER_AUTHORIZATION')
        # Construct the prior runtime's persisted protocol state by disabling
        # only the new ownership fence during fixture setup. Recovery below is
        # unpatched production controller/CLI and HTTP, across disk restarts.
        with patch.object(ReviewBatchStore, '_reporting_failure_intents', return_value=set()):
            payload = reviews.claim(now, {}, observe)
            reviews.recheck(payload['batch_id'], now, {}, observe)
            reviews.begin_delivery(payload['batch_id'], now)
            if retained:
                providers = case.adapters['configure_reporting'](reporting_policy_snapshot(case), case.binding)
                ReviewReportingPort(case.helper.store, {'reporting': case.settings}, case.binding,
                                    providers, case.clock.now)._retain_review_batch(payload)
        before = [r for r in case.status()['reports'] if r['kind'] == 'final-human-review']
        for now in ('2026-11-13T10:16:01Z', '2026-11-13T10:17:01Z', '2026-11-13T10:18:01Z'):
            e6.ControllerTests.restart(case)
            run(now)
        after = [r for r in case.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual({r['report_id'] for r in before}, {r['report_id'] for r in after})
        duplicate = next((r for r in after if r['report_id'] == 'review-' + payload['batch_id']), None)
        if retained:
            self.assertIsNotNone(duplicate)
            states = [d for d in case.status()['deliveries'] if d['delivery_id'] in duplicate['delivery_ids']]
            self.assertEqual(2, len(states))
            self.assertTrue(all(not d['attempts'] for d in states))
        else:
            self.assertIsNone(duplicate)

    def test_controller_legacy_claim_recovery_cannot_replace_terminal_report(self):
        for retained in (False, True):
            with self.subTest(retained=retained):
                case = e6.ControllerTests()
                case.setUp()
                self.addCleanup(case.doCleanups)
                self.legacy_probe(case, lambda now: case.cycle(now), retained)

    def test_cli_legacy_claim_recovery_cannot_replace_terminal_report(self):
        for retained in (False, True):
            with self.subTest(retained=retained):
                e6.CliTests.probe(self, lambda c, n: None,
                    lambda c, r, o: self.legacy_probe(c, lambda now: self.legacy_cli_cycle(c, now), retained))

    def legacy_cli_cycle(self, case, now):
        import test_reporting_e3_regressions as e3
        from agentic.canonical import sha256
        case.clock.value = now
        e3.CLI_ADAPTERS['controller_clock'] = case.clock.now
        root = Path(case.helper.temp.name)
        project, adapter = root / 'cli-project', root / 'cli_adapter.py'
        cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/continuous_controller.py'))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli['main'](['--state', str(case.helper.path), '--worktree-root', str(project / 'worker-worktrees'),
                '--cadence-seconds', '3600', '--stream', 'A', '--stream', 'B', '--stream', 'C',
                '--project-config', str(project / '.agentic/PROJECT_CONFIG.yaml'),
                '--adapter-module', str(adapter), '--adapter-sha256', sha256(adapter.read_bytes()),
                'cycle', '--inventory-binding', str(root / 'binding.json'), '--repository-root', str(project),
                '--repository-head-sha', case.repository['repository_head_sha'],
                '--repository-tree-sha', case.repository['repository_tree_sha'], '--now', now, '--host-capacity', '3'], default_root=project)
        self.assertEqual(0, code, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertTrue(all((e.get('operation'), e.get('state'), e.get('reason')) in {
            ('reporting_review_resume', 'UNKNOWN', 'ValidationError'),
            ('review_readback', 'PENDING', 'REPORT_REVIEW_INTENT_TERMINAL'),
            # A prior runtime's started fence is still ambiguous. Refusal must
            # preserve that owner alert rather than inventing non-delivery.
            ('review_owner_attention', 'UNKNOWN', 'Delivery unresolved; receipt-only reconciliation required')
        } for e in result['errors']), result['errors'])
        return result


class InputShapeTests(unittest.TestCase):
    setUpClass = classmethod(owner.ReportingOwnerAdmissionTests.setUpClass.__func__)
    setUp = owner.ReportingOwnerAdmissionTests.setUp
    save_governance = owner.ReportingOwnerAdmissionTests.save_governance
    events = owner.ReportingOwnerAdmissionTests.events
    cli = owner.ReportingOwnerAdmissionTests.cli
    git = owner.ReportingOwnerAdmissionTests.git
    unchanged = owner.ReportingOwnerAdmissionTests.unchanged
    bindings = owner.ReportingOwnerAdmissionTests.bindings
    assignments = owner.ReportingOwnerAdmissionTests.assignments
    forge_config_event = owner_fixture.forge_config_event
    write = owner_fixture.write
    publish = owner_fixture.publish
    refresh = owner_fixture.refresh
    def assert_refused_without_storage(self, assignments, value):
        result = self.cli('set', '--instruction', 'Reject malformed settings',
            *[part for assignment in assignments for part in ('--set', assignment)], '--json')
        self.assertEqual(2, result.returncode, 'Malformed setting was admitted')
        self.assertNotIn(value, result.stdout + result.stderr)
        self.assertFalse((self.fixture.base / 'protected').exists())
        self.assertFalse(self.calls.exists())
        self.unchanged()

    def test_every_reporting_field_refuses_non_shape_without_echo_or_storage(self):
        value = 'ghp_' + 'a' * 36
        self.env['REPORT_TEST_SECRET'] = value
        for field in self.bindings()[0]:
            with self.subTest(field=field):
                bindings = self.bindings()
                bindings[0][field] = value
                self.assert_refused_without_storage(self.assignments(bindings=bindings), value)
        self.assert_refused_without_storage(('reporting.mode=' + value,), value)
        self.assert_refused_without_storage(('reporting.bindings=[{"' + value + '":1,"' + value + '":2}]',), value)

    def test_budget_and_calendar_refuse_non_shape_without_echo_or_storage(self):
        value = 'ghp_' + 'b' * 36
        paths = ('budgeting.accounting_enabled', 'budgeting.enforcement_enabled',
                 'budgeting.max_tokens_per_ticket', 'budgeting.max_tokens_per_project',
                 'operating_calendar.timezone', 'operating_calendar.work_mode',
                 'operating_calendar.review_batching.enabled', 'operating_calendar.review_batching.weekdays',
                 'operating_calendar.review_batching.presentation_time', 'operating_calendar.review_batching.cutoff_time',
                 'operating_calendar.eod.time', 'operating_calendar.eod.weekdays')
        for path in paths:
            with self.subTest(path=path):
                self.assert_refused_without_storage((path + '=' + value,), value)
        self.assert_refused_without_storage(('operating_calendar.eod.weekdays={"' + value + '":1,"' + value + '":2}',), value)

    def test_unsafe_hand_edit_is_withheld_and_preserved(self):
        from agentic import operating
        value = 'ghp_' + 'e' * 36
        changed = json.loads(self.before)
        bindings = self.bindings()
        bindings[0]['revision'] = value
        changed['reporting'] = dict(mode='both', bindings=bindings)
        self.forge_config_event(changed)
        before = (self.root / operating.CONFIG).read_bytes()
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode)
        self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])
        self.assertEqual('off', json.loads(shown.stdout)['configuration']['reporting']['mode'])
        self.assertNotIn(value, shown.stdout + shown.stderr)
        self.assertEqual(before, (self.root / operating.CONFIG).read_bytes())
        self.assertFalse((self.fixture.base / 'protected').exists())


class SchemaTests(unittest.TestCase):
    def test_reporting_shapes_match_both_schemas_and_refusals_hide_values(self):
        from agentic import operating
        from agentic.reporting.settings import BINDING_PATTERNS
        from jsonschema import Draft202012Validator
        value = 'ghp_' + 'd' * 36
        for name in ('operating-config', 'operating-change'):
            schema = json.loads((operating.SCHEMAS / (name + '.schema.json')).read_text())
            root = schema['properties']['reporting' if name == 'operating-config' else 'reporting_owner_observation']
            if name == 'operating-change':
                root = root['properties']['changes']
            properties = root['properties']['bindings']['items']['properties']
            self.assertTrue(Draft202012Validator(properties['repository']).is_valid('logical-repo'))
            for field, pattern in BINDING_PATTERNS.items():
                self.assertEqual('^(?:' + pattern + ')$', properties[field]['pattern'])
                if field != 'secret_ref':
                    self.assertFalse(Draft202012Validator(properties[field]).is_valid(value))
        for domain in ('reporting', 'budgeting', 'operating_calendar'):
            errors = operating._schema('operating-config', {domain: {value: value}})
            self.assertTrue(errors)
            self.assertNotIn(value, json.dumps(errors))


if __name__ == '__main__':
    unittest.main()
