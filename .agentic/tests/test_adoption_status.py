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
from agentic.providers import github_status as status
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
        cls.run_git = staticmethod(git)
        git('init', '-b', 'trunk')
        git('remote', 'add', 'origin', 'https://github.com/fixture/example.git')
        git('-c', 'user.name=AWF Synthetic Fixture', '-c', 'user.email=awf@example.invalid',
            'commit', '--allow-empty', '-m', 'Synthetic baseline fixture')
        cls.before_receipt = git('rev-parse', 'HEAD')
        git('add', '.')
        git('-c', 'user.name=AWF Synthetic Fixture', '-c', 'user.email=awf@example.invalid', 'commit', '-m', 'Synthetic adoption fixture')
        cls.head = git('rev-parse', 'HEAD')
        cls.base = 'repos/fixture/example'
        entry = lambda name, raw: {'path': name, 'type': 'blob', 'mode': '100644', 'sha': status.blob_sha(raw)}
        cls.responses = {
            cls.base: {'id': 101, 'node_id': 'R_fixture', 'full_name': 'fixture/example', 'default_branch': 'trunk'},
            cls.base + '/branches/trunk': {'name': 'trunk', 'commit': {'sha': cls.head}},
            cls.base + '/pulls/7': {'number': 7, 'node_id': 'PR_fixture', 'merged': True,
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
        cls.graphql_response = {'data': {'repository': {
            'id': 'R_fixture', 'nameWithOwner': 'fixture/example',
            'defaultBranchRef': {'name': 'trunk', 'target': {'oid': cls.head}},
            'pullRequest': {'id': 'PR_fixture', 'number': 7, 'baseRefName': 'trunk', 'state': 'MERGED',
                            'merged': True, 'mergedAt': '2026-09-14T12:00:00Z',
                            'mergeCommit': {'oid': cls.head, 'repository': {
                                'id': 'R_fixture', 'nameWithOwner': 'fixture/example'}}}}}}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.api = copy.deepcopy(self.responses)
        self.graphql = copy.deepcopy(self.graphql_response)
        self.requests = []
        self.reader = None

    def tearDown(self):
        self.run_git('reset', '--hard', self.head)
        for name, raw in self.files.items():
            (self.root / name).write_bytes(raw)

    def observe(self, pr=7):
        def read(endpoint, deadline, gh):
            self.requests.append(endpoint)
            value = self.reader(endpoint) if self.reader else self.api[endpoint]
            return copy.deepcopy(value), len(json_bytes(value))
        def read_graphql(repository, number, deadline, gh):
            self.requests.append(('graphql', repository, number))
            return copy.deepcopy(self.graphql), len(json_bytes(self.graphql))
        # Keep the real bounded Git implementation and real adapter tuple contract.
        with patch.object(status, 'host_executable', side_effect=lambda name, root: self.git if name == 'git' else sys.executable), \
                patch.object(status, '_gh_get', side_effect=read), \
                patch.object(status, '_gh_graphql', side_effect=read_graphql):
            return status.project_status(self.root, adoption_pr=pr, release_source=self.source,
                                         expected_manifest_sha256=self.manifest_pin)

    def test_accepted_default_checkout_and_merged_receipt_are_active(self):
        result = self.observe()
        self.assertEqual(result['project_state'], 'ACTIVE', result)
        self.assertEqual(result['accepted_checkout'], 'VERIFIED')
        self.assertEqual(result['adoption_acceptance_sha'], self.head)
        self.assertEqual(result['adoption_acceptance_basis'], 'github_graphql_merge_commit')
        self.assertEqual(result['adoption_merge_sha'], self.head)
        self.assertEqual(result['adoption_receipt_commit_sha'], self.head)
        self.assertEqual(result['merge_identity_source'], 'github_graphql')
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

    def test_removed_or_null_rest_merge_identity_uses_graphql(self):
        for value in ['missing', None]:
            with self.subTest(value=value):
                self.api = copy.deepcopy(self.responses)
                if value != 'missing':
                    self.api[self.base + '/pulls/7']['merge_commit_sha'] = value
                result = self.observe()
                self.assertEqual(result['project_state'], 'ACTIVE', result)
                self.assertEqual(result['adoption_merge_sha'], self.head)
                self.assertIn(self.base + f'/contents/{INSTALLED}?ref={self.head}', self.requests)

    def test_legacy_rest_merge_identity_must_match_graphql(self):
        values = [self.head, '', 'f' * 39, 'f' * 40, ['f' * 40]]
        for value in values:
            with self.subTest(value=value):
                self.api = copy.deepcopy(self.responses)
                self.api[self.base + '/pulls/7']['merge_commit_sha'] = value
                result = self.observe()
                expected = 'ACTIVE' if value == self.head else 'CONFIGURED'
                self.assertEqual(result['project_state'], expected, result)

    def test_missing_malformed_or_nonancestor_graphql_merge_is_rejected(self):
        self.graphql['data']['repository']['pullRequest']['mergeCommit'] = None
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED', result)
        self.assertNotIn('report_active', result['decision_codes'])

        tree = self.run_git('rev-parse', self.head + '^{tree}')
        future = self.run_git('-c', 'user.name=AWF Synthetic Fixture',
                              '-c', 'user.email=awf@example.invalid',
                              'commit-tree', tree, '-p', self.head, '-m', 'Synthetic future integration')
        self.graphql = copy.deepcopy(self.graphql_response)
        self.graphql['data']['repository']['pullRequest']['mergeCommit']['oid'] = future
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED', result)
        self.assertNotIn('report_active', result['decision_codes'])

        self.graphql = copy.deepcopy(self.graphql_response)
        self.graphql['data']['repository']['pullRequest']['mergeCommit']['oid'] = self.before_receipt
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED', result)
        self.assertNotIn('report_active', result['decision_codes'])

        self.graphql = copy.deepcopy(self.graphql_response)
        self.graphql['data']['repository']['pullRequest']['mergeCommit']['oid'] = 'f' * 40
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED', result)
        self.assertNotIn('report_active', result['decision_codes'])

    def test_graphql_identity_is_cross_bound_to_rest_repository_default_and_pr(self):
        mutations = [
            ('nameWithOwner', 'fixture/other'),
            ('id', 'R_other'),
            ('defaultBranchRef.name', 'main'),
            ('defaultBranchRef.target.oid', 'f' * 40),
            ('pullRequest.number', 8),
            ('pullRequest.number', True),
            ('pullRequest.id', 'PR_other'),
            ('pullRequest.merged', False),
            ('pullRequest.mergeCommit.repository.id', 'R_other'),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                self.graphql = copy.deepcopy(self.graphql_response)
                target = self.graphql['data']['repository']
                parts = path.split('.')
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
                result = self.observe()
                self.assertEqual(result['project_state'], 'CONFIGURED', result)
                self.assertNotIn('report_active', result['decision_codes'])

    def test_graphql_errors_partial_data_and_bad_timestamp_fail_closed(self):
        values = [
            {'errors': [{'message': 'synthetic'}], 'data': copy.deepcopy(self.graphql_response['data'])},
            {'data': None},
            {'data': {'repository': None}},
        ]
        for value in values:
            with self.subTest(value=value):
                self.graphql = value
                result = self.observe()
                self.assertEqual(result['project_state'], 'CONFIGURED', result)
                self.assertNotIn('report_active', result['decision_codes'])
        self.graphql = copy.deepcopy(self.graphql_response)
        self.graphql['data']['repository']['pullRequest']['mergedAt'] = 'not-a-timestamp'
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED', result)

    def test_receipt_may_precede_integration_which_may_precede_current_head(self):
        self.run_git('-c', 'user.name=AWF Synthetic Fixture', '-c', 'user.email=awf@example.invalid',
                     'commit', '--allow-empty', '-m', 'Synthetic terminal integration commit')
        integration = self.run_git('rev-parse', 'HEAD')
        self.run_git('-c', 'user.name=AWF Synthetic Fixture', '-c', 'user.email=awf@example.invalid',
                     'commit', '--allow-empty', '-m', 'Synthetic later default-branch commit')
        current = self.run_git('rev-parse', 'HEAD')
        self.api[self.base + '/branches/trunk']['commit']['sha'] = current
        self.api[self.base + f'/contents/{INSTALLED}?ref={integration}'] = copy.deepcopy(
            self.api[self.base + f'/contents/{INSTALLED}?ref={self.head}'])
        self.api[self.base + f'/git/commits/{current}'] = {
            'sha': current, 'tree': {'sha': 'a' * 40}}
        self.graphql['data']['repository']['defaultBranchRef']['target']['oid'] = current
        self.graphql['data']['repository']['pullRequest']['mergeCommit']['oid'] = integration
        result = self.observe()
        self.assertEqual(result['project_state'], 'ACTIVE', result)
        self.assertEqual(result['adoption_receipt_commit_sha'], self.head)
        self.assertEqual(result['adoption_merge_sha'], integration)
        self.api[self.base + f'/contents/{INSTALLED}?ref={integration}']['sha'] = 'f' * 40
        result = self.observe()
        self.assertEqual(result['project_state'], 'CONFIGURED', result)
        self.assertIn('does not accept', result['next_action'])

    def test_two_parent_integration_commit_is_accepted(self):
        tree = self.run_git('rev-parse', self.head + '^{tree}')
        integration = self.run_git('-c', 'user.name=AWF Synthetic Fixture',
                                   '-c', 'user.email=awf@example.invalid',
                                   'commit-tree', tree, '-p', self.before_receipt, '-p', self.head,
                                   '-m', 'Synthetic two-parent integration')
        self.run_git('reset', '--hard', integration)
        receipt_commit = self.run_git('log', '-1', '--format=%H', '--', INSTALLED)
        self.api[self.base + '/branches/trunk']['commit']['sha'] = integration
        self.api[self.base + f'/commits/{receipt_commit}/pulls?per_page=100'] = copy.deepcopy(
            self.responses[self.base + f'/commits/{self.head}/pulls?per_page=100'])
        self.api[self.base + f'/contents/{INSTALLED}?ref={integration}'] = copy.deepcopy(
            self.api[self.base + f'/contents/{INSTALLED}?ref={self.head}'])
        self.api[self.base + f'/git/commits/{integration}'] = {
            'sha': integration, 'tree': {'sha': 'a' * 40}}
        self.graphql['data']['repository']['defaultBranchRef']['target']['oid'] = integration
        self.graphql['data']['repository']['pullRequest']['mergeCommit']['oid'] = integration
        result = self.observe()
        self.assertEqual(result['project_state'], 'ACTIVE', result)
        self.assertEqual(result['adoption_receipt_commit_sha'], receipt_commit)
        self.assertEqual(result['adoption_merge_sha'], integration)

    def test_graphql_helper_sends_encoded_json_on_stdin_and_bounds_output(self):
        calls = []

        class Process:
            def __init__(self, command, stdin, stdout, stderr, env, *, raw=None, returncode=0):
                calls.append((command, stdin.read(), env))
                stdout.write(raw if raw is not None else json_bytes(self.graphql_response))
                self.returncode = returncode

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        response = self.graphql_response

        def completed(command, stdin, stdout, stderr, env):
            process = Process.__new__(Process)
            calls.append((command, stdin.read(), env))
            stdout.write(json_bytes(response))
            process.returncode = 0
            return process

        with patch.object(status.subprocess, 'Popen', side_effect=completed):
            value, count = status._gh_graphql('fixture/example', 7, status.time.monotonic() + 5,
                                              gh='/trusted/gh')
        self.assertEqual(value, response)
        self.assertEqual(count, len(json_bytes(response)))
        self.assertEqual(sha256(status.MERGE_IDENTITY_QUERY.encode()),
                         'b2f87ad0ebc3cb42da0f69c044c01a51ea0032982c4c892c84eb5ed37b164558')
        command, payload, env = calls[0]
        self.assertEqual(command, ['/trusted/gh', 'api', '--hostname', 'github.com', '--method', 'POST',
                                   'graphql', '--input', '-'])
        self.assertEqual(json.loads(payload), {'query': status.MERGE_IDENTITY_QUERY,
                                               'variables': {'owner': 'fixture', 'name': 'example', 'number': 7}})
        self.assertEqual((env['GH_PROMPT_DISABLED'], env['GH_PAGER']), ('1', 'cat'))

        def oversized(command, stdin, stdout, stderr, env):
            process = Process.__new__(Process)
            stdout.write(b'x' * (status.MAX_BYTES + 1))
            process.returncode = 0
            return process

        with patch.object(status.subprocess, 'Popen', side_effect=oversized):
            with self.assertRaises(ValidationError):
                status._gh_graphql('fixture/example', 7, status.time.monotonic() + 5,
                                   gh='/trusted/gh')

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
