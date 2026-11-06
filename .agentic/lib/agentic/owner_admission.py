"""Interactive stored-GitHub-login admission for exact operating proposals.

No chat assertion authenticates an owner. Protected host state is outside worker
worktrees. This boundary cannot resist a process with the owner's full rights
and an interactive terminal of its own. Digests bind requests, not identities.
"""
from contextlib import contextmanager
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
from .budget_enrollment import ProtectedDatabase, require
from .budgeting import project_ledger_path
from .canonical import canonical, fingerprint, load_yaml, loads, now_text, sha256, timestamp

TOKEN_VARIABLES = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')
TTL_SECONDS = 15 * 60
DDL = '''
CREATE TABLE IF NOT EXISTS owner_proposals(digest TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner_confirmations(digest TEXT PRIMARY KEY, record TEXT NOT NULL);
'''
for _table in ('owner_proposals', 'owner_confirmations'):
    for _action in ('UPDATE', 'DELETE'):
        DDL += (f'CREATE TRIGGER IF NOT EXISTS {_table}_no_{_action} BEFORE {_action} ON {_table} '
                "BEGIN SELECT RAISE(ABORT,'immutable owner admission'); END;")


class PendingOwnerConfirmation(ValidationError):
    def __init__(self, report):
        self.report = report
        super().__init__('PENDING_OWNER_CONFIRMATION: owner must confirm in their own terminal')


class ProposalStore(ProtectedDatabase):
    """Durable proposals and single-use admissions, independent of budget usage."""
    @classmethod
    def for_project(cls, root, project_id):
        # Strict, read-only location resolution; this does not open a ledger or
        # enable accounting. Only this store's owner-admission tables are created.
        path = project_ledger_path(project_id, accounting_enabled=True, worktree_roots=(root,))
        return cls(path.with_name('owner-admission.sqlite3'), (root,))

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
        # One confirmation per complete operating transaction, across domains.
        require(db.execute('SELECT 1 FROM owner_confirmations WHERE digest=?', (digest,)).fetchone() is None,
                'PROPOSAL_ALREADY_CONSUMED: create a new request')
        row = db.execute('SELECT record FROM owner_proposals WHERE digest=?', (digest,)).fetchone()
        require(row is not None and fingerprint('owner-admission-proposal', loads(row['record'])) == digest,
                'PROPOSAL_BINDING_MISMATCH: protected proposal differs at consumption')
        _check_time(loads(row['record']))
        db.execute('INSERT INTO owner_confirmations VALUES (?,?)', (digest, canonical(audit).decode('utf-8')))


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
                repository={key: github[key] for key in ('host', 'repository_id', 'repository', 'base_branch')},
                project_location_hash=sha256(str(Path(root).resolve()).encode('utf-8')),
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
    store = ProposalStore.for_project(root, governance['project']['id'])
    digest = store.save(record)
    # Repository-relative invocation contains no host-local paths or credentials.
    command = ('workflow.py operating confirm --digest ' + digest)
    return dict(status='PENDING_OWNER_CONFIRMATION', digest=digest, request_id=record['request_id'],
                changes=record['changes'], expires_at=record['expires_at'], confirm_command=command,
                execution_authority=False,
                next_action='Owner must confirm the exact changes in their own terminal: ' + command)


def require_terminal(stdin, stdout):
    require(callable(getattr(stdin, 'isatty', None)) and stdin.isatty()
            and callable(getattr(stdout, 'isatty', None)) and stdout.isatty(),
            'INTERACTIVE_TERMINAL_REQUIRED: owner must run confirm in their own terminal')


def _require_stored_login_environment():
    require(not any(key in os.environ for key in TOKEN_VARIABLES),
            'TOKEN_ENVIRONMENT_REFUSED: unset GH_TOKEN, GITHUB_TOKEN, GH_ENTERPRISE_TOKEN and GITHUB_ENTERPRISE_TOKEN')
    require(os.environ.get('GH_HOST', 'github.com') == 'github.com',
            'GITHUB_HOST_REFUSED: GH_HOST must be absent or github.com')


