"""AWF-286: dependency-free clone recovery without changing adoption bytes."""
from __future__ import annotations

import json
import base64
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.runtime_commands import command_catalog, installed_paths
from agentic import ValidationError
from agentic.runtime_tags import validate_host_tags


class RecoveryAdviceTests(unittest.TestCase):
    def test_missing_runtime_status_names_provision_command(self):
        from agentic.providers.github_status import project_status
        with tempfile.TemporaryDirectory() as raw:
            report = project_status(raw)
            self.assertIn('provision-runtime', report['next_action'])
            self.assertIn(str(Path(raw).resolve()), report['next_action'])
            self.assertEqual(report['next_action'], report['activation']['next_command'])

    def test_missing_runtime_doctor_names_provision_command(self):
        with tempfile.TemporaryDirectory() as raw:
            report = command_catalog(raw)
            self.assertFalse(report['runtime']['interpreter_exists'])
            self.assertIn('provision-runtime', report['next_action'])
            self.assertIn(str(Path(raw).resolve()), report['next_action'])

    def test_import_error_names_dependency_free_recovery(self):
        code = """
import importlib.abc, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'yaml', 'jsonschema', 'attrs', 'referencing', 'rpds'}:
            raise ImportError('AWF-286 blocked ' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = [sys.argv[1], '--root', sys.argv[2], 'doctor']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        result = subprocess.run([sys.executable, '-B', '-I', '-c', code,
                                 str(ROOT / '.agentic/scripts/workflow.py'), str(ROOT)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(3, result.returncode, result.stderr)
        self.assertIn('provision-runtime', result.stderr)
        self.assertNotIn('Install .agentic/requirements.lock', result.stderr)

    def test_provision_reaches_builder_with_all_locked_imports_blocked(self):
        code = """
