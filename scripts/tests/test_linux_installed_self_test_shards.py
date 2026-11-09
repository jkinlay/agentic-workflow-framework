"""Linux launcher regressions runnable without a POSIX host or dependency download."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))


class LauncherTests(unittest.TestCase):
    def test_dependency_import_orders_in_fresh_isolated_process(self):
        code = '''import ast,pathlib,sys
path=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(path.parent))
tree=ast.parse(path.read_text())
dependencies={'installed_self_test_fixture','installed_self_test_capture','agentic.child_process'}
positions=[i for i,node in enumerate(tree.body)
           if isinstance(node,ast.ImportFrom) and node.module in dependencies]
nodes=[tree.body[i] for i in positions]
nodes.sort(key=lambda node: (node.module=='agentic.child_process') != (sys.argv[2]=='agentic-first'))
for i,node in zip(positions,nodes): tree.body[i]=node
tree.body=tree.body[:max(positions)+1]
exec(compile(tree,str(path),'exec'),{'__file__':str(path)})
'''
        for order in ('helpers-first', 'agentic-first'):
            with self.subTest(order=order):
                done = subprocess.run([sys.executable, '-B', '-I', '-c', code,
                                       str(ROOT / 'scripts/run_installed_self_test_shard.py'), order],
                                      capture_output=True, text=True, timeout=30)
                self.assertEqual(0, done.returncode, done.stderr)

    def setUp(self):
        self.launcher = importlib.import_module('run_installed_self_test_shard')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        self.selection = self.artifacts / 'selection.json'
        self.selection.write_text('{}')
        self.digest = hashlib.sha256(self.selection.read_bytes()).hexdigest()
        self.args = ['--work-dir', str(self.root / 'work'), '--artifacts', str(self.artifacts),
                     '--shard', '3', '--selection-file', str(self.selection),
                     '--expected-selection-sha256', self.digest]
        self.report = dict(status='SHARD_PASS', scope='installed-shard', shard=3,
                           suite_complete=False, release_qualified=False,
                           selected_modules=['.agentic/tests/test_example.py'],
                           tests=dict(failures=0, errors=0, skipped=[]))

    def prepare(self, root, project, **kwargs):
        project.mkdir(parents=True, exist_ok=True)
        return project / '.agentic/.venv/bin/python'

    def child(self, command, **kwargs):
        (self.artifacts / 'self-test.json').write_text(json.dumps(self.report))
        return subprocess.CompletedProcess(command, 0, '', '')

    def invoke(self, **overrides):
        with mock.patch.object(self.launcher, 'prepare_project',
                               side_effect=overrides.get('prepare', self.prepare)) as prepare, \
                mock.patch.object(self.launcher, 'assert_unborn_head',
                                  side_effect=overrides.get('unborn')) as unborn, \
                mock.patch.object(self.launcher, 'run_bounded',
                                  side_effect=overrides.get('child', self.child)) as child, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = self.launcher.main(self.args)
        return result, prepare, unborn, child

    def test_fresh_installed_child_argv_cwd_environment_and_selection(self):
        with mock.patch.dict(os.environ, {'PYTHONPATH': 'source', 'pythonhome': 'source',
                                         'PyThOnSaFePaTh': '0', 'OPENAI_API_KEY': 'synthetic',
                                         'AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS': '86400'}):
            status, prepare, unborn, child = self.invoke()
        self.assertEqual(0, status)
        project = self.root / 'work/project'
        command = child.call_args.args[0]
        self.assertEqual([str(project / '.agentic/.venv/bin/python'), '-B', '-I',
                          str(project / '.agentic/scripts/self_test.py'), '--checks-only',
                          '--report', str(self.artifacts / 'self-test.json'), '--shard', '3',
                          '--selection-file', str(self.selection), '--expected-selection-sha256', self.digest], command)
        self.assertNotIn(str(ROOT / '.agentic/scripts/self_test.py'), command)
        self.assertEqual(project, child.call_args.kwargs['cwd'])
        self.assertEqual(self.artifacts / 'capture', child.call_args.kwargs['evidence_parent'])
        env = child.call_args.kwargs['env']
        self.assertFalse([k for k in env if k.upper().startswith('PYTHON')])
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertEqual('bin', prepare.call_args.kwargs['bin_dir'])
        unborn.assert_called_once()
        self.assertLessEqual(child.call_args.kwargs['timeout'], 3000 - self.launcher.CLEANUP_RESERVE_SECONDS)

    def test_unborn_probe_is_immediately_before_launch(self):
        order = []
        def prepare(*args, **kwargs):
            order.append('prepare')
            return self.prepare(*args, **kwargs)
        def child(*args, **kwargs):
            order.append('child')
            return self.child(*args, **kwargs)
        status, *_ = self.invoke(prepare=prepare, unborn=lambda *a, **k: order.append('unborn'), child=child)
        self.assertEqual(0, status)
        self.assertEqual(['prepare', 'unborn', 'child'], order)

    def test_setup_failure_child_failure_and_timeout_create_failed_evidence(self):
        for stage, failure in (('prepare', OSError('setup failed')),
                               ('child', subprocess.TimeoutExpired('installed child', 1)),
                               ('child', AssertionError('subprocess timeout')),
                               ('unborn', AssertionError('fixture unexpectedly has a commit'))):
            with self.subTest(stage=stage, failure=type(failure).__name__):
                path = self.artifacts / 'self-test.json'
                path.unlink(missing_ok=True)
                def fail(*a, **k):
                    raise failure
                status, _, _, child = self.invoke(**{stage: fail})
                self.assertEqual(1, status)
                report = json.loads(path.read_bytes())
                self.assertEqual('FAILED', report['status'])
                self.assertEqual(3, report['shard'])
                self.assertFalse(report['suite_complete'])
                self.assertTrue(report['reason'])
                if stage != 'child':
                    child.assert_not_called()

    def test_nonzero_child_and_report_validation_failures_propagate(self):
        original = json.loads(json.dumps(self.report))
        for mutation in (dict(status='PASS'), dict(suite_complete=True), dict(release_qualified=True),
                         dict(shard=4), dict(tests=dict(failures=1, errors=0, skipped=[])),
                         dict(tests=dict(failures=0, errors=1, skipped=[])),
                         dict(tests=dict(failures=0, errors=0, skipped=[{'test': 'x', 'reason': ''}]))):
            with self.subTest(mutation=mutation):
                self.report = dict(original, **mutation)
                (self.artifacts / 'self-test.json').unlink(missing_ok=True)
                self.assertEqual(1, self.invoke()[0])
                self.assertEqual(self.report, json.loads((self.artifacts / 'self-test.json').read_bytes()))
        (self.artifacts / 'self-test.json').unlink()
        self.report = original
        def nonzero(*args, **kwargs):
            self.child(*args, **kwargs)
            return subprocess.CompletedProcess(args[0], 1, '', 'failure')
        self.assertEqual(1, self.invoke(child=nonzero)[0])

    def test_committed_head_skips_required_only_for_selected_modules(self):
        for module in ('test_continuous_controller', 'test_heavy_validation'):
            self.report['selected_modules'] = [f'.agentic/tests/{module}.py']
            path = self.artifacts / 'self-test.json'
            path.unlink(missing_ok=True)
            self.assertEqual(1, self.invoke()[0])
            self.report['tests']['skipped'] = [{'test': module + '.fixture', 'reason': 'requires committed HEAD'}]
            path.unlink()
            self.assertEqual(0, self.invoke()[0])
            self.report['tests']['skipped'] = []

    def test_existing_report_refused_before_setup_and_never_overwritten(self):
        path = self.artifacts / 'self-test.json'
        path.write_bytes(b'original evidence')
        status, prepare, unborn, child = self.invoke()
        self.assertEqual(1, status)
        self.assertEqual(b'original evidence', path.read_bytes())
        prepare.assert_not_called()
        child.assert_not_called()

    def test_partial_child_report_preserved_after_timeout(self):
        def child(*a, **k):
            (self.artifacts / 'self-test.json').write_bytes(b'partial real report')
            raise AssertionError('subprocess timeout')
        self.assertEqual(1, self.invoke(child=child)[0])
        self.assertEqual(b'partial real report', (self.artifacts / 'self-test.json').read_bytes())

    def test_real_isolated_child_executes_only_its_selected_installed_tests(self):
        # Exercise the actual process boundary and InstalledLoader, without pip
        # or a native Linux interpreter on the Windows writer host.
        def prepare(root, project, **kwargs):
            library = project / '.agentic/lib/agentic'
            library.mkdir(parents=True)
            (library / '__init__.py').write_text('')
            shutil.copyfile(ROOT / '.agentic/lib/agentic/self_test_shards.py', library / 'self_test_shards.py')
            tests = project / '.agentic/tests'
            tests.mkdir()
            (tests / 'test_selected.py').write_text(
                'import unittest\nclass Case(unittest.TestCase):\n def test_selected(self): pass\n')
            (tests / 'test_unselected.py').write_text("raise AssertionError('unselected module imported')\n")
            scripts = project / '.agentic/scripts'
            scripts.mkdir()
            (scripts / 'self_test.py').write_text('''import json,os,pathlib,sys,unittest
root=pathlib.Path.cwd()
assert sys.flags.isolated and sys.flags.dont_write_bytecode
assert not any(k.upper().startswith('PYTHON') for k in os.environ)
assert not any(name in sys.modules for name in ('plan_installed_self_test','installed_self_test_fixture'))
sys.path[:]=[str(root/'.agentic/lib'),str(root/'.agentic/tests')]+[p for p in sys.path if 'site-packages' not in p]
from agentic.self_test_shards import InstalledLoader
n=int(sys.argv[sys.argv.index('--shard')+1]); assert n==3
p='.agentic/tests/test_selected.py'
loader=InstalledLoader(root,dict(shards={'3':[p]},split={},zero_test_modules=[]),n)
suite=loader.discover(str(root/'.agentic/tests'),top_level_dir=str(root/'.agentic/tests'))
result=unittest.TextTestRunner().run(suite);loader.reconcile()
assert result.testsRun==1 and result.wasSuccessful()
report=dict(scope='installed-shard',status='SHARD_PASS',shard=n,suite_complete=False,
 release_qualified=False,selected_modules=loader.selected,
 tests=dict(failures=0,errors=0,skipped=[]))
pathlib.Path(sys.argv[sys.argv.index('--report')+1]).write_text(json.dumps(report))
''')
            return Path(sys.executable)
        with mock.patch.object(self.launcher, 'prepare_project', side_effect=prepare), \
                mock.patch.object(self.launcher, 'assert_unborn_head'), \
                mock.patch.dict(os.environ, {'PYTHONPATH': str(ROOT / 'scripts')}), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(0, self.launcher.main(self.args))
        report = json.loads((self.artifacts / 'self-test.json').read_bytes())
        self.assertEqual(['.agentic/tests/test_selected.py'], report['selected_modules'])
        captures = list((self.artifacts / 'capture').glob('*/capture.json'))
        self.assertEqual(1, len(captures))
        evidence = json.loads(captures[0].read_bytes())
        self.assertEqual('exit', evidence['outcome'])
        self.assertEqual(0, evidence['returncode'])

    def test_remaining_budget_caps_setup_child_and_stops_expired_launch(self):
        observed = []
        def prepare(root, project, **kwargs):
            for cap in (180, 600, 1200):
                kwargs['run'](['setup'], timeout=cap)
            return self.prepare(root, project, **kwargs)
        with mock.patch.object(self.launcher.time, 'monotonic', return_value=0), \
                mock.patch.object(self.launcher.subprocess, 'run') as run:
            self.assertEqual(0, self.invoke(prepare=prepare)[0])
            observed = [c.kwargs['timeout'] for c in run.call_args_list]
        self.assertEqual([180, 600, 1200], observed)
        (self.artifacts / 'self-test.json').unlink()
        with mock.patch.object(self.launcher.time, 'monotonic', side_effect=[0] + [2980] * 20), \
                mock.patch.object(self.launcher.subprocess, 'run') as run:
            status, _, _, child = self.invoke(prepare=prepare)
        self.assertEqual(1, status)
        run.assert_not_called()
        child.assert_not_called()
        (self.artifacts / 'self-test.json').unlink()
        with mock.patch.object(self.launcher.time, 'monotonic', side_effect=[0] + [2800] * 30), \
                mock.patch.object(self.launcher.subprocess, 'run') as run:
            status, _, _, child = self.invoke(prepare=prepare)
        self.assertEqual(0, status)
        self.assertTrue(all(c.kwargs['timeout'] <= 170 for c in run.call_args_list))
        self.assertLessEqual(child.call_args.kwargs['timeout'], 170)

    def test_setup_captured_output_retained_on_success_timeout_and_nonzero(self):
        for outcome in ('success', 'timeout', 'nonzero'):
            with self.subTest(outcome=outcome):
                (self.artifacts / 'self-test.json').unlink(missing_ok=True)
                (self.artifacts / 'installer.log').unlink(missing_ok=True)
                stdout, stderr = b'partial setup stdout', b'partial setup stderr'
                def runner(command, **kwargs):
                    if outcome == 'timeout':
                        raise subprocess.TimeoutExpired(command, 1, output=stdout, stderr=stderr)
                    if outcome == 'nonzero':
                        raise subprocess.CalledProcessError(1, command, output=stdout, stderr=stderr)
                    return subprocess.CompletedProcess(command, 0, stdout.decode(), stderr.decode())
                def prepare(root, project, **kwargs):
                    kwargs['run'](['setup'], timeout=1, capture_output=True, check=True)
                    return self.prepare(root, project, **kwargs)
                with mock.patch.object(self.launcher.subprocess, 'run', side_effect=runner):
                    status, _, _, child = self.invoke(prepare=prepare)
                self.assertEqual(0 if outcome == 'success' else 1, status)
                log = (self.artifacts / 'installer.log').read_bytes()
                self.assertIn(stdout, log)
                self.assertIn(stderr, log)
                if outcome != 'success':
                    child.assert_not_called()

    def test_setup_output_is_byte_bounded_and_marked_when_truncated(self):
        def prepare(root, project, **kwargs):
            for _ in range(3):
                kwargs['run'](['setup'], timeout=1, capture_output=True)
            return self.prepare(root, project, **kwargs)
        done = subprocess.CompletedProcess(['setup'], 0, b'x' * 200000, b'y' * 200000)
        with mock.patch.object(self.launcher.subprocess, 'run', return_value=done):
            self.assertEqual(0, self.invoke(prepare=prepare)[0])
        log = (self.artifacts / 'installer.log').read_bytes()
        self.assertLessEqual(len(log), self.launcher.SETUP_OUTPUT_LIMIT_BYTES)
        self.assertIn(b'[setup output truncated]', log)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = importlib.import_module('installed_self_test_fixture')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'project'

    def test_manifest_only_fixture_copies_locked_dependencies_and_unborn_git(self):
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        install = mock.Mock()
        interpreter = self.fixture.prepare_project(ROOT, self.project, run=run, env={},
                                                   installer=install, bin_dir='bin')
        install.assert_called_once_with(ROOT, self.project,
            hashlib.sha256((ROOT / 'MANIFEST.json').read_bytes()).hexdigest(), mode='install', discover=False)
        self.assertEqual({'adopter': 'owned'}, json.loads((self.project / 'MANIFEST.json').read_bytes()))
        self.assertFalse((self.project / 'scripts').exists())
        self.assertFalse((self.project / '.gitattributes').exists())
        self.assertEqual(self.project / '.agentic/.venv/bin/python', interpreter)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn('--copies', commands[0])
        self.assertEqual('-B', commands[0][1])
        self.assertEqual(str(self.project / '.agentic/.venv'), commands[0][-1])
        self.assertIn('--require-hashes', commands[1])
        self.assertIn('--only-binary=:all:', commands[1])
        self.assertEqual(str(self.project / '.agentic/requirements.lock'), commands[1][-1])
        self.assertEqual(['init', '-q', '-b', 'main'], commands[2][-4:])
        self.assertFalse(any('commit' in command or '--checks-only' in command for command in commands))
        self.assertEqual([600, 1200, 180], [c.kwargs['timeout'] for c in run.call_args_list])

    def test_source_files_and_root_attributes_refused(self):
        for forbidden in ('scripts', 'MANIFEST.json', '.gitattributes'):
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as raw:
                project = Path(raw) / 'project'
                def install(*a, **k):
                    path = project / forbidden
                    if forbidden == 'scripts':
                        path.mkdir()
                    else:
                        path.write_text('unexpected')
                with self.assertRaises(AssertionError):
                    self.fixture.prepare_project(ROOT, project, run=mock.Mock(), env={}, installer=install, bin_dir='bin')

    def test_base_interpreter_canonicalized_and_head_probe_fails_closed(self):
        with mock.patch.object(self.fixture.os.path, 'realpath', return_value='canonical-python'):
            run = mock.Mock()
            self.fixture.prepare_project(ROOT, self.project, run=run, env={}, installer=mock.Mock(), bin_dir='bin')
        self.assertEqual('canonical-python', run.call_args_list[0].args[0][0])
        for code, accepted in ((0, False), (1, True), (128, True)):
            run = mock.Mock(return_value=subprocess.CompletedProcess([], code))
            if accepted:
                self.fixture.assert_unborn_head(self.project, run=run, env={})
            else:
                with self.assertRaisesRegex(AssertionError, 'unexpectedly has a commit'):
                    self.fixture.assert_unborn_head(self.project, run=run, env={})


class WorkflowFallbackTests(unittest.TestCase):
    def test_always_fallback_creates_once_and_preserves_real_or_partial_reports(self):
        import yaml
        jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
        step = next(s for s in jobs['linux_installed_self_test']['steps']
                    if s.get('name') == 'Preserve self-test evidence after an earlier failure')
        code = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        self.assertEqual('always()', step['if'])
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {'AWF_ARTIFACT_DIR': raw, 'SHARD': '7'}):
            path = Path(raw) / 'self-test.json'
            exec(compile(code, '<workflow-fallback>', 'exec'), {})
            self.assertEqual('FAILED', json.loads(path.read_bytes())['status'])
            self.assertEqual(7, json.loads(path.read_bytes())['shard'])
            for content in (path.read_bytes(), b'partial real report', b'{"status":"SHARD_PASS"}'):
                path.write_bytes(content)
                exec(compile(code, '<workflow-fallback>', 'exec'), {})
                self.assertEqual(content, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
