"""Real installed workflow subprocesses; terminal and gh doubles stay test-only."""
import json
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
        base = Path(temporary.name)
        self.fixture = SimpleNamespace(base=base, dest=base / 'project')
        self.root = self.fixture.dest
        shutil.copytree(self.template, self.root)
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
            'import json,sys\nfrom pathlib import Path\n'
            f'p=Path({str(self.identity)!r}); calls=Path({str(self.calls)!r})\n'
            'with calls.open("a") as s: s.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'v=json.loads(p.read_text())\n'
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
        self.env = {k: v for k, v in os.environ.items() if k not in (*TOKENS, 'GH_HOST')}
        self.env.update(AWF_STATE_HOME=str(self.fixture.base / 'protected'), PATH=str(self.bin))
        if os.name == 'nt':
            self.env['PATH'] += os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
        self.before = (self.root / op.CONFIG).read_bytes()
        self.audit_before = self.events()

    def save_governance(self):
        self.govpath.write_text(json.dumps(self.gov), encoding='utf-8')

    def events(self):
        return sorted(p.read_bytes() for p in (self.root / op.STATE / 'changes').glob('*.json'))

    def cli(self, *args, tty=False, answer='', env=None, clock=None, root=None, project_cwd=False):
        root = root or self.root
        entry = root / '.agentic/scripts/workflow.py'
        command = [sys.executable, '-B', '-I', str(entry), '--root', str(root), 'operating', *args]
        if tty or clock:
            # Exercise the actual script in a fresh process. Only stream identity
            # and time are fixture facts; production has no test mode or flag.
            wrapper = ('import sys,io,runpy\n'
                       f'sys.path.insert(0,{str(root / ".agentic/lib")!r})\n')
            if tty in (True, 'stdin'):
                wrapper += ('class Terminal(io.StringIO):\n def isatty(self): return True\n'
                            f'sys.stdin=Terminal({answer!r})\n')
            if tty in (True, 'stdout'):
                wrapper += ('class Output:\n def write(self,s): return sys.__stdout__.write(s)\n'
                    ' def flush(self): return sys.__stdout__.flush()\n'
                    ' def isatty(self): return True\n'
                    ' def __getattr__(self,k): return getattr(sys.__stdout__,k)\n'
                    'sys.stdout=Output()\n')
            if clock:
                wrapper += f'import agentic.owner_admission as admission\nadmission.now_text=lambda: {clock!r}\n'
            wrapper += f'sys.argv={command[3:]!r}\nrunpy.run_path({str(entry)!r},run_name="__main__")\n'
            command = [sys.executable, '-B', '-I', '-c', wrapper]
        return subprocess.run(command, capture_output=True, text=True, env=env or self.env,
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
        executable = 'gh.cmd' if os.name == 'nt' else 'gh'
        shutil.copy2(self.bin / executable, self.root / executable)
        for path in ('', str(self.root)):
            with self.subTest(path=path):
                self.refused(digest, 'GITHUB_AUTH_UNAVAILABLE', project_cwd=True,
                             env={**self.env, 'PATH': path})
        self.assertFalse(self.calls.exists())

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
        result = self.confirm(digest, root=foreign)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('PROPOSAL_BINDING_MISMATCH', result.stdout)
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


if __name__ == '__main__':
    unittest.main()
