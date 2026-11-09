"""Controller-ruling regressions for file/spec origins and the entry point."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_installed_self_test_shards as fixtures


class OriginRulingTests(unittest.TestCase):
    def setUp(self):
        fixtures.LoaderProcessTests.setUp(self)

    def child(self, code):
        return fixtures.LoaderProcessTests.child(self, code)

    def test_originless_pseudo_modules_pass_by_general_rule(self):
        done = self.child('''import pathlib,sys,types
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins,isolation_inventory
# Both observed families and an unrelated name exercise the same general rule:
# Cython runtime modules (_cython_*/cython_runtime), and typing.io / typing.re.
names=('_cython_fixture','cython_runtime','typing.io','typing.re','unrelated_pseudo_module')
for name in names:
 for origin in (None,'built-in','frozen','<generated runtime>','runtime-label'):
  module=types.ModuleType(name);module.__file__=None
  module.__spec__=types.SimpleNamespace(origin=origin)
  sys.modules[name]=module
  bad={r['module']:r for r in isolation_origins(root)}
  assert name not in bad,(name,origin,bad.get(name))
 module.__spec__=None
 assert name not in {r['module'] for r in isolation_origins(root)}
inventory=isolation_inventory(root)
assert sum(inventory['root_counts'].values())==len(inventory['loaded_modules'])
assert inventory['root_counts']['outside_roots']==len(inventory['outside_roots'])
assert inventory['root_counts']['entry_point']==1
assert inventory['root_counts']['originless']>=len(names)
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_fake_file_outside_roots_still_refuses(self):
        done = self.child('''import pathlib,sys,types
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins
module=types.ModuleType('fake_external_file');module.__file__=str(root/'missing_source.py')
module.__spec__=None;sys.modules[module.__name__]=module
assert module.__name__ in {r['module'] for r in isolation_origins(root)}
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_real_spec_path_cannot_hide_behind_an_allowed_file(self):
        done = self.child('''import pathlib,sys,types
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins
source=root/'outside_source.py';source.write_text('VALUE=1')
module=types.ModuleType('disguised_source');module.__file__=str(root/'.agentic/lib/allowed.py')
module.__spec__=types.SimpleNamespace(origin=str(source));sys.modules[module.__name__]=module
bad={r['module']:r for r in isolation_origins(root)}
assert module.__name__ in bad,'foreign spec path was accepted behind an allowed __file__'
assert 'spec origin' in bad[module.__name__]['reason'],bad[module.__name__]
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_spec_only_existing_relative_path_and_nonexistent_absolute_path_refuse(self):
        done = self.child('''import pathlib,sys,types
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins
(root/'relative_source').write_text('source fixture')
for origin in ('relative_source',str(root/'absent.py'),'missing/relative_source.py'):
 module=types.ModuleType('spec_only_source');module.__spec__=types.SimpleNamespace(origin=origin)
 sys.modules[module.__name__]=module
 bad={r['module']:r for r in isolation_origins(root)}
 assert module.__name__ in bad,(origin,bad)
 assert 'spec origin' in bad[module.__name__]['reason'],bad[module.__name__]
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_installed_scripts_are_allowed_but_main_must_be_exact_entry(self):
        done = self.child('''import pathlib,sys,types
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins
entry=root/'.agentic/scripts/self_test.py';entry.parent.mkdir(parents=True,exist_ok=True)
main=types.ModuleType('__main__');main.__file__=str(entry);main.__spec__=None
sys.modules['__main__']=main
alias=types.ModuleType('entry_alias');alias.__file__=str(entry);sys.modules['entry_alias']=alias
other=types.ModuleType('other_script');other.__file__=str(entry.parent/'workflow.py');sys.modules['other_script']=other
bad={r['module']:r for r in isolation_origins(root)}
assert '__main__' not in bad,bad.get('__main__')
assert 'entry_alias' not in bad and 'other_script' not in bad,bad
main.__file__=other.__file__
assert '__main__' in {r['module'] for r in isolation_origins(root)},'wrong script __main__ was accepted'
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_main_from_tests_directory_is_refused(self):
        entry = self.tests / 'wrong_main.py'
        entry.write_text('''import pathlib,sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import isolation_origins
assert '__main__' in {r['module'] for r in isolation_origins(root)},'wrong __main__ was accepted'
''', encoding='utf-8')
        done = subprocess.run([sys.executable, '-B', '-I', '-S', str(entry), str(self.root)],
                              cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class ProbeOutputTests(unittest.TestCase):
    def test_probe_prints_per_root_counts_and_every_outside_module(self):
        import probe_installed_self_test_isolation as probe
        value = {'snapshots': [{'stage': 'before_discovery', 'root_counts': {
            'installed_lib': 4, 'installed_tests': 2, 'installed_scripts': 4, 'installed_site_packages': 5,
            'stdlib': 30, 'entry_point': 1, 'originless': 8, 'outside_roots': 2},
            'root_boundary_checks': [{'root': '.agentic/.venv/Lib/site-packages',
                                      'resolved': '/fixture/vendor/site-packages',
                                      'boundary': '/fixture/.agentic/.venv', 'status': 'REFUSED'}],
            'refused': [{'module': 'source_tool', 'file': '/fixture/source.py', 'reason': 'outside allowed roots'},
                        {'module': 'source_spec', 'file': None, 'spec_origin': '/fixture/spec.py',
                         'reason': 'spec origin outside allowed roots'}]}]}
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as raw:
            root = probe.Path(raw)
            output = root / 'report.json'
            def child_result(*args, **kwargs):
                output.write_text(json.dumps(value), encoding='utf-8')
                return subprocess.CompletedProcess([], 1)
            with mock.patch.object(probe.subprocess, 'run', side_effect=child_result), redirect_stdout(out):
                self.assertEqual(1, probe.main(['--project', str(root / 'project'), '--output', str(output),
                    '--selection-file', str(root / 'selection.json'), '--expected-selection-sha256', 'a' * 64]))
        text = out.getvalue()
        for expected in ('installed_lib', 'installed_tests', 'installed_scripts', 'installed_site_packages', 'stdlib',
                         'entry_point', 'originless', 'outside_roots', 'source_tool', 'source_spec',
                         '/fixture/source.py', '/fixture/spec.py', 'ORIGIN_ROOT_BOUNDARIES',
                         '/fixture/vendor/site-packages', '/fixture/.agentic/.venv', 'REFUSED'):
            self.assertIn(expected, text)


if __name__ == '__main__':
    unittest.main()
