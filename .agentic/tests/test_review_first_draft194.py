from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
from io import StringIO
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentic import ValidationError
from agentic.canonical import sha256
from agentic.review_first_draft import (enroll_created_pr, publication_scan,
                                         publish_tested_tree,
                                         render_first_draft_body,
                                         run_first_draft,
                                         validate_worker_receipt)
from agentic.gittree import candidate_tree
from agentic.review_loop import LoopStore
from agentic.providers.github_review_host import HostDriver, load_config


ROOT = Path(__file__).resolve().parents[2]


class FirstDraftTests(unittest.TestCase):
    def test_generator_reproduces_first_draft_worker_schema(self):
        spec = importlib.util.spec_from_file_location('awf_generate_review_loop',
                                                       ROOT / 'scripts/generate_review_loop.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as name:
            module.ROOT = Path(name)
            module.main()
            generated = module.ROOT / 'first-draft-worker-result.schema.json'
            checked_in = ROOT / '.agentic/review-loop/first-draft-worker-result.schema.json'
            self.assertEqual(generated.read_bytes(), checked_in.read_bytes())

    def test_body_records_tier_and_worker_pins(self):
        body = render_first_draft_body('Ticket contract', risk_tier='Tier 3',
                                       worker_model='gpt-5.6-luna', reasoning_effort='medium',
                                       branch='codex/awf-30', tested_tree='a' * 40)
        self.assertIn('Risk tier: Tier 3', body)
        self.assertIn('gpt-5.6-luna', body)
        self.assertIn('amendment cycles start at zero', body)

    def test_receipt_rejects_scope_escape(self):
        receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'src/a.py', 'action': 'modified'}],
                   'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'changed'}
        with self.assertRaises(ValidationError):
            validate_worker_receipt(receipt, allowed_paths={'src/other.py'})

    def test_publication_scan_blocks_first_push_body(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def run(*args):
                return subprocess.run([git, '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            run('config', 'user.name', 'fixture')
            run('config', 'user.email', 'fixture@example.invalid')
            (root / 'a.txt').write_text('safe\n')
            run('add', 'a.txt'); run('commit', '-m', 'base')
            base = run('rev-parse', 'HEAD')
            unsafe_value = '/' + 'home' + '/' + 'fixture' + '/' + 'secret'
            (root / 'a.txt').write_text(unsafe_value + '\n')
            run('add', 'a.txt'); run('commit', '-m', 'candidate')
            head = run('rev-parse', 'HEAD')
            with self.assertRaises(ValidationError):
                publication_scan(root, base, head, 'body')

    def test_publisher_rejects_tree_mismatch_before_push(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def run(*args):
                return subprocess.run([git, '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            run('config', 'user.name', 'fixture'); run('config', 'user.email', 'fixture@example.invalid')
            (root / 'a.txt').write_text('safe\n'); run('add', 'a.txt'); run('commit', '-m', 'base')
            base = run('rev-parse', 'HEAD')
            (root / 'a.txt').write_text('changed\n')
            receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'a.txt', 'action': 'modified'}],
                       'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'changed'}
            class NoPush:
                def run(self, *args):
                    raise AssertionError('publisher must stop before Git mutation')
            with self.assertRaises(ValidationError):
                publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                                    commit_message='first draft', git=NoPush(), allowed_paths={'a.txt'})

    def test_publisher_scans_after_commit_but_withholds_push(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def run(*args):
                return subprocess.run([git, '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            run('config', 'user.name', 'fixture'); run('config', 'user.email', 'fixture@example.invalid')
            (root / 'a.txt').write_text('safe\n'); run('add', 'a.txt'); run('commit', '-m', 'base')
            base = run('rev-parse', 'HEAD')
            (root / 'a.txt').write_text('/'.join(('', 'home', 'fixture', 'secret')) + '\n')
            tree = candidate_tree(root, base, [{'path': 'a.txt', 'action': 'modified'}])
            receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'a.txt', 'action': 'modified'}],
                       'tested_tree': tree.tested_tree, 'ignored_untracked': list(tree.ignored_untracked), 'summary': 'changed'}
            pushes = []
            class Adapter:
                def run(self, *args):
                    if args[0] == 'push':
                        pushes.append(args)
                        raise AssertionError('scan must withhold push')
                    return run(*args)
            with self.assertRaises(ValidationError):
                publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                                    commit_message='first draft', git=Adapter(), allowed_paths={'a.txt'})
            self.assertEqual(pushes, [])

    def test_publisher_commits_exact_tested_tree_before_push(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); remote = root.parent / (root.name + '-remote.git')
            def command(cwd, *args):
                return subprocess.run([git, '-C', str(cwd), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            subprocess.run([git, 'init', '--bare', str(remote)], check=True, capture_output=True)
            command(root, 'config', 'user.name', 'fixture'); command(root, 'config', 'user.email', 'fixture@example.invalid')
            command(root, 'config', 'core.autocrlf', 'false')
            command(root, 'remote', 'add', 'origin', str(remote))
            (root / 'a.txt').write_text('safe\n'); command(root, 'add', 'a.txt'); command(root, 'commit', '-m', 'base')
            base = command(root, 'rev-parse', 'HEAD')
            (root / 'a.txt').write_text('changed\n')
            changes = [{'path': 'a.txt', 'action': 'modified'}]
            tree = candidate_tree(root, base, changes)
            receipt = {'outcome': 'CHANGED', 'changes': changes, 'tested_tree': tree.tested_tree,
                       'ignored_untracked': list(tree.ignored_untracked), 'summary': 'changed'}
            class Adapter:
                def run(self, *args):
                    return command(root, *args)
            published = publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                                            commit_message='first draft', git=Adapter(), allowed_paths={'a.txt'})
            self.assertEqual(published['head_tree'], tree.tested_tree)
            self.assertEqual(command(root, 'rev-parse', 'HEAD^{tree}'), tree.tested_tree)

    def test_publisher_requires_and_retains_git_ignored_residue(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            base_dir = Path(name)
            root, remote = base_dir / 'repo', base_dir / 'remote.git'
            def command(cwd, *args):
                return subprocess.run([git, '-C', str(cwd), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True,
                           capture_output=True)
            subprocess.run([git, 'init', '--bare', str(remote)], check=True,
                           capture_output=True)
            command(root, 'config', 'user.name', 'fixture')
            command(root, 'config', 'user.email', 'fixture@example.invalid')
            command(root, 'config', 'core.autocrlf', 'false')
            command(root, 'remote', 'add', 'origin', str(remote))
            (root / '.gitignore').write_text('*.secret\n', encoding='utf-8')
            (root / 'a.txt').write_text('safe\n', encoding='utf-8')
            command(root, 'add', '.gitignore', 'a.txt')
            command(root, 'commit', '-m', 'base')
            base = command(root, 'rev-parse', 'HEAD')
            (root / 'a.txt').write_text('changed\n', encoding='utf-8')
            (root / 'local.secret').write_text('excluded\n', encoding='utf-8')
            changes = [{'path': 'a.txt', 'action': 'modified'}]
            tree = candidate_tree(root, base, changes)
            class Adapter:
                def run(self, *args):
                    return command(root, *args)
            receipt = {'outcome': 'CHANGED', 'changes': changes,
                       'tested_tree': tree.tested_tree, 'ignored_untracked': [],
                       'summary': 'changed'}
            with self.assertRaisesRegex(ValidationError, 'ignored_untracked inventory'):
                publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                    commit_message='first draft', git=Adapter(), allowed_paths={'a.txt'})
            receipt['ignored_untracked'] = ['local.secret']
            published = publish_tested_tree(root, base, 'codex/awf-30', receipt,
                body='body', commit_message='first draft', git=Adapter(),
                allowed_paths={'a.txt'})
            self.assertEqual(published['ignored_untracked'], ['local.secret'])
            self.assertTrue((root / 'local.secret').is_file())

    def test_enrollment_charges_first_draft_but_not_amendment_cycles(self):
        with tempfile.TemporaryDirectory() as name:
            store = LoopStore(Path(name))
            config = {'key': '12:30', 'repository': 'fixture/project', 'config_hash': 'x',
                      'initial_findings': [], 'max_agent_runs': 10}
            snapshot = {'repository_id': 12, 'pr': 30, 'head': 'a' * 40, 'base': 'b' * 40,
                        'head_ref': 'codex/awf-30', 'base_ref': 'main'}
            state = enroll_created_pr(store, config, snapshot)
            self.assertEqual(state['agent_runs'], 1)
            self.assertEqual(state['cycles'], 0)
            store.close()

    def test_first_draft_bridge_observes_published_head_before_enrollment(self):
        with tempfile.TemporaryDirectory() as name:
            store = LoopStore(Path(name))
            config = {'key': '12:31', 'repository_id': 12, 'pr': 31,
                      'repository': 'fixture/project', 'config_hash': 'x',
                      'initial_findings': [], 'max_agent_runs': 10, 'allowed_paths': ['src/a.py']}
            receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'src/a.py', 'action': 'added'}],
                       'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'changed'}
            calls = []
            snapshot = {'repository_id': 12, 'pr': 31, 'head': 'c' * 40, 'base': 'b' * 40,
                        'head_ref': 'codex/awf-30', 'base_ref': 'main'}
            body = 'body'
            publication = {'base': snapshot['base'], 'head': snapshot['head'],
                           'head_tree': 'd' * 40, 'branch': snapshot['head_ref'],
                           'body': body, 'body_sha256': sha256(body.encode()),
                           'title': 'first draft', 'ignored_untracked': []}
            state = run_first_draft(store, config, worker=lambda run_id: receipt,
                                    publisher=lambda value: calls.append('publish') or publication,
                                    observe_pr=lambda value: calls.append('observe') or snapshot)
            self.assertEqual(calls, ['publish', 'observe'])
            self.assertEqual(state['agent_runs'], 1)
            store.close()

    def test_failed_first_draft_is_paused_with_reserved_run(self):
        with tempfile.TemporaryDirectory() as name:
            store = LoopStore(Path(name))
            config = {'key': '12:32', 'repository_id': 12, 'repository': 'fixture/project', 'config_hash': 'x',
                      'initial_findings': [], 'max_agent_runs': 10, 'allowed_paths': ['src/a.py']}
            seen = []
            with self.assertRaisesRegex(RuntimeError, 'worker failed'):
                run_first_draft(store, config,
                    worker=lambda run_id: seen.append(run_id) or (_ for _ in ()).throw(RuntimeError('worker failed')),
                    publisher=lambda value: None, observe_pr=lambda value: None)
            state = store.get('12:0')
            self.assertEqual(state['phase'], 'PAUSED')
            self.assertEqual(state['inflight']['id'], seen[0])
            store.close()


class RecordedGitHubDriver(HostDriver):
    """Real HostDriver/Git operations with an in-memory recorded GitHub seam."""

    def __init__(self, config, runtime_root, command, base_sha):
        super().__init__(config, runtime_root)
        self.command = command
        self.provider_base_sha = base_sha
        self.pr = None
        self.calls = []
        self.fail_codex_once = False
        self.fail_snapshot_once = False
        self.lose_create_response_once = False
        self.race_base_on_create = False
        self.mutate_git_controls = False

    @staticmethod
    def _field(args, name):
        prefix = name + '='
        return next(value[len(prefix):] for value in args if value.startswith(prefix))

    def run(self, name, args, cwd=None, stdin=None, timeout=None, log=None, binary=False):
        if name == 'git':
            return super().run(name, args, cwd=cwd, stdin=stdin, timeout=timeout,
                               log=log, binary=binary)
        if name == 'codex':
            self.calls.append(('codex', tuple(args)))
            if self.fail_codex_once:
                self.fail_codex_once = False
                raise ValidationError('recorded worker failure')
            (self.worker / 'a.txt').write_text('changed by recorded worker\n', encoding='utf-8')
            changes = [{'path': 'a.txt', 'action': 'modified'}]
            tree = candidate_tree(self.worker, self.provider_base_sha, changes)
            output = Path(args[args.index('--output-last-message') + 1])
            output.write_text(json.dumps({
                'outcome': 'CHANGED', 'changes': changes,
                'tested_tree': tree.tested_tree,
                'ignored_untracked': list(tree.ignored_untracked),
                'summary': 'recorded worker edit',
            }), encoding='utf-8')
            if log:
                Path(log).write_text('{"recorded":true}\n', encoding='utf-8')
            if self.mutate_git_controls:
                self.command('-C', str(self.worker), 'config', 'remote.origin.pushurl',
                             'https://example.invalid/captured.git')
                self.command('-C', str(self.worker), 'config', 'credential.helper',
                             '!recorded-credential-helper')
            return ''
        if name != 'gh':
            raise AssertionError(f'unexpected executable: {name}')
        endpoint = next(value for value in args if value.startswith('repos/'))
        suffix = endpoint.split('/', 3)[3]
        method = args[args.index('--method') + 1]
        self.calls.append((method, suffix))
        if method == 'GET' and suffix == f'branches/{self.c["base_branch"]}':
            return json.dumps({'name': self.c['base_branch'],
                               'commit': {'sha': self.provider_base_sha}})
        if method == 'GET' and suffix.startswith('pulls?'):
            return json.dumps([] if self.pr is None else [self.pr])
        if method == 'GET' and suffix.startswith('git/matching-refs/heads/'):
            ref = f'refs/heads/{self.c["head_branch"]}'
            value = self.command('--git-dir', str(self.remote), 'show-ref', ref,
                                 check=False)
            return json.dumps([] if not value else [{
                'ref': ref, 'object': {'type': 'commit', 'sha': value.split()[0]}}])
        if method == 'POST' and suffix == 'pulls':
            if self.pr is not None:
                raise AssertionError('a second draft PR must never be created')
            if self.race_base_on_create:
                self.provider_base_sha = 'd' * 40
            head = self.command('--git-dir', str(self.remote), 'rev-parse',
                                f'refs/heads/{self.c["head_branch"]}')
            body = self._field(args, 'body')
            self.pr = {
                'number': 41, 'state': 'open', 'draft': True, 'body': body,
                'head': {'ref': self._field(args, 'head'), 'sha': head,
                         'repo': {'id': self.c['repository_id']}},
                'base': {'ref': self._field(args, 'base'),
                         'sha': self.provider_base_sha,
                         'repo': {'id': self.c['repository_id']}},
            }
            if self.lose_create_response_once:
                self.lose_create_response_once = False
                raise ValidationError('recorded lost create response')
            return json.dumps(self.pr)
        if method == 'GET' and suffix == 'pulls/41':
            if self.fail_snapshot_once:
                self.fail_snapshot_once = False
                raise ValidationError('recorded snapshot failure')
            return json.dumps(self.pr)
        raise AssertionError(f'unexpected provider call: {method} {suffix}')


class FirstDraftHostIntegrationTests(unittest.TestCase):
    def setUp(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        self.git = git
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ['state', 'worker', 'critic']:
            (self.base / name).mkdir()

        def command(*args, check=True):
            return subprocess.run([git, *args], check=check, capture_output=True,
                                  text=True, encoding='utf-8').stdout.strip()
        self.command = command
        worker = self.base / 'worker'
        command('init', '--initial-branch=main', str(worker))
        command('-C', str(worker), 'config', 'user.name', 'AWF fixture')
        command('-C', str(worker), 'config', 'user.email', 'fixture@example.invalid')
        command('-C', str(worker), 'config', 'commit.gpgsign', 'false')
        command('-C', str(worker), 'config', 'core.autocrlf', 'true')
        (worker / 'a.txt').write_text('baseline\n', encoding='utf-8')
        command('-C', str(worker), 'add', 'a.txt')
        command('-C', str(worker), 'commit', '-m', 'fixture baseline')
        self.base_sha = command('-C', str(worker), 'rev-parse', 'HEAD')
        command('-C', str(worker), 'checkout', '-b', 'codex/awf-30')
        self.remote = self.base / 'remote.git'
        command('init', '--bare', str(self.remote))
        command('-C', str(worker), 'remote', 'add', 'origin', str(self.remote))
        command('-C', str(worker), '-c', 'protocol.file.allow=always',
                'push', 'origin', 'main')

        contract = self.base / 'state' / 'contract.md'
        contract.write_text('Implement the recorded first draft.', encoding='utf-8')
        self.config_path = self.base / 'state' / 'config.json'
        value = json.loads((ROOT / '.agentic/review-loop/host-config.example.json').read_text())
        executable = {'path': sys.executable,
                      'sha256': sha256(Path(sys.executable).read_bytes())}
        value.update(
            repository='fixture/project', repository_id=12, pr=0,
            head_branch='codex/awf-30', base_branch='main',
            state_dir=str(self.base / 'state'), worker_checkout=str(worker),
            critic_checkout=str(self.base / 'critic'), contract_path=str(contract),
            contract_sha256=sha256(contract.read_bytes()),
            runtime_manifest_sha256='f' * 64,
            executables={name: deepcopy(executable) for name in ['git', 'gh', 'codex']},
            models={'worker': 'fixture-worker', 'critic': 'fixture-critic'},
            allowed_paths=['a.txt'],
            required_checks=[{'name': 'test', 'app_id': 1,
                              'workflow_path': '.github/workflows/ci.yml',
                              'workflow_sha256': sha256(b'workflow\n')}],
            qualification={'operator': 'fixture', 'evidence': 'recorded provider',
                           'sandbox_verified': True, 'credentials_isolated': True,
                           'branch_owned': True, 'single_host_database': True})
        value['executables']['git'] = {'path': git, 'sha256': sha256(Path(git).read_bytes())}
        self.config_path.write_text(json.dumps(value), encoding='utf-8')
        self.config = load_config(self.config_path, ROOT)
        self.driver = RecordedGitHubDriver(self.config, ROOT, command, self.base_sha)
        self.driver.remote = self.remote
        self.driver.url = str(self.remote)
        real_git = self.driver.git

        def local_git(checkout, *args, **kwargs):
            if args and args[0] in {'fetch', 'push'}:
                args = ('-c', 'protocol.file.allow=always', *args)
            return real_git(checkout, *args, **kwargs)
        self.driver.git = local_git

    def cli(self, *args):
        spec = importlib.util.spec_from_file_location('awf_review_loop_cli',
                                                       ROOT / '.agentic/scripts/review_loop.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stdout, stderr = StringIO(), StringIO()
        with patch.object(module, 'verify_installed', return_value='f' * 64), \
                patch.object(module, 'load_config', return_value=self.config), \
                patch.object(module, 'HostDriver', return_value=self.driver), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = module.main(['--config', str(self.config_path), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def state(self, key):
        store = LoopStore(self.base / 'state')
        try:
            return store.get(key)
        finally:
            store.close()

    def test_cli_real_host_driver_creates_observes_binds_and_enrolls(self):
        code, output, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual((code, error), (0, ''))
        self.assertIn('"phase": "REVIEW"', output)
        self.assertEqual(json.loads(self.config_path.read_text())['pr'], 41)
        state = self.state('12:41')
        self.assertEqual((state['agent_runs'], state['cycles']), (1, 0))
        self.assertEqual((state['candidate']['head'], state['candidate']['base']),
                         (self.pr_head(), self.base_sha))
        self.assertIn('- Risk tier: Tier 3', self.driver.pr['body'])
        remote_tree = self.command('--git-dir', str(self.remote), 'rev-parse',
                                   'refs/heads/codex/awf-30^{tree}')
        self.assertEqual(remote_tree, state['first_draft_publication']['head_tree'])
        self.assertIn(('GET', 'branches/main'), self.driver.calls)
        self.assertIn(('POST', 'pulls'), self.driver.calls)
        self.assertIn(('GET', 'pulls/41'), self.driver.calls)

    def test_created_pr_base_race_pauses_without_enrollment_or_refund(self):
        self.driver.race_base_on_create = True
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        self.assertIn('base does not match the publication base', error)
        self.assertEqual(json.loads(self.config_path.read_text())['pr'], 41)
        state = self.state('12:0')
        self.assertEqual((state['phase'], state['agent_runs'], state['cycles']),
                         ('PAUSED', 1, 0))
        self.assertIn('base does not match the publication base', state['reason'])
        with self.assertRaises(ValidationError):
            self.state('12:41')

    def test_bound_pr_snapshot_failure_resumes_by_observation_without_second_create(self):
        self.driver.fail_snapshot_once = True
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        self.assertEqual(json.loads(self.config_path.read_text())['pr'], 41)
        code, _, error = self.cli('resume', '--reconciled-run', paused['inflight']['id'])
        self.assertEqual((code, error), (0, ''))
        state = self.state('12:41')
        self.assertEqual((state['phase'], state['agent_runs'], state['cycles']),
                         ('REVIEW', 1, 0))
        self.assertEqual(self.driver.calls.count(('POST', 'pulls')), 1)

    def test_uncertain_create_is_reconciled_by_head_lookup_without_second_create(self):
        self.driver.lose_create_response_once = True
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        self.assertEqual(json.loads(self.config_path.read_text())['pr'], 0)
        code, _, error = self.cli('resume', '--reconciled-run', paused['inflight']['id'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(json.loads(self.config_path.read_text())['pr'], 41)
        state = self.state('12:41')
        self.assertEqual((state['phase'], state['agent_runs'], state['cycles']),
                         ('REVIEW', 1, 0))
        self.assertEqual(self.driver.calls.count(('POST', 'pulls')), 1)
        self.assertTrue(any(call[0] == 'GET' and call[1].startswith('pulls?state=all&head=')
                            for call in self.driver.calls))

    def test_uncertain_create_closed_before_reconciliation_never_creates_second_pr(self):
        self.driver.lose_create_response_once = True
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        self.driver.pr['state'] = 'closed'
        code, _, error = self.cli('resume', '--reconciled-run', paused['inflight']['id'])
        self.assertEqual(code, 2)
        self.assertIn('closed; a second PR is forbidden', error)
        self.assertEqual(json.loads(self.config_path.read_text())['pr'], 41)
        self.assertEqual(self.driver.calls.count(('POST', 'pulls')), 1)

    def test_process_loss_after_push_reconciles_remote_ref_without_worker_replay(self):
        real_git = self.driver.git
        failed = [False]
        def lose_push_response(checkout, *args, **kwargs):
            value = real_git(checkout, *args, **kwargs)
            if args and args[0] == 'push' and not failed[0]:
                failed[0] = True
                raise ValidationError('recorded lost push response')
            return value
        self.driver.git = lose_push_response
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        self.assertEqual(paused['first_draft_publication_status'], 'PREPARED')
        self.assertEqual(self.pr_head(), paused['first_draft_publication']['head'])
        code, _, error = self.cli('resume', '--reconciled-run', paused['inflight']['id'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(self.state('12:0')['first_draft_publication_status'], 'PUSHED')
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(sum(1 for call in self.driver.calls if call[0] == 'codex'), 1)
        self.assertEqual(self.driver.calls.count(('POST', 'pulls')), 1)

    def test_proven_absent_prepared_push_retries_exact_head_without_worker_replay(self):
        real_git = self.driver.git
        failed = [False]
        def fail_before_push(checkout, *args, **kwargs):
            if args and args[0] == 'push' and not failed[0]:
                failed[0] = True
                raise ValidationError('recorded push refusal')
            return real_git(checkout, *args, **kwargs)
        self.driver.git = fail_before_push
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        code, _, error = self.cli('resume', '--reconciled-run', paused['inflight']['id'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(self.state('12:0')['first_draft_publication_status'], 'RETRY_PUSH')
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(sum(1 for call in self.driver.calls if call[0] == 'codex'), 1)
        self.assertEqual(self.driver.calls.count(('POST', 'pulls')), 1)

    def test_precreation_failure_reuses_reservation_uuid_and_charge(self):
        self.driver.fail_codex_once = True
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        run_id = paused['inflight']['id']
        code, _, error = self.cli('resume', '--reconciled-run', run_id)
        self.assertEqual((code, error), (0, ''))
        ready = self.state('12:0')
        self.assertEqual((ready['inflight']['id'], ready['agent_runs'], ready['cycles']),
                         (run_id, 1, 0))
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual((code, error), (0, ''))
        state = self.state('12:41')
        self.assertEqual((state['agent_runs'], state['cycles']), (1, 0))
        self.assertTrue((self.base / 'state' / 'runs' / run_id / 'attempt-2').is_dir())
        self.assertEqual(self.driver.calls.count(('POST', 'pulls')), 1)

    def test_worker_git_pushurl_and_credential_helper_block_publication(self):
        self.driver.mutate_git_controls = True
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        self.assertIn('changed Git configuration, remote routing or hooks', error)
        result = subprocess.run([self.git, '--git-dir', str(self.remote), 'rev-parse',
                                 '--verify', 'refs/heads/codex/awf-30'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(('POST', 'pulls'), self.driver.calls)
        state = self.state('12:0')
        self.assertEqual((state['phase'], state['agent_runs']), ('PAUSED', 1))

    def test_poisoned_git_controls_from_failed_attempt_are_not_new_baseline(self):
        self.driver.fail_codex_once = True
        code, _, _ = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        paused = self.state('12:0')
        self.command('-C', str(self.driver.worker), 'config', 'remote.origin.pushurl',
                     'https://example.invalid/captured.git')
        self.command('-C', str(self.driver.worker), 'config', 'credential.helper',
                     '!recorded-credential-helper')
        code, _, error = self.cli('resume', '--reconciled-run', paused['inflight']['id'])
        self.assertEqual((code, error), (0, ''))
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        self.assertIn('fetch/push origin is not the enrolled repository', error)
        self.assertEqual(sum(1 for call in self.driver.calls if call[0] == 'codex'), 1)
        self.assertNotIn(('POST', 'pulls'), self.driver.calls)

    def test_preexisting_local_credential_helper_blocks_cli_before_worker(self):
        self.command('-C', str(self.driver.worker), 'config', 'credential.helper',
                     '!recorded-credential-helper')
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        self.assertIn('repository-local credential configuration', error)
        self.assertFalse(any(call[0] == 'codex' for call in self.driver.calls))
        self.assertNotIn(('POST', 'pulls'), self.driver.calls)

    def test_delayed_git_control_mutation_is_rechecked_before_commit(self):
        worker = self.driver.first_draft_worker
        def mutate_after_worker(**kwargs):
            receipt = worker(**kwargs)
            self.command('-C', str(self.driver.worker), 'config', 'remote.origin.pushurl',
                         'https://example.invalid/captured.git')
            self.command('-C', str(self.driver.worker), 'config', 'credential.helper',
                         '!recorded-credential-helper')
            return receipt
        self.driver.first_draft_worker = mutate_after_worker
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        self.assertIn('changed Git configuration, remote routing or hooks', error)
        self.assertEqual(self.command('-C', str(self.driver.worker), 'rev-parse', 'HEAD'),
                         self.base_sha)
        self.assertNotIn(('POST', 'pulls'), self.driver.calls)

    def test_first_draft_binds_reviewed_effort_policy_before_worker(self):
        self.command('-C', str(self.driver.worker), 'checkout', 'main')
        policy = self.driver.worker / '.agentic'
        policy.mkdir()
        (policy / 'PROJECT_CONFIG.yaml').write_text(json.dumps({
            'execution': {'model_routing': {'models': {
                'fixture-worker': {'reasoning_efforts': ['high']}}}}}), encoding='utf-8')
        self.command('-C', str(self.driver.worker), 'add', '.agentic/PROJECT_CONFIG.yaml')
        self.command('-C', str(self.driver.worker), 'commit', '-m', 'reviewed routing policy')
        reviewed_base = self.command('-C', str(self.driver.worker), 'rev-parse', 'HEAD')
        self.command('-C', str(self.driver.worker), 'branch', '-f', 'codex/awf-30', reviewed_base)
        self.command('-C', str(self.driver.worker), 'checkout', 'codex/awf-30')
        self.driver.provider_base_sha = reviewed_base
        self.driver.c['reasoning_effort'] = {'worker': 'ultra'}
        self.driver.c['_requires_reviewed_effort_policy'] = True
        code, _, error = self.cli('first-draft', '--title', 'AWF-30 first draft')
        self.assertEqual(code, 2)
        self.assertIn('not an approved model/effort pair', error)
        self.assertFalse(any(call[0] == 'codex' for call in self.driver.calls))
        self.assertNotIn(('POST', 'pulls'), self.driver.calls)

    def pr_head(self):
        return self.command('--git-dir', str(self.remote), 'rev-parse',
                            'refs/heads/codex/awf-30')
