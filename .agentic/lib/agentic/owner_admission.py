"""Interactive stored-GitHub-login admission for exact operating proposals.

No chat assertion authenticates an owner. Protected host state is outside worker
worktrees. A process with the owner's full rights can still get around this.
Digests bind requests, not identities.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid

from . import ValidationError
from .budget_enrollment import EnrollmentRegistry, ProtectedDatabase, require
from .budgeting import fresh_settings, project_ledger_path
from .canonical import canonical, fingerprint, load_yaml, loads, now_text, sha256, timestamp
from .child_process import child_env, isolated_git_env

TOKEN_VARIABLES = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')
TTL_SECONDS = 15 * 60
# Extend this inventory when another operating domain requires owner admission.
PROTECTED_DOMAINS = (('budgeting', fresh_settings),)
DDL = '''
CREATE TABLE IF NOT EXISTS owner_proposals(digest TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner_confirmations(digest TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner_transactions(digest TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, journal_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner_applied(digest TEXT PRIMARY KEY);
'''
for _table in ('owner_proposals', 'owner_confirmations', 'owner_transactions', 'owner_applied'):
    for _action in ('UPDATE', 'DELETE'):
        DDL += (f'CREATE TRIGGER IF NOT EXISTS {_table}_no_{_action} BEFORE {_action} ON {_table} '
                "BEGIN SELECT RAISE(ABORT,'immutable owner admission'); END;")


class PendingOwnerConfirmation(ValidationError):
    def __init__(self, report):
        self.report = report
        super().__init__('PENDING_OWNER_CONFIRMATION: owner must confirm in their own terminal')


class OwnerProfileUnavailable(ValidationError):
    """This token has no registered OS profile, hence no owner store."""


def _owner_state_home():
    """OS account location, never a caller environment/configuration selection."""
    if os.name != 'nt':
        import pwd
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local' / 'state'
        except KeyError as exc:
            raise OwnerProfileUnavailable('OWNER_STORAGE_UNAVAILABLE: token has no password-database home') from exc
    import ctypes
    from ctypes import wintypes
    import winreg
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    advapi.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    token, size, sid = wintypes.HANDLE(), wintypes.DWORD(), ctypes.c_void_p()
    message = 'OWNER_STORAGE_UNAVAILABLE: cannot resolve token account ProfileList; repair the OS account profile'
    require(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)), message)
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        require(size.value > 0, message)
        info = ctypes.create_string_buffer(size.value)
        require(advapi.GetTokenInformation(token, 1, info, size, ctypes.byref(size)), message)
        require(advapi.ConvertSidToStringSidW(ctypes.c_void_p.from_buffer(info), ctypes.byref(sid)), message)
        account = ctypes.wstring_at(sid)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                '\\'.join(('SOFTWARE', 'Microsoft', 'Windows NT', 'CurrentVersion', 'ProfileList', account)),
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            profile, _kind = winreg.QueryValueEx(key, 'ProfileImagePath')
        # Registry expansion must not consult this process's environment either.
        windows = ctypes.create_unicode_buffer(32768)
        kernel.GetWindowsDirectoryW.argtypes = (wintypes.LPWSTR, wintypes.UINT)
        require(kernel.GetWindowsDirectoryW(windows, len(windows)), message)
        profile = re.sub('%SystemDrive%', lambda _m: Path(windows.value).anchor.rstrip('\\'), profile, flags=re.I)
        require('%' not in profile and Path(profile).is_absolute(), message)
        return Path(profile) / 'AppData/Local'
    except FileNotFoundError as exc:
        raise OwnerProfileUnavailable(message) from exc
    except OSError as exc:
        raise ValidationError(message) from exc
    finally:
        if sid.value:
            kernel.LocalFree(sid)
        kernel.CloseHandle(token)


def protected_settings(value):
    return {name: fingerprint('owner-protected-' + name.replace('_', '-'), value[name])
            for name, _defaults in PROTECTED_DOMAINS if name in value}


class OwnerConfirmationRequired(ValidationError):
    """Budget admission refused; read-only diagnostics may report raw choices."""

    status = 'OWNER_CONFIRMATION_REQUIRED'


def _git_metadata_identity(root):
    """Read clone identity without consulting or executing Git/gh at load.

    Ordinary directories rely on the host metadata boundary. Linked checkouts
    additionally require registered backlinks outside sandbox write authority.
    The owner's own metadata rights are deliberately outside this boundary.
    """
    from .path_containment import is_reparse_point
    root = Path(root).resolve(strict=True)
    marker = root / '.git'
    message = ('OWNER_GIT_IDENTITY_UNAVAILABLE: owner confirmations require this clone\'s real Git common '
               'directory; restore protected Git metadata, not a worker-created gitfile target')
    try:
        require(not is_reparse_point(marker) and marker.exists(), message)
        if marker.is_dir():
            require(not (marker / 'commondir').exists(), message)
            common = marker.resolve(strict=True)
        else:
            raw = marker.read_text(encoding='utf-8').strip()
            require(raw.startswith('gitdir: ') and '\n' not in raw, message)
            admin = (root / raw[8:]).resolve(strict=True)
            relative = (admin / 'commondir').read_text(encoding='utf-8').strip()
            require(relative and '\n' not in relative, message)
            common = (admin / relative).resolve(strict=True)
            require(admin.parent == common / 'worktrees'
                    and Path((admin / 'gitdir').read_text(encoding='utf-8').strip()).resolve(strict=True) == marker, message)
            require(all(not is_reparse_point(member) and not _git_directory_writable(member)
                        for member in (common, common / 'worktrees', admin, admin / 'commondir', admin / 'gitdir')), message)
        require(common.is_dir() and not any(is_reparse_point(p) for p in (common, *common.parents)), message)
        return sha256(os.path.normcase(str(common)).encode('utf-8'))
    except (OSError, UnicodeError) as exc:
        raise ValidationError(message) from exc


def git_common_identity(root):
    """Admission checks the native Git installation; loads only read metadata."""
    from .budgeting import protected_state_roots
    from .path_containment import is_within
    identity = _git_metadata_identity(root)
    executable = _git_executable()
    message = 'OWNER_GIT_IDENTITY_UNAVAILABLE: a protected native git executable is required'
    try:
        require(executable is not None and Path(executable).is_absolute(), message)
        path = Path(executable).resolve(strict=True)
        require(not any(is_within(path, p) for p in protected_state_roots(root))
                and _native_executable(path) and not _worker_writable(path), message)
    except OSError as exc:
        raise ValidationError(message) from exc
    return identity


def _git_executable():
    """Prefer the OS installation, independent of a caller's PATH/GIT_* values."""
    if os.name == 'nt':
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\GitForWindows',
                                    0, winreg.KEY_READ | view) as key:
                    installed = Path(winreg.QueryValueEx(key, 'InstallPath')[0]) / 'cmd/git.exe'
                if installed.is_file():
                    return str(installed)
            except OSError:
                pass
        return shutil.which('git.exe')
    return shutil.which('git', path=os.defpath)


