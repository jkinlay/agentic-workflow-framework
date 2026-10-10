"""Offline qualification-procedure tests; no network or live model is used."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.canonical import sha256
from agentic.review_loop import LoopStore
from agentic.review_qualification import (collect_qualification,
    host_binding_sha256, validate_config_qualification)
from agentic.providers.github_review_host import HostDriver
from source_only import skip_unless_source_repo


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = {
    'repository_id': 12, 'pr': 77, 'head': 'a' * 40, 'base': 'b' * 40,
    'head_ref': 'codex/disposable-qualification', 'base_ref': 'main',
}


class FakeQualificationDriver:
    def __init__(self, config, *, readable_auth=False):
        self.config = config
        self.readable_auth = readable_auth
        self.calls = []

    def snapshot(self):
        return deepcopy(CANDIDATE)

    def preflight(self, candidate):
        self.calls.append(('preflight', candidate['pr']))

    def qualification_agent(self, role, record_id, probe):
        self.calls.append((role, record_id))
        if role == 'worker':
            Path(probe['checkout_marker']).write_text(probe['marker_text'],
                                                       encoding='utf-8')
        return {
            'result': {
                'role': role,
                'checkout_write': 'DENIED' if role == 'critic' else 'SUCCEEDED',
                'outside_write': 'DENIED',
                'network': 'DENIED',
                'credential_environment_names': [],
                'agent_auth_files': [
                    {'location': '~/.codex/auth.json',
                     'status': ('READABLE' if self.readable_auth and role == 'critic'
                                else 'DENIED')},
                    {'location': '$CODEX_HOME/auth.json', 'status': 'ABSENT'},
                ],
            },
            'artifacts': {name: 'f' * 64 for name in
                          ('input_sha256', 'effective_config_sha256',
                           'codex_log_sha256', 'result_sha256')},
        }

    def git(self, checkout, *args):
        self.calls.append(('git', Path(checkout).name, args[0]))
        return ''


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ('state', 'worker', 'critic'):
            (self.base / name).mkdir()
        self.config = {
            'version': 1, 'github_host': 'github.com',
            'repository': 'fixture/project', 'repository_id': 12,
            'base_branch': 'main',
            'state_dir': str(self.base / 'state'),
            'worker_checkout': str(self.base / 'worker'),
            'critic_checkout': str(self.base / 'critic'),
            'runtime_manifest_sha256': 'd' * 64,
            'executables': {name: {'path': f'<fixture>/{name}.exe',
                                   'sha256': 'e' * 64}
                            for name in ('git', 'gh', 'codex')},
            'models': {'worker': 'fixture-worker', 'critic': 'fixture-critic'},
            'reasoning_effort': {'worker': 'medium', 'critic': 'high'},
            'codex_config_overrides': {'windows.sandbox': 'elevated'},
            'agent_timeout_seconds': 1800,
            'qualification': {
                'operator': 'Jonathan fixture',
                'evidence_path': str(self.base / 'state' / 'qualification.json'),
                'evidence_sha256': 'CHANGE_ME', 'max_age_seconds': 604800,
                'sandbox_verified': False, 'credentials_isolated': False,
                'branch_owned': False, 'single_host_database': False,
            },
        }
        self.store = LoopStore(self.base / 'state')
        self.addCleanup(self.store.close)

    def collect(self, **kwargs):
        driver = FakeQualificationDriver(self.config, **kwargs)
        result = collect_qualification(
            self.config, driver, self.store, confirm_disposable_pr=True,
            record_id='00000000-0000-0000-0000-000000000123')
        return result, driver

    def pin(self, result):
        self.config['qualification']['evidence_sha256'] = result['evidence_sha256']
        for name in ('sandbox_verified', 'credentials_isolated',
                     'branch_owned', 'single_host_database'):
            self.config['qualification'][name] = True

    def launchable_driver(self):
        self.config['command_timeout_seconds'] = 30
        executable = {
            'path': sys.executable,
            'sha256': sha256(Path(sys.executable).read_bytes()),
        }
        self.config['executables'] = {
            name: deepcopy(executable) for name in ('git', 'gh', 'codex')
        }
        config_path = self.base / 'state' / 'host-config.json'
        config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.config['_config_path'] = str(config_path)
        self.config['config_hash'] = sha256(config_path.read_bytes())
        return HostDriver(self.config, ROOT)

    @staticmethod
    def isolated_agent_environment():
        return {
            'PATH': '/fixture/bin',
            'HOME': '/fixture/home',
            'USERPROFILE': '/fixture/profile',
            'CODEX_HOME': '/fixture/codex',
        }

    def test_live_shape_probes_write_hashed_record_without_setting_config(self):
        before = deepcopy(self.config['qualification'])
        result, driver = self.collect()
        self.assertEqual(result['result'], 'PASS')
        self.assertFalse(result['qualification_updated'])
        self.assertEqual(self.config['qualification'], before)
        evidence = Path(result['evidence_path'])
        self.assertEqual(sha256(evidence.read_bytes()), result['evidence_sha256'])
        record = json.loads(evidence.read_text(encoding='utf-8'))
        self.assertEqual(record['candidate'], CANDIDATE)
        self.assertEqual(record['probes']['worker']['host_observation']
                         ['checkout_marker'], 'EXACT')
        self.assertFalse((self.base / 'worker' /
                          '.awf-qualification-00000000-0000-0000-0000-000000000123-worker.tmp').exists())
        self.assertIn(('critic', result['record_id']), driver.calls)
        self.pin(result)
        validated = validate_config_qualification(self.config, self.base / 'state')
        self.assertEqual(validated['result'], 'PASS')

    def test_hash_binding_staleness_and_host_change_fail_closed(self):
        result, _ = self.collect()
        self.pin(result)
        self.config['qualification']['evidence_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValidationError, 'SHA-256 mismatch'):
            validate_config_qualification(self.config, self.base / 'state')
        self.config['qualification']['evidence_sha256'] = result['evidence_sha256']
        with self.assertRaisesRegex(ValidationError, 'stale'):
            validate_config_qualification(self.config, self.base / 'state',
                                          now='2100-01-01T00:00:00Z')
        self.config['models']['critic'] = 'different-critic'
        with self.assertRaisesRegex(ValidationError, 'different host controls'):
            validate_config_qualification(self.config, self.base / 'state')

    def test_awf31_r3_001_new_credential_name_invalidates_and_refuses_worker_and_critic_launches(self):
        environment = self.isolated_agent_environment()
        with patch.dict(os.environ, environment, clear=True):
            driver = self.launchable_driver()
            result, _ = self.collect()
            self.pin(result)
            self.assertEqual(
                validate_config_qualification(self.config, self.base / 'state')['result'],
                'PASS')
            baseline_binding = json.loads(
                Path(result['evidence_path']).read_text(encoding='utf-8')
            )['host_binding_sha256']

            with patch.dict(os.environ,
                            {'LATE_DATABASE_SECRET': 'fixture-value-one'},
                            clear=False):
                with self.assertRaisesRegex(ValidationError,
                                            'credential|host controls'):
                    validate_config_qualification(self.config,
                                                  self.base / 'state')
                with patch('agentic.providers.github_review_host.subprocess.run') as launch:
                    for sandbox in ('workspace-write', 'read-only'):
                        with self.subTest(sandbox=sandbox), \
                             self.assertRaisesRegex(ValidationError,
                                                    'credential|host controls'):
                            driver.run('codex', ['exec', '--sandbox', sandbox])
                    launch.assert_not_called()

            with patch.dict(os.environ,
                            {'LATE_DATABASE_SECRET': 'fixture-value-two'},
                            clear=False):
                second_binding = host_binding_sha256(self.config)
            self.assertNotEqual(baseline_binding, second_binding)

            with patch.dict(os.environ,
                            {'LATE_DATABASE_SECRET': 'fixture-value-one'},
                            clear=False):
                first_value_binding = host_binding_sha256(self.config)
            self.assertEqual(first_value_binding, second_binding)

    def test_awf31_r3_001_agent_auth_root_changes_invalidate_and_refuse_launch(self):
        environment = self.isolated_agent_environment()
        with patch.dict(os.environ, environment, clear=True):
            driver = self.launchable_driver()
            result, _ = self.collect()
            self.pin(result)

            for name in ('CODEX_HOME', 'HOME', 'USERPROFILE'):
                with self.subTest(name=name), \
                     patch.dict(os.environ, {name: f'/changed/{name.lower()}'},
                                clear=False), \
                     self.assertRaisesRegex(ValidationError,
                                            'auth-root|host controls'), \
                     patch('agentic.providers.github_review_host.subprocess.run') as launch:
                    driver.run('codex', ['exec', '--sandbox', 'read-only'])
                launch.assert_not_called()

    def test_awf31_r3_001_unchanged_environment_remains_pass_and_launches(self):
        environment = self.isolated_agent_environment()
        with patch.dict(os.environ, environment, clear=True):
            driver = self.launchable_driver()
            result, _ = self.collect()
            self.pin(result)
            self.assertEqual(
                validate_config_qualification(self.config, self.base / 'state')['result'],
                'PASS')
            completed = unittest.mock.Mock(returncode=0, stdout='')
            with patch('agentic.providers.github_review_host.subprocess.run',
                       return_value=completed) as launch:
                self.assertEqual(
                    driver.run('codex', ['exec', '--sandbox', 'read-only']), '')
            launch.assert_called_once()

    def test_readable_agent_auth_is_an_explicit_blocking_finding(self):
        result, _ = self.collect(readable_auth=True)
        self.assertEqual(result['result'], 'FAIL')
        self.assertFalse(result['qualification']['credentials_isolated'])
        self.assertIn('QUAL-CRITIC-AGENT-AUTH',
                      {finding['id'] for finding in result['findings']})
        record = json.loads(Path(result['evidence_path']).read_text(encoding='utf-8'))
        observation = record['probes']['critic']['result']['agent_auth_files'][0]
        self.assertEqual(observation,
                         {'location': '~/.codex/auth.json', 'status': 'READABLE'})

    def test_bare_booleans_and_unconfirmed_disposable_pr_are_refused(self):
        legacy = deepcopy(self.config)
        legacy['qualification'] = {
            'operator': 'fixture', 'evidence': 'free text',
            'sandbox_verified': True, 'credentials_isolated': True,
            'branch_owned': True, 'single_host_database': True,
        }
        with self.assertRaisesRegex(ValidationError, 'Legacy bare'):
            validate_config_qualification(legacy, self.base / 'state')
        with self.assertRaisesRegex(ValidationError, 'disposable PR'):
            collect_qualification(self.config, FakeQualificationDriver(self.config),
                                  self.store, confirm_disposable_pr=False)

    def test_protected_evidence_paths_and_hardlink_alias_fail_before_probes(self):
        state = self.base / 'state'
        host_config = state / 'host.json'
        contract = state / 'contract.md'
        host_config.write_bytes(b'host configuration sentinel')
        contract.write_bytes(b'pinned contract sentinel')
        self.config['_config_path'] = str(host_config)
        self.config['contract_path'] = str(contract)
        protected = [
            host_config,
            contract,
            state / 'review-loop.sqlite3',
            state / 'writer-lock.sqlite3',
            state / 'publication-deny.json',
        ]
        for path in protected:
            with self.subTest(path=path.name):
                before = path.read_bytes() if path.is_file() else None
                self.config['qualification']['evidence_path'] = str(path)
                driver = FakeQualificationDriver(self.config)
                with self.assertRaisesRegex(ValidationError,
                                            'aliases protected host state'):
                    collect_qualification(
                        self.config, driver, self.store,
                        confirm_disposable_pr=True,
                        record_id='00000000-0000-0000-0000-000000000123')
                self.assertEqual(driver.calls, [])
                if before is not None:
                    self.assertEqual(path.read_bytes(), before)

        alias = state / 'qualification-alias.json'
        try:
            alias.hardlink_to(host_config)
        except OSError as exc:
            self.skipTest(f'hardlink alias unavailable: {exc}')
        self.config['qualification']['evidence_path'] = str(alias)
        driver = FakeQualificationDriver(self.config)
        with self.assertRaisesRegex(ValidationError,
                                    'aliases protected host state'):
            collect_qualification(
                self.config, driver, self.store, confirm_disposable_pr=True,
                record_id='00000000-0000-0000-0000-000000000123')
        self.assertEqual(driver.calls, [])
        self.assertEqual(host_config.read_bytes(), b'host configuration sentinel')

    @skip_unless_source_repo()
    def test_generator_reproduces_qualification_schema_and_config(self):
        spec = importlib.util.spec_from_file_location(
            'awf_generate_review_loop_qualification',
            ROOT / 'scripts/generate_review_loop.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as name:
            module.ROOT = Path(name)
            module.main()
            for relative in ('qualification-probe-result.schema.json',
                             'host-config.example.json'):
                self.assertEqual((module.ROOT / relative).read_bytes(),
                                 (ROOT / '.agentic/review-loop' / relative).read_bytes())

    def test_review_loop_qualify_cli_uses_unqualified_loader_and_writes_record(self):
        spec = importlib.util.spec_from_file_location(
            'awf_review_loop_qualification_cli',
            ROOT / '.agentic/scripts/review_loop.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observed = {}
        def load_config(path, root, **kwargs):
            observed.update(kwargs)
            return self.config
        stdout, stderr = StringIO(), StringIO()
        with patch.object(module, 'verify_installed', return_value='d' * 64), \
             patch.object(module, 'load_config', side_effect=load_config), \
             patch.object(module, 'HostDriver',
                          side_effect=lambda config, root:
                              FakeQualificationDriver(config)), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = module.main([
                '--config', str(self.base / 'state' / 'config.json'),
                'qualify', '--confirm-disposable-pr'])
        self.assertEqual((code, stderr.getvalue()), (0, ''))
        self.assertFalse(observed['require_qualification'])
        value = json.loads(stdout.getvalue())
        self.assertEqual(value['status'], 'QUALIFICATION_EVIDENCE_RECORDED')
        self.assertEqual(value['result'], 'PASS')
        self.assertFalse(value['qualification_updated'])


if __name__ == '__main__':
    unittest.main()