import importlib.abc, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'yaml', 'jsonschema', 'attrs', 'referencing', 'rpds'}:
            raise ImportError('AWF-286 blocked ' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        with tempfile.TemporaryDirectory() as raw:
            result = subprocess.run([sys.executable, '-B', '-I', '-c', code,
                                     str(ROOT / '.agentic/scripts/workflow.py'), '--root', str(ROOT),
                                     'provision-runtime', '--wheelhouse', str(Path(raw) / 'absent')],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn('Offline runtime wheelhouse is unavailable', result.stderr)


class RuntimeReuseTests(unittest.TestCase):
    def test_current_runtime_uses_fresh_build_validator_without_allocating_stage(self):
        from agentic import adoption_config as adoption
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            runtime = root / '.agentic/.venv'
            runtime.mkdir(parents=True)
            interpreter = installed_paths(root)[1]
            interpreter.parent.mkdir(exist_ok=True)
            interpreter.write_bytes(b'fixture interpreter')
            entry = installed_paths(root)[2]
            entry.parent.mkdir()
            entry.write_bytes(b'fixture entry')
            dependencies = {name: {'name': name, 'version': '1.0'} for name in adoption._RUNTIME_IMPORTS}
            artifacts = {name: {'files': {name + '.py': b'fixture'}, 'display_name': name,
                                'artifact_sha256': 'a' * 64} for name in dependencies}
            with patch('agentic.installer.Tree'), \
                    patch('agentic.installer._retained_directory_result', return_value=None), \
                    patch.object(adoption, '_locked_dependencies', return_value=(dependencies, 'b' * 64)), \
                    patch.object(adoption, '_load_locked_wheels', return_value=artifacts), \
                    patch('agentic.runtime_tags.validate_host_tags'), \
                    patch.object(adoption, '_validate_runtime') as validate, \
                    patch.object(adoption, '_transaction_sibling', side_effect=AssertionError('stage allocated')):
                result = adoption.ensure_installed_runtime(root, wheelhouse=root, runtime_only=True)
            validate.assert_called_once()
            self.assertEqual((interpreter, root, runtime), validate.call_args.args[:3])
            self.assertEqual({name: '1.0' for name in dependencies}, validate.call_args.args[3])
            self.assertEqual(len(dependencies), len(validate.call_args.args[4]))
            self.assertTrue(result['runtime_unchanged'])

    def test_backup_warning_is_advisory_and_quotes_both_shells(self):
        from agentic.runtime_commands import render_doctor
        from agentic.providers.github_status import project_status, render_status
        from agentic.contracts import Contracts
        with tempfile.TemporaryDirectory() as raw:
            root = (Path(raw) / "project's files").resolve()
            (root / '.agentic').mkdir(parents=True)
            before = project_status(root)
            backup = root / ('.agentic/.venv.backup-' + 'a' * 32)
            backup.mkdir()
            doctor = command_catalog(root)
            Contracts(ROOT / '.agentic/schemas').validate('doctor-output', doctor)
            status = project_status(root)
            self.assertEqual(before['project_state'], status['project_state'])
            self.assertEqual(
                [{k: v for k, v in row.items() if k != 'observed_at'}
                 for row in before['activation']['blockers']],
                [{k: v for k, v in row.items() if k != 'observed_at'}
                 for row in status['activation']['blockers']])
            for text in (render_doctor(doctor), render_status(status)):
                CloneRuntimeTests.assert_cleanup_warning(self, text, backup)
                self.assertIn("project''s files", text)
                self.assertIn(chr(39) + chr(34) + chr(39) + chr(34) + chr(39), text)


class RuntimeTagTests(unittest.TestCase):
    def validate(self, tag):
        validate_host_tags({'fixture': {'filename': 'fixture-1.0-' + tag + '.whl'}})

    def test_exact_cpython_and_portable_tags(self):
        tag = 'cp' + ''.join(map(str, sys.version_info[:2]))
        with patch('agentic.runtime_tags.sysconfig.get_config_var', return_value=0):
            self.validate(tag + '-' + tag + '-any')
        self.validate('py3-none-any')
        self.validate('py2.py3-none-any')

    def test_wrong_minor_and_ambiguous_tags_name_host_and_wheel(self):
        for tag in ('cp399-cp399-any', 'cp312.cp313-cp312-any', 'unknown-none-any'):
            with self.subTest(tag=tag), self.assertRaisesRegex(
                    ValidationError, r'Host Python \d+\.\d+ .*wheelhouse tag '):
                self.validate(tag)

    def test_stable_abi_has_a_minimum_python_version(self):
        with patch('agentic.runtime_tags.sysconfig.get_config_var', return_value=0):
            self.validate('cp39-abi3-any')
        with self.assertRaisesRegex(ValidationError, 'cp399-abi3'):
            self.validate('cp399-abi3-any')

    def test_wrong_platform_is_named(self):
        with self.assertRaisesRegex(ValidationError, 'other_platform'):
            self.validate('py3-none-other_platform')

    def test_malformed_filename_names_host_and_ambiguous_wheel(self):
        with self.assertRaisesRegex(ValidationError, r'Host Python \d+\.\d+ .*ambiguous.*fixture.whl'):
            validate_host_tags({'fixture': {'filename': 'fixture.whl'}})

    def test_platform_mapping_windows_linux_and_macos(self):
        from agentic.runtime_tags import _platform_matches
        with patch('agentic.runtime_tags.sysconfig.get_platform', return_value='win-amd64'):
            self.assertTrue(_platform_matches('win_amd64'))
            self.assertFalse(_platform_matches('win32'))
        with patch('agentic.runtime_tags.sys.platform', 'linux'), \
                patch('agentic.runtime_tags.platform.machine', return_value='x86_64'), \
                patch('agentic.runtime_tags.platform.libc_ver', return_value=('glibc', '2.31')):
            self.assertTrue(_platform_matches('manylinux_2_17_x86_64'))
            self.assertFalse(_platform_matches('manylinux_2_99_x86_64'))
        with patch('agentic.runtime_tags.sys.platform', 'darwin'), \
                patch('agentic.runtime_tags.platform.machine', return_value='arm64'), \
                patch('agentic.runtime_tags.platform.mac_ver', return_value=('14.0', (), 'arm64')):
            self.assertTrue(_platform_matches('macosx_11_0_universal2'))
            self.assertFalse(_platform_matches('macosx_11_0_x86_64'))


class CloneRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_bootstrap_configuration import ConfiguredInstallerTests
        cls.fixture = ConfiguredInstallerTests()
        cls.fixture.setUp()
        cls.addClassCleanup(cls.fixture.doCleanups)
        cls.wheelhouse = cls.fixture.prepare_runtime_source()
        cls.fixture.perform()
        cls.adopted = cls.fixture.dest
        cls.git = shutil.which('git')
        if not cls.git:
            cls.fixture.doCleanups()
            raise unittest.SkipTest('Local Git executable unavailable for clone fixture')
        # Seed a local adopted fixture without using the worker's index or refs.
        cls.git_run(cls.adopted, 'init', '-b', 'trunk')
        data = bytearray(b'commit refs/heads/trunk\ncommitter AWF Fixture <fixture@example.invalid> 1794484800 +0000\n'
                         b'data 16\nAdoption fixture\n')
        for path in sorted(cls.adopted.rglob('*')):
            relative = path.relative_to(cls.adopted)
            if '.git' in relative.parts or not path.is_file():
                continue
            raw = path.read_bytes()
            name = json.dumps(relative.as_posix()).encode('ascii')
            data.extend(b'M 100644 inline ' + name + b'\ndata ' + str(len(raw)).encode() + b'\n' + raw + b'\n')
        data.extend(b'\ndone\n')
        result = subprocess.run([cls.git, '-C', str(cls.adopted), 'fast-import', '--quiet'],
                                input=bytes(data), capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr.decode())
        cls.git_run(cls.adopted, 'reset', '--mixed', 'HEAD')

    @classmethod
    def git_run(cls, root, *args):
        result = subprocess.run([cls.git, '-c', 'core.autocrlf=false', '-C', str(root), *args],
                                capture_output=True, text=True, timeout=30, check=True)
        return result.stdout.strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='awf286-')
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / "cloned project's files").resolve()
        self.git_run(Path(self.temp.name), 'clone', '--no-hardlinks', str(self.adopted), str(self.root))
        self.assertFalse(installed_paths(self.root)[1].exists())

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): ('file', p.read_bytes()) if p.is_file() else ('directory', None)
                for p in self.root.rglob('*') if not any(
                    part == '.venv' or part.startswith(('.venv.staging-', '.venv.backup-'))
                    for part in p.relative_to(self.root).parts)}

    def provision(self, wheelhouse=None, *, blocked=False):
        script = str(self.root / '.agentic/scripts/workflow.py')
        argv = [script, '--root', str(self.root), 'provision-runtime',
                '--wheelhouse', str(wheelhouse or self.wheelhouse)]
        if blocked:
            code = """
import importlib.abc, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'yaml', 'jsonschema', 'attrs', 'referencing', 'rpds'}:
            raise ImportError('AWF-286 blocked ' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
            command = [sys.executable, '-B', '-I', '-c', code, *argv]
        else:
            command = [sys.executable, '-B', '-I', *argv]
        return subprocess.run(command, capture_output=True, text=True, timeout=120)

    def test_clone_recovers_in_one_command_without_receipt_or_other_writes(self):
        from agentic.installer import INSTALLED
        receipt_commit = self.git_run(self.root, 'log', '-1', '--format=%H', '--', INSTALLED)
        before = self.snapshot()
        result = self.provision(blocked=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('Interpreter: ' + str(installed_paths(self.root)[1]), result.stdout)
        self.assertEqual(before, self.snapshot())
        self.assertEqual('', self.git_run(self.root, 'status', '--porcelain'))
        self.assertEqual(receipt_commit, self.git_run(self.root, 'log', '-1', '--format=%H', '--', INSTALLED))
        for command, key, expected in [('verify-installation', 'integrity_valid', True),
                                        ('validate-config', 'status', 'ACCEPTED')]:
            check = subprocess.run([str(installed_paths(self.root)[1]), '-B', '-I',
                                    str(installed_paths(self.root)[2]), '--root', str(self.root), command],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(0, check.returncode, check.stderr + check.stdout)
            self.assertEqual(expected, json.loads(check.stdout)[key])
        # Observe the already accepted adoption through a synthetic provider;
        # Git, release trust and installed bytes remain real and unchanged.
        self.assert_active(receipt_commit)

    def assert_active(self, receipt_commit):
        from agentic.installer import INSTALLED
        from agentic.providers.github_status import installed_bytes, blob_sha
        _, files = installed_bytes(self.root)
        repo = {'id': 54321, 'node_id': 'R_fixture', 'full_name': 'fixture/widget'}
        base = 'repos/fixture/widget'
        head = self.git_run(self.root, 'rev-parse', 'HEAD')
        self.git_run(self.root, 'remote', 'set-url', 'origin', 'https://github.com/fixture/widget.git')
        pr_head = 'd' * 40
        entry = lambda path, raw: dict(path=path, type='blob', mode='100644', sha=blob_sha(raw))
        responses = {
            base: {**repo, 'default_branch': 'trunk'},
            base + '/branches/trunk': {'name': 'trunk', 'commit': {'sha': head}},
            base + '/pulls/7': dict(number=7, node_id='PR_fixture', state='closed', merged=True,
                merged_at='2026-11-12T12:00:00Z', merge_commit_sha=head,
                base=dict(ref='trunk', repo=repo), head=dict(ref='awf/adoption', sha=pr_head, repo=repo)),
            base + '/pulls/7/files?per_page=50&page=1': [
                dict(filename=INSTALLED, status='added', sha=blob_sha(files[INSTALLED]))],
            base + f'/commits/{receipt_commit}/pulls?per_page=100': [
                dict(number=7, base=dict(ref='trunk', repo=repo))],
            base + f'/contents/{INSTALLED}?ref={head}': dict(type='file', path=INSTALLED,
                sha=blob_sha(files[INSTALLED]), encoding='base64', content=base64.b64encode(files[INSTALLED]).decode()),
            base + f'/git/commits/{head}': dict(sha=head, tree=dict(sha='a' * 40)),
            base + '/git/trees/' + 'a' * 40: dict(truncated=False, tree=[
                entry('AGENTS.md', files['AGENTS.md']),
                dict(path='.agentic', type='tree', mode='040000', sha='b' * 40),
                dict(path='.github', type='tree', mode='040000', sha='c' * 40)]),
            base + '/git/trees/' + 'b' * 40 + '?recursive=1': dict(truncated=False, tree=[
                entry(path.removeprefix('.agentic/'), raw) for path, raw in files.items() if path.startswith('.agentic/')]),
            base + '/git/trees/' + 'c' * 40 + '?recursive=1': dict(truncated=False, tree=[
                entry('CODEOWNERS', files['.github/CODEOWNERS'])]),
        }
        graph_repo = dict(id=repo['node_id'], databaseId=repo['id'], nameWithOwner=repo['full_name'])
        graphql = dict(data=dict(repository={**graph_repo,
            'defaultBranchRef': dict(name='trunk', target=dict(oid=head)),
            'pullRequest': dict(id='PR_fixture', number=7, baseRefName='trunk', headRefName='awf/adoption',
                headRefOid=pr_head, headRepository=graph_repo, state='MERGED', merged=True,
                mergedAt='2026-11-12T12:00:00Z', mergeCommit=dict(oid=head, repository=graph_repo))}))
        code = """
