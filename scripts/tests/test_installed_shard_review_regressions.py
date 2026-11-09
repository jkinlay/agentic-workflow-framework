"""Pre-review counterexamples for phase evidence, origins and rerun selection."""
from __future__ import annotations

import ast
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_installed_self_test_shards as fixtures

ROOT = fixtures.ROOT
checker = fixtures.checker
installed = fixtures.installed
PHASE_CHECKS = {
    'discovery': ('integrity', 'schema_catalog_and_workflow', 'host_loop_output_schemas',
                  'default_automatic_policy_unqualified_until_enrolled',
                  'repository_ruleset_template', 'complete_unfilled_forms'),
    'syntax': ('python_syntax_files', 'launch_surfaces'),
    'documentation': ('local_markdown_links', 'windows_command_document_lint'),
    'release_hygiene': ('source_release_hygiene',),
}


def entry_module():
    spec = importlib.util.spec_from_file_location('review_entry', ROOT / '.agentic/scripts/self_test.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    return entry


class PhaseEvidenceTests(unittest.TestCase):
    def progress(self, duplicate=False):
        progress = entry_module().SelfTestProgress(stream=io.StringIO())
        progress.component_checks = {k: 'observed successful check' for keys in PHASE_CHECKS.values() for k in keys}
        progress.component_check_names = PHASE_CHECKS
        for phase in PHASE_CHECKS:
            for _ in range(2 if duplicate and phase == 'syntax' else 1):
                progress.begin_phase(phase)
                progress.complete_phase(phase)
        return progress

    def test_progress_retains_count_and_individual_results(self):
        progress = self.progress(duplicate=True)
        evidence = getattr(progress, 'component_phases', {})
        self.assertEqual(2, evidence.get('syntax', {}).get('count'), evidence)
        self.assertEqual(2, len(evidence['syntax']['results']))
        for run in evidence['syntax']['results']:
            self.assertEqual('PASS', run['status'])
            self.assertEqual(set(PHASE_CHECKS['syntax']), set(run['checks']))
            self.assertTrue(all(v['status'] == 'PASS' for v in run['checks'].values()))

    def test_syntax_executed_twice_cannot_aggregate_as_once(self):
        plan = fixtures.sample_plan()
        records, parts = fixtures.sample_records(plan)
        progress = self.progress(duplicate=True)
        # The fallback is precisely the pre-review report projection.
        records[0][1]['component_phases'] = getattr(progress, 'component_phases',
                                                   dict.fromkeys(progress.timings, 'PASS'))
        with self.assertRaisesRegex(ValueError, 'component phases'):
            checker.reconcile(plan, records, parts)

    def test_each_component_check_must_pass_and_phase_evidence_cannot_be_empty(self):
        plan = fixtures.sample_plan()
        for fault in ('failed check', 'missing check', 'boolean count', 'empty results', 'other shard'):
            records, parts = fixtures.sample_records(plan)
            syntax = records[0][1]['component_phases']['syntax']
            if fault == 'failed check':
                syntax['results'][0]['checks']['python_syntax_files']['status'] = 'FAILED'
            elif fault == 'missing check':
                syntax['results'][0]['checks'].clear()
            elif fault == 'boolean count':
                syntax['count'] = True
            elif fault == 'empty results':
                syntax['results'].clear()
            else:
                records[2][1]['component_phases']['syntax'] = deepcopy(syntax)
            with self.subTest(fault=fault), self.assertRaisesRegex(ValueError, 'component phases'):
                checker.reconcile(plan, records, parts)


class PartitionEvidenceTests(unittest.TestCase):
    def test_missing_half_changes_only_report_and_hits_report_membership_guard(self):
        plan = fixtures.sample_plan()
        records, parts = fixtures.sample_records(plan)
        expected = deepcopy(parts)
        records[1][1]['split_cases'][installed.UPGRADE_MATRIX].clear()
        self.assertEqual(expected, parts, 'report partitions alias the independently expected partitions')
        with self.assertRaisesRegex(ValueError, 'split overlap/omission or case membership mismatch'):
            checker.reconcile(plan, records, parts)

    def test_aggregate_negative_cases_kill_targeted_guard_mutants(self):
        source = inspect.getsource(checker.reconcile)
        for fault, message in (('missing', 'missing shard'), ('split', 'split overlap/omission'),
                               ('isolation', 'failed shard, isolation or digest binding')):
            plan = fixtures.sample_plan()
            records, parts = fixtures.sample_records(plan)
            self.assertEqual('PASS', checker.reconcile(plan, records, parts)['status'])
            if fault == 'missing':
                records.pop()
            elif fault == 'split':
                records[8][1]['split_cases'][installed.UPGRADE_MATRIX] = deepcopy(parts[1])
            else:
                records[3][1]['isolation'] = 'FAILED'
            with self.subTest(fault=fault), self.assertRaisesRegex(ValueError, message):
                checker.reconcile(plan, records, parts)
            tree = ast.parse(source)
            class RemoveGuard(ast.NodeTransformer):
                def visit_If(self, node):
                    self.generic_visit(node)
                    text = ast.unparse(node.test)
                    if ((fault == 'missing' and text.startswith('set(indexed) !='))
                            or (fault == 'split' and "r.get('split_cases') !=" in text)):
                        node.test = ast.Constant(False)
                    return node

                def visit_Compare(self, node):
                    self.generic_visit(node)
                    if fault == 'isolation' and "r.get('isolation')" in ast.unparse(node):
                        return ast.copy_location(ast.Constant(False), node)
                    return node
            mutated = ast.fix_missing_locations(RemoveGuard().visit(tree))
            namespace = dict(checker.__dict__)
            exec(compile(mutated, '<aggregate-guard-mutant>', 'exec'), namespace)
            mutant = namespace['reconcile']
            class RejectionOracle(unittest.TestCase):
                def runTest(inner):
                    with inner.assertRaisesRegex(ValueError, message):
                        mutant(plan, records, parts)
            result = unittest.TestResult()
            RejectionOracle().run(result)
            self.assertFalse(result.wasSuccessful(), f'{fault}: guard mutant escaped the rejection oracle')


class RerunEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf257-artifact-review-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.event = self.root / 'event.json'
        self.event.write_text('{}')
        self.plan = fixtures.sample_plan()
        self.records, self.parts = fixtures.sample_records(self.plan)
        for version, report in self.records:
            self.artifact(version, report, 1)

    def artifact(self, version, report, attempt, run_id='741'):
        path = self.artifacts / f'windows-installed-runtime-self-test-{version}-shard-{report["shard"]}-{run_id}-{attempt}'
        path.mkdir()
        report = deepcopy(report)
        report.update(python=version + ' fixture interpreter', platform='Windows-fixture')
        (path / 'self-test.json').write_text(json.dumps(report))
        raw = fixtures.planner.encode(self.plan)
        (path / 'selection.json').write_bytes(raw)
        (path / 'selection.json.sha256').write_text(hashlib.sha256(raw).hexdigest())
        return path

    def invoke(self):
        out, error = io.StringIO(), io.StringIO()
        with mock.patch.object(checker, 'build_plan', return_value=self.plan), \
                mock.patch.object(checker, 'expected_split_cases', return_value=self.parts), \
                mock.patch.dict(os.environ, {'GITHUB_RUN_ID': '741'}), \
                redirect_stdout(out), redirect_stderr(error):
            status = checker.main(['--artifacts', str(self.artifacts), '--matrix-result', 'success',
                                   '--event-name', 'push', '--event-file', str(self.event)])
        return status, out.getvalue(), error.getvalue()

    def test_partial_rerun_uses_highest_attempt_and_reports_every_choice(self):
        version, report = self.records[4]
        self.artifact(version, report, 2)
        status, out, error = self.invoke()
        self.assertEqual(0, status, error)
        result = json.loads(out)
        self.assertEqual('741', result['run_id'])
        self.assertEqual({v: {str(n): (2 if v == version and n == 4 else 1) for n in range(9)}
                          for v in checker.VERSIONS}, result['chosen_attempts'])

    def test_failed_latest_attempt_cannot_fall_back_to_passing_attempt(self):
        version, report = self.records[4]
        report = deepcopy(report)
        report['status'] = 'FAILED'
        self.artifact(version, report, 2)
        status, out, error = self.invoke()
        self.assertEqual(1, status)
        self.assertIn('failed shard, isolation or digest binding', error)
        self.assertNotIn('duplicate', error)

    def test_artifacts_from_another_run_refuse(self):
        version, report = self.records[4]
        self.artifact(version, report, 2, run_id='742')
        status, out, error = self.invoke()
        self.assertEqual(1, status)
        self.assertIn('run_id', error)

    def test_older_broken_artifact_is_ignored_when_a_newer_attempt_passes(self):
        version, report = self.records[4]
        older = self.artifacts / f'windows-installed-runtime-self-test-{version}-shard-4-741-1'
        (older / 'self-test.json').unlink()
        self.artifact(version, report, 2)
        status, out, error = self.invoke()
        self.assertEqual(0, status, error)
        self.assertEqual(2, json.loads(out)['chosen_attempts'][version]['4'])

    def test_duplicate_attempt_bad_names_and_report_shard_mismatch_refuse(self):
        folder = next(self.artifacts.iterdir())
        with self.assertRaisesRegex(ValueError, 'same attempt'):
            checker.select_artifacts([folder, folder], '741')
        bad = self.artifacts / 'unexpected'
        bad.mkdir()
        with self.assertRaisesRegex(ValueError, 'unexpected artifact'):
            checker.select_artifacts([bad], '741')
        bad.rmdir()
        report = json.loads((folder / 'self-test.json').read_text())
        report['shard'] = 99
        (folder / 'self-test.json').write_text(json.dumps(report))
        status, out, error = self.invoke()
        self.assertEqual(1, status)
        self.assertIn('artifact shard differs', error)


class IsolationReviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.LoaderProcessTests.setUp(self)

    def child(self, code, *args):
        return fixtures.LoaderProcessTests.child(self, code, *args)

    def test_preloaded_source_tool_refuses_even_with_installed_agentic(self):
        shutil.copytree(ROOT / '.agentic/lib/agentic', self.root / '.agentic/lib/agentic', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
        done = self.child('''import pathlib,sys
root=pathlib.Path(sys.argv[1]);source=pathlib.Path(sys.argv[2])
sys.path.insert(0,str(root/'.agentic/lib'));import agentic
sys.path.insert(0,str(source/'scripts'));import run_windows_unittests
from agentic.self_test_shards import assert_isolation
assert pathlib.Path(agentic.__file__).resolve().is_relative_to(root/'.agentic/lib')
try: assert_isolation(root)
except ValueError as exc:
 assert 'run_windows_unittests' in str(exc),str(exc);sys.exit(0)
raise AssertionError('preloaded source tool was accepted')
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_foreign_third_party_roots_and_spec_only_source_modules_refuse(self):
        done = self.child('''import pathlib,sys,types
root=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins
outside=types.ModuleType('foreign_dependency');outside.__file__=str(root/'outside/site-packages/foreign.py')
unknown=types.ModuleType('unattributed_runtime')
source=root/'unattributed_source.py';source.write_text('source fixture')
unknown.__spec__=types.SimpleNamespace(origin=str(source))
sys.modules['foreign_dependency']=outside;sys.modules['unattributed_runtime']=unknown
bad={item['module']:item for item in isolation_origins(root)}
assert bad['foreign_dependency']['reason']=='file outside allowed roots'
assert bad['unattributed_runtime']['reason']=='spec origin outside allowed roots'
assert 'sys' not in bad and 'os' not in bad
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_pythonpath_is_refused_even_under_isolated_interpreter_flag(self):
        with mock.patch.dict(os.environ, {'PYTHONPATH': str(ROOT / '.agentic/lib')}):
            done = self.child('''import pathlib,sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import assert_isolation
try: assert_isolation(root)
except ValueError as exc:
 assert 'PYTHONPATH' in str(exc),str(exc);sys.exit(0)
raise AssertionError('PYTHONPATH was accepted')
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_source_and_installed_loaders_call_the_shared_rule_dynamically(self):
        (self.tests / 'test_awf36_automation_fixtures.py').write_text(
            'import test_awf36_controller_automation as consumer\nconsumer.READY=True\n')
        (self.tests / 'test_awf36_controller_automation.py').write_text('''import unittest
READY=False
class Case(unittest.TestCase):
 def test_ready(self): self.assertTrue(READY)
def load_tests(loader,tests,pattern):
 if not READY: raise AssertionError('prerequisite not loaded before cases')
 return tests
''')
        code = '''import pathlib,sys,unittest
root=pathlib.Path(sys.argv[1]);source=pathlib.Path(sys.argv[2]);mode=sys.argv[3]
sys.path.insert(0,str(root/'.agentic/tests'))
if mode=='source':
 sys.path.insert(0,str(source/'scripts'));import run_windows_unittests as runner
else: sys.path.insert(0,str(root/'.agentic/lib'))
from agentic import self_test_shards as shared
original=shared.load_fixture_prerequisites;calls=[]
def observed(root,path):
 calls.append(path);return original(root,path)
shared.load_fixture_prerequisites=observed
p='.agentic/tests/test_awf36_controller_automation.py'
loader=(runner.SelectedLoader(root,[p],3) if mode=='source' else
 shared.InstalledLoader(root,dict(shards={'3':[p]},split={},zero_test_modules=[]),3))
suite=loader.discover(str(root/'.agentic/tests'),top_level_dir=str(root/'.agentic/tests'))
assert calls==[p],('shared rule bypassed',calls)
r=unittest.TextTestRunner().run(suite);assert r.testsRun==1 and r.wasSuccessful()
'''
        for mode in ('source', 'installed'):
            with self.subTest(mode=mode):
                done = self.child(code, mode)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_ci_scrubs_python_path_environment_before_installed_child(self):
        import yaml
        steps = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'))['jobs']['installed-runtime-self-test']['steps']
        run = next(s['run'] for s in steps if s.get('name') == 'Run the installed self-test')
        self.assertIn("Get-ChildItem Env:", run)
        start = run.index('Get-ChildItem Env:')
        end = run.index('\n\n', start)
        scrub = run[start:end]
        self.assertLess(end, run.index('& $installedPython'))
        code = '''$names = @('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONUSERBASE','PYTHONEXECUTABLE','PYTHONPLATLIBDIR','PYTHONSAFEPATH','PYTHONCASEOK','PYTHONPYCACHEPREFIX')
foreach ($name in $names) { Set-Item -LiteralPath "Env:$name" -Value 'fixture' }
''' + scrub + '''
foreach ($name in $names) { if (Test-Path -LiteralPath "Env:$name") { throw "path environment survived: $name" } }
'''
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if powershell is None:
            self.skipTest('neither pwsh nor powershell is available for the CI environment cleanup test')
        done = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', code],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class OriginProbeTests(unittest.TestCase):
    def test_host_probe_uses_installed_interpreter_clean_environment_and_fresh_reports(self):
        import probe_installed_self_test_isolation as probe
        compile(probe.CHILD, '<installed-origin-probe>', 'exec')
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / 'project'
            project.mkdir()
            output = Path(raw) / 'origins.json'
            selection = Path(raw) / 'selection.json'
            arguments = ['--project', str(project), '--output', str(output), '--selection-file', str(selection),
                         '--expected-selection-sha256', 'a' * 64]
            inherited = {'PYTHONPATH': 'source-fixture', 'PYTHONHOME': 'source-fixture',
                         'PYTHONFUTURECONTROL': 'source-fixture', 'OPENAI_API_KEY': 'fixture',
                         'PRIVATE_PROBE_VALUE': 'fixture', 'PROBE_SAFE_VALUE': 'preserved'}
            with mock.patch.dict(os.environ, inherited), \
                    mock.patch('agentic.child_process.configured_child_env_strip_extra',
                               return_value=frozenset({'PRIVATE_PROBE_VALUE'})), \
                    mock.patch.object(probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(0, probe.main(arguments))
                command = run.call_args.args[0]
                self.assertIn(str(project.resolve() / '.agentic/.venv'), command[0])
                self.assertEqual(['-B', '-I', '-c'], command[1:4])
                self.assertFalse(any(k.upper().startswith('PYTHON') for k in run.call_args.kwargs['env']))
                self.assertNotIn('OPENAI_API_KEY', run.call_args.kwargs['env'])
                self.assertNotIn('PRIVATE_PROBE_VALUE', run.call_args.kwargs['env'])
                self.assertEqual('preserved', run.call_args.kwargs['env']['PROBE_SAFE_VALUE'])
                self.assertTrue(all(os.environ[key] == value for key, value in inherited.items()))
                output.write_text('preserved evidence')
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main(arguments)
                self.assertEqual('preserved evidence', output.read_text())


if __name__ == '__main__':
    unittest.main()
