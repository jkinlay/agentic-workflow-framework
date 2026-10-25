"""Synthetic acceptance tests for the Windows module shard runner."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'run_windows_unittests.py'
SPEC = importlib.util.spec_from_file_location('windows_unittest_shards', SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

ROOTS = ('.agentic/tests', 'scripts/tests', 'global/awf/tests',
         'global/awf-portable/tests')
PASSING = '''import unittest
class Example(unittest.TestCase):
    def test_example(self):
        self.assertTrue(True)
'''


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='awf shard fixtures ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for suite in ROOTS:
            self.write(suite, 'test_awf.py', PASSING)

    def write(self, suite, name, source):
        path = self.root / suite / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8', newline='\n')
        return path.relative_to(self.root).as_posix()

    def allocation(self):
        return runner.allocate(runner.inventory(self.root))

    def invoke(self, shard=0, *arguments):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), '--root', str(self.root),
             '--shard', str(shard), *arguments],
            cwd=self.root, capture_output=True, text=True, check=False,
        )

    def events(self, completed, event):
        records = []
        for line in completed.stdout.splitlines():
            if line.startswith('{'):
                record = json.loads(line)
                if record.get('event') == event:
                    records.append(record)
        return records

    def test_allocation_is_ordinal_per_suite_and_order_independent(self):
        for suite in ROOTS:
            for name in ('test_z.py', 'test_B.py', 'test_a.py', 'test_c.py'):
                self.write(suite, name, PASSING)
        inventory = runner.inventory(self.root)
        reversed_inventory = {s: list(reversed(paths)) for s, paths in inventory.items()}
        allocation = runner.allocate(reversed_inventory)
        self.assertEqual(allocation, runner.allocate(inventory))
        for suite in ROOTS:
            paths = sorted(inventory[suite])
            self.assertEqual([suite + '/test_B.py', suite + '/test_c.py'], allocation[suite][0])
            self.assertEqual(paths[1::3], allocation[suite][1])
            self.assertEqual(paths[2::3], allocation[suite][2])
        runner.guard(self.root, allocation)

    def test_inventory_is_dynamic_and_uses_canonical_relative_paths(self):
        self.write(ROOTS[0], 'support.py', 'raise RuntimeError("not a test")\n')
        self.write(ROOTS[0], 'package/__init__.py', '')
        added = self.write(ROOTS[0], 'package/test_new.py', PASSING)
        inventory = runner.inventory(self.root)
        self.assertIn(added, inventory[ROOTS[0]])
        self.assertEqual(2, len(inventory[ROOTS[0]]))
        allocation = runner.allocate(inventory)
        self.assertEqual(1, sum(added in paths for paths in allocation[ROOTS[0]].values()))
        self.assertTrue(all('\\' not in path for paths in inventory.values() for path in paths))
        runner.guard(self.root, allocation)

    def test_guard_rejects_missing_module(self):
        allocation = self.allocation()
        allocation[ROOTS[0]][0].clear()
        with self.assertRaisesRegex(ValueError, 'missing'):
            runner.guard(self.root, allocation)

    def test_guard_rejects_duplicate_within_and_between_shards(self):
        for shard in (0, 1):
            with self.subTest(shard=shard):
                allocation = self.allocation()
                allocation[ROOTS[0]][shard].append(allocation[ROOTS[0]][0][0])
                with self.assertRaisesRegex(ValueError, 'duplicate'):
                    runner.guard(self.root, allocation)

    def test_guard_rejects_unknown_and_wrong_suite_paths(self):
        for unknown in (ROOTS[0] + '/test_unknown.py', ROOTS[1] + '/test_awf.py'):
            allocation = self.allocation()
            allocation[ROOTS[0]][1].append(unknown)
            with self.assertRaisesRegex(ValueError, 'unknown'):
                runner.guard(self.root, allocation)

    def test_guard_rejects_invalid_or_absent_shard_indices(self):
        for invalid in (-1, 3, '0', True):
            allocation = self.allocation()
            allocation[ROOTS[0]] = {key: value for key, value in allocation[ROOTS[0]].items() if key != invalid}
            allocation[ROOTS[0]][invalid] = []
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'shard'):
                runner.guard(self.root, allocation)
        allocation = self.allocation()
        del allocation[ROOTS[0]][2]
        with self.assertRaisesRegex(ValueError, 'shard'):
            runner.guard(self.root, allocation)

    def test_guard_rejects_missing_or_unknown_suite_allocations(self):
        allocation = self.allocation()
        del allocation[ROOTS[0]]
        with self.assertRaisesRegex(ValueError, 'suite'):
            runner.guard(self.root, allocation)
        allocation['other/tests'] = {0: [], 1: [], 2: []}
        with self.assertRaisesRegex(ValueError, 'suite'):
            runner.guard(self.root, allocation)

    def test_inventory_rejects_missing_or_empty_suite_root(self):
        missing_root = self.root / 'missing roots'
        missing_root.mkdir()
        with self.assertRaisesRegex(ValueError, 'missing suite root'):
            runner.inventory(missing_root)
        (missing_root / ROOTS[0]).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'no test modules'):
            runner.inventory(missing_root)

    def test_guard_enumerates_again_instead_of_trusting_allocator(self):
        allocation = self.allocation()
        self.write(ROOTS[0], 'test_added.py', PASSING)
        with self.assertRaisesRegex(ValueError, 'missing'):
            runner.guard(self.root, allocation)

    def test_allocator_rejects_duplicate_inventory(self):
        inventory = runner.inventory(self.root)
        inventory[ROOTS[0]].append(inventory[ROOTS[0]][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            runner.allocate(inventory)

    def test_execution_reconciliation_rejects_missing_duplicate_and_unknown(self):
        selected = [ROOTS[0] + '/test_awf.py']
        runner.reconcile(selected, selected)
        for executed in ([], selected * 2, ['unknown.py']):
            with self.subTest(executed=executed), self.assertRaisesRegex(ValueError, 'execution'):
                runner.reconcile(selected, executed)

    def test_list_checks_all_shards_without_importing_or_spawning(self):
        for suite in ROOTS:
            self.write(suite, 'test_awf.py', 'raise RuntimeError("must not import")\n')
        for shard in range(3):
            with self.subTest(shard=shard), mock.patch.object(runner.subprocess, 'run') as child:
                with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(0, runner.main(['--root', str(self.root), '--shard', str(shard), '--list']))
                child.assert_not_called()
                record = json.loads(output.getvalue())
                self.assertEqual('selected', record['event'])
                self.assertEqual(shard, record['shard'])
                self.assertEqual(self.allocation(), runner.allocate(runner.inventory(self.root)))

    def test_all_three_cli_shards_cover_each_module_once(self):
        for suite in ROOTS:
            self.write(suite, 'test_second.py', PASSING)
        selected = []
        for shard in range(3):
            completed = self.invoke(shard, '--list')
            self.assertEqual(0, completed.returncode, completed.stderr)
            record, = self.events(completed, 'selected')
            selected.extend(path for paths in record['modules'].values() for path in paths)
        expected = [p for paths in runner.inventory(self.root).values() for p in paths]
        self.assertCountEqual(expected, selected)
        self.assertEqual(len(selected), len(set(selected)))

    def test_invalid_cli_shard_fails(self):
        for shard in (-1, 3):
            completed = self.invoke(shard, '--list')
            self.assertNotEqual(0, completed.returncode)
            self.assertIn('invalid choice', completed.stderr)

    def test_separate_interpreters_preserve_colliding_imports_and_repo_cwd(self):
        (self.root / 'project_helper.py').write_text('VALUE = "repository root"\n', encoding='utf-8')
        for index, suite in enumerate(ROOTS):
            self.write(suite, 'helper.py', f'VALUE = {index}\n')
            self.write(suite, 'test_awf.py',
                       'import unittest, helper, project_helper\nfrom pathlib import Path\n'
                       'class Example(unittest.TestCase):\n'
                       '    def test_example(self):\n'
                       f'        self.assertEqual({index}, helper.VALUE)\n'
                       '        self.assertEqual("repository root", project_helper.VALUE)\n'
                       f'        self.assertEqual(Path({str(self.root)!r}), Path.cwd())\n')
        completed = self.invoke()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual('selected', json.loads(completed.stdout.splitlines()[0])['event'])
        records = self.events(completed, 'suite_result')
        self.assertEqual(list(ROOTS), [r['suite'] for r in records])
        for record in records:
            self.assertEqual([record['suite'] + '/test_awf.py'], record['executed'])
            self.assertEqual(1, record['tests_run'])

    def test_module_hooks_and_fixtures_use_standard_discovery(self):
        self.write(ROOTS[0], 'test_awf.py', '''import unittest
READY = False
def setUpModule():
    global READY
    READY = True
def tearDownModule():
    assert READY
class HookCase(unittest.TestCase):
    def check_hook(self):
        self.assertTrue(READY)
def load_tests(loader, tests, pattern):
    assert pattern == 'test*.py'
    return unittest.TestSuite([HookCase('check_hook')])
''')
        completed = self.invoke()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn('check_hook', completed.stderr)

    def test_nested_packages_are_discovered(self):
        self.write(ROOTS[0], 'package/__init__.py', '')
        self.write(ROOTS[0], 'package/test_nested.py', PASSING)
        completed = self.invoke()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn('package.test_nested', completed.stderr)

    def test_unselected_modules_are_not_imported(self):
        self.write(ROOTS[0], 'test_z.py', 'raise RuntimeError("unselected")\n')
        completed = self.invoke()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertNotIn('unselected', completed.stderr)

    def test_other_selected_modules_continue_after_failure(self):
        self.write(ROOTS[0], 'test_a.py', PASSING.replace('assertTrue(True)', 'fail("first module failed")'))
        self.write(ROOTS[0], 'test_b.py', PASSING)
        self.write(ROOTS[0], 'test_z.py', PASSING.replace('self.assertTrue(True)', 'print("later module ran")'))
        completed = self.invoke()
        self.assertNotEqual(0, completed.returncode)
        self.assertIn('first module failed', completed.stderr)
        self.assertIn('later module ran', completed.stdout)

    def test_genuine_empty_shard_does_not_fail_or_import_modules(self):
        completed = self.invoke(2)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(4, len(self.events(completed, 'suite_result')))
        self.assertTrue(all(r['executed'] == [] and r['tests_run'] == 0
                            for r in self.events(completed, 'suite_result')))

    def test_failure_import_error_hook_error_and_empty_discovery_stay_failures(self):
        cases = (
            ('assertion', PASSING.replace('assertTrue(True)', 'fail("fixture failure")'), 'fixture failure'),
            ('import', 'raise ImportError("fixture import")\n', 'fixture import'),
            ('hook', PASSING + '\ndef load_tests(*args):\n    raise RuntimeError("fixture hook")\n', 'fixture hook'),
            ('empty', 'VALUE = 1\n', 'empty discovery'),
            ('omitted', PASSING, 'execution'),
        )
        for name, source, message in cases:
            with self.subTest(case=name):
                target = 'orphan/test_awf.py' if name == 'omitted' else 'test_awf.py'
                root = ROOTS[0]
                # The orphan module sorts first and is not importable by discovery.
                self.write(root, target, source)
                completed = self.invoke()
                self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn(message, completed.stdout + completed.stderr)
                self.assertTrue(any(r['suite'] == ROOTS[-1] for r in self.events(completed, 'suite_result')))

    def test_explicit_unittest_skip_remains_visible(self):
        self.write(ROOTS[0], 'test_awf.py', 'import unittest\nraise unittest.SkipTest("explicit fixture skip")\n')
        completed = self.invoke()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn('explicit fixture skip', completed.stderr)

    def test_module_fixture_skip_remains_visible(self):
        self.write(ROOTS[0], 'test_awf.py', PASSING +
                   '\ndef setUpModule():\n    raise unittest.SkipTest("fixture setup skip")\n')
        completed = self.invoke()
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn('fixture setup skip', completed.stderr)

    def test_suite_with_cases_that_does_not_execute_them_fails(self):
        self.write(ROOTS[0], 'test_awf.py', PASSING + '''
class HollowSuite(unittest.TestSuite):
    def run(self, result, debug=False):
        return result
def load_tests(loader, tests, pattern):
    return HollowSuite([Example('test_example')])
''')
        completed = self.invoke()
        self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn('empty execution', completed.stderr)

    def test_nonzero_child_exit_is_propagated_and_later_suites_continue(self):
        self.write(ROOTS[0], 'test_awf.py', 'import os\nos._exit(7)\n')
        completed = self.invoke()
        self.assertEqual(7, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn('missing suite_result', completed.stderr)
        self.assertEqual(3, len(self.events(completed, 'suite_result')))

    def test_zero_child_exit_without_execution_evidence_fails(self):
        self.write(ROOTS[0], 'test_awf.py', 'import os\nos._exit(0)\n')
        completed = self.invoke()
        self.assertNotEqual(0, completed.returncode)
        self.assertIn('missing suite_result', completed.stderr)

    def test_parent_rejects_omitted_execution_record_and_child_launch_error(self):
        def fake_child(command, **kwargs):
            suite = command[command.index('--_suite') + 1]
            record = dict(event='suite_result', suite=suite, executed=[], discovered=[],
                          tests_run=1, successful=True)
            return subprocess.CompletedProcess(command, 0, json.dumps(record) + '\n', '')
        for effect in (fake_child, OSError('fixture launch error')):
            with self.subTest(effect=effect), mock.patch.object(runner.subprocess, 'run', side_effect=effect) as child:
                with mock.patch('sys.stdout', new_callable=io.StringIO), mock.patch('sys.stderr', new_callable=io.StringIO):
                    self.assertNotEqual(0, runner.main(['--root', str(self.root), '--shard', '0']))
                self.assertEqual(4, child.call_count)


if __name__ == '__main__':
    unittest.main()
