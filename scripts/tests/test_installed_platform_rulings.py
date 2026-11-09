"""Installed scripts, host metadata and portable PowerShell regressions."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_installed_self_test_shards as fixtures
import test_installed_shard_review_regressions as review


class InstalledPlatformTests(unittest.TestCase):
    def setUp(self):
        fixtures.LoaderProcessTests.setUp(self)

    def child(self, code):
        return fixtures.LoaderProcessTests.child(self, code)

    def test_installed_top_level_scripts_pass_with_file_and_spec_origins(self):
        done = self.child('''import importlib,pathlib,sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
scripts=root/'.agentic/scripts';sys.path.insert(0,str(scripts))
names=('benchmark_native','evaluate_native','prepare_routine_pilot','routine_decisions')
for name in names:
 (scripts/(name+'.py')).write_text('VALUE=1\\n')
 module=importlib.import_module(name)
 assert module.__file__==module.__spec__.origin
from agentic.self_test_shards import assert_isolation,isolation_inventory
assert assert_isolation(root)=='PASS'
inventory=isolation_inventory(root)
assert inventory['root_counts']['installed_scripts']==4,inventory['root_counts']
assert inventory['root_counts']['entry_point']==1,inventory['root_counts']
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def environment_case(self, environment, platform, refuses=False):
        done = self.child('''import os,pathlib,sys,types
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic import self_test_shards as policy
# Keep pathlib on the real host while testing each host's name matching rule.
policy.os=types.SimpleNamespace(**vars(os))
policy.os.name=''' + repr(platform) + '''
policy.os.environ=''' + repr(environment) + '''
try:
 result=policy.assert_isolation(root)
except ValueError as exc:
 assert ''' + repr(refuses) + ''',str(exc)
 assert 'Python path environment is set:' in str(exc),str(exc)
else:
 assert not ''' + repr(refuses) + ''','unsafe path environment accepted'
 assert result=='PASS'
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_harmless_host_metadata_passes_on_both_platforms(self):
        for platform in ('nt', 'posix'):
            with self.subTest(platform=platform):
                self.environment_case({'PYTHON_VERSION': '3.12', 'PYTHON_SHA256': 'fixture',
                                       'pythonLocation': 'fixture', 'Python3_ROOT_DIR': 'fixture'}, platform)

    def test_startup_and_import_path_controls_refuse(self):
        for platform in ('nt', 'posix'):
            for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE',
                         'PYTHONEXECUTABLE', 'PYTHONPLATLIBDIR'):
                with self.subTest(platform=platform, name=name):
                    self.environment_case({name: 'fixture'}, platform, refuses=True)

    def test_lowercase_pythonpath_refuses_on_windows(self):
        self.environment_case({'pythonpath': 'fixture'}, 'nt', refuses=True)

    def test_lowercase_pythonpath_is_harmless_on_posix(self):
        self.environment_case({'pythonpath': 'fixture'}, 'posix')

    def test_safe_path_values_never_disable_isolated_safe_path(self):
        for value in ('', '0', 'false', '1'):
            for platform in ('nt', 'posix'):
                with self.subTest(platform=platform, value=value):
                    self.environment_case({'PYTHONSAFEPATH': value}, platform)

    def test_cpython_safe_path_nonempty_values_enable_and_empty_preserves_default(self):
        clean = {k: v for k, v in os.environ.items() if not k.upper().startswith('PYTHON')}
        for value in ('', '0', 'false', '1'):
            with self.subTest(value=value):
                done = subprocess.run([sys.executable, '-B', '-S', '-c',
                                       'import sys;print(int(sys.flags.safe_path))'],
                                      env=dict(clean, PYTHONSAFEPATH=value), capture_output=True,
                                      text=True, timeout=30)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual(str(int(bool(value))), done.stdout.strip())


class PortablePowerShellTests(unittest.TestCase):
    def invoke(self):
        case = review.IsolationReviewTests('test_ci_scrubs_python_path_environment_before_installed_child')
        case.test_ci_scrubs_python_path_environment_before_installed_child()

    def test_missing_powershell_records_skip(self):
        with mock.patch.object(review.shutil, 'which', return_value=None), \
                mock.patch.object(review.subprocess, 'run', side_effect=FileNotFoundError('no shell')), \
                self.assertRaisesRegex(unittest.SkipTest, 'neither pwsh nor powershell'):
            self.invoke()

    def test_pwsh_is_preferred_and_powershell_is_the_fallback(self):
        for available, expected in (({'pwsh': 'pwsh-fixture', 'powershell': 'powershell-fixture'}, 'pwsh-fixture'),
                                    ({'powershell': 'powershell-fixture'}, 'powershell-fixture')):
            with self.subTest(expected=expected), \
                    mock.patch.object(review.shutil, 'which', side_effect=available.get) as which, \
                    mock.patch.object(review.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
                self.invoke()
                self.assertEqual(expected, run.call_args.args[0][0])
                self.assertEqual('pwsh', which.call_args_list[0].args[0])


class AllShardProbeTests(unittest.TestCase):
    def test_every_shard_prints_installed_scripts_counts_and_outside_lists(self):
        import probe_installed_self_test_isolation as probe
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for shard in range(9):
                output = root / f'probe-{shard}.json'
                snapshot = dict(stage='before_discovery', root_counts={'installed_scripts': 4},
                                root_boundary_checks=[], refused=[{'module': 'source_tool', 'reason': 'outside'}])
                def child(*args, **kwargs):
                    output.write_text(json.dumps({'snapshots': [snapshot]}))
                    return subprocess.CompletedProcess([], 1)
                stream = io.StringIO()
                with self.subTest(shard=shard), mock.patch.object(probe.subprocess, 'run', side_effect=child) as run, \
                        redirect_stdout(stream):
                    self.assertEqual(1, probe.main(['--project', str(root / 'project'), '--output', str(output),
                        '--selection-file', str(root / 'selection.json'), '--expected-selection-sha256', 'a' * 64,
                        '--shard', str(shard)]))
                    command = run.call_args.args[0]
                    self.assertEqual(str(shard), command[command.index('--shard') + 1])
                    for text in ('ORIGIN_ROOT_COUNTS', 'installed_scripts', 'ORIGIN_OUTSIDE_ROOTS', 'source_tool'):
                        self.assertIn(text, stream.getvalue())


if __name__ == '__main__':
    unittest.main()
