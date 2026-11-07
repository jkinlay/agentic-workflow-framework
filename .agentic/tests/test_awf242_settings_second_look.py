"""Second-look regressions through operating entry points and local bare remotes."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import source_only
from agentic import operating as op
from test_operating import governance

class SecondLookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-second-look-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "checkout"
        self.root.mkdir()
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        (self.root / op.GOVERNANCE).parent.mkdir()
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        self.write(self.value)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.git('add', op.CONFIG, op.GOVERNANCE)
        self.git('commit', '-qm', 'Initial policy')
        self.bare = self.base / 'remote.git'
        self.git('init', '-q', '--bare', '-b', 'main', str(self.bare))
        self.git('remote', 'add', 'origin', str(self.bare))
        self.git('push', '-q', 'origin', 'main')
        self.git('remote', 'set-head', 'origin', 'main')

    def git(self, *args):
        from agentic.child_process import isolated_git_env
        return subprocess.run([shutil.which('git'), '-C', str(self.root), *args],
            env=isolated_git_env(), check=True, capture_output=True, text=True).stdout.strip()

    def write(self, value):
        (self.root / op.CONFIG).write_bytes(op._json(value))

    def drop_origin_head(self):
        from agentic.protected_settings import _default_ref
        if _default_ref(self.root)[0]:
            self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        self.assertIsNone(_default_ref(self.root)[0])

    def publish(self, value):
        self.write(value)
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Owner policy', '--allow-empty')
        self.git('push', '-q', 'origin', 'main')

    def enabled(self):
        value = deepcopy(self.value)
        value['budgeting'].update(accounting_enabled=True, enforcement_enabled=True)
        return value

    def show(self, as_json=True):
        out = io.StringIO()
        with redirect_stdout(out):
            code = op.main(['--root', str(self.root), 'show', *(['--json'] if as_json else [])])
        return code, json.loads(out.getvalue()) if as_json else out.getvalue()

    def test_absent_default_file_is_no_policy(self):
        self.git('rm', '-q', op.CONFIG)
        self.git('commit', '-qm', 'Owner removes policy')
        self.git('push', '-q', 'origin', 'main')
        self.write(self.value)
        code, report = self.show()
        self.assertEqual(0, code, report)
        self.assertIn('fresh defaults', report['protected_settings']['source'])
        self.assertIn('absent', report['protected_settings']['policy_observation'])

    def test_no_fetched_default_allows_defaults_but_nondefault_set_is_atomic(self):
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        code, report = self.show()
        self.assertEqual(0, code, report)
        self.assertIn('no fetched default branch', report['protected_settings']['policy_observation'])
        self.write(self.enabled())
        before = (self.root / op.CONFIG).read_bytes()
        out = io.StringIO()
        with redirect_stdout(out):
            code = op.main(['--root', str(self.root), 'set', '--instruction', 'three streams', '--set', 'streams.count=3', '--json'])
        self.assertEqual(2, code)
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        self.assertFalse((self.root / '.agentic-state').exists())

    def test_audit_aggregate_refusal_changes_no_bytes_or_inventory(self):
        changes = self.root / op.STATE / 'changes'
        changes.mkdir(parents=True)
        for n in range(8):
            (changes / ('.awf-operating-' + format(n, '032x') + '.tmp')).write_bytes(b' ' * op.MAX_BYTES)
        before = (self.root / op.CONFIG).read_bytes()
        inventory = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file())
        out = io.StringIO()
        with redirect_stdout(out):
            code = op.main(['--root', str(self.root), 'set', '--instruction', 'four streams', '--set', 'streams.count=4', '--json'])
        self.assertEqual(2, code, out.getvalue())
        self.assertIn('aggregate', out.getvalue())
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(inventory, sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()))

    def test_gc_and_upstream_and_single_branch_defaults(self):
        self.git('remote', 'remove', 'origin')
        self.git('gc', '--quiet')
        self.assertEqual(0, self.show()[0])
        self.git('remote', 'add', 'upstream', str(self.bare))
        self.git('fetch', '-q', 'upstream')
        self.assertEqual(0, self.show()[0])
        self.git('checkout', '-qb', 'other')
        self.git('push', '-q', 'upstream', 'other')
        clone = self.base / 'single'
        self.git('clone', '-q', '--single-branch', '--branch', 'other', str(self.bare), str(clone))
        self.root = clone
        self.assertEqual(0, self.show()[0])

    def test_r17_config_identity_edit_cannot_hide_confirmation(self):
        self.publish(self.enabled())
        self.git('checkout', '-qb', 'worker')
        self.write(self.value)
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Worker OFF')
        self.git('push', '-q', 'origin', 'worker')
        edited = deepcopy(self.gov)
        edited['github']['base_branch'] = 'worker'
        (self.root / op.GOVERNANCE).write_bytes(op._json(edited))
        code, report = self.show()
        self.assertEqual(0, code, report)
        self.assertTrue(report['configuration']['budgeting']['enforcement_enabled'])
        self.assertEqual('refs/remotes/origin/main', report['protected_settings']['ref'])
        self.assertTrue(report['protected_settings']['differences'])

    def test_missing_origin_head_is_unverified_and_working_disagreement_is_reported(self):
        self.publish(self.enabled())
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        self.write(self.value)
        self.assertIn('unverified', self.show()[1]['protected_settings']['branch_observation'])
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        committed = deepcopy(self.gov)
        committed['github']['base_branch'] = 'other'
        (self.root / op.GOVERNANCE).write_bytes(op._json(committed))
        self.git('add', op.GOVERNANCE)
        self.git('commit', '-qm', 'Old configured branch')
        report = self.show()[1]['protected_settings']
        self.assertIn('disagree', report['branch_observation'])

    def test_pushed_worker_off_and_absent_cannot_select_policy(self):
        from agentic.budgeting import ControllerBudgeting
        from agentic import ValidationError
        self.publish(self.enabled())
        main = self.git('rev-parse', 'origin/main')
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        for branch, absent in (('worker/off', False), ('worker/absent', True)):
            with self.subTest(branch=branch):
                self.git('checkout', '-qB', branch, 'main')
                self.write(self.value)
                edited = deepcopy(self.gov)
                edited['github']['base_branch'] = branch
                (self.root / op.GOVERNANCE).write_bytes(op._json(edited))
                if absent:
                    self.git('rm', '-qf', op.CONFIG)
                self.git('add', '-A')
                self.git('commit', '-qm', 'Unmerged worker identity')
                self.git('push', '-q', 'origin', branch)
                self.write(self.value)
                snapshot = op.read_operating(self.root, edited)
                self.assertIn('unverified', snapshot.provenance['protected_settings']['branch_observation'])
                # Force offline admission: it must refuse even with OFF bytes.
                with patch('agentic.protected_settings.refresh_default_policy', return_value={'status': 'UNAVAILABLE'}):
                    with self.assertRaisesRegex(ValidationError, 'unverified'):
                        ControllerBudgeting(None, edited, snapshot, None, worktree_roots=(self.root,), repository_root=self.root)
                self.assertEqual(main, self.git('rev-parse', 'origin/main'))
        from agentic.protected_settings import refresh_default_policy
        self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
        self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])

    def test_no_origin_cannot_fetch_planted_worktree_repository(self):
        from agentic.protected_settings import refresh_default_policy
        self.publish(self.enabled())
        before = self.git('rev-parse', 'origin/main')
        self.git('config', '--remove-section', 'remote.origin')
        planted = self.root / 'origin'
        self.git('clone', '-q', '--bare', str(self.bare), str(planted))
        initial = self.git('rev-list', '--max-parents=0', 'main')
        self.git('--git-dir=' + str(planted), 'update-ref', 'refs/heads/main', initial)
        with patch('agentic.safeio.run_bounded', side_effect=AssertionError('must not contact a remote')):
            self.assertEqual('UNOBSERVED', refresh_default_policy(self.root)['status'])
        self.assertEqual(before, self.git('rev-parse', 'origin/main'))
        self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])

    def test_relative_and_checkout_local_origin_urls_are_refused(self):
        from agentic.protected_settings import refresh_default_policy
        for url in ('origin', './origin', str(self.root / 'origin'), (self.root / 'origin').as_uri()):
            with self.subTest(url=url):
                self.git('remote', 'set-url', 'origin', url)
                with patch('agentic.safeio.run_bounded', side_effect=AssertionError('unsafe URL used')):
                    result = refresh_default_policy(self.root)
                self.assertEqual('UNAVAILABLE', result['status'])
                self.assertEqual('remote identity', result['step'])

    def test_url_rewrite_cannot_hide_checkout_local_remote(self):
        from agentic.protected_settings import refresh_default_policy
        self.git('config', 'url.' + str(self.root / 'origin') + '.insteadOf', str(self.bare))
        result = refresh_default_policy(self.root)
        self.assertEqual('UNAVAILABLE', result['status'])
        self.assertEqual('remote identity', result['step'])

    def test_hostile_refspec_and_failed_publication_keep_policy_identity(self):
        from agentic.protected_settings import refresh_default_policy
        from agentic.safeio import run_bounded
        from agentic import ValidationError
        before = self.git('rev-parse', 'origin/main')
        self.publish(self.enabled())
        self.git('config', '--replace-all', 'remote.origin.fetch', '+refs/heads/*:refs/heads/hostile/*')
        self.git('update-ref', 'refs/remotes/origin/main', before)
        original = self.git('rev-parse', 'main')
        def fail_publish(args, **kwargs):
            if 'update-ref' in args:
                raise ValidationError('injected publication failure')
            return run_bounded(args, **kwargs)
        with patch('agentic.safeio.run_bounded', side_effect=fail_publish):
            result = refresh_default_policy(self.root)
        self.assertEqual('policy ref transaction', result['step'])
        self.assertEqual(before, self.git('rev-parse', 'origin/main'))
        self.assertEqual('refs/remotes/origin/main', self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))
        self.assertFalse((self.root / '.git/awf-default-policy.lock').exists())
        self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
        self.assertEqual(original, self.git('rev-parse', 'main'))
        self.assertEqual('', self.git('for-each-ref', 'refs/heads/hostile'))
        self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])

    def test_discovery_and_fetch_bounds_fail_without_changing_policy_refs(self):
        from agentic.protected_settings import refresh_default_policy
        from agentic.safeio import run_bounded
        from agentic import ValidationError
        before = self.git('rev-parse', 'origin/main')
        for stage in ('default discovery', 'branch fetch'):
            for failure in (TimeoutError, ValidationError):
                with self.subTest(stage=stage, failure=failure):
                    def fail(args, **kwargs):
                        self.assertEqual(30, kwargs['timeout'])
                        self.assertEqual(65536, kwargs['stdout_limit'])
                        self.assertEqual(65536, kwargs['stderr_limit'])
                        if ('--symref' in args if stage == 'default discovery' else 'fetch' in args):
                            raise failure('timeout or output flood')
                        return run_bounded(args, **kwargs)
                    with patch('agentic.safeio.run_bounded', side_effect=fail):
                        result = refresh_default_policy(self.root)
                    self.assertEqual('UNAVAILABLE', result['status'])
                    self.assertEqual(stage, result['step'])
                    self.assertEqual(before, self.git('rev-parse', 'origin/main'))
                    self.assertEqual('refs/remotes/origin/main', self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))

    def test_readers_ignore_fresh_and_old_refresh_locks_without_writes(self):
        self.publish(self.enabled())
        lock = self.root / '.git/awf-default-policy.lock'
        lock.write_text('Controller refresh')
        import time
        for age in (0, 601):
            with self.subTest(age=age):
                stamp = time.time() - age
                os.utime(lock, (stamp, stamp))
                before = {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                          for p in self.root.rglob('*') if p.is_file()}
                code, report = self.show()
                self.assertEqual(0, code, report)
                self.assertTrue(report['configuration']['budgeting']['enforcement_enabled'])
                snapshot = op.read_operating(self.root, self.gov)
                self.assertTrue(snapshot.config['budgeting']['enforcement_enabled'])
                self.assertEqual(before, {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                                         for p in self.root.rglob('*') if p.is_file()})

    def test_publication_uses_direct_update_ref_without_stdin_symref_protocol(self):
        from agentic.protected_settings import refresh_default_policy
        from agentic.safeio import run_bounded
        seen = []
        def inspect_publication(args, **kwargs):
            if 'update-ref' in args:
                seen.append(args[args.index('update-ref'):])
                self.assertNotIn('--stdin', args)
                self.assertNotIn('symref-update', kwargs.get('stdin') or '')
            return run_bounded(args, **kwargs)
        with patch('agentic.safeio.run_bounded', side_effect=inspect_publication):
            result = refresh_default_policy(self.root)
        self.assertEqual('FETCHED', result['status'], result)
        self.assertTrue(seen)

    def test_failure_between_publications_retains_old_policy_then_completes(self):
        from agentic.protected_settings import refresh_default_policy
        from agentic.safeio import run_bounded
        from agentic import ValidationError
        before = self.git('rev-parse', 'origin/main')
        self.git('checkout', '-qb', 'trunk')
        self.write(self.enabled())
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Renamed default ON')
        self.git('push', '-q', 'origin', 'trunk')
        target = self.git('rev-parse', 'trunk')
        self.git('update-ref', '-d', 'refs/remotes/origin/trunk')
        self.git('--git-dir=' + str(self.bare), 'symbolic-ref', 'HEAD', 'refs/heads/trunk')
        def fail_identity(args, **kwargs):
            if 'symbolic-ref' in args:
                self.assertEqual(target, self.git('rev-parse', 'origin/trunk'))
                raise ValidationError('injected second-step failure')
            return run_bounded(args, **kwargs)
        with patch('agentic.safeio.run_bounded', side_effect=fail_identity):
            result = refresh_default_policy(self.root)
        self.assertEqual('UNAVAILABLE', result['status'], result)
        self.assertEqual('default identity publication', result['step'])
        self.assertEqual(before, self.git('rev-parse', 'origin/main'))
        self.assertEqual('refs/remotes/origin/main', self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))
        self.assertFalse(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])
        self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
        self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])
        self.assertEqual(before, self.git('rev-parse', 'origin/main'))

    def test_fresh_lock_retains_policy_and_old_lock_is_replaced(self):
        from agentic.protected_settings import refresh_default_policy
        import time
        before = self.git('rev-parse', 'origin/main')
        self.publish(self.enabled())
        target = self.git('rev-parse', 'main')
        self.git('update-ref', 'refs/remotes/origin/main', before)
        lock = self.root / '.git/awf-default-policy.lock'
        lock.write_text('Dead controller refresh')
        result = refresh_default_policy(self.root)
        self.assertEqual('UNAVAILABLE', result['status'], result)
        self.assertEqual('refresh ownership', result['step'])
        self.assertEqual(before, self.git('rev-parse', 'origin/main'))
        self.assertFalse(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])
        stamp = time.time() - 601
        os.utime(lock, (stamp, stamp))
        result = refresh_default_policy(self.root)
        self.assertEqual('FETCHED', result['status'], result)
        self.assertTrue(result['stale_lock_replaced'])
        self.assertIn('replaced', result['reason'])
        self.assertEqual(target, self.git('rev-parse', 'origin/main'))
        self.assertFalse(lock.exists())

    def test_fetched_commit_must_match_advertised_head(self):
        from agentic.protected_settings import refresh_default_policy
        from agentic.safeio import run_bounded
        before = self.git('rev-parse', 'origin/main')
        self.publish(self.enabled())
        self.git('update-ref', 'refs/remotes/origin/main', before)
        def concurrent_fetch(args, **kwargs):
            result = run_bounded(args, **kwargs)
            if 'fetch' in args:
                self.git('fetch', '-q', '--refmap=', str(self.bare), before)
            return result
        with patch('agentic.safeio.run_bounded', side_effect=concurrent_fetch):
            result = refresh_default_policy(self.root)
        self.assertEqual('UNAVAILABLE', result['status'], result)
        self.assertEqual('advertised commit check', result['step'])
        self.assertIn('Fetched commit differs from advertised HEAD', result['reason'])
        self.assertIn('next controller cycle', result['reason'])
        self.assertNotIn('Restore access', result['reason'])
        self.assertEqual(before, self.git('rev-parse', 'origin/main'))
        self.assertFalse(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])
        self.assertEqual('FETCHED', refresh_default_policy(self.root)['status'])
        self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])

    def test_live_refresher_survives_clock_jump_and_readers_work_during_fetch(self):
        from agentic.protected_settings import refresh_default_policy
        from agentic.safeio import run_bounded
        import time
        lock = self.root / '.git/awf-default-policy.lock'
        before = self.git('rev-parse', 'origin/main')
        self.publish(self.enabled())
        self.git('update-ref', 'refs/remotes/origin/main', before)
        seen = []
        def during_network(args, **kwargs):
            if '--symref' in args:
                self.assertTrue(lock.exists())
            if 'fetch' in args:
                seen.append('fetch')
                self.assertTrue(lock.exists())
                self.assertEqual(0, self.show()[0])
                self.assertFalse(op.read_operating(self.root, self.gov).config['budgeting']['enforcement_enabled'])
                future = time.time() + 3600
                # Age appears stale, but a live kernel lock still excludes publication.
                with patch('time.time', return_value=future), patch('agentic.safeio.run_bounded', side_effect=run_bounded):
                    other = refresh_default_policy(self.root)
                self.assertEqual('UNAVAILABLE', other['status'], other)
                self.assertEqual('refresh ownership', other['step'])
                self.assertEqual(before, self.git('rev-parse', 'origin/main'))
            return run_bounded(args, **kwargs)
        with patch('agentic.safeio.run_bounded', side_effect=during_network):
            result = refresh_default_policy(self.root)
        self.assertEqual(['fetch'], seen)
        self.assertEqual('FETCHED', result['status'], result)
        self.assertFalse(lock.exists())

    def test_remote_default_rename_follows_with_old_branch_kept_or_deleted(self):
        from agentic.protected_settings import refresh_default_policy
        for delete_old in (False, True):
            with self.subTest(delete_old=delete_old):
                self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
                self.git('checkout', '-qB', 'trunk')
                self.write(self.enabled())
                self.git('add', op.CONFIG)
                self.git('commit', '-qm', 'New default ON', '--allow-empty')
                self.git('push', '-q', 'origin', 'trunk')
                self.git('--git-dir=' + str(self.bare), 'symbolic-ref', 'HEAD', 'refs/heads/trunk')
                if delete_old:
                    self.git('--git-dir=' + str(self.bare), 'update-ref', '-d', 'refs/heads/main')
                result = refresh_default_policy(self.root)
                self.assertEqual('FETCHED', result['status'], result)
                self.assertTrue(result['name_changed'])
                self.assertEqual('refs/remotes/origin/trunk', self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))
                self.assertTrue(self.show()[1]['configuration']['budgeting']['enforcement_enabled'])

    def test_missing_head_clone_shapes_on_and_off_refresh_before_admission(self):
        from agentic.budgeting import ControllerBudgeting
        for enabled in (False, True):
            self.publish(self.enabled() if enabled else self.value)
            original = self.root
            for shape in ('single', 'actions', 'never-fetched'):
                with self.subTest(enabled=enabled, shape=shape):
                    clone = self.base / (shape + str(enabled))
                    if shape == 'single':
                        self.git('clone', '-q', '--single-branch', '--branch', 'main', str(self.bare), str(clone))
                    else:
                        clone.mkdir()
                        self.git('init', '-q', str(clone))
                    self.root = clone
                    try:
                        if shape != 'single':
                            self.git('remote', 'add', 'origin', str(self.bare))
                            if shape == 'actions':
                                self.git('fetch', '-q', 'origin', '+refs/heads/main:refs/remotes/origin/main')
                        self.drop_origin_head()
                        (clone / op.GOVERNANCE).parent.mkdir(exist_ok=True)
                        (clone / op.GOVERNANCE).write_bytes(op._json(self.gov))
                        self.write(self.value)
                        snapshot = op.read_operating(clone, self.gov)
                        self.assertIn('unverified', snapshot.provenance['protected_settings']['branch_observation'])
                        context = ControllerBudgeting(self.base / (shape + str(enabled) + '.sqlite') if enabled else None,
                            self.gov, snapshot, None, worktree_roots=(clone,), repository_root=clone)
                        self.assertEqual(enabled, context.settings['enforcement_enabled'])
                        self.assertEqual('refs/remotes/origin/main', self.git('symbolic-ref', 'refs/remotes/origin/HEAD'))
                    finally:
                        self.root = original

    def test_offline_without_refs_keeps_fresh_off_nonbudget_work_available(self):
        from agentic.budgeting import ControllerBudgeting
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.git('remote', 'set-url', 'origin', str(self.base / 'offline.git'))
        snapshot = op.read_operating(self.root, self.gov)
        context = ControllerBudgeting(None, self.gov, snapshot, None, worktree_roots=(self.root,), repository_root=self.root)
        self.assertFalse(context.settings['accounting_enabled'])
        self.assertEqual(0, self.show()[0])

    def test_offline_with_tracking_refs_reports_failed_step_at_admission(self):
        from agentic.budgeting import ControllerBudgeting
        from agentic import ValidationError
        self.drop_origin_head()
        self.git('remote', 'set-url', 'origin', str(self.base / 'offline.git'))
        snapshot = op.read_operating(self.root, self.gov)
        self.assertFalse(snapshot.provenance['protected_settings']['budget_admission'])
        with self.assertRaisesRegex(ValidationError, 'default discovery'):
            ControllerBudgeting(None, self.gov, snapshot, None, worktree_roots=(self.root,), repository_root=self.root)
        with self.assertRaisesRegex(ValidationError, 'default discovery'):
            self.cycle()
        self.assertEqual(0, self.show()[0])

    def test_malformed_committed_current_config_has_no_effect(self):
        self.publish(self.enabled())
        self.git('checkout', '-qb', 'worker/malformed')
        (self.root / op.GOVERNANCE).write_text('{{{ malformed')
        self.git('add', op.GOVERNANCE)
        self.git('commit', '-qm', 'Malformed branch configuration')
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        code, report = self.show()
        self.assertEqual(0, code, report)
        self.assertTrue(report['configuration']['budgeting']['enforcement_enabled'])

    def test_relative_local_script_and_sandbox_writable_git_are_refused(self):
        for name in ('git.cmd', 'git.bat', 'git.ps1', 'git.exe'):
            fake = self.root / name
            fake.write_text('planted executable')
            for candidate in (str(fake), name):
                with self.subTest(candidate=candidate), patch('shutil.which', return_value=candidate), patch('subprocess.run', side_effect=AssertionError('planted Git executed')):
                    self.assertEqual(2, self.show()[0])

    def test_reftable_reads_policy_through_git(self):
        self.publish(self.enabled())
        self.git('refs', 'migrate', '--ref-format=reftable')
        self.write(self.value)
        code, report = self.show()
        self.assertEqual(0, code, report)
        self.assertTrue(report['configuration']['budgeting']['enforcement_enabled'])

    def test_external_script_and_sandbox_writable_git_refuse_before_execution(self):
        fake = self.base / ('git-script.exe' if os.name == 'nt' else 'git-script')
        fake.write_bytes(b'#!/bin/sh\n')
        with patch('shutil.which', return_value=str(fake)), patch('subprocess.run', side_effect=AssertionError('script executed')):
            self.assertEqual(2, self.show()[0])
        with patch('agentic.git_isolation._worker_writable', return_value=True, create=True):
            self.assertEqual(2, self.show()[0])

    def cycle(self):
        import runpy
        controller = runpy.run_path(str(Path(op.__file__).resolve().parents[2] / 'scripts/continuous_controller.py'))
        main = controller['main']
        names = {'observe_inventory', 'observe_reservations', 'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication', 'observe_host_resources', 'verify_host_observation', 'observe_planning_backlog', 'verify_planning_backlog', 'observe_planning_events', 'verify_planning_events', 'verify_planning_owner'}
        binding = self.base / 'binding.json'
        binding.write_text('{}')
        argv = ['--state', str(self.base / 'controller.sqlite'), '--stream', 'A', '--worktree-root', str(self.base / 'worker'),
                '--project-config', str(self.root / op.GOVERNANCE), 'cycle', '--inventory-binding', str(binding),
                '--repository-root', str(self.root), '--repository-head-sha', self.git('rev-parse', 'HEAD'),
                '--repository-tree-sha', self.git('rev-parse', 'HEAD^{tree}'), '--now', '2026-11-07T10:00:00Z']
        def observed_cycle(*args, **kwargs):
            return {'status': 'ACCEPTED', 'budget': kwargs['planning_operating_snapshot'].config['budgeting']}
        with patch.dict(main.__globals__, _load_reviewed_adapters=lambda *a: dict.fromkeys(names, lambda *a: None),
                ContinuousControllerStore=lambda *a, **k: object(), production_controller_cycle=observed_cycle), patch('agentic.store.Store'):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(argv, default_root=self.root))
            return json.loads(out.getvalue())

    def test_controller_cycle_fetches_and_failure_retains_policy(self):
        old = self.git('rev-parse', 'HEAD')
        self.publish(self.enabled())
        self.git('update-ref', 'refs/remotes/origin/main', old)
        result = self.cycle()
        self.assertTrue(result['budget']['enforcement_enabled'])
        self.assertEqual('FETCHED', result['protected_policy_fetch']['status'])
        self.git('remote', 'set-url', 'origin', str(self.base / 'unavailable.git'))
        result = self.cycle()
        self.assertTrue(result['budget']['enforcement_enabled'])
        self.assertEqual('UNAVAILABLE', result['protected_policy_fetch']['status'])

    @source_only.skip_unless_source_repo("requires both source skill descriptions", "global/awf/SKILL.md", "global/awf-portable/SKILL.md")
    def test_skill_and_rules_docs_describe_observed_behaviour(self):
        root = Path(op.__file__).resolve().parents[3]
        for name in ('awf', 'awf-portable'):
            skill = (root / 'global' / name / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('budget', skill.split('description:', 1)[1].split('\n', 1)[0].lower())
            self.assertIn('Fetch after observed merge', skill)
        doc = (root / '.agentic/docs/39-BUDGETING.md').read_text(encoding='utf-8')
        self.assertIn('`show` reports rules UNOBSERVED', doc)
        for phrase in ('update-ref', 'fetch-refspec', 'gitfile', "controller's own checkout"):
            self.assertIn(phrase, doc)

    @source_only.skip_unless_source_repo("requires genuine published 1.9.5 Git objects", "scripts/publish_release.py")
    def test_legacy_event_chain_survives_merged_block(self):
        legacy = deepcopy(self.value)
        legacy.pop('budgeting')
        self.publish(legacy)
        import sys
        import tarfile
        source_root = Path(op.__file__).resolve().parents[3]
        older = self.base / 'published-runtime'
        older.mkdir()
        published = '8ad399943c8f5e4f17db72f3e321b9365ee27782'
        archived = subprocess.run([shutil.which('git'), '-C', str(source_root), 'archive', published,
            '.agentic/lib', '.agentic/schemas'], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
            archive.extractall(older, filter='data')
        command = "import sys; sys.path.insert(0,sys.argv[1]); from agentic import operating as op; from agentic.canonical import load; from pathlib import Path; op.set_operating(sys.argv[2],load(Path(sys.argv[2])/op.GOVERNANCE),'four streams',['streams.count=4'])"
        historical_run = subprocess.run([sys.executable, '-B', '-I', '-c', command, str(older / '.agentic/lib'), str(self.root)],
                       capture_output=True, text=True)
        self.assertEqual(0, historical_run.returncode, historical_run.stderr)
        events = list((self.root / op.STATE / 'changes').glob('*.json'))
        self.assertTrue(events)
        self.assertTrue(all('unprotected_after_hash' not in json.loads(path.read_bytes()) for path in events))
        upgraded = json.loads((self.root / op.CONFIG).read_bytes())
        upgraded['budgeting'] = self.enabled()['budgeting']
        self.publish(upgraded)
        out = io.StringIO()
        with redirect_stdout(out):
            code = op.main(['--root', str(self.root), 'set', '--instruction', 'three streams', '--set', 'streams.count=3', '--json'])
        self.assertEqual(0, code, out.getvalue())
        self.assertEqual(0, self.show()[0])

    def test_behind_checkout_guidance_and_fetch_provenance(self):
        old = self.git('rev-parse', 'HEAD')
        self.publish(self.enabled())
        self.git('checkout', '-q', '--detach', old)
        code, report = self.show()
        self.assertEqual(0, code, report)
        self.assertIn('update the checkout', report['protected_settings']['next_action'])
        self.assertIn('fetched', report['protected_settings']['visibility'])
        self.assertIn('fetch_attempt_at', report['protected_settings'])
        self.assertIn('not a successful policy refresh', report['protected_settings']['visibility'])

    def test_legacy_source_empty_and_missing_blob_diagnostics(self):
        legacy = deepcopy(self.value)
        legacy.pop('budgeting')
        self.publish(legacy)
        self.assertIn('legacy retained', self.show()[1]['protected_settings']['source'])
        (self.root / op.CONFIG).write_bytes(b'')
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Empty policy')
        self.git('push', '-q', 'origin', 'main')
        self.write(self.value)
        self.assertIn('empty', str(self.show()[1]))
        self.publish(self.enabled())
        blob = self.git('rev-parse', 'HEAD:' + op.CONFIG)
        obj = self.root / '.git/objects' / blob[:2] / blob[2:]
        obj.chmod(0o600)
        obj.unlink()
        self.assertIn('blob', str(self.show()[1]))

    def test_plain_show_budget_is_readable(self):
        code, output = self.show(False)
        self.assertEqual(0, code)
        self.assertIn('Accounting: OFF', output)
        self.assertIn('Enforcement: OFF', output)
        self.assertIn('4,000,000', output)
        self.assertIn('1,000,000,000', output)

    def test_linked_worktree_requires_matching_backlink(self):
        linked = self.base / 'linked'
        self.git('worktree', 'add', '-q', '--detach', str(linked))
        marker = linked / '.git'
        admin = Path(marker.read_text().strip()[8:])
        (admin / 'gitdir').write_text(str(self.root / '.git'))
        self.root = linked
        self.assertEqual(2, self.show()[0])


from agentic import git_isolation as admission

class NativeGitACLTests(unittest.TestCase):
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
