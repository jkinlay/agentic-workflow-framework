"""Real local Git/installed bytes with explicitly synthetic GitHub observations."""
from __future__ import annotations

import base64
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION, ValidationError
from agentic import adoption_status as status
from agentic.canonical import load, sha256
from agentic.installer import CONFIG, INSTALLED, PROVENANCE, json_bytes
from agentic.lifecycle import definition


class AdoptionStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='awf-status-fixture-')
        cls.root = Path(cls.temporary.name) / 'project'
        cls.root.mkdir()
        cls.source = Path(cls.temporary.name) / 'approved-source'
        cls.source.mkdir()
        cls.git = shutil.which('git')
        if not cls.git:
            cls.temporary.cleanup()
            raise unittest.SkipTest('Git executable unavailable')
        schemas = cls.root / '.agentic/schemas'
        shutil.copytree(ROOT / '.agentic/schemas', schemas)
        cls.config = load(ROOT / '.agentic/examples/PROJECT_CONFIG.yaml')
        cls.config['github']['base_branch'] = 'trunk'
        files = {p.relative_to(cls.root).as_posix(): p.read_bytes() for p in schemas.glob('*.json')}
        files['.agentic/workflow.yaml'] = json_bytes(definition())
        files['AGENTS.md'] = b'# Explicit synthetic status fixture\n'
        for name, raw in files.items():
            (cls.root / name).write_bytes(raw)
        manifest = {'format': 'awf-manifest-1', 'template_version': VERSION,
                    'files': {name: sha256(raw) for name, raw in files.items()}}
        manifest_text = json_bytes(manifest).decode()
        manifest_pin = sha256(manifest_text.encode())
        cls.manifest_pin = manifest_pin
        for name, raw in files.items():
            (cls.source / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.source / name).write_bytes(raw)
        (cls.source / 'MANIFEST.json').write_bytes(manifest_text.encode())
        receipt = {'template_version': VERSION, 'source_manifest_sha256': manifest_pin,
                   'source_manifest_json': manifest_text, 'immutable_files': manifest['files'],
                   'project_id': cls.config['project']['id'],
                   'install_id': '953e9182-2b55-40ec-9f0e-2f4ab863b641',
                   'initial_config_sha256': sha256(json_bytes(cls.config)), 'mutable_paths': [CONFIG]}
        files[CONFIG] = json_bytes(cls.config)
        files[INSTALLED] = json_bytes(receipt)
        files[PROVENANCE] = json_bytes({'template': {'version': VERSION},
                                       'installation': {'source_manifest_sha256': manifest_pin, 'install_id': receipt['install_id']}})
        cls.files = files
        for name, raw in files.items():
            (cls.root / name).write_bytes(raw)
        from agentic.operating import initialize_operating
        initialize_operating(cls.root, cls.config)
        def git(*args):
            completed = subprocess.run([cls.git, '-c', 'core.autocrlf=false', '-c', 'core.fsmonitor=false',
                '-c', 'core.hooksPath=' + str(cls.root / 'no-hooks'), '-C', str(cls.root), *args],
                capture_output=True, timeout=30, check=True)
            return completed.stdout.decode().strip()
        git('init', '-b', 'trunk')
        git('remote', 'add', 'origin', 'https://github.com/fixture/example.git')
        git('add', '.')
        git('-c', 'user.name=AWF Synthetic Fixture', '-c', 'user.email=awf@example.invalid', 'commit', '-m', 'Synthetic adoption fixture')
        cls.head = git('rev-parse', 'HEAD')
        cls.base = 'repos/fixture/example'
        entry = lambda name, raw: {'path': name, 'type': 'blob', 'mode': '100644', 'sha': status.blob_sha(raw)}
        cls.responses = {
            cls.base: {'id': 101, 'full_name': 'fixture/example', 'default_branch': 'trunk'},
            cls.base + '/branches/trunk': {'name': 'trunk', 'commit': {'sha': cls.head}},
            cls.base + '/pulls/7': {'number': 7, 'merged': True, 'merge_commit_sha': cls.head,
                                  'base': {'ref': 'trunk', 'repo': {'id': 101}}},
            cls.base + '/pulls/7/files?per_page=100&page=1': [
                {'filename': INSTALLED, 'status': 'added', 'sha': status.blob_sha(files[INSTALLED])}],
            cls.base + f'/commits/{cls.head}/pulls?per_page=100': [
                {'number': 7, 'merged_at': '2026-09-14T12:00:00Z', 'base': {'ref': 'trunk', 'repo': {'id': 101}}}],
            cls.base + f'/contents/{INSTALLED}?ref={cls.head}': {
                'type': 'file', 'path': INSTALLED, 'sha': status.blob_sha(files[INSTALLED]),
                'encoding': 'base64', 'content': base64.b64encode(files[INSTALLED]).decode()},
            cls.base + f'/git/commits/{cls.head}': {'sha': cls.head, 'tree': {'sha': 'a' * 40}},
            cls.base + '/git/trees/' + 'a' * 40: {'truncated': False, 'tree': [
                entry('AGENTS.md', files['AGENTS.md']), {'path': '.agentic', 'type': 'tree', 'mode': '040000', 'sha': 'b' * 40}]},
            cls.base + '/git/trees/' + 'b' * 40 + '?recursive=1': {'truncated': False, 'tree': [
                entry(name.removeprefix('.agentic/'), raw) for name, raw in files.items() if name.startswith('.agentic/')]},
        }

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.api = copy.deepcopy(self.responses)
        self.requests = []
        self.reader = None

    def tearDown(self):
        for name, raw in self.files.items():
            (self.root / name).write_bytes(raw)

    def observe(self, pr=7):
        def read(endpoint, deadline, gh):
            self.requests.append(endpoint)
            value = self.reader(endpoint) if self.reader else self.api[endpoint]
            return copy.deepcopy(value), len(json_bytes(value))
        # Keep the real bounded Git implementation and real adapter tuple contract.
        with patch.object(status, 'host_executable', side_effect=lambda name, root: self.git if name == 'git' else sys.executable), \
                patch.object(status, '_gh_get', side_effect=read):
            return status.project_status(self.root, adoption_pr=pr, release_source=self.source,
                                         expected_manifest_sha256=self.manifest_pin)

    def test_accepted_default_checkout_and_merged_receipt_are_active(self):
        result = self.observe()
        self.assertEqual(result['project_state'], 'ACTIVE', result)
        self.assertEqual(result['accepted_checkout'], 'VERIFIED')
        self.assertFalse(result['execution_authority'])
        self.assertEqual(result['line'], f'AWF {VERSION}: ACTIVE — streams 3/6')
        preflight_next = result['host_preflight']['next_action']
        rendered = status.render_status(result)
        if preflight_next:
            self.assertEqual(rendered, result['line'] + '\nNext: Host preflight WARN: ' + preflight_next)
        else:
            self.assertNotIn('Next:', rendered)

    def test_one_command_discovers_receipt_adoption_pr(self):
        result = self.observe(pr=None)
        self.assertEqual((result['project_state'], result['adoption_pr']), ('ACTIVE', 7), result)

    def test_status_denominator_uses_enabled_broker_ceiling(self):
        config = copy.deepcopy(self.config)
        config['execution']['host_broker'].update(enabled=True, broker_id='synthetic-broker', max_workers=3)
        (self.root / CONFIG).write_bytes(json_bytes(config))
        with patch.object(status, 'Observation', side_effect=ValidationError('No live metadata in this test')):
            result = status.project_status(self.root)
        self.assertEqual('CONFIGURED', result['project_state'])
        self.assertIn('streams 3/3', result['line'])
        self.assertEqual(3, result['operating']['effective_ceiling'])
        self.assertEqual('$.execution.host_broker.max_workers', result['operating']['effective_ceiling_governance_path'])

    def test_installed_unconfigured_reports_paths_without_gh(self):
        config = copy.deepcopy(self.config)
        config['validation']['commands'] = ['CHANGE_ME_TEST_COMMAND']
        (self.root / CONFIG).write_bytes(json_bytes(config))
        with patch.object(status, 'Observation', side_effect=AssertionError('No metadata call for unconfigured project')):
            result = status.project_status(self.root)
        self.assertEqual(result['project_state'], 'INSTALLED')
        self.assertEqual(result['decision_codes'], ['report_state_installed_unconfigured'])
        self.assertNotIn('report_active', result['decision_codes'])
        self.assertIn('$.validation.commands[0]', result['next_action'])
        self.assertIn('--test-command', result['next_action'])

    def test_no_gh_preserves_configured_and_action(self):
        with patch.object(status, 'Observation', side_effect=ValidationError('Trusted host gh executable is unavailable')):
            result = status.project_status(self.root, release_source=self.source, expected_manifest_sha256=self.manifest_pin)
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('gh', result['next_action'])

    def test_absent_host_home_has_clean_remedy_and_retains_machine_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            absent_home = str(Path(directory) / '.codex')
            with patch.dict(os.environ, {'CODEX_HOME': absent_home}), \
                    patch.object(status, 'Observation', side_effect=AssertionError('No API call without release trust')):
                result = status.project_status(self.root)
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('--release-source', result['next_action'])
        self.assertIn('--expected-manifest-sha256', result['next_action'])
        rendered = status.render_status(result)
        self.assertNotIn('[Errno', rendered)
        self.assertNotIn('No such file or directory', rendered)
        self.assertTrue(result['observation_error'])
        self.assertNotIn('report_active', result['decision_codes'])

    def test_filesystem_observation_failure_keeps_raw_error_out_of_next_action(self):
        with patch.object(status, 'Observation', side_effect=OSError(2, 'Synthetic missing host executable')):
            result = status.project_status(self.root, release_source=self.source,
                                           expected_manifest_sha256=self.manifest_pin)
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertNotIn('[Errno', status.render_status(result))
        self.assertIn('Synthetic missing host executable', result['observation_error'])

    def test_unmerged_pr_never_reports_active(self):
        self.api[self.base + '/pulls/7']['merged'] = False
        result = self.observe()
        self.assertEqual((result['project_state'], result['adoption']), ('CONFIGURED', 'NOT_MERGED'))
        self.assertIn('Merge adoption PR #7', result['next_action'])

    def test_unrelated_merged_pr_does_not_accept_new_receipt(self):
        self.api[self.base + f'/contents/{INSTALLED}?ref={self.head}']['sha'] = 'f' * 40
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('does not accept', result['next_action'])

    def test_explicit_unrelated_pr_cannot_inherit_an_unreviewed_receipt(self):
        unrelated = copy.deepcopy(self.api[self.base + '/pulls/7'])
        unrelated['number'] = 8
        self.api[self.base + '/pulls/8'] = unrelated
        result = self.observe(pr=8)
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('did not introduce', result['next_action'])
        self.assertNotIn(self.base + '/pulls/8', self.requests)

    def test_accepted_tree_must_match_every_governance_blob(self):
        self.api[self.base + '/git/trees/' + 'a' * 40]['tree'][0]['sha'] = 'f' * 40
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('AGENTS.md', result['next_action'])

    def test_truncated_remote_tree_is_not_accepted(self):
        self.api[self.base + '/git/trees/' + 'b' * 40 + '?recursive=1']['truncated'] = True
        self.assertEqual(self.observe()['project_state'], 'CONFIGURED')

    def test_different_default_head_requires_checkout(self):
        self.api[self.base + '/branches/trunk']['commit']['sha'] = 'f' * 40
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('Check out', result['next_action'])

    def test_mid_observation_default_change_is_rejected(self):
        seen = 0
        def reader(endpoint):
            nonlocal seen
            if endpoint == self.base + '/branches/trunk':
                seen += 1
                if seen == 2:
                    return {'name': 'trunk', 'commit': {'sha': 'f' * 40}}
            return self.api[endpoint]
        self.reader = reader
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('changed during observation', result['next_action'])

    def test_receipt_cannot_omit_required_files(self):
        receipt = json.loads(self.files[INSTALLED])
        del receipt['immutable_files']['AGENTS.md']
        (self.root / INSTALLED).write_bytes(json_bytes(receipt))
        result = self.observe()
        self.assertIsNone(result['project_state'])
        self.assertFalse(result['integrity_valid'])

    def test_missing_embedded_manifest_is_not_active(self):
        receipt = json.loads(self.files[INSTALLED])
        del receipt['source_manifest_json']
        (self.root / INSTALLED).write_bytes(json_bytes(receipt))
        result = self.observe()
        self.assertIsNone(result['project_state'])
        self.assertIn('source_manifest_json', result['next_action'])

    def test_receipt_must_actually_change_in_adoption_pr(self):
        self.api[self.base + '/pulls/7/files?per_page=100&page=1'] = []
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('did not add or modify', result['next_action'])

    def test_mutable_project_identity_must_match_receipt(self):
        config = copy.deepcopy(self.config)
        config['project']['id'] = '11a4d8c0-7009-4cae-a609-ea4447a6a452'
        (self.root / CONFIG).write_bytes(json_bytes(config))
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('identity', result['next_action'])

    def test_external_pin_is_required_and_project_local_source_is_rejected(self):
        for source, pin in [(self.source, '0' * 64), (self.root, self.manifest_pin), (self.source, None)]:
            result = status.project_status(self.root, release_source=source, expected_manifest_sha256=pin)
            self.assertEqual(result['project_state'], 'CONFIGURED', result)
            self.assertNotIn('report_active', result['decision_codes'])

    def test_missing_install_ids_cannot_match_each_other(self):
        receipt = json.loads(self.files[INSTALLED])
        provenance = json.loads(self.files[PROVENANCE])
        del receipt['install_id']
        del provenance['installation']['install_id']
        (self.root / INSTALLED).write_bytes(json_bytes(receipt))
        (self.root / PROVENANCE).write_bytes(json_bytes(provenance))
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED')
        self.assertIn('installation UUID', result['next_action'])

    def test_status_does_not_execute_product_clean_filters(self):
        marker = Path(self.temporary.name) / 'filter-executed.txt'
        attribute = self.root / '.gitattributes'
        attribute.write_text('* filter=awf-inert-marker\n', encoding='utf-8')
        subprocess.run([self.git, '-C', str(self.root), 'config', 'filter.awf-inert-marker.clean',
                        'echo executed > "' + str(marker).replace('\\', '/') + '"'], check=True, timeout=20)
        try:
            result = self.observe()
            self.assertEqual(result['project_state'], 'ACTIVE', result)
            self.assertFalse(marker.exists())
        finally:
            subprocess.run([self.git, '-C', str(self.root), 'config', '--unset', 'filter.awf-inert-marker.clean'], check=True, timeout=20)
            attribute.unlink()

    def test_malformed_remote_objects_remain_structured(self):
        cases = [(self.base, None), (self.base, {'id': 101, 'default_branch': 'trunk', 'full_name': None}),
                 (self.base + '/branches/trunk', {'name': 'trunk', 'commit': []}),
                 (self.base + '/pulls/7', {'number': 7, 'base': None}),
                 (self.base + f'/git/commits/{self.head}', {'sha': self.head, 'tree': None})]
        for endpoint, malformed in cases:
            with self.subTest(endpoint=endpoint, malformed=malformed):
                self.api = copy.deepcopy(self.responses)
                self.api[endpoint] = malformed
                result = self.observe()
                self.assertEqual(result['project_state'], 'CONFIGURED', result)
                self.assertTrue(result['next_action'])

    def test_cli_status_json_and_plain_have_same_state(self):
        from agentic.cli import main
        result = {'line': f'AWF {VERSION}: INSTALLED', 'project_state': 'INSTALLED',
                  'next_action': 'Configure $.github.repository_id using --repository-id'}
        with patch.object(status, 'project_status', return_value=result):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--root', str(self.root), 'status']), 0)
            self.assertEqual(out.getvalue().strip(), status.render_status(result))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--root', str(self.root), 'status', '--json']), 0)
            self.assertEqual(json.loads(out.getvalue()), result)


if __name__ == '__main__':
    unittest.main()