def _github_executable(root):
    """Resolve explicit host PATH entries, never a worker's current directory."""
    from .budgeting import protected_state_roots
    from .path_containment import is_within
    excluded = protected_state_roots(root)
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        path = Path(directory)
        if not path.is_absolute():
            continue
        # A qualified name prevents Windows' implicit current-directory search.
        executable = shutil.which(str(path / 'gh'))
        if executable is not None:
            resolved = Path(executable).resolve()
            if not any(is_within(resolved, worktree) for worktree in excluded):
                return str(resolved)
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
        status = subprocess.run([executable, 'auth', 'status', '--hostname', 'github.com', '--active'],
                                capture_output=True, text=True, timeout=30, check=False)
        status_text = status.stdout + '\n' + status.stderr
        account = re.search(r'Logged in to github\.com account ([A-Za-z0-9-]+)(?:\s|$)', status_text)
        require(status.returncode == 0 and account is not None
                and re.search(r'Active account:\s*true\b', status_text) is not None,
                'GITHUB_AUTH_UNAVAILABLE: stored active github.com login was not observed')
        identity = subprocess.run([executable, 'api', '--hostname', 'github.com', '--method', 'GET', 'user'],
                                  capture_output=True, text=True, timeout=30, check=False)
        require(identity.returncode == 0, 'GITHUB_AUTH_UNAVAILABLE: GitHub user lookup failed')
        user = loads(identity.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise ValidationError('GITHUB_AUTH_UNAVAILABLE: stored GitHub login could not be verified') from exc
    require(isinstance(user, dict) and type(user.get('id')) is int and user['id'] > 0
            and isinstance(user.get('login'), str) and re.fullmatch(r'[A-Za-z0-9-]{1,39}', user['login'])
            and user.get('type') == 'User' and user['id'] in owners,
            'OWNER_IDENTITY_REFUSED: require a User with numeric id in merge_gate.trusted_owner_ids')
    require(account.group(1).casefold() == user['login'].casefold(),
            'OWNER_IDENTITY_REFUSED: active stored login differs from GitHub user response')
    return {key: user[key] for key in ('id', 'login', 'type')}


class _ConfirmedOwner:
    """Trusted-process admission, constructed only by interactive confirm."""
    def __init__(self, root, store, digest, record, identity):
        self.root, self.store, self.digest = root, store, digest
        self.record, self.identity = deepcopy(record), deepcopy(identity)

    def admit_operating_change(self, root, governance, snapshot, instruction, intent, changes,
                               *, validate_admission):
        """Rebind under operating's lock; validate full bytes before consumption."""
        require(Path(root).resolve() == Path(self.root).resolve(),
                'PROPOSAL_BINDING_MISMATCH: project location differs')
        record = self.store.load(self.digest)
        require(record == self.record, 'PROPOSAL_BINDING_MISMATCH: protected proposal differs')
        _check_time(record)
        require(record['binding'] == accepted_binding(self.root, governance, snapshot)
                and record['instruction'] == instruction and canonical(record['intent']) == canonical(intent)
                and canonical(record['changes']) == canonical(changes),
                'PROPOSAL_BINDING_MISMATCH: project, repository, governance, prior settings or exact changes differ')
        require(self.identity['type'] == 'User' and self.identity['id'] in governance['merge_gate']['trusted_owner_ids'],
                'OWNER_IDENTITY_REFUSED: owner is no longer trusted')
        audit = dict(actor_id=self.identity['id'], login=self.identity['login'], type=self.identity['type'],
                     digest=self.digest, request_id=self.record['request_id'], confirmed_at=now_text())
        validate_admission(deepcopy(audit))
        with self.store.transaction() as db:
            self.store.consume(self.digest, audit, db)
        return audit


def confirm(root, governance, digest, *, stdin=None, stdout=None):
    """Only interactive owner entry point; injectable streams are not CLI options."""
    from .operating import read_operating, set_operating
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    require_terminal(stdin, stdout)
    _require_stored_login_environment()
    require(bool(governance['merge_gate']['trusted_owner_ids']),
            'OWNER_GATE_NOT_CONFIGURED: set merge_gate.trusted_owner_ids through reviewed .agentic/PROJECT_CONFIG.yaml')
    store = ProposalStore.for_project(root, governance['project']['id'])
    record = store.load(digest)
    _check_time(record)
    require(record['binding'] == accepted_binding(root, governance, read_operating(root, governance)),
            'PROPOSAL_BINDING_MISMATCH: project, repository, governance or prior settings differ')
    identity = observe_github_owner(governance, root)
    stdout.write('Exact effective changes:\n' + json.dumps(record['changes'], sort_keys=True, indent=2) + '\n')
    stdout.write('Digest: ' + digest + '\nType the first 8 digest characters to confirm: ')
    stdout.flush()
    require(stdin.readline(128).rstrip('\r\n') == digest[:8],
            'DIGEST_CONFIRMATION_REFUSED: settings unchanged; type the exact first 8 digest characters')
    host = _ConfirmedOwner(root, store, digest, record, identity)
    return set_operating(root, governance, record['instruction'], owner_admission=host, **record['intent'])
