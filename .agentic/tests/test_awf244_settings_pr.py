"""Calendar PR regressions use real temporary Git repositories, offline."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError, operating as op
from agentic.canonical import fingerprint
from agentic.operating_calendar import effective_calendar, next_review_presentation
from agentic.protected_settings import audit_matches, unprotected_hash
import test_awf242_settings_pr as budget_fixture


class CalendarPRTests(budget_fixture.SettingsPRTests):
    def calendar_value(self, enabled):
        value = deepcopy(self.value)
        value['operating_calendar']['review_batching']['enabled'] = enabled
        return value

    def held(self):
        snapshot = op.read_operating(self.root, self.gov)
        return next_review_presentation('2026-11-07T10:00:00Z', effective_calendar(snapshot.config))['held']

    def test_calendar_working_and_pr_edits_do_not_change_batching_in_either_direction(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.publish(self.calendar_value(enabled))
                self.write(self.calendar_value(not enabled))
                self.assertEqual(enabled, self.held())
                self.git('checkout', '-B', 'codex/calendar-' + str(enabled))
                self.git('add', op.CONFIG)
                self.git('commit', '-qm', 'Proposed calendar')
                self.assertEqual(enabled, self.held())
                commit = self.git('rev-parse', 'HEAD')
                self.git('update-ref', 'refs/remotes/origin/main', commit)
                self.assertEqual(not enabled, self.held())
                self.assertEqual(commit, op.read_operating(self.root, self.gov).provenance['protected_settings']['commit'])

    def test_calendar_set_refuses_pr_without_any_write(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValidationError, 'PR'):
            op.set_operating(self.root, self.gov, 'disable batching', ['operating_calendar.review_batching.enabled=false'])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_calendar_malformed_default_refuses_instead_of_releasing_hold(self):
        for invalid in (None, [], {'review_batching': {'enabled': 'false'}}, {'timezone': 'Bad/Zone'}):
            with self.subTest(invalid=invalid):
                value = deepcopy(self.value)
                value['operating_calendar'] = invalid
                self.publish(value)
                self.write(self.calendar_value(False))
                with self.assertRaisesRegex(ValidationError, 'default.branch'):
                    self.held()

    def test_calendar_absent_default_retains_legacy_defaults(self):
        value = deepcopy(self.value)
        value.pop('operating_calendar')
        self.publish(value)
        self.write(self.calendar_value(False))
        snapshot = op.read_operating(self.root, self.gov)
        self.assertNotIn('operating_calendar', snapshot.config)
        self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))
        self.assertTrue(self.held())

    def test_calendar_show_and_proposed_validation(self):
        accepted = self.calendar_value(False)
        self.publish(accepted)
        self.write(self.value)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, op.main(['--root', str(self.root), 'show', '--json']))
        report = json.loads(output.getvalue())
        self.assertFalse(report['effective_operating_calendar']['review_batching']['enabled'])
        self.assertTrue(any(x['path'] == 'operating_calendar' for x in report['protected_settings']['differences']))
        proposed = self.root / 'proposed.yaml'
        proposed.write_bytes(op._json(accepted))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, op.main(['--root', str(self.root), 'validate', '--file', str(proposed)]))
        accepted['operating_calendar']['review_batching']['cutoff_time'] = '06:00'
        proposed.write_bytes(op._json(accepted))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(2, op.main(['--root', str(self.root), 'validate', '--file', str(proposed)]))

    def test_pre_calendar_protection_and_195_audits_still_verify(self):
        legacy = deepcopy(self.value)
        legacy.pop('budgeting')
        legacy.pop('operating_calendar')
        old_raw = {'after_hash': fingerprint('operating', legacy)}
        self.assertTrue(audit_matches(self.value, old_raw))
        old_projection = {'after_hash': fingerprint('operating', self.value),
            'unprotected_after_hash': fingerprint('operating-unprotected', {k: v for k, v in self.value.items() if k != 'budgeting'})}
        self.assertTrue(audit_matches(self.value, old_projection))
        edited = self.calendar_value(False)
        self.assertEqual(unprotected_hash(self.value), unprotected_hash(edited))
        self.assertTrue(audit_matches(edited, old_projection))
        edited['streams']['count'] = 1
        self.assertFalse(audit_matches(edited, old_projection))

    def test_historical_audit_records_bridge_next_unprotected_change(self):
        for projection in (False, True):
            with self.subTest(projection=projection):
                # Each history starts in a separate real repository.
                from settings_pr_fixtures import publish_fixture_settings
                root = self.root / ('projection' if projection else 'legacy195')
                value = deepcopy(self.value)
                if not projection:
                    value.pop('budgeting')
                    value.pop('operating_calendar')
                publish_fixture_settings(self, root, self.gov, value)
                op.initialize_operating(root, self.gov)
                record = next((root / op.STATE / 'changes').glob('*.json'))
                event = json.loads(record.read_bytes())
                if projection:
                    event['unprotected_after_hash'] = fingerprint('operating-unprotected',
                        {k: v for k, v in value.items() if k != 'budgeting'})
                else:
                    event.pop('unprotected_before_hash', None)
                    event.pop('unprotected_after_hash', None)
                record.write_bytes(op._json(event))
                # A default-branch policy update must preserve old local audit.
                accepted = deepcopy(value)
                accepted['operating_calendar'] = effective_calendar({})
                accepted['operating_calendar']['eod']['time'] = '18:00'
                publish_fixture_settings(self, root, self.gov, accepted)
                working = deepcopy(self.value)
                working['operating_calendar']['review_batching']['enabled'] = False
                (root / op.CONFIG).write_bytes(op._json(working))
                self.assertEqual('18:00', effective_calendar(op.read_operating(root, self.gov).config)['eod']['time'])
                changed = op.set_operating(root, self.gov, 'two streams', ['streams.count=2'])
                self.assertEqual(2, changed.count)
                self.assertEqual('18:00', effective_calendar(changed.config)['eod']['time'])
                self.assertEqual(changed, op.read_operating(root, self.gov))

    def test_nonrepository_absent_empty_and_full_calendars_retain_default_behavior(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='awf-calendar-defaults-') as raw:
            root = Path(raw)
            (root / op.GOVERNANCE).parent.mkdir()
            (root / op.GOVERNANCE).write_bytes(op._json(self.gov))
            for calendar in (None, {}, effective_calendar({})):
                with self.subTest(calendar=calendar):
                    value = deepcopy(self.value)
                    if calendar is None:
                        value.pop('operating_calendar')
                    else:
                        value['operating_calendar'] = calendar
                    (root / op.CONFIG).write_bytes(op._json(value))
                    with patch('agentic.protected_settings._git', side_effect=AssertionError('Git called')), \
                            patch('agentic.git_isolation.native_git_executable', side_effect=AssertionError('Git admitted')):
                        snapshot = op.read_operating(root, self.gov)
                    self.assertEqual(value, snapshot.config)
                    self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))


if __name__ == '__main__':
    unittest.main()


class OperatingNoEchoSubprocessTests(unittest.TestCase):
    """Rejected bytes must stay private across actual CLI/process boundaries."""
    def setUp(self):
        import test_awf242_settings_second_look as fixtures
        self.fixture = fixtures.SecondLookTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.gov, self.value = self.fixture.root, self.fixture.gov, self.fixture.value
        self.marker = 'AWF244_REJECTED_SENTINEL_7F39'

    def run_cli(self, *args, controller=False, route=False, preflight=False, status=False, expected=2,
                diagnostic='OPERATING_CONFIG.yaml'):
        import os
        from pathlib import Path
        import subprocess
        import sys
        source = Path(__file__).resolve().parents[2]
        if controller:
            import hashlib
            script = self.root / '.agentic/scripts/continuous_controller.py'
            script.parent.mkdir(exist_ok=True)
            script.write_bytes((source / '.agentic/scripts/continuous_controller.py').read_bytes())
            adapter = self.root.parent / 'adapter.py'
            names = ('observe_inventory observe_reservations dispatch_ticket observe_dispatch deliver_status '
                     'observe_publication observe_host_resources verify_host_observation observe_planning_backlog '
                     'verify_planning_backlog observe_planning_events verify_planning_events verify_planning_owner').split()
            adapter.write_text('ADAPTERS = {name: (lambda *a, **k: None) for name in ' + repr(names) + '}\n')
            # The reviewed adapter loader accepts the factory below; no port may run.
            adapter.write_text(adapter.read_text() + 'def build_adapters(config): return ADAPTERS\n')
            state = self.root.parent / 'controller.sqlite3'
            command = [str(script), '--state', str(state), '--stream', 'A', '--worktree-root', str(self.root),
                       '--adapter-module', str(adapter), '--adapter-sha256', hashlib.sha256(adapter.read_bytes()).hexdigest(),
                       'cycle', '--inventory-binding', str(self.root.parent / 'unused.json'),
                       '--repository-root', str(self.root), '--repository-head-sha', 'a'*40,
                       '--repository-tree-sha', 'b'*40, '--now', '2026-11-07T12:00:00Z']
        elif status:
            command = [str(source / '.agentic/scripts/workflow.py'), '--root', str(self.root), 'status',
                       '--gh', str(self.root.parent / 'unavailable-gh'), *args]
        elif preflight:
            command = [str(source / '.agentic/scripts/workflow.py'), '--root', str(self.root), 'preflight']
        elif route:
            request = self.root.parent / 'routing-request.json'
            request.write_text('{}')
            command = [str(source / '.agentic/scripts/route_model.py'), 'suggest',
                       '--config', str(self.root / op.GOVERNANCE), '--project-root', str(self.root),
                       '--request', str(request), '--capabilities', str(request)]
        else:
            command = [str(source / '.agentic/scripts/operating.py'), '--root', str(self.root), *args]
        before = {p: p.read_bytes() for p in self.root.parent.rglob('*') if p.is_file() and '.git' not in p.parts
                  and 'remote.git' not in p.parts}
        result = subprocess.run([sys.executable, '-B', *command], capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': str(source / '.agentic/lib'),
                 'AWF_STATE_HOME': str(self.root.parent / 'external-state')}, timeout=60)
        self.assertNotIn(self.marker, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        self.assertIn(diagnostic, result.stdout + result.stderr)
        for path in self.root.parent.rglob('*'):
            if path.is_file() and '.git' not in path.parts and 'remote.git' not in path.parts:
                raw = path.read_bytes()
                if before.get(path) != raw:
                    self.assertNotIn(self.marker.encode(), raw, str(path))
        return result

    def invalid_cases(self):
        cases = [('source', self.marker, 'not allowed'), ('version', self.marker, 'wrong type'),
                 ('streams.count', self.marker, 'wrong type'), ('streams.count', 0, 'out of range')]
        for role in ('controller', 'critic', 'specialist', 'simple_worker', 'streams.A.worker', 'streams.B.worker', 'streams.C.worker',
                     'streams.A.reviewer', 'streams.B.reviewer', 'streams.C.reviewer'):
            for key in ('model', 'reasoning_effort', 'pinned'):
                cases.append((role + '.' + key, self.marker, 'wrong type' if key == 'pinned' else 'not allowed'))
        cases.append(('simple_worker.enabled', self.marker, 'wrong type'))
        for key in ('accounting_enabled', 'enforcement_enabled', 'max_tokens_per_ticket', 'max_tokens_per_project'):
            cases.append(('budgeting.' + key, self.marker, 'wrong type'))
        from agentic.operating_calendar import SETTING_PATHS
        for key in sorted(SETTING_PATHS):
            cases.append(('operating_calendar.' + key, self.marker,
                          'wrong type' if key.endswith(('enabled', 'weekdays')) else 'not allowed'))
        for parent in ('', 'streams', 'streams.A.worker', 'budgeting', 'operating_calendar',
                       'operating_calendar.review_batching', 'operating_calendar.eod', 'epic_overrides', 'reporting'):
            cases.append(((parent + '.' if parent else '') + self.marker, self.marker, 'unknown key'))
        return cases

    def mutate(self, path, value):
        result = deepcopy(self.value)
        cursor = result
        parts = path.split('.')
        for key in parts[:-1]:
            if key == 'reviewer' and key not in cursor:
                cursor[key] = deepcopy(self.value['critic'])
            cursor = cursor.setdefault(key, {})
        cursor[parts[-1]] = value
        return result

    def test_schema_and_semantic_refusals_through_validate_and_show(self):
        proposed = self.root.parent / 'OPERATING_CONFIG.yaml'
        for path, value, kind in self.invalid_cases():
            with self.subTest(path=path, kind=kind):
                invalid = self.mutate(path, value)
                proposed.write_bytes(op._json(invalid))
                result = self.run_cli('validate', '--file', str(proposed), '--json')
                self.assertIn(kind, result.stdout)
                safe_path = (path.rsplit('.', 1)[0] if '.' in path else '$') if self.marker in path else path
                if path.startswith('reporting.'):
                    safe_path = '$'  # The root schema refuses this entire unknown subtree.
                self.assertIn(safe_path, result.stdout)
                self.fixture.publish(invalid)
                from agentic.protected_settings import refresh_default_policy
                self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
                self.fixture.write(self.value)
                for flag in ([], ['--json']):
                    result = self.run_cli('show', *flag)
                    self.assertIn(kind, result.stdout)

    def test_parser_refusals_through_controller_and_operating(self):
        for raw, kind in ((f'{self.marker}: 1\n{self.marker}: 2\n', 'duplicate key'),
                          (f'budgeting: [{self.marker}', 'malformed YAML')):
            with self.subTest(kind=kind):
                (self.root / op.CONFIG).write_text(raw)
                result = self.run_cli(controller=True)
                self.assertIn(kind, result.stdout + result.stderr)
                for flag in ([], ['--json']):
                    result = self.run_cli('validate', '--file', str(self.root / op.CONFIG), *flag)
                    self.assertIn(kind, result.stdout)
                    result = self.run_cli('show', *flag)
                    self.assertIn(kind, result.stdout)

    def test_unknown_keys_and_model_binding_paths_never_echo_in_refusal_records(self):
        for path in (self.marker, 'streams.' + self.marker + '.worker.model',
                     'epic_overrides.' + self.marker + '.controller.model', 'controller.model'):
            with self.subTest(path=path):
                with self.assertRaises(op.OperatingError) as caught:
                    op.validate_operating(self.mutate(path, self.marker), self.gov)
                self.assertNotIn(self.marker, str(caught.exception))
                self.assertNotIn(self.marker, json.dumps(caught.exception.refusals))

    def test_semantic_epic_keys_through_validate_and_accepted_show(self):
        self.marker = 'ECHO_SENTINEL-244'
        proposed = self.root.parent / 'OPERATING_CONFIG.yaml'
        cases = []
        for role in ('controller', 'critic', 'specialist'):
            cases.append(('epic_overrides.' + self.marker + '.' + role,
                          {'model': 'REJECTED_MODEL_SENTINEL', 'reasoning_effort': 'high'}))
        for role in ('worker', 'reviewer'):
            cases.append(('epic_overrides.' + self.marker + '.streams.A.' + role,
                          {'model': 'REJECTED_MODEL_SENTINEL', 'reasoning_effort': 'high'}))
        for path, pair in cases:
            with self.subTest(path=path):
                invalid = self.mutate(path, pair)
                proposed.write_bytes(op._json(invalid))
                for flags in ([], ['--json']):
                    result = self.run_cli('validate', '--file', str(proposed), *flags)
                    self.assertIn('<epic>', result.stdout)
                    self.assertNotIn('REJECTED_MODEL_SENTINEL', result.stdout + result.stderr)
                self.fixture.publish(invalid)
                from agentic.protected_settings import refresh_default_policy
                self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
                self.fixture.write(self.value)
                for flags in ([], ['--json']):
                    result = self.run_cli('show', *flags)
                    self.assertIn('<epic>', result.stdout)
                    self.assertNotIn('REJECTED_MODEL_SENTINEL', result.stdout + result.stderr)

    def test_scoped_assignment_refusals_never_echo_keys(self):
        self.marker = 'ECHO_SENTINEL-244'
        route = 'epic_overrides.' + self.marker + '.streams.A.worker'
        cases = [([route + '=invalid'], 'Route requires'),
                 ([route + '.pinned=invalid'], 'Expected true or false'),
                 ([route + '.pinned=true'], 'Choose a complete scoped route'),
                 ([route + '=gpt-6.1-sol/high', route + '=gpt-6.1-sol/high'], 'Duplicate or overlapping')]
        for assignments, reason in cases:
            with self.subTest(reason=reason):
                args = ['set', '--instruction', 'fixture instruction', '--json']
                for assignment in assignments:
                    args.extend(['--set', assignment])
                result = self.run_cli(*args)
                self.assertIn('<epic>', result.stdout)
                self.assertIn('<stream>', result.stdout)
                self.assertIn(reason, result.stdout)

    def test_recommendation_scope_refusal_never_echoes_epic_or_scope(self):
        self.marker = 'ECHO_SENTINEL-244'
        epics = self.root.parent / 'epics.json'
        epics.write_text(json.dumps({'schema_version': 1, 'epics': [{'id': self.marker, 'title': 'fixture',
                                              'scope': [self.marker + '/[private]']}]}))
        result = self.run_cli('recommend', '--epics', str(epics), '--json')
        self.assertIn('<epic>', result.stdout)
        self.assertIn('unsupported glob syntax', result.stdout)

    def test_recommendation_json_parser_never_echoes_keys(self):
        epics = self.root.parent / 'epics.json'
        epics.write_text(f'{{"{self.marker}":1,"{self.marker}":2}}')
        result = self.run_cli('recommend', '--epics', str(epics), '--json')
        self.assertIn('<key>', result.stdout)

    def test_recommendation_inventory_refusals_never_echo_keys(self):
        epics = self.root.parent / 'epics.json'
        epics.write_text(json.dumps({'schema_version': 1, 'epics': [
            {'id': 'FIX-244', 'title': 'fixture', 'scope': ['src/']}]}))
        inventory = self.root.parent / 'inventory.json'
        for raw in (json.dumps({self.marker: self.marker}),
                    f'{{"{self.marker}":1,"{self.marker}":2}}'):
            with self.subTest(raw=raw[:20]):
                inventory.write_text(raw)
                result = self.run_cli('recommend', '--epics', str(epics), '--inventory', str(inventory), '--json')
                self.assertIn('$.inventory', result.stdout)
                self.assertIn('<key>', result.stdout)

    def test_report_input_keys_through_status_subprocess(self):
        import test_adoption_status as status_fixture
        status_fixture.AdoptionStatusTests.setUpClass()
        self.addCleanup(status_fixture.AdoptionStatusTests.tearDownClass)
        self.root = status_fixture.AdoptionStatusTests.root
        request = self.root.parent / 'report-input.json'
        for raw in (f'{{"{self.marker}":1,"{self.marker}":2}}',
                    json.dumps({self.marker: self.marker})):
            with self.subTest(raw=raw[:20]):
                request.write_text(raw)
                result = self.run_cli('--json', '--report', '--report-input', str(request),
                    '--report-store', str(self.root.parent / 'unused.db'), status=True, expected=0,
                    diagnostic='Status report input')
                self.assertEqual('FAILED', json.loads(result.stdout)['status_report']['status'])
                self.assertIn('<key>', result.stdout + result.stderr)

    def test_programming_errors_are_not_disguised_as_yaml_refusals(self):
        from agentic.protected_settings import load_operating_yaml
        with patch('agentic.protected_settings.load_yaml', side_effect=RuntimeError('programming defect')):
            with self.assertRaisesRegex(RuntimeError, 'programming defect'):
                load_operating_yaml(b'{}')


    def test_working_protected_differences_are_safe_for_every_field(self):
        for path, value, kind in self.invalid_cases():
            if not path.startswith(('budgeting.', 'operating_calendar.')):
                continue
            with self.subTest(path=path):
                self.fixture.write(self.mutate(path, value))
                for flag in ([], ['--json']):
                    result = self.run_cli('show', *flag, expected=0)
                    self.assertIn('REJECTED protected value', result.stdout)
                    self.assertIn(kind, result.stdout)
                self.fixture.write(self.value)

    def test_controller_semantic_refusals_after_local_remote_refresh(self):
        for path, value, kind in self.invalid_cases():
            with self.subTest(path=path):
                self.fixture.publish(self.mutate(path, value))
                self.fixture.write(self.value)
                result = self.run_cli(controller=True)
                self.assertIn(kind, result.stdout + result.stderr)

    def test_route_cli_catches_parser_and_semantic_refusals(self):
        for raw in (f'{self.marker}: 1\n{self.marker}: 2\n', f'budgeting: [{self.marker}',
                    json.dumps(self.mutate('source', self.marker))):
            with self.subTest(raw=raw[:10]):
                (self.root / op.CONFIG).write_text(raw)
                self.run_cli(route=True)

    def test_suppressed_parser_chain_cannot_print_source(self):
        import traceback
        from agentic.protected_settings import load_operating_yaml
        for raw in (f'{self.marker}: 1\n{self.marker}: 2\n', f'budgeting: [{self.marker}'):
            with self.subTest(raw=raw[:10]):
                try:
                    load_operating_yaml(raw.encode())
                except ValidationError as exc:
                    self.assertNotIn(self.marker, ''.join(traceback.format_exception(exc)))
                else:
                    self.fail('Rejected YAML was accepted')


    def test_set_unknown_field_names_and_rejected_routes_are_safe(self):
        for path, selected in ((self.marker, self.marker),
                               ('streams.' + self.marker.replace('_', '') + '.worker', self.marker),
                               ('controller', self.marker + '/high'),
                               ('operating_calendar.timezone', self.marker),
                               ('budgeting.max_tokens_per_ticket', self.marker)):
            with self.subTest(path=path):
                self.run_cli('set', '--instruction', 'fixture instruction', '--set', path + '=' + selected, '--json')


    def test_preflight_cli_refuses_private_operating_diagnostics(self):
        for raw in (f'{self.marker}: 1\n{self.marker}: 2\n', f'budgeting: [{self.marker}',
                    json.dumps(self.mutate('controller.model', self.marker))):
            with self.subTest(raw=raw[:10]):
                (self.root / op.CONFIG).write_text(raw)
                result = self.run_cli(preflight=True)
                self.assertFalse(json.loads(result.stdout)['blocks_installation'])

    def test_source_status_parser_diagnostic_is_private(self):
        from agentic.operating_status import with_operating
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/build_release.py').write_text('# synthetic source marker\n')
        (self.root / 'MANIFEST.json').write_text(json.dumps(
            {'format': 'awf-manifest-1', 'files': {'scripts/build_release.py': 'a'*64}}))
        for raw in (f'{self.marker}: 1\n{self.marker}: 2\n', f'budgeting: [{self.marker}'):
            with self.subTest(raw=raw[:10]):
                (self.root / op.CONFIG).write_text(raw)
                report = with_operating(self.root, self.gov, {'status': 'ACCEPTED', 'unresolved': []})
                self.assertEqual('REJECTED', report['status'])
                self.assertNotIn(self.marker, json.dumps(report))
                kind = 'duplicate key' if ': 1' in raw else 'malformed YAML'
                self.assertIn(kind, report['operating']['refusals'][0]['reason'])


    def test_status_cli_refuses_operating_errors_even_with_configured_governance(self):
        import test_adoption_status as status_fixture
        status_fixture.AdoptionStatusTests.setUpClass()
        self.addCleanup(status_fixture.AdoptionStatusTests.tearDownClass)
        self.root = status_fixture.AdoptionStatusTests.root
        for raw in (f'{self.marker}: 1\n{self.marker}: 2\n', f'budgeting: [{self.marker}'):
            for flag in ([], ['--json']):
                with self.subTest(raw=raw[:10], json=bool(flag)):
                    (self.root / op.CONFIG).write_text(raw)
                    self.run_cli(*flag, status=True)
