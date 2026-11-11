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

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        plan = json.loads(Path(command[command.index('--selection-file') + 1]).read_bytes())
        shard = int(command[command.index('--shard') + 1])
        selected = plan['shards'][str(shard)]
        parts = release.expected_partitions(self.root, plan)
        report = dict(scope='source-shard', status='SHARD_PASS', suite_complete=False,
                      release_qualified=False, shard=shard, shard_count=plan['shard_count'],
                      source_manifest_sha256=plan['source_manifest_sha256'],
                      selection_sha256=hashlib.sha256(planner.encode(plan)).hexdigest(),
                      selected_modules=selected[:], discovered_modules=selected[:], completed_modules=selected[:],
                      component_phases={phase: {'count': 1, 'results': [{'status': 'PASS', 'checks': {
                          key: {'status': 'PASS', 'result': 'PASS'} for key in keys}}]}
                          for phase, keys in COMPONENT_CHECKS.items()} if shard == 0 else {},
                      checks={'integrity': 'PASS'}, current_review={'status': 'NOT_PROVIDED'},
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
        self.assertEqual(('PASS', True, False), (report['status'], report['suite_complete'], report['release_qualified']))
        self.assertEqual(9, len(self.calls))
        self.assertEqual([600] * 9, [kw['timeout'] for _, kw in self.calls])
        self.assertEqual(9, len(report['shard_reports']))
        self.assertEqual('PASS', report['coverage_reconciliation']['status'])
        plan = json.loads((self.work / 'stage.json.shards/selection.json').read_bytes())
        self.assertIn('scripts/tests/test_installed_self_test.py', plan['inventory'])
        self.assertIn('global/awf/tests/test_global.py', plan['inventory'])
        with self.assertRaises(FileExistsError):
            self.invoke()

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

    def test_real_child_timeout_retains_log(self):
        log = self.work / 'timeout.log'
        with self.assertRaises(subprocess.TimeoutExpired):
            release.run_child([sys.executable, '-B', '-c', 'import time; print("started", flush=True); time.sleep(30)'],
                              cwd=self.root, env=os.environ, timeout=0.3, log_path=log)
        self.assertIn(b'started', log.read_bytes())

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
        self.assertEqual(set(plan['inventory']), set(completed))
        self.assertEqual(len(plan['inventory']) + 1, len(completed))
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
        self.assertTrue(report['release_qualified'])
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
        output = self.work / 'publisher'
        output.mkdir()
        aggregate = {'tests': {'run': 3}, 'plan_sha256': 'a' * 64,
                     'shard_reports': [{'shard': 0, 'sha256': 'b' * 64}]}
        def orchestration(source, path, **kwargs):
            path.write_text(json.dumps(aggregate), encoding='utf-8')
            return aggregate
        with mock.patch.object(release, 'run_self_test', side_effect=orchestration) as invoke, \
                mock.patch.object(fixtures.publisher, 'run', return_value=subprocess.CompletedProcess([], 0, '{"status":"PASS"}', 'Ran 2 tests')):
            result = fixtures.publisher.default_validations(self.root, output, {})
        self.assertEqual(3600, invoke.call_args.kwargs['timeout'])
        self.assertEqual(aggregate['shard_reports'], result['self_test_shards'])
        self.assertEqual(hashlib.sha256((output / 'self-test.json').read_bytes()).hexdigest(), result['self_test_aggregate_sha256'])


if __name__ == '__main__':
    unittest.main()
