"""Real installed workflow subprocesses; terminal and gh doubles stay test-only."""
import json
from copy import deepcopy
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import source_only  # noqa: F401
import test_bootstrap_configuration as bootstrap
from agentic import operating as op


TOKENS = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')


class OwnerAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = bootstrap.ConfiguredInstallerTests()
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        # Include explicit fresh budget defaults with the minimal release's
        # compatible routes. Omitting defaults preserves legacy behavior.
        defaults = '.agentic/defaults/OPERATING_CONFIG.yaml'
        value = op._upgrade_safe_operating(bootstrap.template())
        value['budgeting'] = json.loads((bootstrap.ROOT / defaults).read_bytes())['budgeting']
        raw = bootstrap.json_bytes(value)
        fixture.files[defaults] = raw
        target = fixture.source / defaults
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest = bootstrap.json_bytes(dict(format='awf-manifest-1', template_version=bootstrap.VERSION,
            files={p: bootstrap.sha256(b) for p, b in fixture.files.items()}))
        (fixture.source / 'MANIFEST.json').write_bytes(manifest)
        fixture.pin = bootstrap.sha256(manifest)
        fixture.perform()
        cls.template = fixture.dest

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf-owner-')
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.fixture = SimpleNamespace(base=base, dest=base / 'project')
        self.root = self.fixture.dest
        shutil.copytree(self.template, self.root)
        self.git('init', '-q', '-b', 'main')
        self.govpath = self.root / op.GOVERNANCE
        self.gov = json.loads(self.govpath.read_bytes())
        self.gov['merge_gate']['trusted_owner_ids'] = [1001]
        self.save_governance()
        self.bin = self.fixture.base / 'bin'
        self.bin.mkdir()
        self.calls = self.fixture.base / 'gh-calls.jsonl'
        self.identity = self.fixture.base / 'identity.json'
        self.identity.write_text(json.dumps(dict(id=1001, login='owner', type='User')))
        fake = self.bin / 'fake_gh.py'
        fake.write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            f'p=Path({str(self.identity)!r}); calls=Path({str(self.calls)!r})\n'
            'with calls.open("a") as s: s.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'v=json.loads(p.read_text())\n'
            'if v.get("replace_executable") and "status" in sys.argv: Path(v["replace_executable"]).write_bytes(b"changed")\n'
            'if any(k in os.environ for k in v.get("forbidden_environment", [])): sys.exit(9)\n'
            'if "status" in sys.argv:\n'
            ' print(v.get("status", "github.com\\n  Logged in to github.com account owner (keyring)\\n  - Active account: true"))\n'
            ' sys.exit(v.get("exit",0))\n'
            'print(json.dumps(v))\n', encoding='utf-8')
        if os.name == 'nt':
            (self.bin / 'gh.cmd').write_text(f'@"{sys.executable}" -B "{fake}" %*\n')
        else:
            gh = self.bin / 'gh'
            gh.write_text(f'#!{sys.executable}\nexec(compile(open({str(fake)!r}).read(), "fake_gh.py", "exec"))\n')
            gh.chmod(0o700)
        self.native = self.bin / ('gh.exe' if os.name == 'nt' else 'gh-native')
        shutil.copy2(Path(sys.executable).resolve(), self.native)
        if os.name != 'nt':
            (self.bin / 'gh').unlink()
            self.native.rename(self.bin / 'gh')
            self.native = self.bin / 'gh'
        self.fake = fake
        self.env = {k: v for k, v in os.environ.items() if k not in (*TOKENS, 'GH_HOST')}
        self.env.update(AWF_STATE_HOME=str(self.fixture.base / 'protected'), PATH=str(self.bin))
        self.env['PATH'] += os.pathsep + str(Path(shutil.which('git')).parent)
        if os.name == 'nt':
            self.env['PATH'] += os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
        self.before = (self.root / op.CONFIG).read_bytes()
        self.audit_before = self.events()

    def save_governance(self):
        self.govpath.write_text(json.dumps(self.gov), encoding='utf-8')

    def events(self):
        return sorted(p.read_bytes() for p in (self.root / op.STATE / 'changes').glob('*.json'))

    def git(self, *args, cwd=None):
        env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 'fixture',
               'GIT_AUTHOR_EMAIL': 'fixture@example.invalid', 'GIT_COMMITTER_NAME': 'fixture',
               'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        result = subprocess.run([shutil.which('git'), *args], cwd=cwd or self.root,
                                env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result

    def protect_git(self):
        # Model the actual host boundary with filesystem permissions, never a
        # mocked Git identity or admission decision. Restore for fixture cleanup.
        common = self.root / '.git'
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('advapi32', use_last_error=True)
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            api.GetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p))
            api.SetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
            api.SetSecurityInfo.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
            api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (ctypes.c_void_p,
                wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
            api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (wintypes.LPCWSTR,
                wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
            api.GetSecurityDescriptorDacl.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL))
            kernel.LocalFree.argtypes = (ctypes.c_void_p,)
            kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            metadata = [common, common / 'worktrees']
            for admin in (common / 'worktrees').iterdir():
                metadata.extend((admin, admin / 'commondir', admin / 'gitdir'))
            for target, mask in [(common.parent, 0x40), *[(path, 0xd0046) for path in metadata]]:
                descriptor, acl, text = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
                self.assertEqual(0, api.GetNamedSecurityInfoW(str(target), 1, 4, None, None,
                    ctypes.byref(acl), None, ctypes.byref(descriptor)))
                self.assertTrue(api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4,
                    ctypes.byref(text), None))
                original = ctypes.wstring_at(text)
                kernel.LocalFree(text)
                # Keep the trusted fixture's restoration handle out of children.
                handle = kernel.CreateFileW(str(target), 0x60000, 7, None, 3, 0x02000000, None)
                self.assertNotEqual(wintypes.HANDLE(-1).value, handle)
                def restore(handle=handle, acl=acl, descriptor=descriptor):
                    self.assertEqual(0, api.SetSecurityInfo(handle, 1, 0x20000004, None, None, acl, None))
                    kernel.CloseHandle(handle)
                    kernel.LocalFree(descriptor)
                self.addCleanup(restore)
                protected = 'D:P(D;;' + hex(mask) + ';;;WD)' + original[original.index('('):]
                replacement, new_acl = ctypes.c_void_p(), ctypes.c_void_p()
                self.assertTrue(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(protected, 1,
                    ctypes.byref(replacement), None))
                present, defaulted = wintypes.BOOL(), wintypes.BOOL()
                self.assertTrue(api.GetSecurityDescriptorDacl(replacement, ctypes.byref(present),
                    ctypes.byref(new_acl), ctypes.byref(defaulted)))
                self.assertEqual(0, api.SetNamedSecurityInfoW(str(target), 1, 0x80000004,
                                                           None, None, new_acl, None))
                kernel.LocalFree(replacement)
        else:
            previous = [(path, path.stat().st_mode) for path in (common, *common.rglob('*'))]
            self.addCleanup(lambda: [path.chmod(mode) for path, mode in previous])
            for path, mode in previous:
                path.chmod(mode & ~0o222)

    def cli(self, *args, tty=False, answer='', env=None, clock=None, root=None, project_cwd=False, crash=None, stdin_kind='pipe', stdout_kind='pipe', real_permissions=False, python311_lookup=False, ancestor_replace=False, owner_home=None, root_delete_only=False, owner_error=None):
        root = root or self.root
        entry = root / '.agentic/scripts/workflow.py'
        command = [sys.executable, '-B', '-I', str(entry), '--root', str(root), 'operating', *args]
        # Every process injects the protected account location at module level;
        # production exposes no flag, environment variable or file for this.
        if command:
            # Exercise the actual script in a fresh process. Only stream identity
            # and time are fixture facts; production has no test mode or flag.
            wrapper = ('import sys,io,runpy\n'
                       f'sys.path.insert(0,{str(root / ".agentic/lib")!r})\n')
            wrapper += ('import agentic.owner_admission as admission\n'
                'from pathlib import Path\n'
                f'admission._owner_state_home=lambda: Path({str(owner_home or self.fixture.base / "protected")!r})\n'
                'if hasattr(admission, "_worker_writable"):\n'
                ' original_writable=admission._worker_writable\n'
                f' admission._worker_writable=lambda p: False if not {real_permissions!r} and Path(p).resolve()==Path({str(self.native)!r}).resolve() else original_writable(p)\n'
                'admission._git_executable=lambda:sys.executable\n'
                'admission._sandbox_group_rights=lambda p:0\n'
                'original_run=admission.subprocess.run\n'
                'def fake_run(args,**kwargs):\n'
                f' if Path(args[0]).resolve()==Path({str(self.native)!r}).resolve(): args=[sys.executable,"-B",{str(self.fake)!r},*args[1:]]\n'
                ' return original_run(args,**kwargs)\n'
                'admission.subprocess.run=fake_run\n')
            if owner_error:
                wrapper += ('def no_profile():\n'
                    f' raise {owner_error}\n'
                    'admission._owner_state_home=no_profile\n')
            if python311_lookup:
                wrapper += ('original_which=admission.shutil.which\n'
                    'def qualified_which(p,**kwargs):\n'
                    ' if admission.os.name=="nt" and Path(p).suffix.lower()!=".exe": return None\n'
                    ' return original_which(p,**kwargs)\n'
                    'admission.shutil.which=qualified_which\n')
            if ancestor_replace:
                wrapper += (f'probe=Path({str(self.native)!r})\n'
                    'admission._sandbox_group_rights=lambda p: 0x10000 if p==probe.parent else 4 if p==probe.parent.parent else 0\n')
            if root_delete_only:
                wrapper += (f'probe=Path({str(self.native)!r})\n'
                    'admission._sandbox_group_rights=lambda p: 0x40 if p==Path(probe.anchor) else 0\n')
            if tty in (True, 'stdin'):

                wrapper += ('class Terminal(io.StringIO):\n def isatty(self): return True\n'
                            f'sys.stdin=Terminal({answer!r})\n')
            if tty in (True, 'stdout'):
                wrapper += ('class Output:\n def write(self,s): return sys.__stdout__.write(s)\n'
                    ' def flush(self): return sys.__stdout__.flush()\n'
                    ' def isatty(self): return True\n'
                    ' def __getattr__(self,k): return getattr(sys.__stdout__,k)\n'
                    'sys.stdout=Output()\n')
            # Synthetic terminals model only host console observations. Actual
            # redirected descriptors still use the production Windows check.
            wrapper += ('import agentic.owner_admission as admission\n'
                        'if hasattr(admission, "_windows_console"):\n'
                        ' original_console=admission._windows_console\n'
                        ' admission._windows_console=lambda s: True if type(s).__name__ in ("Terminal", "Output") else original_console(s)\n')
            if crash:
                wrapper += ('import os,agentic.operating as op\n'
                    'original_new,original_write,original_unlink=op._new,op.Tree.write,op.Tree.unlink\n'
                    'original_register=admission.register_transaction\n'
                    'original_consume=admission.ProposalStore.consume\n'
                    'original_admit=admission._ConfirmedOwner.admit_operating_change\n'
                    'original_complete=admission.complete_transaction\n'
                    'original_read=op._read\n'
                    f'phase={crash!r}\n'
                    'audited=False\n'
                    'def consume(*args,**kwargs):\n'
                    ' if phase=="before_consume": os._exit(71)\n'
                    ' result=original_consume(*args,**kwargs)\n'
                    ' if phase=="after_consume_write": os._exit(71)\n'
                    ' return result\n'
                    'def admit(*args,**kwargs):\n'
                    ' result=original_admit(*args,**kwargs)\n'
                    ' if phase=="after_consume": os._exit(71)\n'
                    ' return result\n'
                    'def register(root,governance,event,raw):\n'
                    f' if phase=="before_register": Path({str(self.fixture.base / "unjournalled.json")!r}).write_bytes(raw); os._exit(72)\n'
                    ' result=original_register(root,governance,event,raw)\n'
                    ' if phase=="after_register": os._exit(71)\n'
                    ' return result\n'
                    'def new(tree,name,raw):\n'
                    ' global audited\n'
                    f' if phase=="before_journal" and name==op.JOURNAL: Path({str(self.fixture.base / "unjournalled.json")!r}).write_bytes(raw); os._exit(71)\n'
                    ' if phase=="after_config" and "/changes/" in name: os._exit(71)\n'
                    ' result=original_new(tree,name,raw)\n'
                    ' if name==op.JOURNAL and phase=="journal_published": os._exit(71)\n'
                    ' if "/changes/" in name:\n'
                    '  audited=True\n'
                    '  if phase=="after_audit": os._exit(71)\n'
                    ' return result\n'
                    'def write(tree,name,raw):\n'
                    ' if phase=="after_journal" and name==op.CONFIG: os._exit(71)\n'
                    ' result=original_write(tree,name,raw)\n'
                    ' if phase=="config_published" and name==op.CONFIG: os._exit(71)\n'
                    ' return result\n'
                    'def read(tree,name):\n'
                    ' if audited and name==op.CONFIG and phase=="before_readback": os._exit(71)\n'
                    ' result=original_read(tree,name)\n'
                    ' if audited and name==op.CONFIG and phase=="after_readback": os._exit(71)\n'
                    ' return result\n'
                    'def complete(*args,**kwargs):\n'
                    ' if phase=="before_complete": os._exit(71)\n'
                    ' result=original_complete(*args,**kwargs)\n'
                    ' if phase=="after_complete": os._exit(71)\n'
                    ' return result\n'
                    'def unlink(tree,name):\n'
                    ' if phase=="after_event" and name==op.JOURNAL: os._exit(71)\n'
                    ' result=original_unlink(tree,name)\n'
                    ' if phase=="after_unlink" and name==op.JOURNAL: os._exit(71)\n'
                    ' return result\n'
                    'admission.ProposalStore.consume=consume\n'
                    'admission._ConfirmedOwner.admit_operating_change=admit\n'
                    'admission.register_transaction=register\n'
                    'admission.complete_transaction=complete\n'
                    'op._new,op.Tree.write,op.Tree.unlink,op._read=new,write,unlink,read\n')
            if clock:
                wrapper += f'import agentic.owner_admission as admission\nadmission.now_text=lambda: {clock!r}\n'
            wrapper += f'sys.argv={command[3:]!r}\nrunpy.run_path({str(entry)!r},run_name="__main__")\n'
            command = [sys.executable, '-B', '-I', '-c', wrapper]
        from contextlib import ExitStack
        with ExitStack() as stack:
            def descriptor(kind, mode):
                if kind == 'pipe':
                    return subprocess.PIPE
                path = os.devnull if kind == 'null' else self.fixture.base / ('redirect-' + mode)
                if kind == 'file' and mode == 'r':
                    path.write_text('not a terminal\n')
                return stack.enter_context(open(path, mode))
            return subprocess.run(command, stdin=descriptor(stdin_kind, 'r'),
                                  stdout=descriptor(stdout_kind, 'w'), stderr=subprocess.PIPE,
                                  text=True, env=env or self.env,
                                  cwd=root if project_cwd else self.fixture.base, timeout=45)


    def pending(self, *assignments, clock=None):
        result = self.cli('set', '--instruction', 'Exact owner requested change',
            *[item for pair in (('--set', a) for a in (assignments or ('budgeting.accounting_enabled=true',))) for item in pair],
            '--json', clock=clock)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual('PENDING_OWNER_CONFIRMATION', report['status'])
        self.assertEqual(64, len(report['digest']))
        self.assertIn('operating confirm --digest ' + report['digest'], report['confirm_command'])
        self.unchanged()
        return report['digest']

    def confirm(self, digest, **kwargs):
        kwargs.setdefault('tty', True)
        kwargs.setdefault('answer', digest[:8] + '\n')
        return self.cli('confirm', '--digest', digest, '--json', **kwargs)

    def unchanged(self):
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(self.audit_before, self.events())
        self.assertFalse((self.root / op.JOURNAL).exists())

    def refused(self, digest, phrase, **kwargs):
        result = self.confirm(digest, **kwargs)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn(phrase, result.stdout)
        self.unchanged()
        return result

    def test_pending_proposal_changes_nothing(self):
        self.pending()
        self.assertFalse(self.calls.exists())

    def test_non_terminal_refused_before_gh(self):
        digest = self.pending()
        for tty in (False, 'stdin', 'stdout'):
            with self.subTest(terminal=tty):
                self.refused(digest, 'INTERACTIVE_TERMINAL_REQUIRED', tty=tty)
        self.assertFalse(self.calls.exists())

    def test_redirected_streams_refused_before_gh(self):
        digest = self.pending()
        for side in ('stdin', 'stdout'):
            for kind in ('null', 'pipe', 'file'):
                with self.subTest(side=side, kind=kind):
                    result = self.confirm(digest, tty='stdout' if side == 'stdin' else 'stdin',
                                          **{side + '_kind': kind})
                    self.assertEqual(2, result.returncode, str(result))
                    if side == 'stdin' or kind == 'pipe':
                        self.assertIn('INTERACTIVE_TERMINAL_REQUIRED', result.stdout)
                    self.unchanged()
        self.assertFalse(self.calls.exists())

    def forged_journal(self):
        value = json.loads(self.before)
        old = deepcopy(value)
        value['budgeting']['accounting_enabled'] = True
        value['budgeting']['enforcement_enabled'] = True
        changes = [dict(path='budgeting.' + key, before=False, after=True)
                   for key in ('accounting_enabled', 'enforcement_enabled')]
        events = [json.loads(raw) for raw in self.events()]
        prior = max(events, key=lambda e: e['sequence'])
        return dict(format='awf-operating-transaction-1', before=self.before.decode(),
                    after=op._json(value).decode(), event=dict(
                        id='C-' + 'a' * 32, created_at=prior['created_at'], instruction='Budget request',
                        before_hash=op.fingerprint('operating', old), after_hash=op.fingerprint('operating', value),
                        changes=changes, source=value['source'], applied_from='chat',
                        sequence=prior['sequence'] + 1, previous_change_id=prior['id'],
                        governance_hash=op.fingerprint('operating-governance', self.gov)))

    def recover(self, journal):
        raw = op._json(journal)
        (self.root / op.JOURNAL).write_bytes(raw)
        return self.cli('recover', '--expected-journal-sha256', op.sha256(raw), '--json')

    def test_unconfirmed_journal_cannot_change_budget(self):
        journal = self.forged_journal()
        result = self.recover(journal)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(self.audit_before, self.events())
        self.assertEqual(op._json(journal), (self.root / op.JOURNAL).read_bytes())
        self.assertFalse(self.calls.exists())

    def test_pending_proposal_is_not_recovery_admission(self):
        digest = self.pending('budgeting.accounting_enabled=true', 'budgeting.enforcement_enabled=true')
        journal = self.forged_journal()
        journal['event']['owner_admission'] = dict(actor_id=1001, login='owner', type='User',
            digest=digest, request_id='00000000-0000-4000-8000-000000000001', confirmed_at='2026-11-06T12:00:00Z',
            executable_path=str(self.native), executable_sha256=op.sha256(self.native.read_bytes()))
        result = self.recover(journal)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(self.audit_before, self.events())
        self.assertFalse(self.calls.exists())

    def test_confirmed_crash_recovers_once_at_each_publication_phase(self):
        for phase in ('journal_published', 'after_journal', 'config_published', 'after_config',
                      'after_audit', 'before_readback', 'after_readback', 'before_complete',
                      'after_complete', 'after_event'):
            with self.subTest(phase=phase):
                # Alternate limits so every iteration has a real budget change.
                digest = self.pending('budgeting.max_tokens_per_ticket=' + str(123 + len(self.events())))
                result = self.confirm(digest, crash=phase)
                self.assertEqual(71, result.returncode, result.stdout + result.stderr)
                journal = json.loads((self.root / op.JOURNAL).read_bytes())
                calls = self.calls.read_bytes()
                result = self.recover(journal)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(calls, self.calls.read_bytes())
                shown = self.cli('show', '--json')
                self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
                self.assertEqual(json.loads(journal['after']), json.loads(shown.stdout)['configuration'])
                self.before, self.audit_before = (self.root / op.CONFIG).read_bytes(), self.events()
                self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')
                # Replaying a terminal WAL may only clear it, never add an event.
                self.assertEqual(0, self.recover(journal).returncode)
                self.unchanged()

    def test_crash_before_registration_allows_fresh_confirmation(self):
        # F1: the exact pre-registration exit must never strand a published WAL.
        digest = self.pending('budgeting.max_tokens_per_ticket=123')
        result = self.confirm(digest, crash='before_register')
        self.assertEqual(72, result.returncode, result.stdout + result.stderr)
        self.unchanged()
        self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')
        self.apply('budgeting.max_tokens_per_ticket=123')
        self.assertEqual(123, json.loads(self.before)['budgeting']['max_tokens_per_ticket'])

    def test_unpublished_crash_boundaries_allow_fresh_confirmation(self):
        for phase in ('before_consume', 'after_consume_write', 'after_consume',
                      'after_register', 'before_journal'):
            with self.subTest(phase=phase):
                limit = 123 + len(self.events())
                digest = self.pending('budgeting.max_tokens_per_ticket=' + str(limit))
                result = self.confirm(digest, crash=phase)
                self.assertEqual(71, result.returncode, result.stdout + result.stderr)
                self.unchanged()
                calls = self.calls.read_bytes()
                self.apply('budgeting.max_tokens_per_ticket=' + str(limit))
                self.assertEqual(limit, json.loads(self.before)['budgeting']['max_tokens_per_ticket'])
                # Consumption is immutable, and later confirmation supersedes
                # even an unconsumed proposal from the same prior disk state.
                self.assertNotEqual(calls, self.calls.read_bytes())
                result = self.confirm(digest)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.unchanged()

    def test_crash_after_journal_cleanup_is_already_complete(self):
        digest = self.pending('budgeting.max_tokens_per_ticket=123')
        result = self.confirm(digest, crash='after_unlink')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.root / op.JOURNAL).exists())
        self.assertEqual(len(self.audit_before) + 1, len(self.events()))
        self.refresh()
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])
        self.assertEqual(123, json.loads(shown.stdout)['configuration']['budgeting']['max_tokens_per_ticket'])
        self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')
        self.apply('budgeting.max_tokens_per_ticket=124')

    def test_recovery_refuses_mismatched_confirmed_transaction_without_writes(self):
        digest = self.pending('budgeting.accounting_enabled=true', 'budgeting.enforcement_enabled=true')
        result = self.confirm(digest, crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        original = json.loads((self.root / op.JOURNAL).read_bytes())
        calls = self.calls.read_bytes()
        for change in ('audit', 'observation', 'changes', 'after', 'prior', 'project', 'governance'):
            with self.subTest(change=change):
                journal = deepcopy(original)
                root = self.root
                if change == 'audit':
                    journal['event']['owner_admission']['login'] = 'other'
                elif change == 'observation':
                    journal['event'].pop('budget_owner_observation')
                elif change == 'changes':
                    journal['event']['changes'][0]['after'] = 'changed'
                elif change in ('after', 'prior'):
                    member = 'after' if change == 'after' else 'before'
                    value = json.loads(journal[member])
                    value['streams']['count'] = 2
                    journal[member] = op._json(value).decode()
                    journal['event'][member + '_hash'] = op.fingerprint('operating', value)
                elif change == 'project':
                    root = self.fixture.base / 'foreign'
                    shutil.copytree(self.root, root)
                    foreign_governance = deepcopy(self.gov)
                    foreign_governance['project']['id'] = str(__import__('uuid').uuid4())
                    (root / op.GOVERNANCE).write_bytes(op._json(foreign_governance))
                    journal['event']['governance_hash'] = op.fingerprint('operating-governance', foreign_governance)
                else:
                    self.gov['project']['name'] += ' changed'
                    self.save_governance()
                    journal['event']['governance_hash'] = op.fingerprint('operating-governance', self.gov)
                raw = op._json(journal)
                (root / op.JOURNAL).write_bytes(raw)
                result = self.cli('recover', '--expected-journal-sha256', op.sha256(raw), '--json', root=root)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
                self.assertEqual(self.before, (root / op.CONFIG).read_bytes())
                self.assertEqual(self.audit_before, sorted(p.read_bytes() for p in (root / op.STATE / 'changes').glob('*.json')))
                self.assertEqual(raw, (root / op.JOURNAL).read_bytes())
                self.assertEqual(calls, self.calls.read_bytes())

    def test_path_scripts_and_worker_writable_gh_refused(self):
        digest = self.pending()
        for suffix in (('.exe', '.cmd', '.bat', '.ps1', '.py') if os.name == 'nt' else ('', '.sh', '.py')):
            with self.subTest(suffix=suffix):
                folder = self.fixture.base / ('untrusted' + (suffix or '-script'))
                folder.mkdir()
                target = folder / ('gh' + suffix)
                if suffix == '.exe' or (os.name != 'nt' and suffix == ''):
                    shutil.copy2(self.native, target)
                elif os.name == 'nt' and suffix in ('.cmd', '.bat'):
                    target.write_text((self.bin / 'gh.cmd').read_text())
                else:
                    target.write_text('#!/bin/sh\nexit 0\n')
                    target.chmod(0o700)
                result = self.confirm(digest, env={**self.env, 'PATH': str(folder)})
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.unchanged()
        self.assertFalse(self.calls.exists())

    def test_each_token_variable_even_empty_is_refused(self):
        digest = self.pending()
        for token in TOKENS:
            for value in ('fixture-token-never-log', ''):
                with self.subTest(token=token, empty=not value):
                    result = self.refused(digest, 'TOKEN_ENVIRONMENT_REFUSED', env={**self.env, token: value})
                    self.assertNotIn('fixture-token-never-log', result.stdout + result.stderr)
        self.assertFalse(self.calls.exists())

    def test_enterprise_host_refused(self):
        self.refused(self.pending(), 'GITHUB_HOST_REFUSED', env={**self.env, 'GH_HOST': 'enterprise.invalid'})

    def test_gh_children_strip_provider_keys_and_configured_extra_variables(self):
        self.gov['execution']['child_env_strip_extra'] = ['AWF_FIXTURE_PRIVATE']
        self.save_governance()
        self.identity.write_text(json.dumps(dict(id=1001, login='owner', type='User',
            forbidden_environment=['OPENAI_API_KEY', 'AWF_FIXTURE_PRIVATE'])))
        digest = self.pending()
        result = self.confirm(digest, env={**self.env, 'OPENAI_API_KEY': 'synthetic-unusable',
                                          'AWF_FIXTURE_PRIVATE': 'synthetic-unusable'})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_empty_owner_gate_names_configuration(self):
        self.gov['merge_gate']['trusted_owner_ids'] = []
        self.save_governance()
        self.refused(self.pending(), 'OWNER_GATE_NOT_CONFIGURED')
        result = self.confirm('0' * 64)
        self.assertIn('.agentic/PROJECT_CONFIG.yaml', result.stdout)

    def test_wrong_id_bot_and_login_match_with_wrong_id(self):
        digest = self.pending()
        for identity in (dict(id=9, login='stranger', type='User'),
                         dict(id=1001, login='owner', type='Bot'),
                         dict(id=9, login='owner', type='User')):
            with self.subTest(identity=identity):
                self.identity.write_text(json.dumps(identity))
                self.refused(digest, 'OWNER_IDENTITY_REFUSED')

    def test_missing_logged_out_and_success_exit_without_status_proof(self):
        digest = self.pending()
        self.refused(digest, 'GITHUB_AUTH_UNAVAILABLE', env={**self.env, 'PATH': ''})
        for status, code in (('not logged in', 1), ('not logged in', 0),
                             ('github.com\nLogged in to github.com account owner\nActive account: false', 0)):
            self.identity.write_text(json.dumps(dict(id=1001, login='owner', type='User', status=status, exit=code)))
            self.refused(digest, 'GITHUB_AUTH_UNAVAILABLE')

    def test_project_local_gh_cannot_supply_owner_identity(self):
        digest = self.pending()
        executable = 'gh.exe' if os.name == 'nt' else 'gh'
        shutil.copy2(self.bin / executable, self.root / executable)
        for path in ('', str(self.root)):
            with self.subTest(path=path):
                self.refused(digest, 'GITHUB_AUTH_UNAVAILABLE', project_cwd=True,
                             env={**self.env, 'PATH': path})
        self.assertFalse(self.calls.exists())

    def test_first_path_candidate_is_refused_without_searching_past_it(self):
        digest = self.pending()
        untrusted = self.fixture.base / 'first-candidate'
        untrusted.mkdir()
        shutil.copy2(self.native, untrusted / self.native.name)
        self.refused(digest, 'GITHUB_AUTH_UNAVAILABLE',
                     env={**self.env, 'PATH': str(untrusted) + os.pathsep + self.env['PATH']})
        self.assertFalse(self.calls.exists())

    def test_executable_replacement_during_lookup_refused(self):
        digest = self.pending()
        self.identity.write_text(json.dumps(dict(id=1001, login='owner', type='User',
                                                 replace_executable=str(self.native))))
        self.refused(digest, 'GITHUB_AUTH_UNAVAILABLE')
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual([['auth', 'status', '--hostname', 'github.com', '--active']], calls)

    def test_wrong_digest_and_wrong_typed_confirmation_consume_nothing(self):
        digest = self.pending()
        self.refused('0' * 64, 'PROPOSAL_NOT_FOUND')
        self.refused(digest, 'DIGEST_CONFIRMATION_REFUSED', answer='no\n')
        self.assertEqual(0, self.confirm(digest).returncode)

    def test_expired_proposal(self):
        digest = self.pending(clock='2000-01-01T00:00:00Z')
        self.refused(digest, 'PROPOSAL_EXPIRED')

    def test_changed_governance(self):
        digest = self.pending()
        self.gov['project']['name'] += ' changed'
        self.save_governance()
        self.refused(digest, 'PROPOSAL_BINDING_MISMATCH')

    def test_changed_prior_settings(self):
        digest = self.pending()
        result = self.cli('set', '--instruction', 'Two streams', '--set', 'streams.count=2', '--json')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.before = (self.root / op.CONFIG).read_bytes()
        self.audit_before = self.events()
        self.refused(digest, 'PROPOSAL_BINDING_MISMATCH')

    def test_foreign_project(self):
        digest = self.pending()
        foreign = self.fixture.base / 'foreign'
        shutil.copytree(self.root, foreign)
        governance = json.loads((foreign / op.GOVERNANCE).read_bytes())
        governance['project']['id'] = str(__import__('uuid').uuid4())
        (foreign / op.GOVERNANCE).write_bytes(op._json(governance))
        result = self.confirm(digest, root=foreign)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('PROPOSAL_NOT_FOUND', result.stdout)
        self.unchanged()

    def test_mixed_invalid_changes_create_and_consume_nothing(self):
        digest = self.pending()
        result = self.cli('set', '--instruction', 'Invalid mixed change', '--set', 'budgeting.accounting_enabled=true',
                          '--set', 'streams.count=1001', '--json')
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.unchanged()
        self.assertEqual(0, self.confirm(digest).returncode)

    def test_mixed_change_exceeding_audit_bound_creates_and_consumes_nothing(self):
        digest = self.pending()
        route = json.loads(self.before)['streams']['A']['worker']
        assignments = ['budgeting.accounting_enabled=true'] + [
            f'epic_overrides.FIX-{i}.streams.A.worker={route["model"]}/{route["reasoning_effort"]}'
            for i in range(1, 66)]
        result = self.cli('set', '--instruction', 'Invalid audit bound',
                          *[s for a in assignments for s in ('--set', a)], '--json')
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.unchanged()
        database = next((self.fixture.base / 'protected').rglob('owner-admission.sqlite3'))
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM owner_proposals').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT count(*) FROM owner_confirmations').fetchone()[0])
        self.assertEqual(0, self.confirm(digest).returncode)

    def test_valid_confirm_persists_and_replay_refuses(self):
        digest = self.pending('budgeting.accounting_enabled=true', 'budgeting.enforcement_enabled=true', 'streams.count=2')
        result = self.confirm(digest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        value = json.loads(shown.stdout)['configuration']
        self.assertTrue(value['budgeting']['accounting_enabled'])
        self.assertTrue(value['budgeting']['enforcement_enabled'])
        self.assertEqual(2, value['streams']['count'])
        self.before, self.audit_before = (self.root / op.CONFIG).read_bytes(), self.events()
        self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')
        events = [json.loads(raw) for raw in self.events()]
        audit = next(e['owner_admission'] for e in events if 'owner_admission' in e)
        self.assertEqual(1001, audit['actor_id'])
        self.assertEqual('owner', audit['login'])
        self.assertEqual(digest, audit['digest'])
        self.assertTrue(audit['confirmed_at'])
        self.assertEqual(str(self.native.resolve()), audit['executable_path'])
        self.assertEqual(op.sha256(self.native.read_bytes()), audit['executable_sha256'])
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertIn(['auth', 'status', '--hostname', 'github.com', '--active'], calls)
        self.assertIn(['api', '--hostname', 'github.com', '--method', 'GET', 'user'], calls)
        self.assertNotIn('--show-token', str(calls))

    def test_dormant_limits_enable_neither_switch(self):
        digest = self.pending('budgeting.max_tokens_per_ticket=123', 'budgeting.max_tokens_per_project=456')
        result = self.confirm(digest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        value = json.loads(self.cli('show', '--json').stdout)['configuration']['budgeting']
        self.assertEqual(dict(accounting_enabled=False, enforcement_enabled=False,
                              max_tokens_per_ticket=123, max_tokens_per_project=456), value)

    def refresh(self):
        self.before, self.audit_before = (self.root / op.CONFIG).read_bytes(), self.events()

    def apply(self, *assignments):
        result = self.confirm(self.pending(*assignments))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.refresh()

    def test_recovery_cannot_reapply_consumed_digest_under_new_event_id(self):
        result = self.cli('set', '--instruction', 'prepare prior source', '--set', 'streams.count=2', '--json')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.refresh()
        self.apply('budgeting.accounting_enabled=true')
        first = max((json.loads(v) for v in self.events()), key=lambda e: e['sequence'])
        after = self.before.decode()
        self.apply('budgeting.accounting_enabled=false')
        tip = max((json.loads(v) for v in self.events()), key=lambda e: e['sequence'])
        event = deepcopy(first)
        event.update(id='C-' + 'b' * 32, before_hash=op.fingerprint('operating', json.loads(self.before)),
                     sequence=tip['sequence'] + 1, previous_change_id=tip['id'])
        # Original bound prior (source=user) equals the current settings, so
        # hash/chain checks alone cannot detect the repeated confirmation.
        result = self.recover(dict(format='awf-operating-transaction-1', before=self.before.decode(),
                                   after=after, event=event))
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())

    def test_consumption_without_published_journal_cannot_recover(self):
        digest = self.pending()
        result = self.confirm(digest, crash='before_register')
        self.assertEqual(72, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.root / op.JOURNAL).exists())
        result = self.recover(json.loads((self.fixture.base / 'unjournalled.json').read_bytes()))
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())

    def test_exact_journal_cannot_reapply_after_project_audit_is_rewound(self):
        digest = self.pending()
        result = self.confirm(digest, crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        journal = json.loads((self.root / op.JOURNAL).read_bytes())
        original_events = self.events()
        result = self.recover(journal)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        # Worker-owned files can be rewound; protected application history cannot.
        (self.root / op.CONFIG).write_bytes(journal['before'].encode('utf-8'))
        for path in (self.root / op.STATE / 'changes').glob('*.json'):
            if path.read_bytes() not in original_events:
                path.unlink()
        result = self.recover(journal)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(json.loads(journal['before']), json.loads((self.root / op.CONFIG).read_bytes()))

    def test_disable_accounting_recovers_derived_enforcement_at_all_crash_phases(self):
        for phase in ('after_journal', 'after_config', 'after_event'):
            with self.subTest(phase=phase):
                self.apply('budgeting.accounting_enabled=true', 'budgeting.enforcement_enabled=true')
                result = self.confirm(self.pending('budgeting.accounting_enabled=false'), crash=phase)
                self.assertEqual(71, result.returncode, result.stdout + result.stderr)
                result = self.recover(json.loads((self.root / op.JOURNAL).read_bytes()))
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                shown = self.cli('show', '--json')
                self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
                settings = json.loads(shown.stdout)['configuration']['budgeting']
                self.assertFalse(settings['accounting_enabled'])
                self.assertFalse(settings['enforcement_enabled'])
                self.assertEqual(0, self.cli('set', '--instruction', 'unrelated', '--set', 'streams.count=2', '--json').returncode)
                self.refresh()

    def test_protected_initialize_refuses_before_proposal(self):
        (self.root / op.CONFIG).unlink()
        shutil.rmtree(self.root / op.STATE / 'changes')
        result = self.cli('set', '--initialize', '--instruction', 'enable', '--set', 'budgeting.accounting_enabled=true', '--json')
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('Initialize defaults first', result.stdout)
        self.assertFalse((self.fixture.base / 'protected').exists())

    def test_direct_edits_never_prove_protected_settings(self):
        for forged_event in (True, False):
            with self.subTest(forged_event=forged_event):
                value = json.loads(self.before)
                value['budgeting'].update(accounting_enabled=True, enforcement_enabled=True)
                (self.root / op.CONFIG).write_bytes(op._json(value))
                directory = self.root / op.STATE / 'changes'
                if forged_event:
                    tip = max((json.loads(v) for v in self.audit_before), key=lambda e: e['sequence'])
                    event = dict(tip, id='C-' + 'd' * 32, before_hash=tip['after_hash'],
                                 after_hash=op.fingerprint('operating', value), changes=[], instruction='forged',
                                 applied_from='chat', sequence=tip['sequence'] + 1, previous_change_id=tip['id'])
                    (directory / (event['id'] + '.json')).write_bytes(op._json(event))
                else:
                    shutil.rmtree(directory)
                result = self.cli('show', '--json')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertFalse(json.loads(result.stdout)['budget_admission'])
                self.assertIn('OWNER_SETTINGS_REFUSED', result.stdout)
                self.assertIn('fresh owner confirmation', result.stdout)
                result = self.cli('set', '--instruction', 'unrelated', '--set', 'streams.count=2', '--json')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertFalse(json.loads(result.stdout)['budget_admission'])
                refused = self.script('', code=(
                    'from agentic import operating as op\n'
                    f'root=Path({str(self.root)!r})\n'
                    'op.read_operating(root,op.load_yaml((root/op.GOVERNANCE).read_bytes()))\n'))
                self.assertNotEqual(0, refused.returncode, refused.stdout + refused.stderr)
                self.assertIn('OWNER_SETTINGS_REFUSED', refused.stderr)
                for path in directory.glob('*.json'):
                    path.unlink()
                for raw in self.audit_before:
                    (directory / (json.loads(raw)['id'] + '.json')).write_bytes(raw)
        self.assertFalse(self.calls.exists())

    def test_environment_chosen_copy_is_not_recovery_authority(self):
        result = self.confirm(self.pending(), crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        journal = (self.root / op.JOURNAL).read_bytes()
        attacker = self.fixture.base / 'attacker-state'
        shutil.copytree(self.fixture.base / 'protected', attacker)
        shutil.rmtree(self.fixture.base / 'protected')
        calls = self.calls.read_bytes()
        result = self.cli('recover', '--expected-journal-sha256', op.sha256(journal), '--json',
                          env={**self.env, 'AWF_STATE_HOME': str(attacker)})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(calls, self.calls.read_bytes())
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())

    def test_moving_confirmed_project_requires_fresh_confirmation(self):
        self.apply('budgeting.accounting_enabled=true')
        moved = self.fixture.base / 'moved'
        shutil.move(str(self.root), moved)
        result = self.cli('show', '--json', root=moved)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['configuration']['budgeting']['accounting_enabled'])
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(result.stdout)['status'])
        result = self.cli('set', '--instruction', 'unrelated', '--set', 'streams.count=2', '--json', root=moved)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_later_confirmation_supersedes_unapplied_registered_journal(self):
        result = self.confirm(self.pending(), crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        journal = (self.root / op.JOURNAL).read_bytes()
        (self.root / op.JOURNAL).unlink()
        self.apply('budgeting.max_tokens_per_ticket=777')
        accepted = self.before
        # Rewind every project-owned byte, preserving only protected history.
        first = json.loads(journal)
        (self.root / op.CONFIG).write_bytes(first['before'].encode())
        for path in (self.root / op.STATE / 'changes').glob('*.json'):
            if json.loads(path.read_bytes())['id'] == max((json.loads(v) for v in self.audit_before), key=lambda e:e['sequence'])['id']:
                path.unlink()
        result = self.recover(first)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertNotEqual(accepted, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(first['before'].encode(), (self.root / op.CONFIG).read_bytes())

    def forge_config_event(self, value):
        before = json.loads((self.root / op.CONFIG).read_bytes())
        tip = max((json.loads(v) for v in self.events()), key=lambda e:e['sequence'])
        event = dict(tip, id='C-' + __import__('uuid').uuid4().hex,
            before_hash=op.fingerprint('operating', before), after_hash=op.fingerprint('operating', value),
            changes=[], instruction='external edit', applied_from='chat', sequence=tip['sequence']+1,
            previous_change_id=tip['id'])
        event.pop('owner_admission', None)
        event.pop('budget_owner_observation', None)
        (self.root / op.CONFIG).write_bytes(op._json(value))
        (self.root / op.STATE / 'changes' / (event['id']+'.json')).write_bytes(op._json(event))

    def test_confirmed_settings_cannot_be_disabled_by_defaults_or_absent_block(self):
        self.apply('budgeting.accounting_enabled=true', 'budgeting.max_tokens_per_ticket=123')
        for absent in (False, True):
            value = json.loads(self.before)
            if absent:
                value.pop('budgeting')
            else:
                from agentic.budgeting import fresh_settings
                value['budgeting'] = fresh_settings()
            self.forge_config_event(value)
            shown = self.cli('show', '--json')
            self.assertIn('OWNER_SETTINGS_REFUSED', shown.stdout)
            self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(shown.stdout)['status'])
            self.assertFalse(json.loads(shown.stdout)['budget_admission'])
            result = self.cli('set', '--instruction', 'unrelated', '--set', 'streams.count=2', '--json')
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse(json.loads(result.stdout)['budget_admission'])

    def test_new_account_reports_state_and_can_confirm_without_deleting_audit(self):
        self.apply('budgeting.accounting_enabled=true', 'budgeting.max_tokens_per_ticket=123')
        home = self.fixture.base / 'other-account'
        shown = self.cli('show', '--json', owner_home=home)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        report = json.loads(shown.stdout)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', report['status'])
        self.assertTrue(report['configuration']['budgeting']['accounting_enabled'])
        pending = self.cli('set', '--instruction', 'confirm on this account', '--set',
                           'budgeting.accounting_enabled=true', '--json', owner_home=home)
        self.assertEqual(3, pending.returncode, pending.stdout + pending.stderr)
        result = self.confirm(json.loads(pending.stdout)['digest'], owner_home=home)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(set(self.audit_before) <= set(self.events()))
        self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json', owner_home=home).stdout)['status'])

    def test_real_git_worktree_keeps_confirmation(self):
        git = shutil.which('git')
        self.assertIsNotNone(git)
        env = {**os.environ, 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_AUTHOR_NAME':'test',
            'GIT_AUTHOR_EMAIL':'test@example.invalid', 'GIT_COMMITTER_NAME':'test',
            'GIT_COMMITTER_EMAIL':'test@example.invalid'}
        def run(*args):
            result = subprocess.run([git, *args], cwd=self.root, env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        run('init', '-q', '-b', 'main')
        self.apply('budgeting.accounting_enabled=true')
        run('add', '-A')
        run('commit', '-qm', 'fixture')
        worktree = self.fixture.base / 'second-checkout'
        run('worktree', 'add', '-q', str(worktree), '-b', 'worker-a')
        self.protect_git()
        shown = self.cli('show', '--json', root=worktree)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])
        result = self.cli('set', '--instruction', 'worktree', '--set', 'streams.count=2', '--json', root=worktree)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_base_branch_rename_keeps_identity(self):
        self.apply('budgeting.accounting_enabled=true')
        self.gov['github']['base_branch'] = 'renamed-default'
        self.save_governance()
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])

    def test_owner_profile_inside_project_names_working_remedy(self):
        shown = self.cli('show', '--json', owner_home=self.root / 'profile')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])
        changed = self.cli('set', '--instruction', 'enable', '--set', 'budgeting.accounting_enabled=true',
                           '--json', owner_home=self.root / 'profile')
        self.assertEqual(2, changed.returncode, changed.stdout + changed.stderr)
        self.assertIn('OWNER_STORAGE_UNAVAILABLE', changed.stdout)
        self.assertIn('move the checkout', changed.stdout)
        self.assertNotIn('configure an absolute host-owned AWF_STATE_HOME', shown.stdout)

    def script(self, relative, *args, code=None, owner_home=None, owner_error=None):
        entry = self.root / relative
        wrapper = ('import sys,runpy\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(self.root / ".agentic/lib")!r})\n'
            'import agentic.owner_admission as admission\n'
            'admission._git_executable=lambda:sys.executable\n'
            'admission._sandbox_group_rights=lambda p:0\n'
            f'admission._owner_state_home=lambda:Path({str(owner_home or self.fixture.base / "protected")!r})\n')
        if owner_error:
            wrapper += f'def no_profile():\n raise {owner_error}\nadmission._owner_state_home=no_profile\n'
        wrapper += code if code is not None else (
            f'sys.argv={[str(entry), *map(str,args)]!r}\n'
            f'runpy.run_path({str(entry)!r},run_name="__main__")\n')
        return subprocess.run([sys.executable, '-B', '-I', '-c', wrapper],
            capture_output=True, text=True, env=self.env, cwd=self.fixture.base, timeout=60,
            stdin=subprocess.DEVNULL)

    def test_pure_validation_cannot_admit_controller_budgeting(self):
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = True
        self.forge_config_event(value)
        result = self.script('', code=(
            'from agentic import operating as op\nfrom agentic.budgeting import ControllerBudgeting\n'
            f'root=Path({str(self.root)!r})\n'
            'governance=op.load_yaml((root/op.GOVERNANCE).read_bytes())\n'
            'snap=op.validate_operating(op.load_yaml((root/op.CONFIG).read_bytes()),governance)\n'
            f'ControllerBudgeting(Path({str(self.fixture.base / "ledger.sqlite")!r}),governance,snap,object(),worktree_roots=(root,))\n'))
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_SETTINGS_REFUSED', result.stderr)
        self.assertFalse((self.fixture.base / 'ledger.sqlite').exists())

    def test_source_template_reporting_cannot_accept_unconfirmed_settings(self):
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = True
        self.forge_config_event(value)
        (self.root / op.INSTALLED).unlink()
        marker = self.root / op.SOURCE_MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('fixture source marker')
        from agentic.installer import release_member, RELEASE_EXCLUDED_PREFIXES
        # A complete synthetic source manifest is required by the real CLI;
        # a marker plus malformed manifest never reaches operating_status.
        with op.Tree(self.root) as tree:
            files = {name:op.sha256(tree.read(name)) for name in
                tree.file_list(exclude_root_git=True, exclude_prefixes=RELEASE_EXCLUDED_PREFIXES)
                if name not in {'MANIFEST.json','MANIFEST.md'} and release_member(name)}
        (self.root / op.SOURCE_MANIFEST).write_bytes(op._json(dict(
            format='awf-manifest-1', template_version=bootstrap.VERSION, files=files)))
        result = self.script('.agentic/scripts/workflow.py', '--root', self.root, 'validate-config')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_SETTINGS_REFUSED', result.stdout, result.stdout + result.stderr)
        self.assertNotEqual('ACCEPTED', json.loads(result.stdout)['operating']['status'])

    @unittest.skipUnless(os.name == 'nt', 'Windows volume replacement semantics')
    def test_volume_root_delete_grant_alone_does_not_refuse_native_install(self):
        result = self.confirm(self.pending(), real_permissions=True, root_delete_only=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_qualified_exe_lookup_without_pathext_expansion(self):
        result = self.confirm(self.pending(), python311_lookup=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'Windows installation ACL regression')
    def test_normal_native_install_uses_effective_file_permissions(self):
        # Controlled native bytes/ACL grants; the runner's installation is irrelevant.
        digest = self.pending()
        # Real binary/resolver, controlled sandbox grants and identity subprocess.
        result = self.confirm(digest, real_permissions=True,
            env={**self.env, 'PATH': str(self.native.parent)})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'Windows directory replacement rights')
    def test_replaceable_ancestor_directory_cannot_supply_gh(self):
        result = self.confirm(self.pending(), real_permissions=True, ancestor_replace=True)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('can modify or replace gh', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_r17_config_identity_edit_cannot_hide_confirmation(self):
        self.apply('budgeting.accounting_enabled=true')
        original = deepcopy(self.gov)
        for identity in ('project', 'repository'):
            with self.subTest(identity=identity):
                self.gov = deepcopy(original)
                value = json.loads(self.before)
                if identity == 'project':
                    value['budgeting']['accounting_enabled'] = False
                    self.gov['project']['id'] = str(__import__('uuid').uuid4())
                else:
                    value.pop('budgeting')
                    self.gov['github']['repository_id'] += 1
                self.forge_config_event(value)
                self.save_governance()
                result = self.script('.agentic/scripts/workflow.py', '--root', self.root, 'validate-config')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual('OWNER_CONFIRMATION_REQUIRED', report['operating']['status'])
                self.assertFalse(report['operating']['budget_admission'])
                result = self.script('', code=(
                    'from agentic import operating as op\n'
                    f'root=Path({str(self.root)!r})\n'
                    'op.read_operating(root,op.load_yaml((root/op.GOVERNANCE).read_bytes()))\n'))
                self.assertNotEqual(0, result.returncode)
                self.assertIn('OWNER_SETTINGS_REFUSED', result.stderr)

    def test_r17_repair_displays_all_changes_from_confirmed_settings(self):
        self.apply('budgeting.accounting_enabled=true', 'budgeting.enforcement_enabled=true')
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = False
        value['budgeting']['enforcement_enabled'] = False
        self.forge_config_event(value)
        result = self.cli('set', '--instruction', 'limit', '--set', 'budgeting.max_tokens_per_ticket=123', '--json')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        changes = {c['path']: c for c in report['confirmation_changes']}
        for name in ('accounting_enabled', 'enforcement_enabled'):
            self.assertEqual({'path': 'budgeting.' + name, 'before': True, 'after': False}, changes['budgeting.' + name])
        confirmed = self.confirm(report['digest'])
        self.assertEqual(0, confirmed.returncode, confirmed.stdout + confirmed.stderr)
        self.assertIn('"accounting_enabled"', confirmed.stdout)
        self.assertIn('budgeting.accounting_enabled', confirmed.stdout)

    def test_r17_no_change_repair_is_explicit(self):
        self.apply('budgeting.accounting_enabled=true')
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = False
        self.forge_config_event(value)
        result = self.cli('set', '--instruction', 'repair', '--set', 'budgeting.accounting_enabled=false', '--json')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn({'path': 'budgeting.accounting_enabled', 'before': True, 'after': False},
                      json.loads(result.stdout)['confirmation_changes'])
        confirmed = self.confirm(json.loads(result.stdout)['digest'])
        self.assertEqual(0, confirmed.returncode, confirmed.stdout + confirmed.stderr)
        self.refresh()
        reconfirmed = self.confirm(self.pending('budgeting.accounting_enabled=false'))
        self.assertEqual(0, reconfirmed.returncode, reconfirmed.stdout + reconfirmed.stderr)
        self.assertIn('No protected value changes; explicitly reconfirming all settings below.', reconfirmed.stdout)
        self.assertIn('"accounting_enabled": false', reconfirmed.stdout)

    def test_r17_unavailable_profile_defaults_load_protected_work_refuses(self):
        for error in ('admission.ValidationError("OWNER_STORAGE_UNAVAILABLE: profile unavailable")',
                      'KeyError("getpwuid(): uid not found")'):
            with self.subTest(error=error):
                shown = self.cli('show', '--json', owner_error=error)
                self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
                self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])
                checked = self.script('.agentic/scripts/workflow.py', '--root', self.root, 'validate-config', owner_error=error)
                self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
                self.assertEqual('ACCEPTED', json.loads(checked.stdout)['operating']['status'])
                result = self.cli('set', '--instruction', 'limit', '--set', 'budgeting.max_tokens_per_ticket=123',
                                  '--json', owner_error=error)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn('OWNER_STORAGE_UNAVAILABLE', result.stdout)
        self.assertFalse((self.fixture.base / 'protected').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows profile junction')
    def test_r17_profile_junction_defaults_load_and_protected_work_refuses(self):
        import _winapi
        target = self.fixture.base / 'real-profile'
        target.mkdir()
        link = self.fixture.base / 'profile-junction'
        _winapi.CreateJunction(str(target), str(link))
        self.addCleanup(link.rmdir)
        shown = self.cli('show', '--json', owner_home=link)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('ACCEPTED', json.loads(shown.stdout)['status'])
        checked = self.script('.agentic/scripts/workflow.py', '--root', self.root, 'validate-config', owner_home=link)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        self.assertEqual('ACCEPTED', json.loads(checked.stdout)['operating']['status'])
        result = self.cli('set', '--instruction', 'enable', '--set', 'budgeting.accounting_enabled=true',
                          '--json', owner_home=link)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_STORAGE_UNAVAILABLE', result.stdout)
        self.assertFalse(any(target.iterdir()))

    def test_r17_no_git_defaults_load_protected_work_refuses(self):
        shutil.rmtree(self.root / '.git')
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        checked = self.script('.agentic/scripts/workflow.py', '--root', self.root, 'validate-config')
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        self.assertEqual('ACCEPTED', json.loads(checked.stdout)['operating']['status'])
        result = self.cli('set', '--instruction', 'enable', '--set', 'budgeting.accounting_enabled=true', '--json')
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_GIT_IDENTITY_UNAVAILABLE', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_r17_same_identity_copy_cannot_consume_or_recover(self):
        digest = self.pending()
        copy = self.fixture.base / 'copy'
        shutil.copytree(self.root, copy)
        result = self.confirm(digest, root=copy)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('PROPOSAL_NOT_FOUND', result.stdout)
        self.assertFalse(self.calls.exists())
        result = self.confirm(digest, crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        raw = (self.root / op.JOURNAL).read_bytes()
        (copy / op.JOURNAL).write_bytes(raw)
        result = self.cli('recover', '--expected-journal-sha256', op.sha256(raw), '--json', root=copy)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        result = self.cli('recover', '--expected-journal-sha256', op.sha256(raw), '--json')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_r17_older_worktree_reports_mismatch_and_allows_unrelated_set(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'defaults')
        older = self.fixture.base / 'older'
        self.git('worktree', 'add', '-q', '--detach', str(older))
        self.protect_git()
        self.apply('budgeting.accounting_enabled=true')
        identity = self.script('', code=f'print(admission.git_common_identity(Path({str(older)!r})))\n')
        self.assertEqual(0, identity.returncode, identity.stdout + identity.stderr)
        shown = self.cli('show', '--json', root=older)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(shown.stdout)['status'])
        self.assertIn('latest confirmation for this clone', shown.stdout)
        refused = self.script('', code=(
            'from agentic import operating as op\n'
            f'root=Path({str(older)!r})\n'
            'op.read_operating(root,op.load_yaml((root/op.GOVERNANCE).read_bytes()))\n'))
        self.assertNotEqual(0, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn('OWNER_SETTINGS_REFUSED', refused.stderr)
        result = self.cli('set', '--instruction', 'unrelated', '--set', 'streams.count=2', '--json', root=older)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)['budget_admission'])
        epics = self.fixture.base / 'epics.json'
        epics.write_text(json.dumps(dict(schema_version=1, epics=[dict(id='FIX-1', title='Component', scope=['src/'])])))
        recommended = self.cli('recommend', '--epics', str(epics), '--json', root=older)
        self.assertEqual(0, recommended.returncode, recommended.stdout + recommended.stderr)
        accepted = self.cli('set', '--instruction', 'accept routes', '--recommendation',
                            json.loads(recommended.stdout)['id'], '--accept', 'all', '--json', root=older)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertFalse(json.loads(accepted.stdout)['budget_admission'])
        interrupted = self.cli('set', '--instruction', 'unrelated recovery', '--set', 'streams.count=3',
                               '--json', root=older, crash='after_journal')
        self.assertEqual(71, interrupted.returncode, interrupted.stdout + interrupted.stderr)
        raw = (older / op.JOURNAL).read_bytes()
        recovered = self.cli('recover', '--expected-journal-sha256', op.sha256(raw), '--json', root=older)
        self.assertEqual(0, recovered.returncode, recovered.stdout + recovered.stderr)
        self.assertFalse(json.loads(recovered.stdout)['budget_admission'])

    def test_r17_pending_show_reports_pending(self):
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = True
        self.forge_config_event(value)
        result = self.cli('set', '--instruction', 'confirm', '--set', 'budgeting.accounting_enabled=true', '--json')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('PENDING_OWNER_CONFIRMATION', json.loads(shown.stdout)['status'])
        expired = self.cli('show', '--json', clock=json.loads(result.stdout)['expires_at'])
        self.assertEqual(0, expired.returncode, expired.stdout + expired.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(expired.stdout)['status'])

    def test_r17_second_clone_cannot_claim_first_clones_confirmation(self):
        self.apply('budgeting.accounting_enabled=true')
        self.git('add', '-A')
        self.git('commit', '-qm', 'confirmed settings')
        clone = self.fixture.base / 'clone'
        self.git('clone', '-q', '--no-local', str(self.root), str(clone))
        shown = self.cli('show', '--json', root=clone)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(shown.stdout)['status'])
        self.assertFalse(json.loads(shown.stdout)['budget_admission'])
        pending = self.cli('set', '--instruction', 'new clone', '--set', 'budgeting.accounting_enabled=true',
                           '--json', root=clone)
        self.assertEqual(3, pending.returncode, pending.stdout + pending.stderr)
        result = self.confirm(json.loads(pending.stdout)['digest'], root=clone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json').stdout)['status'])

    def test_r17_git_environment_cannot_redirect_identity(self):
        self.apply('budgeting.accounting_enabled=true')
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = False
        self.forge_config_event(value)
        other = self.fixture.base / 'other'
        self.git('init', '-q', str(other))
        env = {**self.env, 'GIT_DIR': str(other / '.git'), 'GIT_COMMON_DIR': str(other / '.git'),
               'GIT_WORK_TREE': str(other), 'GIT_CONFIG_COUNT': '1',
               'GIT_CONFIG_KEY_0': 'core.worktree', 'GIT_CONFIG_VALUE_0': str(other)}
        shown = self.cli('show', '--json', env=env)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(shown.stdout)['status'])
        self.assertFalse(json.loads(shown.stdout)['budget_admission'])

    def test_r17_worker_created_gitfile_target_is_refused(self):
        self.git('config', 'core.autocrlf', 'false')
        self.git('add', '-A')
        self.git('commit', '-qm', 'defaults')
        checkout = self.fixture.base / 'worker-checkout'
        self.git('worktree', 'add', '-q', str(checkout), '-b', 'worker')
        # A missing backlink is unconditionally refused, even without sandbox
        # principals. Avoid machine-specific owner ACLs and checkout EOL changes.
        admin = Path((checkout / '.git').read_text().strip()[8:])
        (admin / 'gitdir').write_text(str(self.root / '.git'))
        for relative in ('.agentic/lib/agentic/owner_admission.py',):
            self.assertEqual((self.root / relative).read_bytes(), (checkout / relative).read_bytes())
        result = self.cli('set', '--instruction', 'enable', '--set', 'budgeting.accounting_enabled=true',
                          '--json', root=checkout)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_GIT_IDENTITY_UNAVAILABLE', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_r17_changed_project_id_in_same_clone_is_binding_mismatch(self):
        digest = self.pending()
        self.gov['project']['id'] = str(__import__('uuid').uuid4())
        self.save_governance()
        self.refused(digest, 'PROPOSAL_BINDING_MISMATCH')

    def test_r17_profileless_edit_to_defaults_cannot_authorize_protected_work(self):
        self.apply('budgeting.accounting_enabled=true')
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = False
        self.forge_config_event(value)
        result = self.cli('set', '--instruction', 'disable', '--set', 'budgeting.accounting_enabled=false',
                          '--json', owner_error='admission.OwnerProfileUnavailable("OWNER_STORAGE_UNAVAILABLE: no profile")')
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_STORAGE_UNAVAILABLE', result.stdout)


class OwnerLocationTests(unittest.TestCase):
    def test_os_location_ignores_environment_without_creating_storage(self):
        library = str(bootstrap.ROOT / '.agentic/lib')
        code = ('import sys,os\nfrom pathlib import Path\n'
                f'sys.path.insert(0,{library!r})\n'
                'from agentic.owner_admission import _owner_state_home\n'
                'from agentic.owner_admission import OwnerProfileUnavailable\n'
                'try: print(_owner_state_home())\n'
                'except OwnerProfileUnavailable: print("TOKEN_PROFILE_UNAVAILABLE")\n')
        baseline = subprocess.run([sys.executable, '-B', '-I', '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, baseline.returncode, baseline.stdout + baseline.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'AppData/Local').mkdir(parents=True)
            env = {**os.environ, **{k:temporary for k in ('AWF_STATE_HOME','HOME','USERPROFILE','LOCALAPPDATA')}}
            result = subprocess.run([sys.executable, '-B', '-I', '-c', code], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(baseline.stdout, result.stdout)


if __name__ == '__main__':
    unittest.main()