def _git_directory_writable(path):
    """Only sandbox principals can disqualify metadata, never owner rights."""
    return os.name == 'nt' and (bool(_sandbox_group_rights(path) & (2 | 4 | 0x40 | 0x10000 | 0x40000 | 0x80000))
                                or _worker_writable(path))


def _latest_confirmation(db, governance):
    # The database is already clone-bound. Editable project/repository values
    # must never filter out a confirmation; project id is a cross-check below.
    row = db.execute('SELECT c.digest,c.record,p.record FROM owner_confirmations c '
                     'JOIN owner_proposals p USING(digest) ORDER BY c.rowid DESC LIMIT 1').fetchone()
    if row is None:
        return None
    return row[0], {**loads(row[1]), 'binding': loads(row[2])['binding']}


def validate_protected_settings(root, governance, value):
    """Read-only load gate. Project audit/journal bytes never establish proof.

    Defaults/absent legacy domains load freely only without a confirmation.
    This is a host isolation boundary, not protection from the full-rights owner.
    """
    needed = {name: digest for name, digest in protected_settings(value).items()
              if canonical(value[name]) != canonical(dict(PROTECTED_DOMAINS)[name]())}
    message = ('OWNER_SETTINGS_REFUSED: settings differ from the latest confirmation for this clone '
               'on this machine account. Restore the last audited operating bytes if externally edited, then use '
               'operating set with explicit budgeting choices and obtain fresh owner confirmation; preserve audit history.')
    try:
        store = ProposalStore.for_project(root, governance['project']['id'])
    except (ValidationError, OSError, KeyError) as exc:
        if not needed:
            return  # No resolvable store means no record to compare.
        raise OwnerConfirmationRequired(message + ' ' + str(exc)) from exc
    store.validate_path()
    if not store.path.is_file():
        if needed:
            raise OwnerConfirmationRequired(message)
        return
    try:
        with closing(sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True)) as db:
            latest = _latest_confirmation(db, governance)
            if latest is None:
                if needed:
                    raise OwnerConfirmationRequired(message)
                return
            digest, confirmation = latest
            if not (db.execute('SELECT 1 FROM owner_applied WHERE digest=?', (digest,)).fetchone() is not None
                    and confirmation['binding']['project_id'] == governance['project']['id']
                    and confirmation['protected_settings'] == protected_settings(value)):
                raise OwnerConfirmationRequired(message)
    except OwnerConfirmationRequired:
        raise
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(message) from exc


