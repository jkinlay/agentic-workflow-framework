import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))

from agentic import ValidationError
from agentic.providers.github_review_host import HostDriver


CODEX_THREAD_ID = '01a1317f-dadb-7071-80de-12832161734c'
CONTROLLER_RUN_ID = '3dd68622-6223-48bc-b236-8ed6df9a55c2'


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

        def git_adapter(checkout, *args):
            if args and args[0] == 'push':
                args = ('-c', 'protocol.file.allow=always', *args)
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
