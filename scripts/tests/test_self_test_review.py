"""Source acceptance regressions with synthetic sealed content and a bounded runner.

Only unittest execution and unrelated hygiene are stubbed. Real source-manifest,
review-file and coverage pins are validated before and after the simulated run.
No model, network, recursive full suite or project adoption is performed.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION
import release_review

spec = importlib.util.spec_from_file_location('awf_self_test_review_fixture', ROOT / '.agentic/scripts/self_test.py')
self_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(self_test)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SelfTestReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf-synthetic-self-test-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / 'source'
        for directory in ['.agentic/schemas', '.agentic/templates', '.agentic/review-loop', '.github']:
            (self.source / directory).mkdir(parents=True, exist_ok=True)
        for path in (ROOT / '.agentic/schemas').glob('*.json'):
            shutil.copyfile(path, self.source / '.agentic/schemas' / path.name)
        for path in (ROOT / '.agentic/templates').iterdir():
            if path.suffix in {'.yaml', '.json'}:
                shutil.copyfile(path, self.source / '.agentic/templates' / path.name)
        for name in ['.agentic/workflow.yaml', '.agentic/PROJECT_CONFIG.yaml',
                     '.agentic/review-loop/critic-result.schema.json', '.agentic/review-loop/worker-result.schema.json',
                     '.agentic/review-loop/host-config.example.json']:
            shutil.copyfile(ROOT / name, self.source / name)
        (self.source / 'AGENTS.md').write_text('Synthetic acceptance fixture; no execution authority.\n', encoding='utf-8')
        (self.source / '.github/PULL_REQUEST_TEMPLATE.md').write_text('Synthetic fixture.\n', encoding='utf-8')
        (self.source / 'README.md').write_text('Synthetic reviewed content.\n', encoding='utf-8')
        self.files = {path.relative_to(self.source).as_posix(): digest(path)
                      for path in self.source.rglob('*') if path.is_file()}
        manifest = {'format': 'awf-manifest-1', 'template_version': VERSION, 'files': self.files}
        (self.source / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.manifest_pin = digest(self.source / 'MANIFEST.json')
        self.reviews = self.work / 'synthetic-review.json'
        self.reviews.write_text(json.dumps({'review_id': 'synthetic-bounded-self-test-fixture', 'status': 'PASS',
            'scope_kind': 'current_release', 'release': VERSION, 'reviewed_file_sha256': self.files}), encoding='utf-8')
        self.review_pin = digest(self.reviews)
        self.scope = self.work / 'synthetic-coverage.json'
        self.scope.write_text(json.dumps({'format': 'awf-review-coverage-1', 'release': VERSION,
                                         'paths': sorted(self.files)}), encoding='utf-8')
        self.scope_pin = digest(self.scope)
        self.report = self.work / 'new-report.json'

    def review_arguments(self, scope=False):
        args = ['--expected-manifest-sha256', self.manifest_pin, '--reviews', str(self.reviews),
                '--expected-reviews-sha256', self.review_pin]
        if scope:
            args += ['--review-required-paths', str(self.scope), '--expected-review-required-paths-sha256', self.scope_pin]
        return args

    def invoke(self, args, during_run=None):
        def run(_suite):
            if during_run:
                during_run()
            return SimpleNamespace(testsRun=1, failures=[], errors=[], skipped=[], wasSuccessful=lambda: True)
        runner = mock.Mock()
        runner.run.side_effect = run
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(self_test, 'ROOT', self.source), mock.patch.object(sys, 'path', sys.path[:]), \
                mock.patch('release_hygiene.check_release', return_value={'status': 'PASS', 'synthetic_fixture_stub': True}), \
                mock.patch.object(unittest.defaultTestLoader, 'discover', return_value=unittest.TestSuite()), \
                mock.patch.object(unittest, 'TextTestRunner', return_value=runner), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = self_test.main(args)
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue(), runner.run.call_count

    def test_stable_real_review_pins_qualify_after_bounded_runner(self):
        with mock.patch.object(release_review, 'review_source', wraps=release_review.review_source) as review:
            code, output, error, calls = self.invoke(self.review_arguments() + ['--report', str(self.report)])
        self.assertEqual((0, 1, 2), (code, calls, review.call_count), output + error)
        value = json.loads(self.report.read_bytes())
        self.assertEqual('PASS', value['status'])
        self.assertTrue(value['release_qualified'])
        self.assertEqual('all_manifest_content', value['current_review']['coverage_basis'])
        self.assertFalse(value['execution_authority'])

    def test_checks_only_can_pass_components_without_release_qualification(self):
        with mock.patch.object(release_review, 'review_source', wraps=release_review.review_source) as review:
            code, output, error, calls = self.invoke(['--checks-only', '--report', str(self.report)])
        self.assertEqual((0, 1), (code, calls), output + error)
        review.assert_not_called()
        value = json.loads(self.report.read_bytes())
        self.assertEqual('PASS', value['status'])
        self.assertIs(value['release_qualified'], False)
        self.assertEqual({'status': 'NOT_PROVIDED'}, value['current_review'])

    def test_source_default_runs_components_without_release_qualification(self):
        code, output, error, calls = self.invoke(['--report', str(self.report)])
        self.assertEqual((0, 1), (code, calls), output + error)
        value = json.loads(self.report.read_bytes())
        self.assertEqual('PASS', value['status'])
        self.assertIs(value['release_qualified'], False)
        self.assertEqual('NOT_PROVIDED', value['review'])
        self.assertEqual({'status': 'NOT_PROVIDED'}, value['current_review'])

    def test_explicit_release_refuses_without_review_inputs(self):
        code, output, error, calls = self.invoke(['--release', '--report', str(self.report)])
        self.assertEqual((1, 0), (code, calls), output + error)
        value = json.loads(self.report.read_bytes())
        self.assertEqual('FAILED', value['status'])
        self.assertIs(value['release_qualified'], False)

    def test_partial_review_arguments_never_silently_downgrade_to_checks(self):
        for args in (['--reviews', str(self.reviews)],
                     ['--expected-manifest-sha256', self.manifest_pin],
                     ['--review-required-paths', str(self.scope)]):
            with self.subTest(args=args):
                code, output, error, calls = self.invoke(args)
                self.assertEqual((1, 0), (code, calls), output + error)

    def test_explicit_release_with_pins_qualifies(self):
        code, output, error, calls = self.invoke(['--release', *self.review_arguments()])
        self.assertEqual((0, 1), (code, calls), output + error)
        self.assertIs(json.loads(output)['release_qualified'], True)

    def test_checks_only_cannot_override_release_or_supplied_review(self):
        for args, expected in ((['--checks-only', '--release'], 2),
                               (['--checks-only', *self.review_arguments()], 1)):
            with self.subTest(args=args):
                code, output, error, calls = self.invoke(args)
                self.assertEqual((expected, 0), (code, calls), output + error)

    def test_existing_report_is_preserved_before_any_runner(self):
        original = b'{"status":"PASS","historical_fixture":true}\n'
        self.report.write_bytes(original)
        code, output, error, calls = self.invoke(self.review_arguments() + ['--report', str(self.report)])
        self.assertEqual((2, 0), (code, calls), output + error)
        self.assertIn('new report path', error)
        self.assertEqual(original, self.report.read_bytes())

    def test_report_cannot_overwrite_the_review_or_coverage_input(self):
        for path in (self.reviews, self.scope):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                code, output, error, calls = self.invoke(self.review_arguments(scope=True) + ['--report', str(path)])
                self.assertEqual((2, 0), (code, calls), output + error)
                self.assertEqual(original, path.read_bytes())
                self.assertIn('preserve existing reports and review inputs', error)

    def test_new_report_inside_source_is_rejected_without_writes(self):
        path = self.source / 'new-report.json'
        code, output, error, calls = self.invoke(self.review_arguments() + ['--report', str(path)])
        self.assertEqual((2, 0), (code, calls), output + error)
        self.assertFalse(path.exists())
        self.assertIn('outside', error)

    def test_direct_tmp_tests_report_is_created_after_integrity_preflight(self):
        path = self.source / '.tmp-tests/self-test.json'
        code, output, error, calls = self.invoke(['--report', str(path)])
        self.assertEqual((0, 1), (code, calls), output + error)
        self.assertEqual('PASS', json.loads(path.read_bytes())['status'])

    def test_linked_tmp_tests_report_is_rejected_before_runner(self):
        outside = self.work / 'outside-scratch'
        outside.mkdir()
        scratch = self.source / '.tmp-tests'
        try:
            scratch.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('PLATFORM_PRIVILEGE: Windows symlink creation privilege unavailable (WinError 1314)')
            raise
        code, output, error, calls = self.invoke(['--report', str(scratch / 'self-test.json')])
        self.assertEqual((2, 0), (code, calls), output + error)
        self.assertIn('reparse-point', error)
        self.assertFalse((outside / 'self-test.json').exists())

    def assert_final_mutation_rejected(self, mutate, *, scope=False):
        with mock.patch.object(release_review, 'review_source', wraps=release_review.review_source) as review:
            code, output, error, calls = self.invoke(self.review_arguments(scope) + ['--report', str(self.report)], mutate)
        self.assertEqual((1, 1, 2), (code, calls, review.call_count), output + error)
        value = json.loads(self.report.read_bytes())
        self.assertEqual('FAILED', value['status'])
        self.assertIs(value['release_qualified'], False)
        self.assertTrue(value['tests']['successful'])
        self.assertIn('error', value)
        return value

    def test_review_bytes_changing_during_runner_prevent_final_pass(self):
        original = self.reviews.read_bytes()
        value = self.assert_final_mutation_rejected(lambda: self.reviews.write_bytes(original + b'\n'))
        self.assertIn('digest mismatch', value['error'])
        self.assertEqual(original + b'\n', self.reviews.read_bytes())

    def test_reviewed_source_bytes_changing_during_runner_prevent_final_pass(self):
        path = self.source / 'README.md'
        changed = b'Synthetic source changed after initial review.\n'
        self.assert_final_mutation_rejected(lambda: path.write_bytes(changed))
        self.assertEqual(changed, path.read_bytes())

    def test_new_source_membership_during_runner_prevents_final_pass(self):
        self.assert_final_mutation_rejected(lambda: (self.source / 'unreviewed.txt').write_bytes(b'synthetic'))

    def test_pinned_coverage_changing_during_runner_prevents_final_pass(self):
        original = self.scope.read_bytes()
        value = self.assert_final_mutation_rejected(lambda: self.scope.write_bytes(original + b'\n'), scope=True)
        self.assertIn('digest mismatch', value['error'])

    def test_report_created_after_preflight_is_not_overwritten(self):
        original = b'Synthetic concurrent report writer.\n'
        code, output, error, calls = self.invoke(self.review_arguments() + ['--report', str(self.report)],
                                               lambda: self.report.write_bytes(original))
        self.assertEqual((1, 1), (code, calls), output + error)
        self.assertEqual(original, self.report.read_bytes())
        value = json.loads(output)
        self.assertEqual('FAILED', value['status'])
        self.assertIs(value['release_qualified'], False)
        self.assertIs(value['report_written'], False)
        self.assertIn('FileExistsError', value['report_write_error'])


if __name__ == '__main__':
    unittest.main()