def protected_diagnostic(root, governance, value):
    """Truthful observation, never an execution snapshot or owner admission."""
    try:
        validate_protected_settings(root, governance, value)
        return dict(status='ACCEPTED', budget_admission=True)
    except OwnerConfirmationRequired as exc:
        status = exc.status
        try:
            store = ProposalStore.for_project(root, governance['project']['id'])
            if store.path.is_file():
                with closing(sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True)) as db:
                    for row in db.execute('SELECT p.record FROM owner_proposals p LEFT JOIN owner_confirmations c USING(digest) '
                                          'WHERE c.digest IS NULL'):
                        record = loads(row[0])
                        try:
                            _check_time(record)
                        except ValidationError:
                            continue
                        if (record['binding']['project_id'] == governance['project']['id']
                                and record['binding']['prior_settings_hash'] == fingerprint('operating', value)):
                            status = 'PENDING_OWNER_CONFIRMATION'
                            break
        except (ValidationError, OSError, sqlite3.Error, KeyError):
            pass
        return dict(status=status, budget_admission=False, diagnostic=str(exc), execution_authority=False)


def register_transaction(root, governance, event, journal_raw):
    """Bind a consumed confirmation to its exact WAL before publication/apply."""
    store = ProposalStore.for_project(root, governance['project']['id'])
    digest = event['owner_admission']['digest']
    with store.transaction() as db:
        require(_latest_confirmation(db, governance)[0] == digest,
                'OWNER_RECOVERY_REFUSED: a later owner confirmation supersedes this transaction')
        row = db.execute('SELECT record FROM owner_confirmations WHERE digest=?', (digest,)).fetchone()
        require(row is not None and loads(row['record'])['audit'] == event['owner_admission'],
                'OWNER_RECOVERY_REFUSED: no matching consumed confirmation')
        proposal = db.execute('SELECT record FROM owner_proposals WHERE digest=?', (digest,)).fetchone()
        require(proposal is not None, 'OWNER_RECOVERY_REFUSED: protected proposal unavailable')
        _check_time(loads(proposal['record']))
        db.execute('INSERT INTO owner_transactions VALUES (?,?,?)', (digest, event['id'], sha256(journal_raw)))


def complete_transaction(root, governance, event):
    store = ProposalStore.for_project(root, governance['project']['id'])
    with store.transaction() as db:
        digest = event['owner_admission']['digest']
        require(_latest_confirmation(db, governance)[0] == digest,
                'OWNER_RECOVERY_REFUSED: a later owner confirmation supersedes this transaction')
        require(db.execute('SELECT 1 FROM owner_transactions WHERE digest=? AND event_id=?',
                           (digest, event['id'])).fetchone() is not None,
                'OWNER_RECOVERY_REFUSED: transaction binding unavailable')
        db.execute('INSERT OR IGNORE INTO owner_applied VALUES (?)', (digest,))


class ProposalStore(ProtectedDatabase):
    """Durable proposals and single-use admissions, independent of budget usage."""
    @classmethod
    def for_project(cls, root, project_id):
        identity = _git_metadata_identity(root)
        try:
            home = _owner_state_home()
        except KeyError as exc:
            raise OwnerProfileUnavailable('OWNER_STORAGE_UNAVAILABLE: token has no OS account profile') from exc
        require(home.is_absolute(), 'OWNER_STORAGE_UNAVAILABLE: OS account state home must be absolute')
        path = home / 'awf/owner-confirmations' / identity / 'owner-admission.sqlite3'
        store = cls(path, (root,))
        try:
            store.validate_path()
        except ValidationError as exc:
            raise ValidationError('OWNER_STORAGE_UNAVAILABLE: OS account profile overlaps a worktree or traverses '
                                  'a link/reparse point; move the checkout outside that profile state path '
                                  'or repair the OS profile; environment overrides cannot select owner storage') from exc
        return store

    @contextmanager
    def transaction(self, ddl=DDL):
        try:
            with super().transaction(ddl) as db:
                yield db
        except (sqlite3.Error, OSError) as exc:
            raise ValidationError('OWNER_STORAGE_UNAVAILABLE: restore protected controller storage') from exc

    def save(self, record):
        raw = canonical(record).decode('utf-8')
        require(len(raw.encode('utf-8')) <= 1024 * 1024, 'Owner proposal exceeds 1 MiB')
        digest = fingerprint('owner-admission-proposal', record)
        with self.transaction() as db:
            db.execute('INSERT INTO owner_proposals VALUES (?,?)', (digest, raw))
        return digest

    def load(self, digest):
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'PROPOSAL_NOT_FOUND: invalid digest')
        require(self.path.exists(), 'PROPOSAL_NOT_FOUND: no protected proposal')
        with self.transaction() as db:
            require(db.execute('SELECT 1 FROM owner_confirmations WHERE digest=?', (digest,)).fetchone() is None,
                    'PROPOSAL_ALREADY_CONSUMED: create a new request')
            row = db.execute('SELECT record FROM owner_proposals WHERE digest=?', (digest,)).fetchone()
            require(row is not None, 'PROPOSAL_NOT_FOUND: no matching protected proposal')
            require(len(row['record'].encode('utf-8')) <= 1024 * 1024, 'Owner proposal exceeds 1 MiB')
            record = loads(row['record'])
            require(fingerprint('owner-admission-proposal', record) == digest,
                    'PROPOSAL_BINDING_MISMATCH: proposal digest differs')
        return record

    def consume(self, digest, audit, db):
        # Same transaction as the registry observation: no partial consumption.
        require(db.execute('SELECT 1 FROM owner_confirmations WHERE digest=?', (digest,)).fetchone() is None,
                'PROPOSAL_ALREADY_CONSUMED: create a new request')
        row = db.execute('SELECT record FROM owner_proposals WHERE digest=?', (digest,)).fetchone()
        require(row is not None and fingerprint('owner-admission-proposal', loads(row['record'])) == digest,
                'PROPOSAL_BINDING_MISMATCH: protected proposal differs at consumption')
        _check_time(loads(row['record']))
        record = loads(row['record'])
        confirmation = dict(audit=audit, protected_settings=record['protected_settings'])
        db.execute('INSERT INTO owner_confirmations VALUES (?,?)', (digest, canonical(confirmation).decode('utf-8')))


