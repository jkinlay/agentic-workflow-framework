"""Exercise archive report assembly without running acceptance subprocesses."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/validate_archive.py'


class ArchiveReportTests(unittest.TestCase):
    def assemble_report(self, source_tests, installed_tests):
        # Execute the real report stage with synthetic outputs from earlier stages.
        # This keeps installation, network and recursive self-tests out of the unit test.
        tree = ast.parse(SCRIPT.read_text(encoding='utf-8'), filename=str(SCRIPT))
        validate = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'validate')
        starts = [index for index, node in enumerate(validate.body)
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'extracted_tests'
                          for target in node.targets)]
        self.assertEqual(1, len(starts), 'Expected one direct report-assembly start')
        start = starts[0]
        end = next(index for index, node in enumerate(validate.body)
                   if isinstance(node, ast.Expr)
                   and ast.unparse(node).startswith('OUTPUT.parent.mkdir('))
        stage = ast.Module(body=validate.body[start:end], type_ignores=[])
        with tempfile.TemporaryDirectory(prefix='awf-archive-report-') as temporary:
            run = Path(temporary)
            archive = run / 'release.zip'
            archive.write_bytes(b'synthetic archive')
            (run / 'git-checkout.json').write_text('{"status":"PASS"}', encoding='utf-8')
            for name, tests in [('extracted', source_tests), ('installed', installed_tests)]:
                (run / f'{name}-self-test.json').write_text(json.dumps({
                    'status': 'PASS', 'tests': tests, 'test_log': 'transient log',
                }), encoding='utf-8')
            namespace = dict(
                json=json, hashlib=hashlib, RUN=run, ARCHIVE=archive,
                VERSION='1.9.4', MANIFEST='a' * 64, current_review={'status': 'PASS'},
                selected_self_test_timeout=600, checks=[], git_checkout={'status': 'PASS'},
                default_checks={'tests': {'skipped': []}, 'test_log': 'transient log'},
                first={}, adopted={}, second={}, unconfigured_status={},
                configured_status={}, ci_enablement_refusal={}, missing_rules={'decision_codes': []},
            )
            exec(compile(stage, str(SCRIPT), 'exec'), namespace)
            return namespace['report']

    def test_external_operator_report_uses_source_and_installed_test_summaries(self):
        source = dict(run=7, failures=0, errors=0, skipped=[], successful=True)
        installed = dict(run=5, failures=0, errors=0,
                         skipped=[{'test': 'portable.fixture', 'reason': 'unavailable feature'}],
                         successful=True)
        report = self.assemble_report(source, installed)
        self.assertEqual({'source': source, 'installed': installed}, report['external_operator_unit'])
        self.assertEqual(source, report['extracted_tests']['tests'])
        self.assertEqual(installed, report['installed_tests']['tests'])
        for name in ('extracted_tests', 'installed_tests', 'default_development_checks'):
            self.assertNotIn('test_log', report[name])

    def test_external_operator_report_sanitizes_symlink_skip_reasons(self):
        def summary(label):
            return dict(run=3, failures=0, errors=0, successful=True, skipped=[
                {'test': label, 'reason': 'WinError 1314: fixture-specific details'},
                {'test': 'other.fixture', 'reason': 'feature unavailable'},
            ])
        report = self.assemble_report(summary('source.fixture'), summary('installed.fixture'))
        for name, label in [('source', 'source.fixture'), ('installed', 'installed.fixture')]:
            fragment = report['external_operator_unit'][name]
            self.assertEqual(3, fragment['run'])
            self.assertEqual([
                {'test': label, 'reason': 'Windows symlink creation privilege unavailable (WinError 1314).'},
                {'test': 'other.fixture', 'reason': 'feature unavailable'},
            ], fragment['skipped'])


if __name__ == '__main__':
    unittest.main()
