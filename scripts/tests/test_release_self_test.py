"""Release orchestration regression tests with small synthetic inventories."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import os
import io
from contextlib import redirect_stdout, redirect_stderr
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))
import plan_installed_self_test as planner
from agentic.self_test_shards import COMPONENT_CHECKS, UPGRADE_MATRIX
from agentic.self_test_shards import InstalledLoader, validate_release_selection
import release_self_test as release
from agentic import VERSION


class SequentialReleaseTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.root = self.work / 'source'
        paths = [UPGRADE_MATRIX, '.agentic/tests/test_adoption_status.py',
                 'scripts/tests/test_installed_self_test.py', 'global/awf/tests/test_global.py']
        for path in paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            names = sorted(planner.source.UPGRADE_MATRIX_FIRST_HALF) + ['test_second'] if path == UPGRADE_MATRIX else ['test_ok']
            target.write_text('import unittest\nclass Case(unittest.TestCase):\n' + ''.join(
                '    def ' + name + '(self): pass\n' for name in names), encoding='utf-8')
        files = {p: hashlib.sha256((self.root / p).read_bytes()).hexdigest() for p in paths}
        (self.root / 'MANIFEST.json').write_text(json.dumps({'format': 'awf-manifest-1', 'template_version': VERSION, 'files': files}), encoding='utf-8')
        self.report = self.work / 'stage.json'
        self.calls = []
        self.mutate = lambda report: None
        # Release shards have already imported the real split module. Let this
        # synthetic root own its cache entry, then restore the surrounding suite.
        split_name = Path(UPGRADE_MATRIX).stem
        existing_split = sys.modules.pop(split_name, None)
        def restore_split():
            sys.modules.pop(split_name, None)
            if existing_split is not None:
                sys.modules[split_name] = existing_split
        self.addCleanup(restore_split)

    def test_split_module_origin_mismatch_refuses_foreign_cached_module(self):
        plan = planner.build_release_plan(self.root)
        name = Path(UPGRADE_MATRIX).stem
        foreign = type(sys)(name)
        foreign.__file__ = str(ROOT / UPGRADE_MATRIX)
        with mock.patch.dict(sys.modules, {name: foreign}):
            with self.assertRaisesRegex(ValueError, 'split module origin mismatch'):
                release.expected_partitions(self.root, plan)
            self.assertIs(foreign, sys.modules[name])

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        plan = json.loads(Path(command[command.index('--selection-file') + 1]).read_bytes())
        shard = int(command[command.index('--shard') + 1])
        selected = plan['shards'][str(shard)]
        parts = release.expected_partitions(self.root, plan)
        report = dict(scope=plan['scope'] + '-shard', status='SHARD_PASS', suite_complete=False,
                      release_qualified=False, shard=shard, shard_count=plan['shard_count'],
                      source_manifest_sha256=plan['source_manifest_sha256'],
                      selection_sha256=hashlib.sha256(planner.encode(plan)).hexdigest(),
                      selected_modules=selected[:], discovered_modules=selected[:], completed_modules=selected[:],
                      component_phases={phase: {'count': 1, 'results': [{'status': 'PASS', 'checks': {
                          key: {'status': 'PASS', 'result': 'PASS'} for key in keys}}]}
                          for phase, keys in COMPONENT_CHECKS.items()} if shard == 0 else {},
                      checks={'integrity': 'PASS'}, isolation='PASS', current_review={'status': 'NOT_PROVIDED'},
                      tests=dict(run=1, failures=0, errors=0, successful=True, skipped=[]),
                      split_cases={p: owners[shard] for p, owners in parts.items() if shard in owners},
                      split_all_cases={p: sorted(sum(owners.values(), [])) for p, owners in parts.items() if shard in owners},
                      zero_test_modules={})
        self.mutate(report)
        path = Path(command[command.index('--report') + 1])
        if self.mode != 'missing':
            path.write_text('[]' if self.mode == 'malformed' else json.dumps(report), encoding='utf-8')
        if self.mode == 'pin':
            (self.root / '.agentic/tests/test_adoption_status.py').write_text('changed', encoding='utf-8')
        if self.mode == 'plan':
            Path(command[command.index('--selection-file') + 1]).write_bytes(b'{}')
        if self.mode == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return subprocess.CompletedProcess(command, 1 if self.mode == 'nonzero' else 0, b'', b'')

    def invoke(self, mode='success'):
        self.mode = mode
        return release.run_self_test(self.root, self.report, timeout=600, runner=self.runner)

    def test_success_sequential_deadlines_and_complete_inventory(self):
        report = self.invoke()
        self.assertEqual(('PASS', False, False), (report['status'], report['suite_complete'], report['release_qualified']))
        self.assertEqual(9, len(self.calls))
        self.assertEqual([600] * 9, [kw['timeout'] for _, kw in self.calls])
        self.assertEqual(9, len(report['shard_reports']))
        self.assertEqual('PENDING_INSTALLED_ACCEPTANCE', report['coverage_reconciliation']['status'])
        plan = json.loads((self.work / 'stage.json.shards/selection.json').read_bytes())
        self.assertIn('scripts/tests/test_installed_self_test.py', plan['inventory'])
        self.assertIn('global/awf/tests/test_global.py', plan['inventory'])
        with self.assertRaises(FileExistsError):
            self.invoke()

    def test_only_named_installed_acceptance_module_is_deferred(self):
        plan = planner.build_release_plan(self.root)
        path = 'scripts/tests/test_installed_self_test.py'
        self.assertEqual({path: 'installed-acceptance'}, plan['deferred_coverage'])
        self.assertNotIn(path, sum(plan['shards'].values(), []))
        self.assertIn(path, plan['inventory'])

    def test_unknown_or_second_ci_exclusion_refuses_release_plan(self):
        for excluded in ({'scripts/tests/test_foreign.py'},
                         {'scripts/tests/test_installed_self_test.py', 'scripts/tests/test_foreign.py'}):
            with self.subTest(excluded=excluded), mock.patch.object(planner.source, 'EXCLUDED_MODULES', excluded):
                with self.assertRaisesRegex(ValueError, 'closed installed acceptance'):
                    planner.build_release_plan(self.root)

    def test_unknown_or_second_coverage_exception_refuses_selection(self):
        for deferred in ({'scripts/tests/test_foreign.py': 'installed-acceptance'},
                         {'scripts/tests/test_installed_self_test.py': 'installed-acceptance',
                          'global/awf/tests/test_global.py': 'installed-acceptance'}):
            plan = planner.build_release_plan(self.root)
            plan['deferred_coverage'] = deferred
            raw = planner.encode(plan)
            with self.subTest(deferred=deferred), self.assertRaisesRegex(ValueError, 'closed installed acceptance'):
                validate_release_selection(raw, hashlib.sha256(raw).hexdigest(), plan['inventory'],
                                           plan['source_manifest_sha256'], 'source')

    def assert_refuses(self, mode):
        with self.assertRaisesRegex(release.ShardFailure, 'shard 0'):
            self.invoke(mode)
        report = json.loads(self.report.read_bytes())
        self.assertEqual(('FAILED', False, False), (report['status'], report['suite_complete'], report['release_qualified']))
        self.assertEqual(1, len(self.calls))

    def test_missing_shard_report(self): self.assert_refuses('missing')
    def test_malformed_shard_report(self): self.assert_refuses('malformed')
    def test_nonzero_exit(self): self.assert_refuses('nonzero')
    def test_timeout(self): self.assert_refuses('timeout')
    def test_source_pin_mutation(self): self.assert_refuses('pin')
    def test_plan_pin_mutation(self): self.assert_refuses('plan')

    def test_duplicate_membership(self):
        self.mutate = lambda r: r['completed_modules'].append(r['completed_modules'][0])
        self.assert_refuses('success')

    def test_foreign_membership(self):
        self.mutate = lambda r: r['completed_modules'].append('.agentic/tests/test_foreign.py')
        self.assert_refuses('success')

    def test_omitted_membership(self):
        self.mutate = lambda r: r['completed_modules'].clear()
        self.assert_refuses('success')

    def test_failed_component(self):
        self.mutate = lambda r: r['component_phases']['syntax']['results'][0].update(status='FAILED')
        self.assert_refuses('success')

    def test_nonfinite_report_is_malformed(self):
        self.mutate = lambda r: r.update(malformed=float('nan'))
        self.assert_refuses('success')

    def test_split_coverage_error(self):
        self.mutate = lambda r: r['split_cases'].update({UPGRADE_MATRIX: ['foreign.case']})
        self.assert_refuses('success')

    def test_rejects_selection_digest_from_another_run(self):
        self.mutate = lambda r: r.update(selection_sha256='0' * 64)
        self.assert_refuses('success')

    def test_rejects_wrong_scope(self):
        self.mutate = lambda r: r.update(scope='installed-shard')
        self.assert_refuses('success')

    def test_rejects_wrong_shard_number(self):
        self.mutate = lambda r: r.update(shard=1)
        self.assert_refuses('success')

    def test_rejects_wrong_shard_count(self):
        self.mutate = lambda r: r.update(shard_count=8)
        self.assert_refuses('success')

    def test_rejects_child_release_qualification(self):
        self.mutate = lambda r: r.update(release_qualified=True)
        self.assert_refuses('success')

    def test_rejects_child_suite_complete(self):
        self.mutate = lambda r: r.update(suite_complete=True)
        self.assert_refuses('success')

    def test_rejects_child_failed_status(self):
        self.mutate = lambda r: r.update(status='FAILED')
        self.assert_refuses('success')

    def test_rejects_failed_integrity(self):
        self.mutate = lambda r: r['checks'].update(integrity='FAILED')
        self.assert_refuses('success')

    def test_rejects_wrong_manifest(self):
        self.mutate = lambda r: r.update(source_manifest_sha256='0' * 64)
        self.assert_refuses('success')

    def test_rejects_test_failures(self):
        self.mutate = lambda r: r['tests'].update(failures=1)
        self.assert_refuses('success')

    def test_rejects_test_errors(self):
        self.mutate = lambda r: r['tests'].update(errors=1)
        self.assert_refuses('success')

    def test_rejects_unsuccessful_tests(self):
        self.mutate = lambda r: r['tests'].update(successful=False)
        self.assert_refuses('success')

    def test_rejects_zero_tests_for_nonempty_shard(self):
        self.mutate = lambda r: r['tests'].update(run=0)
        self.assert_refuses('success')

    def test_rejects_unnamed_skip(self):
        self.mutate = lambda r: r['tests']['skipped'].append({'test': '', 'reason': 'platform'})
        self.assert_refuses('success')

    def test_rejects_unexplained_skip(self):
        self.mutate = lambda r: r['tests']['skipped'].append({'test': 'case.test', 'reason': ' '})
        self.assert_refuses('success')

    def test_rejects_split_all_cases(self):
        self.mutate = lambda r: r['split_all_cases'].update({UPGRADE_MATRIX: ['foreign.case']})
        self.assert_refuses('success')

    def test_rejects_zero_test_module_disposition(self):
        self.mutate = lambda r: r['zero_test_modules'].update({'foreign.py': 0})
        self.assert_refuses('success')

    def test_non_shard_failures_have_phase_and_no_shard(self):
        for phase in ('plan', 'pins', 'aggregate'):
            with self.subTest(phase=phase):
                self.report = self.work / (phase + '.json')
                self.calls.clear()
                if phase == 'plan':
                    patch = mock.patch.object(release, 'build_release_plan', side_effect=ValueError('bad plan'))
                elif phase == 'pins':
                    patch = mock.patch.object(release, 'verify_release', side_effect=ValueError('bad pin'))
                else:
                    patch = mock.patch.object(release, 'Counter', side_effect=ValueError('bad aggregate'))
                with patch, self.assertRaises(release.ShardFailure):
                    self.invoke()
                report = json.loads(self.report.read_bytes())
                self.assertIsNone(report['failing_shard'])
                self.assertIsNone(report['coverage_reconciliation']['failing_shard'])
                self.assertEqual(phase, report['failure_phase'])
                self.assertIn(phase, report['error'])
                self.assertEqual(9 if phase == 'aggregate' else 0, len(self.calls))

    def test_each_child_records_elapsed_wall_time_and_unchanged_deadline(self):
        with mock.patch.object(release.time, 'monotonic', side_effect=range(18)):
            report = self.invoke()
        self.assertEqual([1] * 9, [r['wall_seconds'] for r in report['shard_reports']])
        self.assertEqual([600] * 9, [r['timeout_seconds'] for r in report['shard_reports']])

    def test_timeout_aggregate_binds_failing_shard_log_and_elapsed_time(self):
        def timeout_child(command, **kwargs):
            kwargs['log_path'].write_bytes(b'stdout: partial child output\n')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with mock.patch.object(release, 'run_child', side_effect=timeout_child), \
                self.assertRaises(release.ShardFailure):
            release.run_self_test(self.root, self.report, timeout=600)
        report = json.loads(self.report.read_bytes())
        self.assertEqual(0, report['failing_shard'])
        log = report['failing_shard_log']
        self.assertEqual((self.report.with_name(self.report.name + '.shards') / 'shard-0.log').resolve(), Path(log['path']))
        self.assertEqual(hashlib.sha256(Path(log['path']).read_bytes()).hexdigest(), log['sha256'])
        self.assertEqual(600, report['failed_child']['timeout_seconds'])
        self.assertGreaterEqual(report['failed_child']['wall_seconds'], 0)

    def test_real_child_timeout_retains_log(self):
        log = self.work / 'timeout.log'
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            release.run_child([sys.executable, '-B', '-c', 'import time; print("started", flush=True); time.sleep(30)'],
                              cwd=self.root, env=os.environ, timeout=0.3, log_path=log)
        self.assertIn(b'started', log.read_bytes())
        self.assertEqual(log.read_bytes(), raised.exception.output)

    def test_real_selected_discovery_executes_complete_source_union(self):
        plan = planner.build_release_plan(self.root)
        completed, splits = [], []
        original_path = sys.path[:]
        original_modules = dict(sys.modules)
        self.addCleanup(lambda: setattr(sys, 'path', original_path))
        def restore_modules():
            for name in set(sys.modules) - set(original_modules):
                sys.modules.pop(name, None)
        self.addCleanup(restore_modules)
        for shard in range(plan['shard_count']):
            loader = InstalledLoader(self.root, plan, shard)
            result = unittest.TestResult()
            loader.discover_selected().run(result)
            self.assertTrue(result.wasSuccessful(), result.errors)
            loader.reconcile()
            completed.extend(loader.completed)
            splits.extend(loader.split_cases.get(UPGRADE_MATRIX, []))
        self.assertEqual(set(plan['inventory']) - set(plan['deferred_coverage']), set(completed))
        self.assertEqual(len(plan['inventory']) - len(plan['deferred_coverage']) + 1, len(completed))
        self.assertEqual(len(planner.source.UPGRADE_MATRIX_FIRST_HALF) + 1, len(set(splits)))

    def test_shard_count_is_a_plan_property(self):
        plan = planner.build_release_plan(self.root)
        plan['shard_count'] = 10
        plan['shards']['9'] = []
        with mock.patch.object(release, 'build_release_plan', return_value=plan):
            report = self.invoke()
        self.assertEqual(10, len(self.calls))
        self.assertEqual(10, report['shard_count'])

    def test_installed_plan_uses_receipt_inventory(self):
        target = self.work / 'installed'
        paths = [p for p in planner.build_release_plan(self.root)['inventory'] if p.startswith('.agentic/tests/')]
        for path in paths:
            dest = target / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((self.root / path).read_bytes())
        (target / '.agentic/installed-manifest.json').write_text(json.dumps({'immutable_files': dict.fromkeys(paths)}), encoding='utf-8')
        plan = planner.build_release_plan(self.root, target)
        raw = planner.encode(plan)
        self.assertEqual(paths, plan['inventory'])
        validate_release_selection(raw, hashlib.sha256(raw).hexdigest(), paths, plan['source_manifest_sha256'], 'installed')
        extra = target / '.agentic/tests/test_foreign.py'
        extra.write_text('foreign', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'receipt'):
            planner.build_release_plan(self.root, target)

    def test_installed_receipt_cannot_omit_a_source_test_module(self):
        target = self.work / 'installed'
        self.prepare_installed(target)
        path = '.agentic/tests/test_adoption_status.py'
        (target / path).unlink()
        receipt = target / '.agentic/installed-manifest.json'
        value = json.loads(receipt.read_bytes())
        del value['immutable_files'][path]
        receipt.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'source candidate'):
            planner.build_release_plan(self.root, target)

    def test_review_pin_mutation_stops_before_next_child(self):
        review = self.work / 'review.json'
        review.write_bytes(b'{"synthetic":true}')
        self.mode = 'success'
        self.mutate = lambda r: review.write_bytes(b'changed')
        with mock.patch('release_review.review_source', return_value={'status': 'PASS', 'release_qualified': True}), \
                self.assertRaisesRegex(release.ShardFailure, 'shard 0.*pin changed'):
            release.run_self_test(self.root, self.report, timeout=600, runner=self.runner,
                                  review_args=['--reviews', str(review), '--expected-reviews-sha256', hashlib.sha256(review.read_bytes()).hexdigest()])
        self.assertEqual(1, len(self.calls))

    def test_only_complete_source_aggregate_qualifies(self):
        review = self.work / 'review.json'
        review.write_bytes(b'{"synthetic":true}')
        self.mode = 'success'
        with mock.patch('release_review.review_source', return_value={'status': 'PASS', 'release_qualified': True}) as check:
            report = release.run_self_test(self.root, self.report, timeout=3600, runner=self.runner,
                review_args=['--reviews', str(review), '--expected-reviews-sha256', hashlib.sha256(review.read_bytes()).hexdigest()])
        self.assertFalse(report['release_qualified'])
        self.assertFalse(report['suite_complete'])
        self.assertEqual(3, check.call_count)
        self.assertTrue(all(kwargs['timeout'] == 3600 for _, kwargs in self.calls))

    def test_real_release_children_run_components_once_and_never_qualify(self):
        import test_self_test_review as fixtures
        fixture = fixtures.SelfTestReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path = fixture.source / '.agentic/tests/test_release_shard_synthetic.py'
        path.parent.mkdir()
        path.write_text('import unittest\nclass Case(unittest.TestCase):\n    def test_ok(self): pass\n', encoding='utf-8')
        fixture.files[path.relative_to(fixture.source).as_posix()] = fixtures.digest(path)
        manifest = {'format': 'awf-manifest-1', 'template_version': VERSION, 'files': fixture.files}
        (fixture.source / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
        fixture.manifest_pin = fixtures.digest(fixture.source / 'MANIFEST.json')
        reviews = json.loads(fixture.reviews.read_bytes())
        reviews['reviewed_file_sha256'] = fixture.files
        fixture.reviews.write_text(json.dumps(reviews), encoding='utf-8')
        fixture.review_pin = fixtures.digest(fixture.reviews)
        children = []
        def child(command, **kwargs):
            arguments = command[command.index('--report'):]
            with mock.patch.object(fixtures.self_test, 'ROOT', fixture.source.resolve()), \
                    mock.patch.object(sys, 'path', sys.path[:]), \
                    mock.patch('release_hygiene.check_release', return_value={'status': 'PASS'}), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = fixtures.self_test.main(arguments)
            children.append(json.loads(Path(arguments[1]).read_bytes()))
            return subprocess.CompletedProcess(command, code)
        try:
            report = release.run_self_test(fixture.source, fixture.report, timeout=600,
                                          review_args=fixture.review_arguments(), runner=child)
        finally:
            sys.modules.pop('test_release_shard_synthetic', None)
        self.assertTrue(report['release_qualified'])
        self.assertEqual(1, report['tests']['run'])
        self.assertTrue(all(r['status'] == 'SHARD_PASS' and not r['release_qualified'] for r in children))
        self.assertEqual(set(COMPONENT_CHECKS), set(children[0]['component_phases']))
        self.assertTrue(all(not r['component_phases'] for r in children[1:]))

    def test_publisher_binds_aggregate_and_children_with_3600_per_child(self):
        import test_publish_release as fixtures
        import installed_self_test_fixture as installed_fixture
        output = self.work / 'publisher'
        output.mkdir()
        original = release.run_self_test
        self.mode = 'success'
        def orchestration(source, path, **kwargs):
            return original(source, path, runner=self.runner, **kwargs)
        def prepare(source, project, **kwargs):
            self.prepare_installed(project)
            return project / '.agentic/.venv/Scripts/python.exe'
        with mock.patch.object(release, 'run_self_test', side_effect=orchestration) as invoke, \
                mock.patch.object(installed_fixture, 'prepare_project', side_effect=prepare), \
                mock.patch.object(installed_fixture, 'assert_unborn_head'), \
                mock.patch.object(fixtures.publisher, 'run', return_value=subprocess.CompletedProcess([], 0, '{"status":"PASS"}', 'Ran 2 tests')):
            result = fixtures.publisher.default_validations(self.root, output, {})
        aggregate = json.loads((output / 'self-test.json').read_bytes())
        self.assertEqual([3600, 3600], [c.kwargs['timeout'] for c in invoke.call_args_list])
        self.assertEqual([3600] * 18, [kw['timeout'] for _, kw in self.calls])
        self.assertTrue(all('-I' in cmd for cmd, _ in self.calls[9:]))
        self.assertEqual(list(range(9)) * 2, [int(cmd[cmd.index('--shard') + 1]) for cmd, _ in self.calls])
        self.assertEqual(aggregate['shard_reports'], result['self_test_shards'])
        self.assertEqual(hashlib.sha256((output / 'self-test.json').read_bytes()).hexdigest(), result['self_test_aggregate_sha256'])
        self.assertEqual('PASS', result['coverage_reconciliation']['status'])
        self.assertEqual(hashlib.sha256((output / 'installed-self-test.json').read_bytes()).hexdigest(),
                         result['installed_self_test_aggregate_sha256'])

    def prepare_installed(self, target):
        paths = [p for p in planner.build_release_plan(self.root)['inventory'] if p.startswith('.agentic/tests/')]
        for path in paths:
            dest = target / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((self.root / path).read_bytes())
        (target / '.agentic/installed-manifest.json').write_text(
            json.dumps({'immutable_files': dict.fromkeys(paths)}), encoding='utf-8')

    def test_installed_acceptance_closes_source_coverage_with_600_per_child(self):
        source = self.invoke()
        target = self.work / 'installed'
        self.prepare_installed(target)
        installed = release.run_self_test(self.root, self.work / 'installed.json', timeout=600,
                                          installed_root=target, interpreter='installed-python', runner=self.runner)
        final = release.reconcile_installed_acceptance(source, installed)
        self.assertEqual(('PASS', True), (final['status'], final['suite_complete']))
        self.assertEqual({'scripts/tests/test_installed_self_test.py': 'installed-acceptance'},
                         final['covered_by_installed_acceptance'])
        self.assertEqual([600] * 18, [kw['timeout'] for _, kw in self.calls])
        for field, value in [('status', 'FAILED'), ('suite_complete', False),
                             ('source_manifest_sha256', '0' * 64), ('shard_reports', installed['shard_reports'][:-1])]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'sharded installed acceptance'):
                release.reconcile_installed_acceptance(source, {**installed, field: value})
        for field, value in [('errors', 1), ('failures', 1), ('successful', False)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                release.reconcile_installed_acceptance(source, {**installed, 'tests': {**installed['tests'], field: value}})


if __name__ == '__main__':
    unittest.main()