def accepted_binding(root, governance, snapshot):
    """Recompute accepted governance and repository identity from actual bytes."""
    from .configuration import inspect_config
    from .contracts import Contracts
    from .installer import verify_installed
    from .operating import GOVERNANCE, MAX_BYTES, SCHEMAS, _bound_governance
    from .safeio import Tree
    try:
        verify_installed(root)
    except (ValidationError, OSError, ValueError) as exc:
        raise ValidationError('Trusted-host owner admission requires a verified runtime; reconcile installation integrity') from exc
    with Tree(root) as tree:
        _bound_governance(tree, governance)
        raw = tree.read(GOVERNANCE, maximum=MAX_BYTES)
        workflow = load_yaml(tree.read('.agentic/workflow.yaml', maximum=MAX_BYTES))
        instructions = (tree.read('PROJECT_INSTRUCTIONS.md', maximum=MAX_BYTES).decode('utf-8')
                        if tree.inspect('PROJECT_INSTRUCTIONS.md') is not None else None)
        report = inspect_config(governance, workflow, Contracts(SCHEMAS), instructions)
    require(report['status'] == 'ACCEPTED',
            'GOVERNANCE_NOT_ACCEPTED: reconcile .agentic/PROJECT_CONFIG.yaml before owner admission')
    github = governance['github']
    require(github['host'] == 'https://github.com', 'GITHUB_HOST_REFUSED: owner admission requires github.com')
    return dict(project_id=governance['project']['id'],
                git_common_identity=git_common_identity(root),
                repository={key: github[key] for key in ('host', 'repository_id', 'repository', 'base_branch')},
                governance_digest=report['policy_sha256'], governance_bytes_hash=sha256(raw),
                prior_settings_hash=snapshot.operating_hash)


def _check_time(record):
    now, issued, expires = timestamp(now_text()), timestamp(record['created_at']), timestamp(record['expires_at'])
    require(expires - issued == timedelta(seconds=TTL_SECONDS) and issued <= now < expires,
            'PROPOSAL_EXPIRED: create and confirm a fresh proposal within 15 minutes')


def propose(root, governance, snapshot, instruction, intent, changes):
    """Called only after the entire mixed operating change validates, under lock."""
    created = now_text()
    record = dict(format='awf-owner-proposal-1', request_id=str(uuid.uuid4()),
                  binding=accepted_binding(root, governance, snapshot),
                  instruction=instruction, intent=deepcopy(intent), changes=deepcopy(changes),
                  created_at=created,
                  expires_at=(timestamp(created) + timedelta(seconds=TTL_SECONDS)).isoformat().replace('+00:00', 'Z'))
    from .operating import _put
    produced = snapshot.config
    for change in changes:
        _put(produced, change['path'], deepcopy(change['after']))
    record['protected_settings'] = protected_settings(produced)
    record['protected_values'] = {name: deepcopy(produced[name]) for name, _ in PROTECTED_DOMAINS if name in produced}
    store = ProposalStore.for_project(root, governance['project']['id'])
    record.update(confirmation_delta(store, governance, produced))
    digest = store.save(record)
    # Repository-relative invocation contains no host-local paths or credentials.
    command = ('workflow.py operating confirm --digest ' + digest)
    return dict(status='PENDING_OWNER_CONFIRMATION', digest=digest, request_id=record['request_id'],
                changes=record['changes'], confirmation_changes=record['confirmation_changes'],
                protected_values=record['protected_values'], expires_at=record['expires_at'], confirm_command=command,
                execution_authority=False,
                next_action='Owner must confirm the exact changes in their own terminal: ' + command)