import json, runpy, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from agentic.providers import github_status as status
responses, graphql, git = json.load(sys.stdin)
def read(endpoint, *args, **kwargs):
    value = responses[endpoint]
    return value, len(json.dumps(value).encode())
def graph(*args, **kwargs):
    return graphql, len(json.dumps(graphql).encode())
sys.argv = sys.argv[2:]
with patch.object(status, 'host_executable', side_effect=lambda name, root: git if name == 'git' else sys.executable), \
     patch.object(status, '_gh_get', side_effect=read), \
     patch.object(status, '_gh_get_pr_files', side_effect=read), \
     patch.object(status, '_gh_graphql', side_effect=graph):
    runpy.run_path(sys.argv[0], run_name='__main__')
"""
        runtime = installed_paths(self.root)
        # Keep large mock observations on stdin, independent of OS argv limits.
        result = subprocess.run([str(runtime[1]), '-B', '-I', '-c', code,
                                 str(self.root / '.agentic/lib'), str(runtime[2]), '--root', str(self.root),
                                 'status', '--require-active', '--json', '--release-source', str(self.fixture.source),
                                 '--expected-manifest-sha256', self.fixture.pin],
                                input=json.dumps([responses, graphql, self.git]),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual('ACTIVE', report['project_state'], report)
        self.assertEqual(receipt_commit, report['adoption_receipt_commit_sha'])

    def test_second_and_third_provision_leave_current_runtime_unchanged(self):
        self.assertEqual(0, self.provision().returncode)
        runtime = self.root / '.agentic/.venv'
        identity = (runtime.stat().st_dev, runtime.stat().st_ino)
        before = self.snapshot()
        for run in (2, 3):
            with self.subTest(run=run):
                result = self.provision(blocked=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn('Interpreter: ' + str(installed_paths(self.root)[1]), result.stdout)
                self.assertIn('already current and unchanged', result.stdout)
                self.assertEqual(identity, (runtime.stat().st_dev, runtime.stat().st_ino))
                self.assertEqual([], list(runtime.parent.glob('.venv.backup-*')))
                self.assertEqual([], list(runtime.parent.glob('.venv.staging-*')))
                self.assertEqual(before, self.snapshot())
                self.assertEqual('', self.git_run(self.root, 'status', '--porcelain'))

    def test_broken_runtime_replaced_with_retained_backup_and_advisory_warnings(self):
        from agentic.installer import INSTALLED
        from agentic.runtime_commands import command_catalog, render_doctor
        from agentic.providers.github_status import project_status, render_status
        first = self.provision()
        self.assertEqual(0, first.returncode, first.stderr)
        runtime = self.root / '.agentic/.venv'
        identity = (runtime.stat().st_dev, runtime.stat().st_ino)
        package = next(runtime.rglob('attrs/__init__.py'))
        package.write_bytes(b'# altered locked dependency\n')
        before = self.snapshot()
        second = self.provision()
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertNotEqual(identity, (runtime.stat().st_dev, runtime.stat().st_ino))
        self.assertEqual(before, self.snapshot())
        backups = list(runtime.parent.glob('.venv.backup-*'))
        self.assertEqual(1, len(backups))
        self.assertEqual(b'# altered locked dependency\n',
                         (backups[0] / package.relative_to(runtime)).read_bytes())
        self.assert_cleanup_warning(second.stderr, backups[0])
        check = subprocess.run([str(installed_paths(self.root)[1]), '-B', '-I',
                                str(installed_paths(self.root)[2]), '--root', str(self.root),
                                'verify-installation'], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, check.returncode, check.stderr + check.stdout)
        self.assertTrue(json.loads(check.stdout)['integrity_valid'])
        doctor = command_catalog(self.root)
        self.assert_cleanup_warning(render_doctor(doctor), backups[0])
        # No independent release trust is supplied here: CONFIGURED remains
        # CONFIGURED. The accepted default branch below still establishes ACTIVE.
        status = project_status(self.root)
        self.assertEqual('CONFIGURED', status['project_state'])
        self.assert_cleanup_warning(render_status(status), backups[0])
        self.assertFalse(any('backup' in str(row).lower() for row in status['activation']['blockers']))
        receipt_commit = self.git_run(self.root, 'log', '-1', '--format=%H', '--', INSTALLED)
        self.assert_active(receipt_commit)

    def assert_cleanup_warning(self, text, backup):
        self.assertIn('WARNING', text)
        self.assertIn(str(backup), text)
        self.assertIn('Remove-Item -LiteralPath', text)
        self.assertIn('POSIX sh:', text)
        self.assertIn('rm -rf --', text)
        self.assertIn('not ignored by Git', text)
        self.assertIn('must not be committed', text)

    def test_failed_staged_validation_retains_only_runtime_recovery_paths(self):
        from agentic.adoption_config import ensure_installed_runtime
        before = self.snapshot()
        with patch('agentic.adoption_config._validate_runtime', side_effect=ValidationError('staged validation failed')):
            with self.assertRaisesRegex(ValidationError, 'staged validation failed') as error:
                ensure_installed_runtime(self.root, wheelhouse=self.wheelhouse, runtime_only=True)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(installed_paths(self.root)[1].exists())
        stages = list((self.root / '.agentic').glob('.venv.staging-*'))
        self.assertEqual(1, len(stages))
        self.assertIn(str(stages[0]), '\n'.join(error.exception.__notes__))

    def test_invalid_wheelhouses_refuse_by_name_before_staging(self):
        for fault in ('stray', 'hash', 'platform', 'minor', 'ambiguous'):
            with self.subTest(fault=fault):
                wheels = Path(self.temp.name) / fault
                shutil.copytree(self.wheelhouse, wheels)
                path = next(wheels.glob('*.whl'))
                if fault == 'stray':
                    path = wheels / 'SHA256SUMS.txt'
                    path.write_bytes(b'unrelated')
                elif fault == 'hash':
                    path.write_bytes(path.read_bytes() + b'bad')
                else:
                    tag = {'platform': 'py3-none-other_platform', 'minor': 'cp399-cp399-any',
                           'ambiguous': 'cp312.cp313-cp312-any'}[fault]
                    new = path.with_name(path.name.replace('py3-none-any', tag))
                    path.rename(new)
                    path = new
                before = self.snapshot()
                result = self.provision(wheels)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn(path.name, result.stderr)
                self.assertEqual(before, self.snapshot())
                self.assertFalse(list((self.root / '.agentic').glob('.venv*')))


if __name__ == '__main__':
    unittest.main()
