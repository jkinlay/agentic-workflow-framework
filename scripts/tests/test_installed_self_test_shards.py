"""Installed selection, isolation, loader parity and independent reconciliation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout, redirect_stderr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))
import plan_installed_self_test as planner
import check_installed_self_test_shards as checker
from agentic import self_test_shards as installed


def sample_plan():
    upgrade = installed.UPGRADE_MATRIX
    ordinary = '.agentic/tests/test_example.py'
    return dict(format='awf-installed-selection-1', allocation_policy='windows-unittest-shards-9',
                source_manifest_sha256='a' * 64, inventory=sorted([upgrade, ordinary]),
                shards={str(n): ([ordinary] if n == 0 else [upgrade] if n in (1, 8) else []) for n in range(9)},
                split={upgrade: dict(shards=[1, 8], first_half=['test_first'])}, zero_test_modules=[])


def sample_records(plan):
    parts = {1: ['test_upgrade_matrix.Case.test_first'], 8: ['test_upgrade_matrix.Case.test_second']}
    records = []
    for version in checker.VERSIONS:
        for n in range(9):
            selected = plan['shards'][str(n)]
            records.append((version, dict(
                scope='installed-shard', suite_complete=False, shard=n, shard_count=9,
                status='SHARD_PASS', isolation='PASS', release_qualified=False,
                selection_sha256=hashlib.sha256(planner.encode(plan)).hexdigest(),
                source_manifest_sha256=plan['source_manifest_sha256'],
                selected_modules=selected[:], discovered_modules=selected[:], completed_modules=selected[:],
                component_phases=dict.fromkeys(('discovery', 'syntax', 'documentation', 'release_hygiene'), 'PASS') if n == 0 else {},
                checks={'integrity': 'PASS'},
                tests=dict(run=1, failures=0, errors=0, successful=True, skipped=[]),
                split_cases={installed.UPGRADE_MATRIX: parts[n]} if n in parts else {},
                split_all_cases={installed.UPGRADE_MATRIX: sorted(parts[1] + parts[8])} if n in parts else {},
                zero_test_modules={})))
    return records, parts


class SelectionTests(unittest.TestCase):
    def validate(self, plan, **kwargs):
        raw = kwargs.get('raw', planner.encode(plan))
        return installed.validate_selection(raw, kwargs.get('digest', hashlib.sha256(raw).hexdigest()),
                                            sample_plan()['inventory'], 'a' * 64)

    def test_deterministic_data_and_strict_membership(self):
        plan = sample_plan()
        self.assertEqual(plan, self.validate(plan))
        for bad in ('../test_bad.py', '/test_bad.py', 'C:/test_bad.py', '.agentic/tests/../test_bad.py',
                    '.agentic/tests//test_bad.py', 'scripts/tests/test_example.py',
                    '.agentic/tests/test_unknown.py', '__import__("os")'):
            value = deepcopy(plan)
            value['shards']['0'] = [bad]
            with self.subTest(path=bad), self.assertRaises(ValueError):
                self.validate(value)
        value = deepcopy(plan)
        value['shards']['0'] *= 2
        with self.assertRaises(ValueError):
            self.validate(value)
        for raw in (b'{', b'{"format":1,"format":2}', b'__import__("os")', b'[]', b'null'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.validate(plan, raw=raw)
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.validate(plan, digest='0' * 64)

    def test_split_owners_and_omissions(self):
        for mutate in (
                lambda p: p['shards']['8'].clear(),
                lambda p: p['shards']['2'].append(installed.UPGRADE_MATRIX),
                lambda p: p['split'][installed.UPGRADE_MATRIX].update(shards=[1, 1]),
                lambda p: p['split'][installed.UPGRADE_MATRIX].update(first_half=['test_first', 'test_first']),
                lambda p: p.update(executable='print(1)'),
                lambda p: p.update(source_manifest_sha256='b' * 64)):
            p = sample_plan()
            mutate(p)
            with self.assertRaises(ValueError):
                self.validate(p)

    def test_candidate_plan_covers_manifest_once_and_preserves_allocation(self):
        plan = planner.build_plan(ROOT)
        source = planner.source
        allocation = source.allocate(source.inventory(ROOT))['.agentic/tests']
        self.assertEqual({str(n): allocation[n] for n in range(9)}, plan['shards'])
        self.assertEqual(plan, installed.validate_selection(planner.encode(plan),
            hashlib.sha256(planner.encode(plan)).hexdigest(), plan['inventory'], plan['source_manifest_sha256']))
        self.assertIn('.agentic/tests/test_awf36_controller_automation.py', plan['shards']['3'])
        self.assertIn('.agentic/tests/test_awf36_automation_fixtures.py', plan['shards']['0'])

    def test_planner_never_overwrites_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'selection.json'
            self.assertEqual(0, planner.main(['--output', str(path)]))
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                planner.main(['--output', str(path)])
            self.assertEqual(before, path.read_bytes())


class AggregateTests(unittest.TestCase):
    def test_complete_matrix_passes(self):
        plan = sample_plan()
        records, parts = sample_records(plan)
        self.assertEqual('PASS', checker.reconcile(plan, records, parts)['status'])

    def test_missing_shard_overlapping_split_and_failed_isolation_refuse(self):
        plan = sample_plan()
        for fault in ('missing shard', 'overlapping split', 'failed isolation report'):
            records, parts = sample_records(plan)
            if fault == 'missing shard':
                records.pop()
            elif fault == 'overlapping split':
                records[8][1]['split_cases'][installed.UPGRADE_MATRIX] = parts[1]
            else:
                records[3][1]['isolation'] = 'FAILED'
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                checker.reconcile(plan, records, parts)

    def test_report_cannot_define_union_or_hide_errors_skips_and_components(self):
        plan = sample_plan()
        for mutate in (
                lambda rs: rs[1][1]['split_cases'][installed.UPGRADE_MATRIX].clear(),
                lambda rs: rs[0][1].update(selected_modules=[]),
                lambda rs: rs[0][1].update(component_phases={}),
                lambda rs: rs[2][1].update(component_phases={'syntax': 'PASS'}),
                lambda rs: rs[0][1]['tests'].update(errors=1),
                lambda rs: rs[0][1]['tests'].update(skipped=[{'test': 'x', 'reason': ''}]),
                lambda rs: rs[0][1].update(status='NOT_RUN'),
                lambda rs: rs[0][1].update(selection_sha256='0' * 64),
                lambda rs: rs[0][1].update(source_manifest_sha256='0' * 64),
                lambda rs: rs[0][1].update(zero_test_modules={'unknown': 0})):
            records, parts = sample_records(plan)
            mutate(records)
            with self.assertRaises(ValueError):
                checker.reconcile(plan, records, parts)
        records, parts = sample_records(plan)
        records[0][1]['tests']['skipped'] = [{'test': 'x', 'reason': 'declared platform restriction'}]
        checker.reconcile(plan, records, parts)

    def test_explicit_draft_applicability_and_unexpected_skip(self):
        self.assertFalse(checker.windows_applicable(dict(event_name='pull_request', pull_request=dict(draft=True))))
        self.assertTrue(checker.windows_applicable(dict(event_name='push')))
        self.assertTrue(checker.windows_applicable(dict(event_name='pull_request', pull_request=dict(
            draft=True, base=dict(ref='main'), head=dict(ref='release/1.9.6')))))
        with self.assertRaises(ValueError):
            checker.windows_applicable(dict(event_name='pull_request'))
        with tempfile.TemporaryDirectory() as raw:
            event = Path(raw) / 'event.json'
            event.write_text('{"pull_request":{"draft":false}}')
            self.assertEqual(1, checker.main(['--event-name', 'pull_request', '--event-file', str(event),
                '--matrix-result', 'skipped', '--artifacts', raw]))


class EntryTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('sharded_self_test_entry', ROOT / '.agentic/scripts/self_test.py')
        self.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.entry)

    def test_source_and_release_boundaries_refuse_before_discovery(self):
        arguments = ['--shard', '3', '--selection-file', 'selection.json', '--expected-selection-sha256', 'a' * 64]
        for source, extra in ((True, []), (False, ['--release']),
                              (False, ['--reviews', 'review.json']),
                              (False, ['--expected-manifest-sha256', 'b' * 64])):
            with mock.patch.object(self.entry, 'SOURCE_REPOSITORY', source), \
                    mock.patch('unittest.TestLoader.discover') as discovery, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.entry.main(arguments + extra)
                self.assertEqual(2, raised.exception.code)
                discovery.assert_not_called()

    def test_contaminated_entry_reports_failure_before_discovery_for_all_nine_shards(self):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw).resolve()
            project = work / 'project'
            project.mkdir()
            with mock.patch.object(self.entry, 'ROOT', project), \
                    mock.patch.object(self.entry, 'SOURCE_REPOSITORY', False), \
                    mock.patch('unittest.TestLoader.discover') as discovery, \
                    mock.patch('agentic.installer.verify_installed') as integrity, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                for n in range(9):
                    report = work / f'shard-{n}.json'
                    self.assertEqual(1, self.entry.main(['--shard', str(n), '--selection-file', 'unused.json',
                        '--expected-selection-sha256', 'a' * 64, '--report', str(report)]))
                    value = json.loads(report.read_bytes())
                    self.assertEqual('FAILED', value['status'])
                    self.assertEqual('FAILED', value['isolation'])
                    self.assertEqual('installed-shard', value['scope'])
                    self.assertFalse(value['suite_complete'])
                    self.assertEqual([], value['discovered_modules'])
                    self.assertIn('source contamination', value['error'])
                discovery.assert_not_called()
                integrity.assert_not_called()

    def test_shard_three_has_test_scope_only_and_preserves_report(self):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw).resolve()
            selection = work / 'selection.json'
            selection.write_bytes(b'{}')
            report = work / 'report.json'
            loader = mock.Mock()
            loader.selected = loader.discovered = loader.completed = ['.agentic/tests/test_shard_case.py']
            loader.split_cases = loader.split_all_cases = loader.zero_test_modules = {}
            loader.discover.return_value = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
            args = ['--shard', '3', '--selection-file', str(selection),
                    '--expected-selection-sha256', 'a' * 64, '--report', str(report)]
            with mock.patch.object(self.entry, 'SOURCE_REPOSITORY', False), \
                    mock.patch.object(installed, 'assert_isolation', return_value='PASS') as isolation, \
                    mock.patch.object(installed, 'installed_inventory', return_value=loader.selected), \
                    mock.patch.object(installed, 'validate_selection', return_value={}), \
                    mock.patch.object(installed, 'InstalledLoader', return_value=loader), \
                    mock.patch('agentic.installer.verify_installed', return_value='b' * 64) as integrity, \
                    mock.patch('unittest.TestLoader.discover') as complete_discovery, \
                    mock.patch.object(self.entry, 'documentation_command_problems') as docs, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(0, self.entry.main(args))
                self.assertEqual(3, isolation.call_count)
                self.assertEqual(2, integrity.call_count)
                complete_discovery.assert_not_called()
                docs.assert_not_called()
                value = json.loads(report.read_bytes())
                self.assertEqual('SHARD_PASS', value['status'])
                self.assertFalse(value['suite_complete'])
                self.assertEqual('installed-shard', value['scope'])
                self.assertEqual({}, value['component_phases'])
                self.assertEqual(['installed_selected_tests'], value['tested_components'])
                self.assertEqual({'test_suite'}, set(value['phase_timings_seconds']))
                before = report.read_bytes()
                with self.assertRaises(SystemExit):
                    self.entry.main(args)
                self.assertEqual(before, report.read_bytes())


class LoaderProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='awf257-loader-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        library = self.root / '.agentic/lib/agentic'
        library.mkdir(parents=True)
        (library / '__init__.py').write_text('')
        shutil.copyfile(ROOT / '.agentic/lib/agentic/self_test_shards.py', library / 'self_test_shards.py')
        self.tests = self.root / '.agentic/tests'
        self.tests.mkdir(parents=True)

    def child(self, code, *args):
        return subprocess.run([sys.executable, '-B', '-I', '-c', code, str(self.root), str(ROOT), *args],
                              cwd=self.root, capture_output=True, text=True, timeout=120)

    def test_both_loaders_use_same_prerequisite_before_consumer_cases_across_shards(self):
        (self.tests / 'test_awf36_automation_fixtures.py').write_text(
            'import test_awf36_controller_automation as consumer\nconsumer.READY=True\n')
        (self.tests / 'test_awf36_controller_automation.py').write_text('''import unittest
READY=False
class Case(unittest.TestCase):
    def test_ready(self): self.assertTrue(READY)
def load_tests(loader, tests, pattern):
    if not READY: raise AssertionError('prerequisite not loaded before consumer cases')
    return tests
''')
        code = '''import json,pathlib,sys,unittest
root=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); mode=sys.argv[3]
sys.path.insert(0,str(root/'.agentic/tests'))
p='.agentic/tests/test_awf36_controller_automation.py'
if mode=='source':
 sys.path.insert(0,str(source/'scripts'));import run_windows_unittests as runner
 loader=runner.SelectedLoader(root,[p],3)
else:
 sys.path.insert(0,str(root/'.agentic/lib'))
 from agentic.self_test_shards import InstalledLoader,assert_isolation
 assert_isolation(root)
 loader=InstalledLoader(root,dict(shards={'3':[p]},split={},zero_test_modules=[]),3)
suite=loader.discover(str(root/'.agentic/tests'),top_level_dir=str(root/'.agentic/tests'))
r=unittest.TextTestRunner().run(suite)
completed=loader.executed if mode=='source' else loader.completed
assert loader.discovered==completed==[p]
assert 'test_awf36_automation_fixtures' in sys.modules
assert r.testsRun==1 and r.wasSuccessful()
print('prerequisite before cases; helper not assigned; tests=1 failures=0 errors=0 skips=0')
'''
        for mode in ('source', 'installed'):
            with self.subTest(mode=mode):
                done = self.child(code, mode)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_source_preloaded_is_refused_before_discovery(self):
        done = self.child('''import pathlib,sys
root=pathlib.Path(sys.argv[1]);source=pathlib.Path(sys.argv[2])
sys.path.insert(0,str(source/'.agentic/lib'));import agentic
sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import assert_isolation
try: assert_isolation(root)
except ValueError as exc:
 assert 'contamination' in str(exc);print('REFUSED before discovery');sys.exit(0)
raise AssertionError('source preload was accepted')
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn('REFUSED before discovery', done.stdout)

    def test_isolated_child_excludes_inherited_source_pythonpath(self):
        with mock.patch.dict('os.environ', {'PYTHONPATH': str(ROOT / '.agentic/lib')}):
            done = self.child('''import pathlib,sys
root=pathlib.Path(sys.argv[1]);source=pathlib.Path(sys.argv[2])
assert str(source/'.agentic/lib') not in sys.path
sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import assert_isolation
assert assert_isolation(root)=='PASS'
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_receipt_inventory_rejects_missing_and_extra_disk_modules(self):
        p = '.agentic/tests/test_example.py'
        (self.tests / 'test_example.py').write_text('VALUE=1\n')
        receipt = self.root / '.agentic/installed-manifest.json'
        receipt.write_text(json.dumps({'immutable_files': {p: 'a' * 64}}))
        self.assertEqual([p], installed.installed_inventory(self.root))
        (self.tests / 'test_extra.py').write_text('VALUE=2\n')
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            installed.installed_inventory(self.root)
        (self.tests / 'test_extra.py').unlink()
        (self.tests / 'test_example.py').unlink()
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            installed.installed_inventory(self.root)

    def test_import_errors_skip_carriers_zero_helpers_and_hollow_suites(self):
        code = '''import pathlib,sys,unittest
root=pathlib.Path(sys.argv[1]);sys.path[:0]=[str(root/'.agentic/lib'),str(root/'.agentic/tests')]
from agentic.self_test_shards import InstalledLoader
p='.agentic/tests/test_example.py'
loader=InstalledLoader(root,dict(shards={'3':[p]},split={},zero_test_modules=[p] if sys.argv[3]=='zero' else []),3)
suite=loader.discover(str(root/'.agentic/tests'),top_level_dir=str(root/'.agentic/tests'))
r=unittest.TextTestRunner().run(suite)
if sys.argv[3]=='import':
 assert len(r.errors)==1
 try: loader.reconcile()
 except ValueError: pass
 else: raise AssertionError('import error accepted')
elif sys.argv[3]=='skip':
 loader.reconcile();assert len(r.skipped)==1 and r.skipped[0][1]=='declared skip reason'
else:
 loader.reconcile();assert r.testsRun==0 and loader.zero_test_modules=={p:0}
'''
        for mode, text in (('import', 'raise ImportError("broken import")\n'),
                           ('skip', 'import unittest\nraise unittest.SkipTest("declared skip reason")\n'),
                           ('zero', 'VALUE=1\n')):
            (self.tests / 'test_example.py').write_text(text)
            done = self.child(code, mode)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        (self.tests / 'test_example.py').write_text('VALUE=1\n')
        done = self.child(code, 'undeclared')
        self.assertNotEqual(0, done.returncode)
        self.assertIn('empty discovery', done.stderr)
        (self.tests / 'test_example.py').write_text('''import unittest
class Case(unittest.TestCase):
    def test_case(self): pass
class Hollow(unittest.TestSuite):
    def run(self,result,debug=False): return result
def load_tests(loader,tests,pattern): return Hollow([Case('test_case')])
''')
        done = self.child(code, 'zero')
        self.assertNotEqual(0, done.returncode)
        self.assertIn('empty execution', done.stderr)

    def test_split_cases_reject_unknown_names_and_empty_halves(self):
        (self.tests / 'test_upgrade_matrix.py').write_text('''import unittest
class Case(unittest.TestCase):
    def test_first(self): pass
    def test_second(self): pass
''')
        code = '''import pathlib,sys,unittest
root=pathlib.Path(sys.argv[1]);sys.path[:0]=[str(root/'.agentic/lib'),str(root/'.agentic/tests')]
from agentic.self_test_shards import InstalledLoader,UPGRADE_MATRIX
first=sys.argv[3].split(',')
loader=InstalledLoader(root,dict(shards={'1':[UPGRADE_MATRIX]},split={UPGRADE_MATRIX:dict(first_half=first)},zero_test_modules=[]),1)
suite=loader.discover(str(root/'.agentic/tests'),top_level_dir=str(root/'.agentic/tests'))
r=unittest.TextTestRunner().run(suite);loader.reconcile();assert r.testsRun==1
'''
        self.assertEqual(0, self.child(code, 'test_first').returncode)
        for names in ('test_absent', 'test_first,test_second'):
            done = self.child(code, names)
            self.assertNotEqual(0, done.returncode)
            self.assertIn('split overlap/omission', done.stderr)

    def test_real_awf36_cases_pass_in_isolated_installed_loader_shard_three(self):
        shutil.rmtree(self.root / '.agentic')
        shutil.copytree(ROOT / '.agentic', self.root / '.agentic',
                        ignore=shutil.ignore_patterns('.venv', '__pycache__'))
        shutil.copyfile(ROOT / '.agentic/defaults/OPERATING_CONFIG.yaml', self.root / 'OPERATING_CONFIG.yaml')
        done = self.child('''import pathlib,sys,unittest
root=pathlib.Path(sys.argv[1]);sys.path[:0]=[str(root/'.agentic/lib'),str(root/'.agentic/tests')]
from agentic.self_test_shards import InstalledLoader,assert_isolation
assert_isolation(root)
p='.agentic/tests/test_awf36_controller_automation.py'
loader=InstalledLoader(root,dict(shards={'3':[p]},split={},zero_test_modules=[]),3)
suite=loader.discover(str(root/'.agentic/tests'),top_level_dir=str(root/'.agentic/tests'))
r=unittest.TextTestRunner(verbosity=2).run(suite);loader.reconcile();assert_isolation(root)
assert r.testsRun==7 and not r.skipped and r.wasSuccessful()
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
