import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))

from agentic import ValidationError
from agentic import child_process
from agentic.providers import github_review_host
from agentic.providers.github_review_host import (HostDriver,
    manifest_reproducer_sha256)
from agentic.provenance import (check_commit_range, record_owner_override,
                                verify_provider_owner_override,
                                render_owner_override)
from agentic.safeio import run_bounded
from source_only import skip_unless_source_repo


CODEX_THREAD_ID = '01a1317f-dadb-7071-80de-12832161734c'
CONTROLLER_RUN_ID = '3dd68622-6223-48bc-b236-8ed6df9a55c2'


class ProviderProvenanceTests(unittest.TestCase):
    def driver(self):
        driver = HostDriver.__new__(HostDriver)
        driver.c = {'repository': 'fixture/project', 'repository_id': 12,
                    'pr': 7, 'base_branch': 'main', 'config_hash': 'f' * 64,
                    'trusted_owner_ids': [101],
                    'provenance': {'require_run_logs': False,
                        'default_ref': 'refs/heads/main',
                        'generated_paths': [], 'generators': {}}}
        return driver

    def configured_command_driver(self, directory, name):
        driver = self.driver()
        config = Path(directory) / 'host-config.json'
        config.write_text('{}\n', encoding='utf-8')
        executable = Path(sys.executable).resolve()
        driver.root = ROOT
        driver.state = Path(directory) / 'state'
        driver.state.mkdir()
        driver.c.update({
            '_config_path': str(config),
            'config_hash': __import__('hashlib').sha256(
                config.read_bytes()).hexdigest(),
            'executables': {name: {
                'path': str(executable),
                'sha256': __import__('hashlib').sha256(
                    executable.read_bytes()).hexdigest(),
            }},
            'command_timeout_seconds': 10,
        })
        return driver

    def test_provenance_collector_binds_repository_pr_base_head_and_refs(self):
        driver = self.driver()
        pr = {'number': 7, 'updated_at': '2026-10-13T20:00:00Z', 'body': 'body',
              'labels': [{'name': 'fallback: bot-written'}],
              'head': {'sha': 'a' * 40, 'ref': 'codex/test', 'repo': {'id': 12}},
              'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'id': 12}}}
        def api(suffix, *, operation_budget=None):
            if suffix == '':
                return {'id': 12}
            if suffix == 'pulls/7':
                return deepcopy(pr)
            return []
        from copy import deepcopy
        driver.api = api
        observed = driver.collect_provenance_observation()
        self.assertEqual((12, 7, 'a' * 40, 'b' * 40, 'codex/test', 'main'),
                         (observed['repository_id'], observed['pr'],
                          observed['head'], observed['base'],
                          observed['head_ref'], observed['base_ref']))
        self.assertEqual([101], observed['trusted_owner_ids'])

    def test_provenance_collection_shares_one_provider_response_budget(self):
        driver = self.driver()
        budgets = []
        pr = {'number': 7, 'updated_at': 'same', 'body': '', 'labels': [],
              'head': {'sha': 'a' * 40, 'ref': 'codex/test', 'repo': {'id': 12}},
              'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'id': 12}}}

        def api(suffix, operation_budget=None):
            budgets.append(operation_budget)
            if suffix == '':
                return {'id': 12}
            if suffix == 'pulls/7':
                return dict(pr)
            return []

        driver.api = api
        driver.collect_provenance_observation()

        self.assertGreaterEqual(len(budgets), 6)
        self.assertTrue(all(item is budgets[0] for item in budgets))
        self.assertIsInstance(budgets[0], child_process.OperationBudget)
        self.assertEqual(child_process.HOST_PROVIDER_RUNNER_POLICY.operation_budget,
                         budgets[0].byte_limit)

    def test_host_provider_command_selects_closed_channel_policy(self):
        driver = self.driver()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'host-config.json'
            config.write_text('{}\n', encoding='utf-8')
            executable = Path(sys.executable).resolve()
            driver.c.update({
                '_config_path': str(config),
                'config_hash': __import__('hashlib').sha256(
                    config.read_bytes()).hexdigest(),
                'executables': {'gh': {
                    'path': str(executable),
                    'sha256': __import__('hashlib').sha256(
                        executable.read_bytes()).hexdigest(),
                }},
                'command_timeout_seconds': 120,
            })
            completed = subprocess.CompletedProcess(
                [str(executable)], 0, b'{}', b'')
            with patch.object(github_review_host, 'run_bounded',
                              return_value=completed) as runner:
                self.assertEqual('{}', driver.run('gh', ['-c', 'pass']))

        self.assertIs(runner.call_args.kwargs['policy'],
                      child_process.HOST_PROVIDER_RUNNER_POLICY)
        self.assertNotIn('stdout_limit', runner.call_args.kwargs)
        self.assertNotIn('stderr_limit', runner.call_args.kwargs)

    def test_host_provider_stdout_overflow_has_frozen_reason_code(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = self.configured_command_driver(directory, 'gh')
            program = 'import sys; sys.stdout.buffer.write(b"x" * (9 * 1024 * 1024))'

            with self.assertRaisesRegex(ValidationError,
                                        r'^HOST_STDOUT_LIMIT:'):
                driver.run('gh', ['-c', program])

    def test_legacy_recorded_driver_accounts_shared_provider_budget(self):
        class RecordedDriver(HostDriver):
            def run(self, name, args, cwd=None, stdin=None, timeout=None,
                    log=None, binary=False):
                return '{}'

        driver = RecordedDriver.__new__(RecordedDriver)
        driver.c = {'repository': 'fixture/project'}
        budget = child_process.HOST_PROVIDER_RUNNER_POLICY.make_budget(
            'legacy-recorded-driver')

        self.assertEqual({}, driver.api('', operation_budget=budget))
        self.assertEqual(1, budget.operations)
        self.assertEqual(2, budget.consumed_bytes)

    def test_provider_labels_comments_pagination_caps_and_ambiguity(self):
        driver = self.driver()
        rows = [{'id': index + 1, 'body': 'comment',
                 'user': {'id': 101},
                 'html_url': f'https://github.com/fixture/project/issues/7#{index + 1}'}
                for index in range(100)]
        def api(suffix, *, operation_budget=None):
            if suffix == '':
                return {'id': 12}
            if suffix == 'pulls/7':
                return {'number': 7, 'updated_at': 'same', 'body': '', 'labels': [],
                    'head': {'sha': 'a' * 40, 'ref': 'codex/test', 'repo': {'id': 12}},
                    'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'id': 12}}}
            if 'issues/7/comments' in suffix and 'page=1' in suffix:
                return rows
            if 'issues/7/comments' in suffix and 'page=2' in suffix:
                return [rows[0]]
            return []
        driver.api = api
        with self.assertRaisesRegex(ValidationError, 'duplicate'):
            driver.collect_provenance_observation()

    def test_critic_carrier_is_published_and_bound_to_fresh_provider_artifact(self):
        driver = self.driver()
        binding = {'head_sha': 'a' * 40, 'result_sha256': 'b' * 64,
                   'critic_run_carrier': 'Critic run: ' + CODEX_THREAD_ID}
        body = ('AWF critic evidence v1\nHead: ' + 'a' * 40
                + '\nResult sha256: ' + 'b' * 64 + '\n\n'
                + binding['critic_run_carrier'])
        created = {'id': 91, 'body': body, 'user': {'id': 7001},
                   'html_url': 'https://github.com/fixture/project/issues/7#issuecomment-91'}
        driver.api_post = lambda suffix, payload: created
        driver.collect_provenance_observation = lambda: {
            'head': 'a' * 40,
            'comments': [{'kind': 'issue', 'id': 91, 'actor_id': 7001,
                          'url': created['html_url'], 'body': body,
                          'body_sha256': __import__('hashlib').sha256(
                              body.encode()).hexdigest()}],
        }
        observed = driver.publish_critic_evidence(binding)
        self.assertEqual((91, 7001),
                         (observed['provider_comment_id'],
                          observed['provider_actor_id']))
        self.assertRegex(observed['provider_body_sha256'], r'^[0-9a-f]{64}$')

    def test_override_authority_comes_from_reviewed_config_and_observed_actor(self):
        record = record_owner_override(
            {}, who=101, when='2026-10-13T20:00:00Z', why='Reviewed fallback',
            pr=7, covered_shas=['a' * 40], trusted_owner_ids=[101])
        body = render_owner_override(record)
        observation = {'version': 1, 'repository_id': 12, 'pr': 7,
                       'labels': ['fallback: bot-written'],
                       'trusted_owner_ids': [101],
                       'config_sha256': 'f' * 64,
                       'comments': [{'kind': 'issue', 'id': 91,
                                     'actor_id': 101, 'url': 'https://github.com/fixture/project/issues/7#issuecomment-91',
                                     'body': body,
                                     'body_sha256': __import__('hashlib').sha256(
                                         body.encode()).hexdigest()}]}
        verified = verify_provider_owner_override(
            record, observation=observation, repository_id=12,
            config_sha256='f' * 64)
        self.assertEqual(91, verified['provider_comment_id'])
        forged = dict(observation)
        forged['trusted_owner_ids'] = ['101']
        with self.assertRaises(ValidationError):
            verify_provider_owner_override(
                record, observation=forged, repository_id=12,
                config_sha256='f' * 64)

    def test_host_api_output_is_stream_bounded_before_decode(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with self.assertRaisesRegex(ValidationError, 'STDERR_LIMIT'):
                run_bounded([sys.executable, '-c',
                             'import sys; sys.stderr.buffer.write(b"x" * 4097)'],
                            stdout_limit=1024, stderr_limit=4096, timeout=10)
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=10)

    def test_stream_overflow_kills_sleeping_producer_before_timeout(self):
        program = ('import sys,time; '
                   'sys.stdout.buffer.write(b"x" * (1024 * 1024)); '
                   'sys.stdout.buffer.flush(); time.sleep(30)')
        started = time.monotonic()
        with self.assertRaisesRegex(ValidationError, 'STDOUT_LIMIT'):
            run_bounded([sys.executable, '-c', program],
                        stdout_limit=1024, stderr_limit=1024, timeout=10)
        self.assertLess(time.monotonic() - started, 5)

    @skip_unless_source_repo()
    def test_registered_manifest_reproducer_accepts_real_generator_commit(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            subprocess.run([git, '-c', 'core.autocrlf=false', 'clone',
                            '--quiet', '--no-hardlinks', '--no-checkout',
                            str(ROOT), str(source)], check=True)
            for key, value in (('core.autocrlf', 'false'),
                               ('user.name', 'Fixture'),
                               ('user.email', 'fixture@example.invalid'),
                               ('commit.gpgsign', 'false')):
                subprocess.run([git, '-C', str(source), 'config', key, value],
                               check=True)
            subprocess.run([git, '-C', str(source), 'checkout', '--quiet'],
                           check=True)
            changed_relative = '.agentic/tests/fixtures/fake_host_process.py'
            changed = source / changed_relative
            changed.write_text(changed.read_text(encoding='utf-8')
                               + '\n# manifest reproducer fixture\n', encoding='utf-8',
                               newline='\n')
            subprocess.run([git, '-C', str(source), 'add', '--', changed_relative],
                           check=True)
            subprocess.run([
                git, '-C', str(source), 'commit', '--quiet', '--message',
                'Synthetic source baseline\n\nWriter: codex/' + CODEX_THREAD_ID,
            ], check=True)
            base = subprocess.run(
                [git, '-C', str(source), 'rev-parse', 'HEAD'], check=True,
                capture_output=True, text=True, encoding='utf-8').stdout.strip()
            generated = subprocess.run([
                sys.executable, '-B', str(source / 'scripts/build_release.py'),
                '--manifest-only',
            ], cwd=source, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(generated.returncode, 0,
                             generated.stdout + generated.stderr)
            changed_paths = subprocess.run(
                [git, '-C', str(source), 'diff', '--name-only'], check=True,
                capture_output=True, text=True, encoding='utf-8').stdout.splitlines()
            self.assertEqual(sorted(changed_paths), sorted(['MANIFEST.json',
                                                            'MANIFEST.md']))
            subprocess.run([git, '-C', str(source), 'add', '--',
                            'MANIFEST.json', 'MANIFEST.md'], check=True)
            subprocess.run([
                git, '-C', str(source), 'commit', '--quiet', '--message',
                'Regenerate manifests\n\nWriter: script/manifest-regen',
            ], check=True)
            head = subprocess.run(
                [git, '-C', str(source), 'rev-parse', 'HEAD'], check=True,
                capture_output=True, text=True, encoding='utf-8').stdout.strip()
            driver = HostDriver.__new__(HostDriver)
            driver.worker = source
            driver.state = Path(directory) / 'state'
            policy = {
                'require_run_logs': False,
                'default_ref': 'refs/heads/main',
                'generated_paths': ['MANIFEST.json', 'MANIFEST.md'],
                'generators': {'manifest-regen': {
                    'paths': ['MANIFEST.json', 'MANIFEST.md'],
                    'reproducer_sha256': manifest_reproducer_sha256(),
                }},
            }
            observation = {'version': 1, 'base_ref': 'main', 'base': base,
                           'pr': 1, 'labels': [], 'trusted_owner_ids': []}
            result = check_commit_range(
                source, base, head, reviewed_policy=policy,
                provider_observation=observation,
                runtime_registry=driver.register_provenance_runtime())
            self.assertEqual(result['status'], 'PASS', result['refusals'])

    def test_child_result_file_is_capped_while_owned_process_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.json'
            sentinel = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                program = ('from pathlib import Path; import time; '
                           'Path("result.json").write_bytes(b"x" * 4097); '
                           'time.sleep(30)')
                with self.assertRaisesRegex(ValidationError,
                                            'RESULT_FILE_LIMIT'):
                    run_bounded([sys.executable, '-c', program],
                                watched_files=[result],
                                watched_file_limit=4096, timeout=10,
                                cwd=directory)
                self.assertIsNone(sentinel.poll())
            finally:
                sentinel.terminate()
                sentinel.wait(timeout=10)

    def test_host_driver_stops_oversized_codex_result_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = self.configured_command_driver(directory, 'codex')
            driver._qualification_probe = True
            result = Path(directory) / 'result.json'
            program = ('from pathlib import Path; import sys,time; '
                       'Path(sys.argv[-1]).write_bytes(b"x" * (1024 * 1024 + 1)); '
                       'time.sleep(3)')
            started = time.monotonic()

            with patch.object(github_review_host, 'enforce_pinned_awf_skill'), \
                    self.assertRaisesRegex(ValidationError,
                                            'RESULT_FILE_LIMIT'):
                driver.run('codex', ['-c', program,
                                     '--output-last-message', str(result)])

            self.assertLess(time.monotonic() - started, 2)


class CodexIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.run = self.state / 'runs' / CONTROLLER_RUN_ID
        self.run.mkdir(parents=True)
        self.log = self.run / 'codex.jsonl'
        self.result = self.run / 'result.json'
        self.log.write_text(json.dumps({
            'type': 'thread.started', 'thread_id': CODEX_THREAD_ID,
        }) + '\n', encoding='utf-8')
        self.result.write_text('{"outcome":"CHANGED"}\n', encoding='utf-8')
        self.driver = HostDriver.__new__(HostDriver)
        self.driver.state = self.state

    def test_identity_binding_is_persisted_and_revalidated(self):
        observed = self.driver._record_codex_identity(
            CONTROLLER_RUN_ID, self.run, self.run, self.result)
        self.assertEqual(observed, CODEX_THREAD_ID)
        self.assertEqual(self.driver.codex_run_id(CONTROLLER_RUN_ID), CODEX_THREAD_ID)
        self.log.write_text(json.dumps({
            'type': 'thread.started',
            'thread_id': 'd94a0265-92e9-4210-bba9-7ccba6028720',
        }) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValidationError,
                                    'identity evidence changed after binding'):
            self.driver.codex_run_id(CONTROLLER_RUN_ID)

    def test_identity_binding_rejects_ambiguous_child_threads(self):
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({
                'type': 'thread.started',
                'thread_id': 'd94a0265-92e9-4210-bba9-7ccba6028720',
            }) + '\n')
        with self.assertRaisesRegex(ValidationError, 'exactly one thread.started'):
            self.driver._record_codex_identity(
                CONTROLLER_RUN_ID, self.run, self.run, self.result)

    def test_controller_run_id_cannot_escape_or_alias_run_directory(self):
        for value in ('../escape', '3DD68622-6223-48BC-B236-8ED6DF9A55C2',
                      '00000000-0000-0000-0000-000000000000'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValidationError,
                                            'controller run id.*canonical|controller run id.*nil'):
                    self.driver.codex_run_id(value)

    def test_production_run_log_registry_reopens_retained_evidence(self):
        self.driver._record_codex_identity(
            CONTROLLER_RUN_ID, self.run, self.run, self.result)
        registry = self.driver.register_provenance_runtime()
        entries = [{'path': 'src/a.py', 'action': 'M',
                    'old_mode': '100644', 'new_mode': '100644',
                    'old_oid': 'a' * 40, 'new_oid': 'b' * 40,
                    'old_type': 'blob', 'new_type': 'blob'}]
        self.driver._record_run_log(CONTROLLER_RUN_ID, 'c' * 40, entries)
        record = registry['run_log_verifier'](
            CODEX_THREAD_ID, 'c' * 40, entries)
        self.assertEqual(record['status'], 'MATCH')
        self.assertEqual(record['run_id'], CODEX_THREAD_ID)
        self.assertEqual(record['commit_sha'], 'c' * 40)
        self.assertEqual(record['entries'], entries)
        self.assertRegex(record['evidence_sha256'], r'^[0-9a-f]{64}$')
        self.result.write_text('{"outcome":"BLOCKED"}\n', encoding='utf-8')
        refused = registry['run_log_verifier'](
            CODEX_THREAD_ID, 'c' * 40, entries)
        self.assertEqual(refused['status'], 'MISMATCH')

    def test_production_registry_includes_supported_manifest_reproducer(self):
        registry = self.driver.register_provenance_runtime()
        registered = registry['generators']['manifest-regen']
        self.assertRegex(registered['reproducer_sha256'], r'^[0-9a-f]{64}$')
        self.assertNotEqual(registered['reproducer_sha256'], '0' * 64)
        self.assertTrue(callable(registered['callable']))
        configured = json.loads(
            (ROOT / '.agentic/review-loop/host-config.example.json').read_text(
                encoding='utf-8'))['provenance']['generators']['manifest-regen']
        self.assertEqual(configured['reproducer_sha256'],
                         registered['reproducer_sha256'])

    def test_revalidation_rejects_grown_log_before_reading_it(self):
        self.driver._record_codex_identity(
            CONTROLLER_RUN_ID, self.run, self.run, self.result)
        with self.log.open('ab') as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError(
                'oversized retained log must not be read')):
            with self.assertRaisesRegex(ValidationError, 'log.*too large'):
                self.driver.codex_run_id(CONTROLLER_RUN_ID)

    def test_revalidation_rejects_grown_result_before_reading_it(self):
        self.driver._record_codex_identity(
            CONTROLLER_RUN_ID, self.run, self.run, self.result)
        with self.result.open('ab') as stream:
            stream.truncate(1024 * 1024 + 1)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError(
                'oversized retained result must not be read')):
            with self.assertRaisesRegex(ValidationError, 'result.*too large'):
                self.driver.codex_run_id(CONTROLLER_RUN_ID)


class AmendmentWriterTests(unittest.TestCase):
    def setUp(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        self.git_executable = git
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = self.root / 'worker'
        self.remote = self.root / 'remote.git'
        self.state = self.root / 'state'
        self.state.mkdir()

        def command(cwd, *args):
            return subprocess.run(
                [git, '-C', str(cwd), *args], check=True, capture_output=True,
                text=True, encoding='utf-8').stdout.strip()
        self.command = command
        subprocess.run([git, 'init', '-b', 'main', str(self.worker)], check=True,
                       capture_output=True)
        subprocess.run([git, 'init', '--bare', str(self.remote)], check=True,
                       capture_output=True)
        command(self.worker, 'config', 'user.name', 'fixture')
        command(self.worker, 'config', 'user.email', 'fixture@example.invalid')
        command(self.worker, 'config', 'commit.gpgsign', 'false')
        command(self.worker, 'remote', 'add', 'origin', str(self.remote))
        (self.worker / 'a.txt').write_text('base\n', encoding='utf-8')
        command(self.worker, 'add', 'a.txt')
        command(self.worker, 'commit', '-m', 'base')
        self.base = command(self.worker, 'rev-parse', 'HEAD')
        command(self.worker, 'checkout', '-b', 'codex/test')
        (self.worker / 'a.txt').write_text('candidate\n', encoding='utf-8')
        command(self.worker, 'commit', '-am', 'candidate')
        self.head = command(self.worker, 'rev-parse', 'HEAD')
        command(self.worker, '-c', 'protocol.file.allow=always', 'push', 'origin',
                'HEAD:refs/heads/codex/test')

        self.driver = HostDriver.__new__(HostDriver)
        self.driver.worker = self.worker
        self.driver.state = self.state
        self.driver.c = {'allowed_paths': ['a.txt'], 'governed_source_paths': [],
                         'risk_tier': 3, 'pr': 7}
        self.driver.preflight = lambda candidate: None
        self.driver.repository_git_controls = lambda checkout: {}
        self.driver.api = lambda suffix: {'body': 'Synthetic amendment body'}
        self.driver.collect_provenance_observation = lambda: {
            'version': 1, 'repository_id': 12, 'pr': 7,
            'head': self.head, 'base': self.base, 'labels': [],
            'comments': [], 'trusted_owner_ids': [],
        }
        self.driver.validate_local_provenance = lambda *args, **kwargs: {'status': 'PASS'}

        def git_adapter(checkout, *args, **kwargs):
            if args and args[0] == 'push':
                args = ('-c', 'protocol.file.allow=always', *args)
            if kwargs.get('binary'):
                return subprocess.run(
                    [git, '-C', str(checkout), *args], check=True,
                    input=kwargs.get('input_bytes'), capture_output=True).stdout
            return command(checkout, *args)
        self.driver.git = git_adapter
        self.driver.snapshot = lambda: {
            **self.candidate,
            'head': command(self.remote, 'rev-parse', 'refs/heads/codex/test'),
        }
        self.driver.agent = self._agent
        self.candidate = {
            'repository_id': 12, 'pr': 7, 'head': self.head, 'base': self.base,
            'head_ref': 'codex/test', 'base_ref': 'main',
        }
        self.run = self.state / 'runs' / CONTROLLER_RUN_ID
        self.run.mkdir(parents=True)
        (self.run / 'codex.jsonl').write_text(json.dumps({
            'type': 'thread.started', 'thread_id': CODEX_THREAD_ID,
        }) + '\n', encoding='utf-8')
        result = self.run / 'result.json'
        result.write_text('{"outcome":"CHANGED"}\n', encoding='utf-8')
        self.driver._record_codex_identity(
            CONTROLLER_RUN_ID, self.run, self.run, result)

    def _agent(self, role, candidate, findings, run_id, files=None):
        (self.worker / 'a.txt').write_text('amended\n', encoding='utf-8')
        return {'candidate': candidate, 'outcome': 'CHANGED', 'summary': 'changed'}

    def test_amendment_commit_uses_observed_codex_identity(self):
        amended = self.driver.amend(self.candidate, [], CONTROLLER_RUN_ID)
        self.assertNotEqual(amended['head'], self.head)
        message = self.command(self.worker, 'show', '-s', '--format=%B', 'HEAD')
        self.assertIn('Writer: codex/' + CODEX_THREAD_ID, message)
        self.assertNotIn('Writer: codex/' + CONTROLLER_RUN_ID, message)

    def test_amendment_refuses_commit_when_identity_binding_is_absent(self):
        (self.run / 'codex-identity.json').unlink()
        with self.assertRaisesRegex(ValidationError, 'identity binding is missing'):
            self.driver.amend(self.candidate, [], CONTROLLER_RUN_ID)
        self.assertEqual(self.command(self.worker, 'rev-parse', 'HEAD'), self.head)


if __name__ == '__main__':
    unittest.main()