def confirmation_delta(store, governance, produced):
    """Compare with protected confirmed values, never worker-authored priors."""
    latest = None
    prior = {name: defaults() for name, defaults in PROTECTED_DOMAINS}
    if store.path.is_file():
        with closing(sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True)) as db:
            latest = _latest_confirmation(db, governance)
            if latest is not None:
                row = db.execute('SELECT record FROM owner_proposals WHERE digest=?', (latest[0],)).fetchone()
                prior = loads(row[0])['protected_values']
    changes = []
    for name, _ in PROTECTED_DOMAINS:
        for key in sorted(set(prior.get(name, {})) | set(produced.get(name, {}))):
            before, after = prior.get(name, {}).get(key), produced.get(name, {}).get(key)
            if before != after:
                changes.append(dict(path=name + '.' + key, before=before, after=after))
    return dict(confirmation_prior_digest=latest[0] if latest else None, confirmation_changes=changes)


def _windows_console(stream):
    """isatty also accepts NUL on Windows; require an actual console handle."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetConsoleMode.restype = wintypes.BOOL
        mode = wintypes.DWORD()
        return bool(kernel.GetConsoleMode(msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(mode)))
    except (OSError, ValueError, AttributeError):
        return False


def require_terminal(stdin, stdout):
    def terminal(stream):
        try:
            return (_windows_console(stream) if os.name == 'nt' else
                    callable(getattr(stream, 'isatty', None)) and stream.isatty())
        except (OSError, ValueError):
            return False
    require(terminal(stdin) and terminal(stdout),
            'INTERACTIVE_TERMINAL_REQUIRED: owner must run confirm in their own terminal')


def _require_stored_login_environment():
    require(not any(key in os.environ for key in TOKEN_VARIABLES),
            'TOKEN_ENVIRONMENT_REFUSED: unset GH_TOKEN, GITHUB_TOKEN, GH_ENTERPRISE_TOKEN and GITHUB_ENTERPRISE_TOKEN')
    require(os.environ.get('GH_HOST', 'github.com') == 'github.com',
            'GITHUB_HOST_REFUSED: GH_HOST must be absent or github.com')


def _sandbox_group_rights(path):
    """Read sandbox group/capability grants without using the current token.

    An absent Codex group contributes no rights. Capability SIDs named by the
    DACL are also sandbox principals, including on hosts without that group.
    """
    import ctypes
    from ctypes import wintypes
    class TRUSTEE(ctypes.Structure):
        _fields_ = [('multiple', ctypes.c_void_p), ('operation', ctypes.c_int),
                    ('form', ctypes.c_int), ('type', ctypes.c_int), ('name', wintypes.LPWSTR)]
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p))
    api.GetEffectiveRightsFromAclW.argtypes = (ctypes.c_void_p, ctypes.POINTER(TRUSTEE),
                                             ctypes.POINTER(wintypes.DWORD))
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    api.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    acl, descriptor, rights = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.DWORD()
    require(api.GetNamedSecurityInfoW(str(path), 1, 4, None, None, ctypes.byref(acl), None,
            ctypes.byref(descriptor)) == 0, 'GITHUB_AUTH_UNAVAILABLE: cannot inspect gh ACL; restore read access to the native installation ACL')
    try:
        text = ctypes.c_void_p()
        require(api.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 4, ctypes.byref(text), None),
            'GITHUB_AUTH_UNAVAILABLE: cannot inspect sandbox capability ACL')
        try:
            capabilities = set(re.findall(r'S-1-15-3-(?:\d+-)*\d+', ctypes.wstring_at(text)))
        finally:
            kernel.LocalFree(text)
        value = 0
        for principal in ('CodexSandboxUsers', *sorted(capabilities)):
            sid = ctypes.c_void_p()
            try:
                if principal in capabilities:
                    require(api.ConvertStringSidToSidW(principal, ctypes.byref(sid)),
                            'GITHUB_AUTH_UNAVAILABLE: cannot inspect sandbox capability SID')
                trustee = TRUSTEE(None, 0, 0 if sid.value else 1, 2,
                                  ctypes.cast(sid, wintypes.LPWSTR) if sid.value else principal)
                rights.value = 0
                error = api.GetEffectiveRightsFromAclW(acl, ctypes.byref(trustee), ctypes.byref(rights))
                require(error in (0, 1332), 'GITHUB_AUTH_UNAVAILABLE: cannot inspect sandbox principal rights; repair the native installation ACL')
                value |= rights.value if error == 0 else 0
            finally:
                if sid.value:
                    kernel.LocalFree(sid)
        if value & 0x10000000:  # GENERIC_ALL
            value |= 0x1f01ff
        if value & 0x40000000:  # GENERIC_WRITE
            value |= 0x120116
        return value
    finally:
        kernel.LocalFree(descriptor)


def _worker_writable(path):
    """Check sandbox principals only; full-rights owner processes are out of scope.

    No sandbox principal on this host means no disqualifying grant. POSIX has
    no configured worker principal here. Never probe the current token's rights.
    """
    if os.name != 'nt':
        return False
    rights = {}
    def allowed(member, mask):
        if member not in rights:
            rights[member] = _sandbox_group_rights(member)
        return bool(rights[member] & mask)
    if (allowed(path, 2 | 4 | 0x40000 | 0x80000)
            or allowed(path.parent, 0x40000 | 0x80000)
            or allowed(path.parent, 2) and (allowed(path, 0x10000) or allowed(path.parent, 0x40))):
        return True
    for directory in path.parents:
        # ACL/ownership control can grant replacement rights on descendants.
        if allowed(directory, 0x40000 | 0x80000):
            return True
        if directory.parent == directory:
            continue
        # Renaming/removing a directory can use DELETE on it OR DELETE_CHILD
        # on its parent; installing a replacement also needs ADD_SUBDIRECTORY.
        if (allowed(directory, 0x10000) or allowed(directory.parent, 0x40)) and allowed(directory.parent, 4):
            return True
    return False


def _native_executable(path):
    """Admit executable binaries only, never batch files, shebangs or scripts."""
    if os.name == 'nt' and path.suffix.lower() != '.exe':
        return False
    with path.open('rb') as source:
        magic = source.read(4)
    return (magic[:2] == b'MZ' if os.name == 'nt' else magic == b'\x7fELF' or
            magic in (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
                      b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'))


def _github_executable(root):
    """Resolve PATH once, then validate that exact absolute binary or refuse."""
    from .budgeting import protected_state_roots
    from .path_containment import is_within
    directories = [d for d in os.environ.get('PATH', '').split(os.pathsep) if Path(d).is_absolute()]
    executable = None
    for directory in directories:
        # Qualified search also avoids Python/Windows implicit CWD lookup.
        # Python 3.11 which() does not append PATHEXT for a qualified Windows
        # path. Name .exe explicitly rather than depending on version behavior.
        executable = shutil.which(str(Path(directory) / ('gh.exe' if os.name == 'nt' else 'gh')))
        if executable is not None:
            break
    if executable is None:
        return None
    try:
        resolved = Path(executable).resolve(strict=True)
        if any(is_within(resolved, p) for p in protected_state_roots(root)):
            raise ValidationError('GITHUB_AUTH_UNAVAILABLE: gh is inside a worker/runtime tree; use a protected native installation')
        if not resolved.is_file() or not _native_executable(resolved):
            raise ValidationError('GITHUB_AUTH_UNAVAILABLE: gh must be a native executable binary')
        if _worker_writable(resolved):
            raise ValidationError('GITHUB_AUTH_UNAVAILABLE: sandbox principal can modify or replace gh; '
                                  'install a native gh outside worktrees and deny worker file writes and directory replacement rights')
        return str(resolved)
    except OSError:
        return None


def observe_github_owner(governance, root):
    """Read identity from gh's stored active login, never from caller text."""
    _require_stored_login_environment()
    owners = governance['merge_gate']['trusted_owner_ids']
    require(bool(owners),
            'OWNER_GATE_NOT_CONFIGURED: set merge_gate.trusted_owner_ids through reviewed .agentic/PROJECT_CONFIG.yaml')
    executable = _github_executable(root)
    require(executable is not None, 'GITHUB_AUTH_UNAVAILABLE: install gh and sign in with the owner account')
    try:
        executable_hash = sha256(Path(executable).read_bytes())
        status = subprocess.run([executable, 'auth', 'status', '--hostname', 'github.com', '--active'],
                                capture_output=True, text=True, timeout=30, check=False, env=child_env())
        status_text = status.stdout + '\n' + status.stderr
        account = re.search(r'Logged in to github\.com account ([A-Za-z0-9-]+)(?:\s|$)', status_text)
        require(status.returncode == 0 and account is not None
                and re.search(r'Active account:\s*true\b', status_text) is not None,
                'GITHUB_AUTH_UNAVAILABLE: stored active github.com login was not observed')
        require(sha256(Path(executable).read_bytes()) == executable_hash,
                'GITHUB_AUTH_UNAVAILABLE: gh changed during identity lookup')
        identity = subprocess.run([executable, 'api', '--hostname', 'github.com', '--method', 'GET', 'user'],
                                  capture_output=True, text=True, timeout=30, check=False, env=child_env())
        require(identity.returncode == 0, 'GITHUB_AUTH_UNAVAILABLE: GitHub user lookup failed')
        user = loads(identity.stdout)
        require(sha256(Path(executable).read_bytes()) == executable_hash,
                'GITHUB_AUTH_UNAVAILABLE: gh changed during identity lookup')
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        # Preserve only these fixed local refusals. Parser ValidationErrors can
        # contain response keys and must receive the same sanitization as OS errors.
        if isinstance(exc, ValidationError) and str(exc) in {
                'GITHUB_AUTH_UNAVAILABLE: stored active github.com login was not observed',
                'GITHUB_AUTH_UNAVAILABLE: gh changed during identity lookup',
                'GITHUB_AUTH_UNAVAILABLE: GitHub user lookup failed'}:
            raise
        raise ValidationError('GITHUB_AUTH_UNAVAILABLE: stored GitHub login could not be verified '
                              f'({type(exc).__name__})') from exc
    require(isinstance(user, dict) and type(user.get('id')) is int and user['id'] > 0
            and isinstance(user.get('login'), str) and re.fullmatch(r'[A-Za-z0-9-]{1,39}', user['login'])
            and user.get('type') == 'User' and user['id'] in owners,
            'OWNER_IDENTITY_REFUSED: require a User with numeric id in merge_gate.trusted_owner_ids')
    require(account.group(1).casefold() == user['login'].casefold(),
            'OWNER_IDENTITY_REFUSED: active stored login differs from GitHub user response')
    return {**{key: user[key] for key in ('id', 'login', 'type')},
            'executable_path': executable, 'executable_sha256': executable_hash}


