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
    def sandbox_acl(self, target, mask):
        """Apply a real, non-inheriting capability ACL to a temporary object."""
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
        api.GetSecurityDescriptorDacl.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL))
        api.SetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        descriptor, acl = ctypes.c_void_p(), ctypes.c_void_p()
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        # Full owner/admin/Users access must not count as sandbox authority.
        sddl = ('D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;BU)'
                f'(A;;{hex(mask)};;;S-1-15-3-1024-12345)')
        self.assertTrue(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None))
        try:
            self.assertTrue(api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                ctypes.byref(acl), ctypes.byref(defaulted)))
            self.assertEqual(0, api.SetNamedSecurityInfoW(str(target), 1, 0x80000004,
                None, None, acl, None))
        finally:
            kernel.LocalFree(descriptor)
        self.assertEqual(mask, admission._sandbox_group_rights(target))

    @unittest.skipUnless(os.name == 'nt', 'Windows ancestor replacement ACLs')
    def test_parent_delete_child_replaces_each_executable_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            native = base / 'install/vendor/bin/gh.exe'
            native.parent.mkdir(parents=True)
            shutil.copyfile(sys.executable, native)
            targets = (native, native.parent, native.parent.parent, base / 'install', base)
            inspect = admission._sandbox_group_rights
            for directory in native.parents:
                if directory == base or base not in directory.parents:
                    break
                with self.subTest(ancestor=directory.relative_to(base)):
                    for target in targets:
                        self.sandbox_acl(target, 0x1200ed if target == directory.parent else 0x1200a9)
                    # Outside the fixture is protected; every fixture ACL is real.
                    with patch.object(admission, '_sandbox_group_rights',
                            side_effect=lambda p: inspect(p) if p in targets else 0), \
                            patch.dict(os.environ, {'PATH': str(native.parent)}):
                        self.assertTrue(admission._worker_writable(native))
                        with self.assertRaisesRegex(ValidationError, 'can modify or replace gh'):
                            admission._github_executable(base / 'project')
                        with patch.object(admission, '_git_executable', return_value=str(native)), \
                                patch.object(admission, '_git_metadata_identity', return_value='fixture'):
                            with self.assertRaisesRegex(ValidationError, 'protected native git executable'):
                                admission.git_common_identity(base / 'project')

    @unittest.skipUnless(os.name == 'nt', 'Windows native installation ACLs')
    def test_normal_install_allows_owner_and_users_full_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            native = base / 'Program Files/vendor/bin/gh.exe'
            native.parent.mkdir(parents=True)
            shutil.copyfile(sys.executable, native)
            targets = (native, native.parent, native.parent.parent, base / 'Program Files', base)
            for target in targets:
                self.sandbox_acl(target, 0x1200a9)
            inspect = admission._sandbox_group_rights
            with patch.object(admission, '_sandbox_group_rights',
                    side_effect=lambda p: inspect(p) if p in targets else 0), \
                    patch.dict(os.environ, {'PATH': str(native.parent)}):
                self.assertFalse(admission._worker_writable(native))
                self.assertEqual(str(native), admission._github_executable(base / 'project'))
                with patch.object(admission, '_git_executable', return_value=str(native)), \
                        patch.object(admission, '_git_metadata_identity', return_value='fixture'):
                    self.assertEqual('fixture', admission.git_common_identity(base / 'project'))

    @unittest.skipUnless(os.name == 'nt', 'Windows ancestor ACL control')
    def test_ancestor_acl_control_needs_no_preexisting_create_rights(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            native = base / 'install/vendor/bin/gh.exe'
            native.parent.mkdir(parents=True)
            shutil.copyfile(sys.executable, native)
            targets = (native, native.parent, native.parent.parent, base / 'install', base)
            inspect = admission._sandbox_group_rights
            for directory in targets[1:]:
                for control in (0x40000, 0x80000):
                    with self.subTest(ancestor=directory.relative_to(base), control=control):
                        for target in targets:
                            self.sandbox_acl(target, 0x1200a9 | (control if target == directory else 0))
                        with patch.object(admission, '_sandbox_group_rights',
                                side_effect=lambda p: inspect(p) if p in targets else 0):
                            self.assertTrue(admission._worker_writable(native))

    @unittest.skipUnless(os.name == 'nt', 'Windows volume root ACL routes')
    def test_volume_root_sandbox_replacement_routes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            native = base / 'install/bin/gh.exe'
            native.parent.mkdir(parents=True)
            shutil.copyfile(sys.executable, native)
            inspect = admission._sandbox_group_rights
            root_acl = base / 'root-acl'
            root_acl.mkdir()
            # Inspect real fixture ACLs as the root grant; never mutate a volume ACL.
            for mask, refused in ((0x40, False), (4, False), (0x44, True),
                                  (0x40000, True), (0x80000, True)):
                with self.subTest(root_grant=mask):
                    self.sandbox_acl(root_acl, 0x1200a9 | mask)
                    seen = []
                    def rights(member):
                        seen.append(member)
                        return inspect(root_acl) if member == Path(native.anchor) else 0x1200a9
                    with patch.object(admission, '_sandbox_group_rights', side_effect=rights):
                        self.assertEqual(refused, admission._worker_writable(native))
                    self.assertIn(Path(native.anchor), seen)

    @unittest.skipUnless(os.name == 'nt', 'Windows sandbox principal rights')
    def test_owner_writable_install_without_sandbox_grants_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'gh.exe'
            shutil.copyfile(sys.executable, binary)
            with patch.object(admission, '_sandbox_group_rights', return_value=0):
                self.assertFalse(admission._worker_writable(binary))
                self.assertFalse(admission._git_directory_writable(binary.parent))

    def test_default_load_never_looks_up_git_or_gh(self):
        from agentic.budgeting import fresh_settings
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'project'
            (root / '.git').mkdir(parents=True)
            with patch.object(admission, '_owner_state_home', return_value=Path(temporary) / 'owner'), \
                    patch.object(admission, '_git_executable', side_effect=AssertionError('Git lookup during load')), \
                    patch.object(admission, '_github_executable', side_effect=AssertionError('gh lookup during load')):
                admission.validate_protected_settings(root, {'project': {'id': 'fixture'}},
                                                      {'budgeting': fresh_settings()})

    def test_confirmed_modes_load_without_installed_tools(self):
        from agentic.budgeting import fresh_settings
        from owner_admission_fixtures import record_fixture_owner_confirmation
        governance = {'project': {'id': 'fixture'}, 'github': dict(
            host='https://github.com', repository_id=101, repository='fixture/example', base_branch='main')}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'project'
            (root / '.git').mkdir(parents=True)
            for accounting, limit in ((False, 4000000), (False, 123), (True, 123)):
                with self.subTest(accounting=accounting, limit=limit):
                    settings = dict(fresh_settings(), accounting_enabled=accounting, max_tokens_per_ticket=limit)
                    value = {'budgeting': settings}
                    record_fixture_owner_confirmation(self, root, governance, value)
                    with patch.object(admission, '_git_executable', side_effect=AssertionError('Git lookup during load')), \
                            patch.object(admission, '_github_executable', side_effect=AssertionError('gh lookup during load')):
                        admission.validate_protected_settings(root, governance, value)

    def test_relative_local_script_and_sandbox_writable_git_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'project'
            (root / '.git').mkdir(parents=True)
            local = root / ('git.exe' if os.name == 'nt' else 'git')
            shutil.copyfile(sys.executable, local)
            script = Path(temporary) / ('git-script.exe' if os.name == 'nt' else 'git-script')
            script.write_bytes(b'#!/bin/sh\n')
            for executable, writable in (('git', False), (str(local), False),
                                         (str(script), False), (sys.executable, True)):
                with self.subTest(executable=executable, writable=writable), \
                        patch.object(admission, '_git_executable', return_value=executable), \
                        patch.object(admission, '_worker_writable', return_value=writable):
                    with self.assertRaisesRegex(ValidationError, 'protected native git executable'):
                        admission.git_common_identity(root)

    @unittest.skipUnless(os.name == 'nt', 'Windows linked metadata sandbox ACL')
    def test_linked_metadata_ignores_owner_rights_but_refuses_worker_steering(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            common = base / 'main/.git'
            admin = common / 'worktrees/linked'
            admin.mkdir(parents=True)
            linked = base / 'linked'
            linked.mkdir()
            marker = linked / '.git'
            marker.write_text('gitdir: ' + str(admin), encoding='utf-8')
            (admin / 'commondir').write_text('../..', encoding='utf-8')
            (admin / 'gitdir').write_text(str(marker), encoding='utf-8')
            with patch.object(admission, '_sandbox_group_rights', return_value=0):
                self.assertEqual(admission._git_metadata_identity(base / 'main'),
                                 admission._git_metadata_identity(linked))
            with patch.object(admission, '_sandbox_group_rights', side_effect=lambda p: 2 if p == common else 0):
                with self.assertRaisesRegex(ValidationError, 'OWNER_GIT_IDENTITY_UNAVAILABLE'):
                    admission._git_metadata_identity(linked)
            (admin / 'gitdir').write_text(str(base / 'wrong/.git'), encoding='utf-8')
            with patch.object(admission, '_sandbox_group_rights', return_value=0):
                with self.assertRaisesRegex(ValidationError, 'OWNER_GIT_IDENTITY_UNAVAILABLE'):
                    admission._git_metadata_identity(linked)

    @unittest.skipUnless(os.name == 'nt', 'Windows capability SID ACL')
    def test_capability_write_grant_is_refused(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
        api.GetSecurityDescriptorDacl.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL))
        api.SetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'gh.exe'
            shutil.copyfile(sys.executable, binary)
            descriptor, acl = ctypes.c_void_p(), ctypes.c_void_p()
            present, defaulted = wintypes.BOOL(), wintypes.BOOL()
            self.assertTrue(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                'D:(A;;0x2;;;S-1-15-3-1024-12345)', 1, ctypes.byref(descriptor), None))
            try:
                self.assertTrue(api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                    ctypes.byref(acl), ctypes.byref(defaulted)))
                # Retain inherited owner access; add only a synthetic capability grant.
                self.assertEqual(0, api.SetNamedSecurityInfoW(str(binary), 1, 4, None, None, acl, None))
                self.assertTrue(admission._sandbox_group_rights(binary) & 2)
                self.assertTrue(admission._worker_writable(binary))
            finally:
                kernel.LocalFree(descriptor)

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
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as installation:
            native = Path(installation) / 'bin/gh.exe'
            native.parent.mkdir()
            shutil.copyfile(sys.executable, native)
            with patch.dict(os.environ, {'PATH':str(native.parent)}), \
                    patch.object(admission, '_sandbox_group_rights', return_value=0):
                self.assertEqual(str(Path(native).resolve()), admission._github_executable(Path(temporary)))
                self.assertFalse(admission._worker_writable(Path(native).resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows writable native executable')
    def test_worker_writable_native_is_refused_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base / 'bin' / 'gh.exe'
            binary.parent.mkdir()
            shutil.copyfile(sys.executable, binary)
            with patch.dict(os.environ, {'PATH':str(binary.parent)}), \
                    patch.object(admission, '_sandbox_group_rights', return_value=2):
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
