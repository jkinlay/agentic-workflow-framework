"""Review regressions: real local remotes/processes; no external transports."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import source_only
import test_awf242_settings_second_look as fixtures
from agentic import ValidationError, operating as op
from agentic.child_process import isolated_git_env
from agentic.protected_settings import refresh_default_policy, require_budget_policy
from agentic.safeio import run_bounded


class RefreshReviewTests(unittest.TestCase):
    setUp = fixtures.SecondLookTests.setUp
    write = fixtures.SecondLookTests.write
    publish = fixtures.SecondLookTests.publish
    enabled = fixtures.SecondLookTests.enabled
    show = fixtures.SecondLookTests.show
    drop_origin_head = fixtures.SecondLookTests.drop_origin_head

    def git(self, *args):
        return subprocess.run([shutil.which('git'), '-C', str(self.root), *args],
            env=isolated_git_env(), check=True, capture_output=True,
            encoding='utf-8').stdout.rstrip('\n')

    def rename(self, old, new):
        before = self.git('rev-parse', 'refs/remotes/origin/' + old)
        self.publish(self.enabled())
        target = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/' + old, before)
        self.git('--git-dir=' + str(self.bare), 'update-ref', '-d', 'refs/heads/main')
        if old != 'main':
            self.git('--git-dir=' + str(self.bare), 'update-ref', '-d', 'refs/heads/' + old)
        self.git('--git-dir=' + str(self.bare), 'update-ref', 'refs/heads/' + new, target)
        self.git('--git-dir=' + str(self.bare), 'symbolic-ref', 'HEAD', 'refs/heads/' + new)
        self.write(self.value)
        return before, target

    def test_adapter_budget_refresh_filters_all_git_children_and_keeps_git_auth(self):
        from test_reference_controller_adapter import _base_config
        adapter = runpy.run_path(str(Path(op.__file__).resolve().parents[2] /
                                   'adapters/reference_controller_adapter.py'))
        config = _base_config(self.base)
        config['jira'].update(token_env='CUSTOM_AUTH', email_env='ACCOUNT_EMAIL',
                              credential_type='unscoped_api_token')
        adapters = adapter['build_adapters'](config)
        self.drop_origin_head()
        snapshot = op.read_operating(self.root, self.gov)
        dummy = dict.fromkeys(('CUSTOM_AUTH', 'ACCOUNT_EMAIL', 'AWF_JIRA_TOKEN',
            'JIRA_OTHER', 'DATABASE_PASSWORD', 'OPENAI_API_KEY'), 'dummy-review-value')
        home = self.base / 'home'
        home.mkdir()
        (home / '.gitconfig').write_text('[credential]\n\thelper = review-helper\n', encoding='utf-8')
        keep = dict(HOME=str(home), USERPROFILE=str(home), PATH=os.environ['PATH'],
                    SSH_AUTH_SOCK=str(self.base / 'agent-socket'))
        seen = set()
        def capture(args, **kwargs):
            result = run_bounded(args, **kwargs)
            for command in ('ls-remote', 'fetch', 'update-ref', 'symbolic-ref'):
                if command in args:
                    seen.add(command)
            env = kwargs['env']
            self.assertFalse(set(dummy) & set(env))
            for key, value in keep.items():
                self.assertEqual(value, env[key])
            helper = subprocess.run([args[0], 'config', '--get', 'credential.helper'],
                env=env, cwd=self.root, capture_output=True, check=True)
            self.assertEqual(b'review-helper', helper.stdout.strip())
            return result
        with patch.dict(os.environ, {**dummy, **keep}), patch('agentic.safeio.run_bounded', side_effect=capture):
            context = adapters['dispatch_ticket'].configure_budgeting(path=self.base / 'ledger.sqlite',
                governance=self.gov, operating=snapshot, repository_root=self.root)
        self.assertFalse(context.settings['enforcement_enabled'])
        self.assertEqual({'ls-remote', 'fetch', 'update-ref', 'symbolic-ref'}, seen)

    def wait_file(self, path, process):
        deadline = time.monotonic() + 30
        while not path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(path.exists(), 'child did not reach ownership barrier')

    def test_two_real_refreshers_discover_only_after_ownership_and_keep_newer_on(self):
        ready, resume = self.base / 'ready', self.base / 'resume'
        program = '''
import json, sys, time
from pathlib import Path
from contextlib import contextmanager
sys.path.insert(0, sys.argv[1])
from agentic import protected_settings as ps
root, ready, resume = map(Path, sys.argv[2:])
original = ps._refresh_lock
@contextmanager
def barrier(path):
    ready.touch()
    deadline = time.monotonic() + 30
    while not resume.exists():
        if time.monotonic() > deadline: raise TimeoutError('barrier')
        time.sleep(.02)
    with original(path) as reclaimed: yield reclaimed
ps._refresh_lock = barrier
print(json.dumps(ps.refresh_default_policy(root)))
'''
        library = str(Path(op.__file__).resolve().parents[1])
        first = subprocess.Popen([sys.executable, '-B', '-c', program, library,
            str(self.root), str(ready), str(resume)], env=isolated_git_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_file(ready, first)
            old = self.git('rev-parse', 'origin/main')
            self.rename('main', 'trunk')
            self.git('--git-dir=' + str(self.bare), 'update-ref', 'refs/heads/main', old)
            second = subprocess.run([sys.executable, '-B', '-c',
                'import sys,json; sys.path.insert(0,sys.argv[1]); '
                'from agentic.protected_settings import refresh_default_policy; '
                'print(json.dumps(refresh_default_policy(sys.argv[2])))', library, str(self.root)],
                env=isolated_git_env(), capture_output=True, check=True, timeout=60)
            self.assertEqual('FETCHED', json.loads(second.stdout)['status'])
            self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])
            resume.touch()
            output, error = first.communicate(timeout=60)
            self.assertEqual(0, first.returncode, error)
            self.assertEqual('FETCHED', json.loads(output)['status'])
            self.assertEqual('refs/remotes/origin/trunk', self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))
            self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()

    def test_git_valid_names_publish_read_and_refresh_again(self):
        self.publish(self.enabled())
        self.write(self.value)
        for name in ('release+budget', 'budget@next', 'budget-é', 'budget-\u2003'):
            with self.subTest(name=name):
                self.git('check-ref-format', 'refs/remotes/origin/' + name)
                self.git('--git-dir=' + str(self.bare), 'update-ref', 'refs/heads/' + name,
                         self.git('rev-parse', 'HEAD'))
                self.git('--git-dir=' + str(self.bare), 'symbolic-ref', 'HEAD', 'refs/heads/' + name)
                for _ in range(2):
                    self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
                    code, report = self.show()
                    self.assertEqual(0, code, report)
                    self.assertEqual('refs/remotes/origin/' + name, report['protected_settings']['ref'])
                    self.assertTrue(report['configuration']['budgeting']['enforcement_enabled'])

    def test_git_invalid_names_refuse_without_publication(self):
        from agentic.protected_settings import _default_ref
        before = self.git('rev-parse', 'origin/main')
        for name in ('bad..name', 'bad@{name', '.hidden', 'bad.lock', 'bad//name', 'bad name', 'bad~name'):
            with self.subTest(name=name):
                ref = 'refs/remotes/origin/' + name
                check = subprocess.run([shutil.which('git'), 'check-ref-format', ref], capture_output=True)
                self.assertNotEqual(0, check.returncode)
                # Git cannot create malformed refs; inject only the hostile wire/identity bytes.
                from agentic import protected_settings as ps
                original = ps._git
                def symbolic(root, *args, **kwargs):
                    if args[0] == 'symbolic-ref': return (ref + '\n').encode('utf-8')
                    return original(root, *args, **kwargs)
                with patch('agentic.protected_settings._git', side_effect=symbolic):
                    with self.assertRaises(ValidationError): _default_ref(self.root)
                def wire(args, **kwargs):
                    if '--symref' in args:
                        from types import SimpleNamespace
                        return SimpleNamespace(returncode=0, stdout=(f'ref: refs/heads/{name}\tHEAD\n{before}\tHEAD\n').encode())
                    return run_bounded(args, **kwargs)
                with patch('agentic.safeio.run_bounded', side_effect=wire):
                    self.assertEqual('UNAVAILABLE', refresh_default_policy(self.root)['status'])
                self.assertEqual(before, self.git('rev-parse', 'origin/main'))

    def hierarchical(self, inverse, failure=None, *, without_identity=False):
        old, new = ('main/v2', 'main') if inverse else ('main', 'main/v2')
        if inverse:
            initial = self.git('rev-parse', 'origin/main')
            self.git('update-ref', '-d', 'refs/remotes/origin/main')
            self.git('update-ref', 'refs/remotes/origin/main/v2', initial)
            self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main/v2')
        before, target = self.rename(old, new)
        if without_identity:
            self.drop_origin_head()
        seen = []
        def inject(args, **kwargs):
            mutation = ('anchor' if 'update-ref' in args and args[-2] == 'refs/remotes/origin/HEAD' else
                        'delete' if 'update-ref' in args and '-d' in args else
                        'update' if 'update-ref' in args else
                        'identity' if 'symbolic-ref' in args else None)
            if mutation:
                seen.append(mutation)
                if mutation == failure: raise ValidationError('injected publication failure')
            result = run_bounded(args, **kwargs)
            if mutation:
                # Every completed intermediate step must leave old policy or refusal.
                snapshot = op.read_operating(self.root, self.gov)
                report = snapshot.provenance['protected_settings']
                if without_identity and mutation != 'identity':
                    self.assertIsNone(report['commit'])
                if report['commit']:
                    self.assertEqual(before, report['commit']) if mutation != 'identity' else self.assertEqual(target, report['commit'])
                else:
                    with self.assertRaisesRegex(ValidationError, 'unverified'): require_budget_policy(snapshot)
            return result
        with patch('agentic.safeio.run_bounded', side_effect=inject):
            result = refresh_default_policy(self.root)
        self.assertEqual('UNAVAILABLE' if failure else 'FETCHED', result['status'], result)
        if failure:
            snapshot = op.read_operating(self.root, self.gov)
            report = snapshot.provenance['protected_settings']
            if report['commit']: self.assertEqual(before, report['commit'])
            else:
                with self.assertRaisesRegex(ValidationError, 'unverified'): require_budget_policy(snapshot)
            self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
        expected = (['anchor'] if without_identity else []) + ['delete', 'update', 'identity']
        self.assertEqual(expected[:len(seen)], seen)
        self.assertEqual('refs/remotes/origin/' + new, self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))
        self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])

    def test_hierarchical_rename_forward(self): self.hierarchical(False)
    def test_hierarchical_rename_inverse(self): self.hierarchical(True)
    def test_hierarchical_forward_delete_failure(self): self.hierarchical(False, 'delete')
    def test_hierarchical_forward_update_failure(self): self.hierarchical(False, 'update')
    def test_hierarchical_forward_identity_failure(self): self.hierarchical(False, 'identity')
    def test_hierarchical_inverse_delete_failure(self): self.hierarchical(True, 'delete')
    def test_hierarchical_inverse_update_failure(self): self.hierarchical(True, 'update')
    def test_hierarchical_inverse_identity_failure(self): self.hierarchical(True, 'identity')

    def test_hierarchical_forward_without_identity_delete_failure(self):
        self.hierarchical(False, 'delete', without_identity=True)

    def test_hierarchical_forward_without_identity_anchor_failure(self):
        self.hierarchical(False, 'anchor', without_identity=True)

    def test_hierarchical_forward_without_identity_update_failure(self):
        self.hierarchical(False, 'update', without_identity=True)

    def test_hierarchical_forward_without_identity_publication_failure(self):
        self.hierarchical(False, 'identity', without_identity=True)

    def test_hierarchical_inverse_without_identity_delete_failure(self):
        self.hierarchical(True, 'delete', without_identity=True)

    def test_hierarchical_inverse_without_identity_anchor_failure(self):
        self.hierarchical(True, 'anchor', without_identity=True)

    def test_hierarchical_inverse_without_identity_update_failure(self):
        self.hierarchical(True, 'update', without_identity=True)

    def test_hierarchical_inverse_without_identity_publication_failure(self):
        self.hierarchical(True, 'identity', without_identity=True)

    def test_advertised_commit_mismatch_has_retry_remedy(self):
        def advance(args, **kwargs):
            result = run_bounded(args, **kwargs)
            if '--symref' in args: self.publish(self.enabled())
            return result
        before = self.git('rev-parse', 'origin/main')
        with patch('agentic.safeio.run_bounded', side_effect=advance):
            result = refresh_default_policy(self.root)
        self.assertEqual('UNAVAILABLE', result['status'])
        self.assertEqual('advertised commit check', result['step'])
        self.assertIn('Fetched commit differs from advertised HEAD', result['reason'])
        self.assertIn('next controller cycle', result['reason'])
        self.assertNotIn('Restore access', result['reason'])
        # publish() simulates the push and updates tracking; restore its test-side effect.
        self.git('update-ref', 'refs/remotes/origin/main', before)
        self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])

    def test_fetch_has_no_destination_refmap_or_atomic_requirement(self):
        seen = []
        def capture(args, **kwargs):
            if 'fetch' in args:
                tail = args[args.index('fetch') + 1:]
                seen.append(tail)
                self.assertNotIn('--atomic', tail)
                self.assertIn('--refmap=', tail)
                self.assertEqual(['refs/heads/main'], tail[tail.index('--') + 2:])
            return run_bounded(args, **kwargs)
        with patch('agentic.safeio.run_bounded', side_effect=capture):
            self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
        self.assertEqual(1, len(seen))
