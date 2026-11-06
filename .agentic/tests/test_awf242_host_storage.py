"""Read-only host checks also runnable with dependency-free Python 3.11."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / 'lib'
sys.path.insert(0, str(LIB))
from agentic import ValidationError
from agentic import owner_admission as admission
from agentic.budget_enrollment import ProtectedDatabase
from agentic.path_containment import is_reparse_point


class HostStorageTests(unittest.TestCase):
    def test_environment_is_set_before_first_lookup_in_fresh_processes(self):
        code = ('import sys,json\n'
            f'sys.path.insert(0,{str(LIB)!r})\n'
            'from agentic.owner_admission import _owner_state_home\nfrom agentic import ValidationError\n'
            'try: print(json.dumps(dict(location=str(_owner_state_home()))))\n'
            'except ValidationError as error:\n'
            ' assert str(error).startswith("OWNER_STORAGE_UNAVAILABLE:")\n'
            ' print(json.dumps(dict(refusal=str(error))))\n')
        def lookup(env):
            result = subprocess.run([sys.executable, '-B', '-I', '-c', code], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)
        baseline = lookup(os.environ.copy())
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'AppData/Local').mkdir(parents=True)
            env = {**os.environ, **{key:temporary for key in ('AWF_STATE_HOME','LOCALAPPDATA',
                'USERPROFILE','APPDATA','HOME','HOMEPATH','XDG_STATE_HOME','PROGRAMDATA',
                'ALLUSERSPROFILE','PUBLIC','SYSTEMDRIVE')}, 'HOMEDRIVE':'Z:'}
            self.assertEqual(baseline, lookup(env))

    @unittest.skipUnless(os.name == 'nt', 'Windows native ACL/lookup')
    def test_installed_native_lookup_without_executing_gh(self):
        native = shutil.which('gh.exe')
        if native is None:
            self.skipTest('No native gh installed for read-only ACL/lookup')
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'PATH':str(Path(native).parent)}):
            self.assertEqual(str(Path(native).resolve()), admission._github_executable(Path(temporary)))
            self.assertFalse(admission._worker_writable(Path(native).resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows writable native executable')
    def test_worker_writable_native_is_refused_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base / 'bin' / 'gh.exe'
            binary.parent.mkdir()
            shutil.copyfile(sys.executable, binary)
            with patch.dict(os.environ, {'PATH':str(binary.parent)}):
                with self.assertRaisesRegex(ValidationError, 'can modify or replace gh'):
                    admission._github_executable(base / 'project')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction detection')
    def test_junction_is_refused_without_path_is_junction(self):
        import _winapi
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / 'target'
            target.mkdir()
            junction = base / 'junction'
            _winapi.CreateJunction(str(target), str(junction))
            self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)
            # Exercise the lstat fallback even when pathlib has is_junction.
            with patch.object(Path, 'is_junction', None, create=True):
                self.assertTrue(is_reparse_point(junction))
                with self.assertRaisesRegex(ValidationError, 'link/reparse point'):
                    ProtectedDatabase(junction / 'state.sqlite').validate_path()


if __name__ == '__main__':
    unittest.main()