class _ConfirmedOwner:
    """Trusted-process host implementing EnrollmentRegistry's unchanged protocol."""
    def __init__(self, root, store, digest, record, identity):
        self.root, self.store, self.digest = root, store, digest
        self.record, self.identity = deepcopy(record), deepcopy(identity)
        self.audit = None
        self._validated = None

    def validate_operating_change(self, governance, snapshot, instruction, intent, changes,
                                 prior_settings_hash, requested_changes):
        record = self.store.load(self.digest)
        require(record == self.record, 'PROPOSAL_BINDING_MISMATCH: protected proposal differs')
        _check_time(record)
        require(record['binding'] == accepted_binding(self.root, governance, snapshot)
                and record['instruction'] == instruction and canonical(record['intent']) == canonical(intent)
                and canonical(record['changes']) == canonical(changes),
                'PROPOSAL_BINDING_MISMATCH: project, repository, governance, prior settings or exact changes differ')
        from .operating import _put
        produced = snapshot.config
        for change in changes:
            _put(produced, change['path'], deepcopy(change['after']))
        delta = confirmation_delta(self.store, governance, produced)
        require(all(record[key] == value for key, value in delta.items()),
                'PROPOSAL_BINDING_MISMATCH: latest confirmation or displayed protected changes differ')
        require(self.identity['type'] == 'User' and self.identity['id'] in governance['merge_gate']['trusted_owner_ids'],
                'OWNER_IDENTITY_REFUSED: owner is no longer trusted')
        self._validated = (governance['project']['id'], prior_settings_hash, deepcopy(requested_changes))

    def observe_owner_direction(self, project_id, prior_settings_hash, changes):
        require(self._validated is not None and self._validated[:2] == (project_id, prior_settings_hash)
                and canonical(self._validated[2]) == canonical(changes),
                'PROPOSAL_BINDING_MISMATCH: owner observation requires the exact validated change')
        _check_time(self.record)
        audit = dict(actor_id=self.identity['id'], login=self.identity['login'], type=self.identity['type'],
                     digest=self.digest, request_id=self.record['request_id'], confirmed_at=now_text(),
                     executable_path=self.identity['executable_path'],
                     executable_sha256=self.identity['executable_sha256'])
        self.audit = audit
        self._observation = dict(observation_id='github-terminal:' + self.record['request_id'] + ':' + self.digest,
                    actor_id=str(self.identity['id']), project_id=project_id,
                    prior_settings_hash=prior_settings_hash, changes=deepcopy(changes))
        return deepcopy(self._observation)

    def consume_owner_direction(self, observed, db):
        require(self._validated is not None and observed == self._observation,
                'PROPOSAL_BINDING_MISMATCH: consumption differs from owner observation')
        self.store.consume(self.digest, self.audit, db)
        self._validated = None


