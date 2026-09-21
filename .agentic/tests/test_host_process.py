"""Real child-process contracts using synthetic providers and real local Git.

No model, GitHub, recorded API payload, HTTPS transport, scheduler, or sandbox
qualification is claimed. Production CLI, argv, environment, pins, output schema,
review tamper checks, commits and durable loop transitions execute unchanged.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agentic import VERSION
from agentic.canonical import sha256

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures/fake_host_process.py'


class HostProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(prefix='awf-process-fixtures-')
        cls.addClassCleanup(cls.shared.cleanup)
        cls.launchers = Path(cls.shared.name)
        if os.name == 'nt':
            compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
            if not compiler.is_file():
                raise unittest.SkipTest('Native synthetic process fixtures need the Windows .NET C# compiler')
            # This native launcher preserves stdin/out/err and exit code. The
            # Python fixture is test code, never a production host adapter mode.
            source = cls.launchers / 'launcher.cs'
            def literal(value):
                return '@"' + str(value).replace('"', '""') + '"'
            source.write_text('''using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
class Launcher {
  static string Quote(string value) {
    var output = new StringBuilder("\\\"");
    int slashes = 0;
    foreach (char c in value) {
      if (c == '\\\\') { slashes++; continue; }
      if (c == '\\"') { output.Append('\\\\', slashes * 2 + 1); output.Append(c); }
      else { output.Append('\\\\', slashes); output.Append(c); }
      slashes = 0;
    }
    output.Append('\\\\', slashes * 2); output.Append('\\"'); return output.ToString();
  }
  static int Main(string[] args) {
    string kind = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]).Replace("fake-", "");
    string argv = "-B " + Quote(FIXTURE) + " " + Quote(kind);
    foreach (string arg in args) argv += " " + Quote(arg);
    var info = new ProcessStartInfo(PYTHON, argv);
    info.UseShellExecute = false; info.CreateNoWindow = true;
    info.RedirectStandardInput = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
    var child = Process.Start(info);
    var stdout = Task.Factory.StartNew(() => child.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput()));
    var stderr = Task.Factory.StartNew(() => child.StandardError.BaseStream.CopyTo(Console.OpenStandardError()));
    if (kind == "codex") Console.OpenStandardInput().CopyTo(child.StandardInput.BaseStream);
    child.StandardInput.Close(); child.WaitForExit(); Task.WaitAll(stdout, stderr); return child.ExitCode;
  }
}
'''.replace('FIXTURE', literal(FIXTURE)).replace('PYTHON', literal(sys.executable)), encoding='utf-8')
            compiled = cls.launchers / 'launcher.exe'
            result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:' + str(compiled), str(source)],
                capture_output=True, text=True, encoding='utf-8', timeout=60)
            if result.returncode:
                raise AssertionError('Cannot compile synthetic process fixture: ' + result.stdout + result.stderr)
            for kind in ['git', 'gh', 'codex']:
                shutil.copyfile(compiled, cls.launchers / ('fake-' + kind + '.exe'))
        else:
            if any(c.isspace() for c in sys.executable):
                raise unittest.SkipTest('Synthetic POSIX launcher requires an interpreter path without whitespace')
            for kind in ['git', 'gh', 'codex']:
                target = cls.launchers / ('fake-' + kind + '.exe')
                target.write_text('#!' + sys.executable + '\nimport os, sys\nos.execv(' + repr(sys.executable)
                    + ', [' + repr(sys.executable) + ', "-B", ' + repr(str(FIXTURE)) + ', ' + repr(kind) + '] + sys.argv[1:])\n', encoding='utf-8')
                target.chmod(0o700)

    def setUp(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Real local Git is required by the synthetic transport fixture')
        self.tmp = tempfile.TemporaryDirectory(prefix='awf-host-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.real_git = git
        self.worker, self.critic, self.state, self.runtime = [self.base / x for x in ['worker', 'critic', 'state', 'runtime']]
        self.worker.mkdir(); self.critic.mkdir(); self.state.mkdir()
        self.command('init', '--initial-branch=main', str(self.worker))
        for key, value in [('user.name', 'AWF synthetic fixture'), ('user.email', 'fixture@example.invalid'), ('commit.gpgsign', 'false')]:
            self.command('-C', str(self.worker), 'config', key, value)
        (self.worker / 'src').mkdir()
        (self.worker / 'src/a.py').write_text('value = 1\n', encoding='utf-8')
        (self.worker / '.github/workflows').mkdir(parents=True)
        (self.worker / '.github/workflows/ci.yml').write_text('workflow\n', encoding='utf-8')
        self.command('-C', str(self.worker), 'add', '.')
        self.command('-C', str(self.worker), 'commit', '-m', 'synthetic baseline')
        self.command('-C', str(self.worker), 'checkout', '-b', 'codex/test')
        (self.worker / 'src/a.py').write_text('value = 2\n', encoding='utf-8')
        self.command('-C', str(self.worker), 'commit', '-am', 'synthetic candidate')
        self.remote = self.base / 'remote.git'
        self.command('clone', '--bare', str(self.worker), str(self.remote))
        self.command('clone', '--branch', 'codex/test', str(self.remote), str(self.critic))
        origin = 'https://github.com/fixture/project.git'
        self.command('-C', str(self.worker), 'remote', 'add', 'origin', origin)
        self.command('-C', str(self.critic), 'remote', 'set-url', 'origin', origin)
        self.head = self.command('-C', str(self.worker), 'rev-parse', 'HEAD').strip()
        # A measured miniature runtime uses the production modules and CLI, with
        # its own exact source inventory. No integrity function is monkeypatched.
        shutil.copytree(ROOT / '.agentic/lib', self.runtime / '.agentic/lib', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / '.agentic/review-loop', self.runtime / '.agentic/review-loop')
        (self.runtime / '.agentic/scripts').mkdir()
        shutil.copyfile(ROOT / '.agentic/scripts/review_loop.py', self.runtime / '.agentic/scripts/review_loop.py')
        manifest = {'format': 'awf-manifest-1', 'template_version': VERSION,
            'files': {p.relative_to(self.runtime).as_posix(): sha256(p.read_bytes()) for p in sorted(self.runtime.rglob('*')) if p.is_file()}}
        manifest_raw = json.dumps(manifest).encode()
        (self.runtime / 'MANIFEST.json').write_bytes(manifest_raw)
        contract = self.state / 'contract.md'
        contract.write_text('Synthetic authorized scope: set src/a.py value to 3.', encoding='utf-8')
        self.config = json.loads((ROOT / '.agentic/review-loop/host-config.example.json').read_text(encoding='utf-8'))
        self.config.update(repository='fixture/project', repository_id=12, pr=7, github_host='github.com',
            head_branch='codex/test', state_dir=str(self.state), worker_checkout=str(self.worker), critic_checkout=str(self.critic),
            contract_path=str(contract), contract_sha256=sha256(contract.read_bytes()), runtime_manifest_sha256=sha256(manifest_raw),
            models={'worker': 'fixture-worker', 'critic': 'fixture-critic'}, allowed_paths=['src/a.py'],
            required_checks=[{'name': 'test', 'app_id': 1, 'workflow_path': '.github/workflows/ci.yml', 'workflow_sha256': sha256(b'workflow\n')}],
            qualification={'operator': 'synthetic fixture', 'evidence': 'Synthetic process test only; no live host qualification',
                'sandbox_verified': True, 'credentials_isolated': True, 'branch_owned': True, 'single_host_database': True})
        self.config['executables'] = {kind: {'path': str(self.launchers / ('fake-' + kind + '.exe')),
            'sha256': sha256((self.launchers / ('fake-' + kind + '.exe')).read_bytes())} for kind in ['git', 'gh', 'codex']}
        self.config_path = self.state / 'host.json'
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.calls = self.base / 'process-calls.jsonl'
        self.fixture_path = self.base / 'fixture.json'
        self.fixture_config = {'real_git': self.real_git, 'remote': str(self.remote), 'calls': str(self.calls)}
        self.fixture_path.write_text(json.dumps(self.fixture_config), encoding='utf-8')
        self.env = dict(os.environ, AWF_FIXTURE_CONFIG=str(self.fixture_path), PYTHONDONTWRITEBYTECODE='1')
        for name in ['GH_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY', 'CODEX_API_KEY']:
            self.env[name] = 'synthetic-not-a-secret'
        self.env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.fsmonitor', GIT_CONFIG_VALUE_0='synthetic-invalid-program')

    def command(self, *args):
        result = subprocess.run([self.real_git, *args], capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout

    def mode(self, **values):
        self.fixture_config.update(values)
        self.fixture_path.write_text(json.dumps(self.fixture_config), encoding='utf-8')

    def cli(self, command, code=0):
        result = subprocess.run([sys.executable, '-B', str(self.runtime / '.agentic/scripts/review_loop.py'),
            '--config', str(self.config_path), command], cwd=self.base, env=self.env,
            capture_output=True, text=True, encoding='utf-8', timeout=120)
        logs = '\n'.join(p.read_text(encoding='utf-8') for p in (self.state / 'runs').glob('*/codex.jsonl')) if result.returncode != code else ''
        self.assertEqual(result.returncode, code, result.stdout + result.stderr + logs)
        self.assertNotIn('Traceback (most recent call last)', result.stderr)
        return json.loads(result.stdout or result.stderr)

    def observations(self, kind=None):
        values = [json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()]
        return [value for value in values if kind is None or value['kind'] == kind]

    def test_production_cli_check_enroll_tick_real_process_amendment_cycle(self):
        self.assertEqual(self.cli('check')['status'], 'PREFLIGHT_PASSED')
        self.assertEqual(self.cli('enroll')['phase'], 'REVIEW')
        self.assertEqual([self.cli('tick')['phase'] for _ in range(4)], ['AMEND', 'REVIEW', 'WAIT_CI', 'READY_FOR_FINAL_GATE'])
        state = self.cli('status')
        self.assertEqual(state['agent_runs'], 3)
        self.assertEqual(state['cycles'], 1)
        new = self.command('--git-dir', str(self.remote), 'rev-parse', 'codex/test').strip()
        self.assertNotEqual(new, self.head)
        self.assertEqual(self.command('-C', str(self.worker), 'rev-parse', 'HEAD^').strip(), self.head)
        self.assertEqual(self.command('-C', str(self.worker), 'status', '--porcelain'), '')
        models = self.observations('codex')
        self.assertEqual(len(models), 3)
        for call in models:
            self.assertNotIn('GH_TOKEN', call['environment'])
            self.assertNotIn('GITHUB_TOKEN', call['environment'])
            self.assertIn('--output-schema', call['argv'])
            self.assertIn('--output-last-message', call['argv'])
            self.assertEqual(call['environment']['PYTHONDONTWRITEBYTECODE'], '1')
        for call in self.observations():
            self.assertNotIn('OPENAI_API_KEY', call['environment'])
            self.assertNotIn('GIT_CONFIG_COUNT', call['environment'])
            self.assertEqual(call['environment']['GIT_TERMINAL_PROMPT'], '0')
            if call['kind'] == 'git':
                self.assertIn('protocol.file.allow=never', call['argv'])
                self.assertIn('core.fsmonitor=false', call['argv'])
                self.assertTrue(any(x.startswith('core.hooksPath=') for x in call['argv']))
            elif call['kind'] == 'gh':
                self.assertEqual(call['argv'][:5], ['api', '--hostname', 'github.com', '--method', 'GET'])
                self.assertEqual(call['environment']['GH_TOKEN'], 'synthetic-not-a-secret')
        self.assertTrue(any('show' in call['argv'] for call in self.observations('git')))

    def test_null_ci_app_pauses_with_pinned_identity_diagnostic(self):
        self.mode(critic_mode='approve', api_mode='null_app')
        self.cli('enroll')
        self.assertEqual(self.cli('tick')['phase'], 'WAIT_CI')
        paused = self.cli('tick', code=2)
        self.assertEqual(paused['phase'], 'PAUSED')
        self.assertIn('missing/null app identity', paused['reason'])
        self.assertNotIn('TypeError', paused['reason'])

    def test_real_critic_process_cannot_mutate_checkout(self):
        self.mode(agent_mode='mutate_checkout')
        self.cli('enroll')
        paused = self.cli('tick', code=2)
        self.assertIn('Critic mutated its checkout', paused['reason'])
        self.assertIsNotNone(paused['inflight'])

    def test_agent_schema_rejection_retains_run_and_log(self):
        self.mode(agent_mode='schema_invalid')
        self.cli('enroll')
        paused = self.cli('tick', code=2)
        self.assertIn('Additional properties', paused['reason'])
        run = self.state / 'runs' / paused['inflight']['id']
        self.assertTrue((run / 'codex.jsonl').is_file())
        self.assertTrue((run / 'result.json').is_file())

    def test_nonzero_agent_process_is_retained_without_replay(self):
        self.mode(agent_mode='nonzero')
        self.cli('enroll')
        paused = self.cli('tick', code=2)
        self.assertIn('codex failed; inspect retained run log', paused['reason'])
        self.assertEqual(self.cli('tick', code=2)['inflight'], paused['inflight'])
        self.assertEqual(len(self.observations('codex')), 1)

    def test_gh_malformed_and_nonzero_process_fail_before_enrollment(self):
        for mode in ['malformed', 'nonzero']:
            self.mode(api_mode=mode)
            rejected = self.cli('check', code=2)
            self.assertEqual(rejected['status'], 'REJECTED')
            self.assertFalse(rejected['merge_authorized'])
        self.assertEqual(self.observations('codex'), [])

    def test_unsupported_host_fails_before_external_process(self):
        self.config['github_host'] = 'ghe.example.invalid'
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        rejected = self.cli('check', code=2)
        self.assertIn('supports github.com only', rejected['reason'])
        self.assertFalse(self.calls.exists())

    def test_cli_rejects_runtime_tampering_before_external_process(self):
        (self.runtime / '.agentic/review-loop/critic-prompt.md').write_text('tampered', encoding='utf-8')
        rejected = self.cli('check', code=2)
        self.assertIn('Source digest mismatch', rejected['reason'])
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