def confirm(root, governance, digest, *, stdin=None, stdout=None):
    """Only interactive owner entry point; injectable streams are not CLI options."""
    from .operating import admission_snapshot, set_operating
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    require_terminal(stdin, stdout)
    _require_stored_login_environment()
    require(bool(governance['merge_gate']['trusted_owner_ids']),
            'OWNER_GATE_NOT_CONFIGURED: set merge_gate.trusted_owner_ids through reviewed .agentic/PROJECT_CONFIG.yaml')
    store = ProposalStore.for_project(root, governance['project']['id'])
    record = store.load(digest)
    _check_time(record)
    require(record['binding'] == accepted_binding(root, governance, admission_snapshot(root, governance)),
            'PROPOSAL_BINDING_MISMATCH: project, repository, governance or prior settings differ')
    identity = observe_github_owner(governance, root)
    stdout.write('Protected changes from last confirmation (fresh defaults if none):\n'
                 + json.dumps(record['confirmation_changes'], sort_keys=True, indent=2) + '\n')
    if not record['confirmation_changes']:
        stdout.write('No protected value changes; explicitly reconfirming all settings below.\n')
    stdout.write('Complete protected settings to confirm:\n' + json.dumps(record['protected_values'], sort_keys=True, indent=2) + '\n')
    stdout.write('Exact disk changes:\n' + json.dumps(record['changes'], sort_keys=True, indent=2) + '\n')
    stdout.write('Digest: ' + digest + '\nType the first 8 digest characters to confirm: ')
    stdout.flush()
    require(stdin.readline(128).rstrip('\r\n') == digest[:8],
            'DIGEST_CONFIRMATION_REFUSED: settings unchanged; type the exact first 8 digest characters')
    host = _ConfirmedOwner(root, store, digest, record, identity)
    registry = EnrollmentRegistry(store.path, host, (root,))
    return set_operating(root, governance, record['instruction'], budget_registry=registry, **record['intent'])


def validate_recovery(root, governance, before, after, event, journal_sha256):
    """Read consumed admission from protected storage; a journal is not authority.

    Recovery never authenticates afresh, consumes again or extends the proposal's
    lifetime. Confirmation must have occurred within its original validity window.
    """
    from .operating import _get, _put, validate_operating
    from .budgeting import settings_hash
    message = 'OWNER_RECOVERY_REFUSED: journal requires the exact consumed owner confirmation'
    audit = event.get('owner_admission')
    require(before is not None and isinstance(audit, dict), message)
    store = ProposalStore.for_project(root, governance['project']['id'])
    store.validate_path()
    require(store.path.is_file(), message)
    try:
        with closing(sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True)) as db:
            latest = _latest_confirmation(db, governance)
            require(latest is not None and latest[0] == audit['digest'],
                    message + '; a later owner confirmation supersedes earlier transactions')
            proposal = db.execute('SELECT record FROM owner_proposals WHERE digest=?', (audit['digest'],)).fetchone()
            confirmed = db.execute('SELECT record FROM owner_confirmations WHERE digest=?', (audit['digest'],)).fetchone()
            require(proposal is not None and confirmed is not None, message)
            record = loads(proposal[0])
            confirmation = loads(confirmed[0])
            require(confirmation['audit'] == audit
                    and confirmation['protected_settings'] == protected_settings(after)
                    and fingerprint('owner-admission-proposal', record) == audit['digest'], message)
            transaction = db.execute('SELECT event_id,journal_sha256 FROM owner_transactions WHERE digest=?',
                                     (audit['digest'],)).fetchone()
            require(transaction is not None and tuple(transaction) == (event['id'], journal_sha256), message)
            applied = db.execute('SELECT 1 FROM owner_applied WHERE digest=?', (audit['digest'],)).fetchone()
            observation = event.get('budget_owner_observation')
            require(isinstance(observation, dict), message)
            observed = db.execute('SELECT record FROM budget_owner_observations WHERE observation_id=?',
                                 (observation['observation_id'],)).fetchone()
            require(observed is not None and loads(observed[0]) == observation, message)
        if applied is not None:
            # Completed admissions permit cleanup only, even if a worker rewinds
            # every project-owned file to the old before state and audit prefix.
            from .operating import CONFIG, _events, _read, _json
            from .safeio import Tree
            with Tree(root) as tree:
                events = _events(tree)
                require(_read(tree, CONFIG) is not None
                        and canonical(load_yaml(_read(tree, CONFIG))) == canonical(after)
                        and events and events[-1] == event
                        and _read(tree, '.agentic-state/operating/changes/' + event['id'] + '.json') == _json(event), message)
        prior = validate_operating(before, governance)
        require(record['binding'] == accepted_binding(root, governance, prior)
                and record['request_id'] == audit['request_id']
                and record['instruction'] == event['instruction']
                and record['changes'] == event['changes']
                and audit['type'] == 'User' and audit['actor_id'] in governance['merge_gate']['trusted_owner_ids']
                and timestamp(record['created_at']) <= timestamp(audit['confirmed_at']) < timestamp(record['expires_at'])
                and timestamp(record['expires_at']) - timestamp(record['created_at']) == timedelta(seconds=TTL_SECONDS), message)
        require(observation['project_id'] == governance['project']['id']
                and observation['actor_id'] == str(audit['actor_id'])
                and observation['observation_id'] == 'github-terminal:' + record['request_id'] + ':' + audit['digest']
                and observation['prior_settings_hash'] == settings_hash(before['budgeting']), message)
        expected = deepcopy(before)
        for change in record['changes']:
            require(_get(before, change['path']) == change['before'], message)
            _put(expected, change['path'], deepcopy(change['after']))
        # accounting=false derives enforcement=false even when that second
        # field was not explicitly requested in the authenticated observation.
        from .budgeting import transition_settings
        require(transition_settings(before['budgeting'], observation['changes']) == after['budgeting'], message)
        require(canonical(expected) == canonical(after), message)
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(message) from exc
