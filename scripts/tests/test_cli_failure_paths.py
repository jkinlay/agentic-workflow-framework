"""Offline subprocess argument/rejection regressions, not branch-coverage claims.

Each rejection uses real CLI parsing, source integrity and filesystem boundaries.
Synthetic sealed runtimes avoid depending on a mutable developer manifest.
"""
import hashlib
from datetime import datetime, timezone
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
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_archive import PREFIX, RELEASE_LINE, VERSION, preflight
import validate_archive
import validate_git_checkout


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class ValidatorCommandContractTests(unittest.TestCase):
    def test_archive_launcher_passes_the_real_environment_scrubbing_gate(self):
        from agentic.launch_surfaces import python_launch_findings
        with tempfile.TemporaryDirectory(prefix='awf-archive-launch-surface-') as raw:
            owned = Path(raw)
            script = owned / 'scripts/validate_archive.py'
            script.parent.mkdir()
            script.write_bytes((ROOT / 'scripts/validate_archive.py').read_bytes())
            self.assertEqual([], python_launch_findings(owned))

    def test_expected_installed_commands_are_canonical_and_noninstalling(self):
        destination = ROOT / '.tmp-tests' / 'nonexistent project with spaces'
        expected_root, expected_interpreter, expected_entry = validate_archive.installed_paths(destination)
        expected = [[str(expected_interpreter), '-B', '-I', str(expected_entry),
                     '--root', str(expected_root), action]
                    for action in ('verify-installation', 'validate-config')]
        for helper in (validate_archive.expected_post_install_commands,
                       validate_git_checkout.expected_post_install_commands):
            with self.subTest(module=helper.__module__):
                self.assertEqual(expected, helper(destination))
        self.assertIn(str(Path('.agentic') / '.venv'), expected[0][0])
        self.assertFalse(destination.exists())

    def test_installed_acceptance_stage_selectors_never_return_developer_python(self):
        destination = ROOT / '.tmp-tests' / 'another nonexistent installed project'
        expected = validate_archive.installed_paths(destination)[1]
        for selector in (validate_archive.installed_stage_python,
                         validate_git_checkout.installed_stage_python):
            with self.subTest(module=selector.__module__):
                self.assertEqual(expected, selector(destination))
                self.assertNotEqual(Path(sys.executable).resolve(), selector(destination))
        self.assertFalse(destination.exists())

    def test_installed_git_inventory_excludes_only_the_ignored_canonical_runtime(self):
        managed = validate_git_checkout._managed
        self.assertTrue(managed('.agentic/scripts/workflow.py'))
        self.assertTrue(managed('.agentic/installed-manifest.json'))
        self.assertTrue(managed('AGENTS.md'))
        self.assertFalse(managed('.agentic/.venv'))
        self.assertFalse(managed('.agentic/.venv/Scripts/python.exe'))
        windows_path = chr(92).join((".agentic", ".venv", "bin", "python"))
        self.assertFalse(managed(windows_path))

    def test_installed_clone_runtime_provisioner_uses_target_and_pinned_wheelhouse(self):
        destination = ROOT / '.tmp-tests' / 'pure clone selector'
        wheelhouse = ROOT / '.tmp-tests' / 'pinned wheelhouse'
        expected = {'interpreter': str(validate_git_checkout.installed_stage_python(destination))}
        with mock.patch('agentic.adoption_config.ensure_installed_runtime', return_value=expected) as ensure:
            observed = validate_git_checkout.provision_installed_clone_runtime(destination, wheelhouse)
        self.assertEqual(expected, observed)
        ensure.assert_called_once_with(destination, wheelhouse=wheelhouse)
        self.assertFalse(destination.exists())
        self.assertFalse(wheelhouse.exists())


class ArchiveRebuildMetadataTests(unittest.TestCase):
    """Real ZIP metadata/encoding regressions, not full release acceptance."""
    timestamp = (2026, 10, 29, 12, 34, 56)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf-archive-metadata-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def archive(self, mutate=None, archive_comment=b''):
        files = {'MANIFEST.json': (0o100644, b'{}\n'),
                 'MANIFEST.md': (0o100644, b'Metadata fixture\n'),
                 'shebang.py': (0o100644, b'#!/usr/bin/env python\n'),
                 'worker.py': (0o100755, b'print(1)\n')}
        path = self.root / 'source.zip'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.comment = archive_comment
            for name, (mode, raw) in sorted(files.items()):
                entry = zipfile.ZipInfo(PREFIX + name, date_time=self.timestamp)
                entry.create_system = 3
                entry.external_attr = mode << 16
                if mutate is not None:
                    mutate(name, entry)
                archive.writestr(entry, raw)
        return path, files

    def test_closed_archive_rebuild_preserves_approved_modes_timestamp_and_bytes(self):
        import build_release
        import release_modes
        path, files = self.archive()
        with zipfile.ZipFile(path) as archive:
            metadata, epoch = validate_archive.archive_rebuild_metadata(archive)
        self.assertEqual(int(datetime(*self.timestamp, tzinfo=timezone.utc).timestamp()), epoch)
        self.assertEqual('100644', metadata['files']['shebang.py'])
        self.assertEqual('100755', metadata['files']['worker.py'])
        source = self.root / 'closed-source'
        source.mkdir()
        for name, (_, raw) in files.items():
            (source / name).write_bytes(raw)
        self.assertFalse((source / '.git').exists())
        mode_manifest = self.root / 'modes.json'
        mode_manifest.write_text(json.dumps(metadata), encoding='utf-8')
        rebuilt = self.root / 'rebuilt.zip'
        # Run the real builder ZIP writer on a small metadata fixture. Catalog
        # discovery/sealing is outside this unit's scope, not labelled accepted.
        with mock.patch.object(build_release, 'ROOT', source), \
                mock.patch.object(build_release, 'manifest', return_value='0' * 64), \
                mock.patch.object(build_release, 'release_paths', return_value=sorted(files)), \
                mock.patch.object(release_modes, '_git_modes', side_effect=AssertionError('closed ZIP must not need Git')), \
                mock.patch.dict(os.environ, {'SOURCE_DATE_EPOCH': str(epoch)}), \
                mock.patch.object(sys, 'argv', ['build_release.py', '--output', str(rebuilt),
                                               '--git-mode-manifest', str(mode_manifest)]), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(0, build_release.main())
        self.assertEqual(path.read_bytes(), rebuilt.read_bytes())

    def test_noncanonical_modes_compression_and_metadata_fail_before_outputs(self):
        mutations = {
            'missing_regular_type': lambda entry: setattr(entry, 'external_attr', 0o644 << 16),
            'symlink': lambda entry: setattr(entry, 'external_attr', 0o120777 << 16),
            'unsupported_permissions': lambda entry: setattr(entry, 'external_attr', 0o100600 << 16),
            'dos_flags': lambda entry: setattr(entry, 'external_attr', entry.external_attr | 1),
            'non_unix': lambda entry: setattr(entry, 'create_system', 0),
            'compressed': lambda entry: setattr(entry, 'compress_type', zipfile.ZIP_DEFLATED),
            'extra': lambda entry: setattr(entry, 'extra', b'\xfe\xca\x00\x00'),
            'comment': lambda entry: setattr(entry, 'comment', b'noncanonical'),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                path, _ = self.archive(lambda name, entry: mutation(entry) if name == 'worker.py' else None)
                before = snapshot(self.root)
                with self.assertRaisesRegex(ValueError, 'canonical'):
                    preflight(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                              self.root / 'work', self.root / 'report.json')
                self.assertEqual(before, snapshot(self.root))
        path, _ = self.archive(archive_comment=b'noncanonical')
        with self.assertRaisesRegex(ValueError, 'empty archive comment'):
            preflight(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                      self.root / 'work', self.root / 'report.json')
        self.assertFalse((self.root / 'work').exists())

    def test_inconsistent_approved_timestamps_are_not_replaced_with_an_invented_epoch(self):
        path, _ = self.archive(lambda name, entry: setattr(entry, 'date_time', (2026, 10, 29, 12, 35, 0))
                               if name == 'worker.py' else None)
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValueError, 'one canonical UTC DOS timestamp'):
            preflight(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                      self.root / 'work', self.root / 'report.json')
        self.assertEqual(before, snapshot(self.root))


class ArchiveFixtureSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf-archive-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def source(self):
        source = self.root / 'source'
        (source / '.agentic/templates').mkdir(parents=True)
        files = {}
        for logical in validate_archive.SOURCE_CONFIG_PATHS:
            raw = json.dumps({'synthetic_logical_member': logical}).encode('utf-8') + b'\n'
            (source / logical).write_bytes(raw)
            files[logical] = hashlib.sha256(raw).hexdigest()
        (source / 'MANIFEST.json').write_text(json.dumps({'files': files}), encoding='utf-8')
        return source

    def test_all_three_generator_seeds_copy_pinned_logical_bytes_without_changing_them(self):
        source = self.source()
        before = snapshot(source)
        records = validate_archive.seed_source_configuration(source)
        self.assertEqual(3, len(records))
        for item in records:
            self.assertEqual(before[item['logical_member']], (source / item['fixture']).read_bytes())
            self.assertEqual(hashlib.sha256(before[item['logical_member']]).hexdigest(), item['sha256'])
        self.assertTrue((source / '.agentic/templates/source-config/workflow-version.yaml').is_file())
        for path, raw in before.items():
            self.assertEqual(raw, (source / path).read_bytes())

    def test_corrupt_or_existing_seed_inputs_refuse_without_overwriting(self):
        source = self.source()
        operating = source / 'OPERATING_CONFIG.yaml'
        original = operating.read_bytes()
        operating.write_bytes(b'changed logical bytes')
        before = snapshot(source)
        with self.assertRaisesRegex(ValueError, 'approved logical member'):
            validate_archive.seed_source_configuration(source)
        self.assertEqual(before, snapshot(source))
        self.assertFalse((source / '.agentic/templates/source-config').exists())
        operating.write_bytes(original)
        prefix = source / '.agentic/templates/source-config'
        prefix.mkdir()
        (prefix / 'owner.txt').write_bytes(b'preserve existing fixture')
        before = snapshot(source)
        with self.assertRaisesRegex(ValueError, 'already contains'):
            validate_archive.seed_source_configuration(source)
        self.assertEqual(before, snapshot(source))

    def test_real_installed_fixture_is_unborn_and_ignores_inherited_git_overrides(self):
        root = self.root / 'installed'
        root.mkdir()
        (root / 'owner.txt').write_bytes(b'owned')
        forbidden = self.root / 'not-the-fixture'
        environment = dict(os.environ, GIT_DIR=str(forbidden), GIT_WORK_TREE=str(forbidden))
        validate_archive.initialize_unborn_fixture(root, environment)
        self.assertTrue((root / '.git').is_dir())
        self.assertFalse(forbidden.exists())
        self.assertEqual(b'owned', (root / 'owner.txt').read_bytes())
        before = snapshot(root)
        with self.assertRaisesRegex(ValueError, 'already has Git metadata'):
            validate_archive.initialize_unborn_fixture(root, environment)
        self.assertEqual(before, snapshot(root))

    def test_known_auxiliary_trees_are_preserved_outside_closed_source(self):
        run = self.root / 'run'
        source = run / 'source'
        source.mkdir(parents=True)
        (source / 'owner.txt').write_bytes(b'keep')
        for relative in ('.tmp-tests', '.agentic/templates/source-config'):
            candidate = source / relative
            (candidate / 'nested').mkdir(parents=True)
            (candidate / 'nested/evidence.json').write_bytes(b'{"retained":true}\n')
            before = snapshot(candidate)
            retained = validate_archive.quarantine_source_auxiliary(source, run, relative)
            self.assertEqual(before, snapshot(retained))
            self.assertEqual(run, retained.parent)
            self.assertFalse(candidate.exists())
        self.assertEqual(b'keep', (source / 'owner.txt').read_bytes())

    def test_auxiliary_quarantine_refuses_unknown_outside_existing_and_nondirectory_paths(self):
        run = self.root / 'run'
        source = run / 'source'
        source.mkdir(parents=True)
        candidate = source / '.tmp-tests'
        candidate.mkdir()
        (candidate / 'evidence.txt').write_bytes(b'retain')
        before = snapshot(run)
        with self.assertRaisesRegex(ValueError, 'recognized'):
            validate_archive.quarantine_source_auxiliary(source, run, 'owner-data')
        with self.assertRaisesRegex(ValueError, 'owned validation run'):
            validate_archive.quarantine_source_auxiliary(source, self.root, '.tmp-tests')
        self.assertEqual(before, snapshot(run))
        retained = run / 'retained-source-self-test-diagnostics'
        retained.mkdir()
        (retained / 'prior.txt').write_bytes(b'prior evidence')
        before = snapshot(run)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            validate_archive.quarantine_source_auxiliary(source, run, '.tmp-tests')
        self.assertEqual(before, snapshot(run))
        file_source = run / 'file-source'
        file_source.mkdir()
        (file_source / '.tmp-tests').write_bytes(b'not a directory')
        with self.assertRaisesRegex(ValueError, 'noncanonical'):
            validate_archive.quarantine_source_auxiliary(file_source, run, '.tmp-tests')
        self.assertEqual(b'not a directory', (file_source / '.tmp-tests').read_bytes())


class CliFailurePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wheelhouse = self.root / 'wheelhouse'
        self.wheelhouse.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k.upper() not in {'PYTHONPATH', 'PYTHONHOME'}}
        self.env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')

    def reject(self, script, args=(), reason=None):
        done = subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
                              cwd=self.root, env=self.env, capture_output=True, timeout=30)
        stderr = done.stderr.decode('utf-8')
        self.assertEqual(2, done.returncode, done.stdout.decode('utf-8') + stderr)
        self.assertNotIn('Traceback', stderr)
        if reason:
            self.assertIn(reason, stderr)
        return done

    def runtime(self):
        runtime = self.root / 'runtime'
        for name in ['.agentic/lib', '.agentic/schemas']:
            shutil.copytree(ROOT / name, runtime / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (runtime / '.agentic/scripts').mkdir()
        for name in ['workflow.py', 'validate_config.py', 'project_status.py']:
            shutil.copyfile(ROOT / '.agentic/scripts' / name, runtime / '.agentic/scripts' / name)
        shutil.copyfile(ROOT / '.agentic/workflow.yaml', runtime / '.agentic/workflow.yaml')
        payload = snapshot(runtime)
        manifest = {'format': 'awf-manifest-1', 'template_version': VERSION,
                    'files': {name: hashlib.sha256(body).hexdigest() for name, body in payload.items()}}
        (runtime / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
        return runtime

    def archive(self, entries=None):
        path = self.root / 'release.zip'
        if entries is None:
            logical = {name: b'{}\n' for name in validate_archive.SOURCE_CONFIG_PATHS}
            logical['.agentic/templates/fixture.txt'] = b'Synthetic template directory fixture\n'
            manifest = {'format': 'awf-manifest-1', 'template_version': VERSION,
                        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in logical.items()}}
            entries = {PREFIX + name: raw for name, raw in logical.items()}
            entries[PREFIX + 'MANIFEST.json'] = json.dumps(manifest).encode('utf-8')
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in entries.items():
                entry = zipfile.ZipInfo(name, date_time=(2026, 10, 29, 12, 34, 56))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                # ZipInfo normalizes backslashes on Windows; force raw inventory
                # to exercise the actual foreign/malformed ZIP boundary.
                entry.filename = name
                archive.writestr(entry, data)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def archive_args(self, path, pin, work=None, report=None):
        return ['--archive', path, '--expected-zip-sha256', pin,
                '--reviews',self.root/'independent-review.json','--expected-reviews-sha256','0'*64,
                '--runtime-wheelhouse',self.wheelhouse,
                '--workdir', work or self.root / 'unused-work', '--report', report or self.root / 'report.json']

    def test_each_entry_point_rejects_missing_required_arguments_without_writes(self):
        for name in ['scripts/bootstrap_project.py', '.agentic/scripts/workflow.py',
                     '.agentic/scripts/project_status.py', 'scripts/validate_archive.py',
                     'scripts/validate_git_checkout.py', 'scripts/release_review.py']:
            with self.subTest(script=name):
                self.reject(ROOT / name, reason='required')
                self.assertEqual({}, snapshot(self.root))
        self.reject(ROOT / '.agentic/scripts/validate_config.py', ['--unknown-option'], reason='unrecognized arguments')
        self.assertEqual({}, snapshot(self.root))

    def test_bootstrap_requires_pin_before_creating_destination(self):
        destination = self.root / 'project'
        for pin in [None, 'bad']:
            args = ['--dest', destination] + ([] if pin is None else ['--expected-manifest-sha256', pin])
            self.reject(ROOT / 'scripts/bootstrap_project.py', args, 'externally approved')
            self.assertFalse(destination.exists())

    def test_bootstrap_bad_pin_preserves_existing_destination(self):
        destination = self.root / 'project'
        destination.mkdir()
        (destination / 'AGENTS.md').write_bytes(b'Owner instructions\n')
        before = snapshot(destination)
        self.reject(ROOT / 'scripts/bootstrap_project.py',
                    ['--dest', destination, '--expected-manifest-sha256', '0' * 64], 'approved digest')
        self.assertEqual(before, snapshot(destination))

    def test_bootstrap_missing_or_invalid_wheelhouse_precedes_every_destination_write(self):
        pin = hashlib.sha256((ROOT / 'MANIFEST.json').read_bytes()).hexdigest()
        cases = [
            ('missing-wheelhouse', [], '--runtime-wheelhouse is required'),
            ('empty-wheelhouse', ['--runtime-wheelhouse', self.wheelhouse], 'wheelhouse is empty'),
        ]
        for mode in ('install', 'upgrade'):
            for name, extra, reason in cases:
                with self.subTest(mode=mode, name=name):
                    destination = self.root / (mode + '-' + name)
                    destination.mkdir()
                    (destination / 'owner-file').write_bytes(b'preserve exactly\n')
                    before = sorted((path.relative_to(destination).as_posix(), path.is_dir(),
                                     None if path.is_dir() else path.read_bytes())
                                    for path in destination.rglob('*'))
                    self.reject(ROOT / 'scripts/bootstrap_project.py',
                                ['--dest', destination, '--mode', mode,
                                 '--expected-manifest-sha256', pin, *extra], reason)
                    after = sorted((path.relative_to(destination).as_posix(), path.is_dir(),
                                    None if path.is_dir() else path.read_bytes())
                                   for path in destination.rglob('*'))
                    self.assertEqual(before, after)
                    self.assertFalse((destination / '.agentic').exists())
                    self.assertFalse((destination / '.agentic-install').exists())
                    self.assertFalse((destination / 'AGENTS.md').exists())
                    self.assertFalse((destination / '.agentic/PROJECT_CONFIG.yaml').exists())
                    self.assertFalse((destination / '.agentic/installed-manifest.json').exists())
                    self.assertFalse((destination / '.agentic/workflow-version.yaml').exists())
                    self.assertFalse((destination / '.github/CODEOWNERS').exists())

    def test_bootstrap_invalid_mode_rejects_before_mutation(self):
        destination = self.root / 'project'
        self.reject(ROOT / 'scripts/bootstrap_project.py', ['--dest', destination, '--mode', 'replace'], 'invalid choice')
        self.assertFalse(destination.exists())

    def test_workflow_and_compatibility_reject_missing_root_without_creating_it(self):
        absent = self.root / 'absent'
        for name, args in [('workflow.py', ['--root', absent, 'verify-installation']),
                           ('validate_config.py', ['--root', absent])]:
            with self.subTest(script=name):
                done = self.reject(ROOT / '.agentic/scripts' / name, args)
                self.assertFalse(json.loads(done.stderr)['execution_authority'])
                self.assertFalse(absent.exists())

    def test_workflow_unknown_command_and_missing_record_arguments(self):
        self.reject(ROOT / '.agentic/scripts/workflow.py', ['dispatch'], 'invalid choice')
        self.reject(ROOT / '.agentic/scripts/workflow.py', ['validate-record', 'gate'], 'required')
        self.assertEqual({}, snapshot(self.root))

    def test_workflow_and_config_wrapper_reject_malformed_config_after_integrity(self):
        runtime = self.runtime()
        config = self.root / 'bad-config.json'
        config.write_bytes(b'{not-json')
        before = snapshot(self.root)
        for name, args in [('workflow.py', ['--root', runtime, 'validate-config', '--config', config]),
                           ('validate_config.py', ['--root', runtime, '--config', config])]:
            with self.subTest(script=name):
                done = self.reject(runtime / '.agentic/scripts' / name, args)
                reason = json.loads(done.stderr)['reason']
                self.assertTrue('JSON' in reason or 'property name' in reason, reason)
                self.assertEqual(before, snapshot(self.root))

    def test_workflow_rejects_missing_record_after_integrity(self):
        runtime = self.runtime()
        before = snapshot(self.root)
        done = self.reject(runtime / '.agentic/scripts/workflow.py',
                           ['--root', runtime, 'validate-record', 'gate', self.root / 'missing.json'])
        self.assertIn('missing.json', json.loads(done.stderr)['reason'])
        self.assertEqual(before, snapshot(self.root))

    def test_project_status_invalid_shapes_fail_closed_after_integrity(self):
        runtime = self.runtime()
        record = self.root / 'record.json'
        for command, data, expected in [('report', {}, 'requires exactly'),
                                         ('action', {'kind': 'edit'}, 'Invalid action decision fields'),
                                         ('cycle', {'source': 'live'}, 'actual clock')]:
            with self.subTest(command=command):
                record.write_text(json.dumps(data), encoding='utf-8')
                before = snapshot(self.root)
                args = ['--format', 'json', command, record]
                if command == 'cycle':
                    args += ['--now', '2026-09-13T12:00:00Z']
                done = self.reject(runtime / '.agentic/scripts/project_status.py', args, expected)
                result = json.loads(done.stderr)
                self.assertEqual('REJECTED', result['status'])
                self.assertFalse(result['execution_authority'])
                self.assertTrue(result['next_step']['action'])
                self.assertEqual(before, snapshot(self.root))

    def test_project_status_gate_requires_complete_arguments(self):
        self.reject(ROOT / '.agentic/scripts/project_status.py', ['gate-handoff', '--gate', 'unused.json'], 'required')
        self.assertEqual({}, snapshot(self.root))

    def test_archive_missing_input_is_clean_rejection_without_outputs(self):
        self.reject(ROOT / 'scripts/validate_archive.py', self.archive_args(self.root / 'absent.zip', '0' * 64))
        self.assertEqual({}, snapshot(self.root))

    def test_archive_wrong_pin_and_malformed_zip_leave_inputs_unchanged(self):
        archive, pin = self.archive()
        before = snapshot(self.root)
        self.reject(ROOT / 'scripts/validate_archive.py', self.archive_args(archive, '0' * 64), 'approved ZIP digest')
        self.assertEqual(before, snapshot(self.root))
        archive.write_bytes(b'not a zip')
        before = snapshot(self.root)
        self.reject(ROOT / 'scripts/validate_archive.py',
                    self.archive_args(archive, hashlib.sha256(archive.read_bytes()).hexdigest()), 'not a zip')
        self.assertEqual(before, snapshot(self.root))

    def test_archive_unsafe_inventory_rejects_before_creating_work(self):
        for name in ['../escaped.txt', PREFIX + '../escaped.txt', PREFIX + 'a\\escaped.txt', PREFIX + 'a:stream']:
            with self.subTest(name=name):
                archive, pin = self.archive({PREFIX + 'MANIFEST.json': b'{}', name: b'unsafe'})
                before = snapshot(self.root)
                self.reject(ROOT / 'scripts/validate_archive.py', self.archive_args(archive, pin), 'Unsafe ZIP inventory')
                self.assertEqual(before, snapshot(self.root))
                self.assertFalse((self.root / 'unused-work').exists())

    def test_archive_rejects_aliased_source_work_and_report_before_outputs(self):
        archive, pin = self.archive()
        alias = ROOT.parent / '..' / ROOT.parent.name / ROOT.name
        for work, report in [(alias / 'unused-review-work', self.root / 'report.json'),
                             (self.root / 'unused-work', alias / 'unused-review-report.json')]:
            with self.subTest(work=work, report=report):
                before = snapshot(self.root)
                self.reject(ROOT / 'scripts/validate_archive.py', self.archive_args(archive, pin, work, report), 'outside the source')
                self.assertEqual(before, snapshot(self.root))
                self.assertFalse(work.exists())
                self.assertFalse(report.exists())

    def test_archive_report_cannot_overwrite_approved_input(self):
        archive, pin = self.archive()
        before = snapshot(self.root)
        self.reject(ROOT / 'scripts/validate_archive.py', self.archive_args(archive, pin, report=archive), 'overwrite the approved archive')
        self.assertEqual(before, snapshot(self.root))

    def test_archive_preflight_accepts_current_version_without_writes(self):
        self.assertEqual(VERSION.removesuffix('.0'), RELEASE_LINE)
        self.assertEqual(f'agentic-workflow-template-v{RELEASE_LINE}/', PREFIX)
        archive, pin = self.archive()
        before = snapshot(self.root)
        resolved = preflight(archive, pin, self.root / 'unused-work', self.root / 'report.json')
        self.assertEqual(archive.resolve(), resolved[0])
        self.assertEqual(before, snapshot(self.root))
        self.assertFalse(resolved[1].exists())

    def test_archive_rejects_linked_tmp_tests_before_outputs(self):
        source = self.root / 'source-root'
        outside = self.root / 'outside-scratch'
        source.mkdir()
        outside.mkdir()
        scratch = source / '.tmp-tests'
        try:
            scratch.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('PLATFORM_PRIVILEGE: Windows symlink creation privilege unavailable (WinError 1314)')
            raise
        archive, pin = self.archive()
        before = snapshot(self.root)
        with mock.patch.object(validate_archive, 'ROOT', source), self.assertRaisesRegex(ValueError, 'reparse-point'):
            preflight(archive, pin, scratch / 'work', scratch / 'report.json')
        self.assertEqual(before, snapshot(self.root))

    def test_archive_invalid_self_test_deadline_rejects_before_any_outputs(self):
        archive, pin = self.archive()
        before = snapshot(self.root)
        for value in ('0', '-1', '59', '3601', '1.5', 'unknown'):
            with self.subTest(value=value):
                self.reject(ROOT / 'scripts/validate_archive.py',
                            self.archive_args(archive, pin) + ['--self-test-timeout-seconds', value],
                            '60 through 3600')
                self.assertEqual(before, snapshot(self.root))
                self.assertFalse((self.root / 'unused-work').exists())

    def test_archive_timeout_retains_failed_partial_report_and_source_fixture(self):
        archive, pin = self.archive()
        archive_before = archive.read_bytes()
        for requested, expected in ((None, 600), ('1800', 1800)):
            with self.subTest(requested=requested):
                work = self.root / ('timeout-work-' + str(expected))
                report = self.root / ('timeout-report-' + str(expected) + '.json')
                args = self.archive_args(archive, pin, work, report)
                if requested is not None:
                    args += ['--self-test-timeout-seconds', requested]
                failure = subprocess.TimeoutExpired(['synthetic-self-test'], expected,
                                                    output=b'Synthetic partial stdout', stderr=b'Synthetic partial stderr')
                with mock.patch.object(validate_archive,'review_source',return_value={'status':'PASS','synthetic':True}), \
                        mock.patch.object(validate_archive.subprocess, 'run', side_effect=failure) as run, \
                        redirect_stdout(io.StringIO()) as stdout:
                    code = validate_archive.main(list(map(str,args)))
                data = json.loads(report.read_bytes())
                self.assertEqual((1, 'FAILED', False), (code, data['status'], data['validation_complete']))
                self.assertEqual(expected, run.call_args.kwargs['timeout'])
                self.assertEqual(expected, data['self_test_timeout_seconds'])
                self.assertEqual('Fresh ZIP default self-test runs without review pins', data['stage'])
                self.assertEqual('TIMEOUT', data['failure_kind'])
                self.assertEqual(pin, data['archive_sha256'])
                self.assertTrue(data['report_written'])
                self.assertTrue(Path(data['run_directory'], PREFIX.rstrip('/'), 'MANIFEST.json').is_file())
                self.assertIn('Synthetic partial stdout', Path(data['partial_log']).read_text())
                self.assertIn('Synthetic partial stderr', Path(data['partial_log']).read_text())
                self.assertEqual(['PASS','PASS','PASS','FAILED'],[check['status'] for check in data['checks']])
                modes = Path(data['run_directory']) / 'rebuild-git-modes.json'
                self.assertEqual(hashlib.sha256(modes.read_bytes()).hexdigest(),
                                 data['checks'][0]['raw_mode_manifest_sha256'])
                self.assertEqual(data, json.loads(stdout.getvalue()))
                self.assertEqual(archive_before, archive.read_bytes())

    def test_archive_existing_report_is_preserved_before_any_new_attempt(self):
        archive, pin = self.archive()
        report = self.root / 'previous-pass.json'
        report.write_bytes(b'{"status":"PASS","validation_complete":true}\n')
        before = snapshot(self.root)
        self.reject(ROOT / 'scripts/validate_archive.py',
                    self.archive_args(archive, pin, report=report), 'new report path')
        self.assertEqual(before, snapshot(self.root))
        self.assertFalse((self.root / 'unused-work').exists())

    def test_archive_timeout_report_write_failure_is_explicit_without_stale_pass(self):
        archive, pin = self.archive()
        failure = subprocess.TimeoutExpired(['synthetic-self-test'], 600, output=b'Partial output')
        with mock.patch.object(validate_archive,'review_source',return_value={'status':'PASS','synthetic':True}), \
                mock.patch.object(validate_archive.subprocess, 'run', side_effect=failure), \
                mock.patch.object(validate_archive, 'write_report', side_effect=PermissionError('synthetic write denial')), \
                redirect_stdout(io.StringIO()) as stdout:
            code = validate_archive.main(list(map(str,self.archive_args(archive, pin))))
        report = json.loads(stdout.getvalue())
        self.assertEqual((1, 'FAILED', False), (code, report['status'], report['validation_complete']))
        self.assertFalse(report['report_written'])
        self.assertEqual('PermissionError', report['report_write_error'])
        self.assertFalse((self.root / 'report.json').exists())
        self.assertTrue(Path(report['partial_log']).is_file())

    def synthetic_source_stage(self, command):
        """Advance non-target stages without recursive tests or acceptance writes."""
        if '--report' in command:
            path = Path(command[command.index('--report') + 1])
            if path.name == 'default-self-test.json':
                path.write_text(json.dumps({'status':'PASS','release_qualified':False,
                    'review':'NOT_PROVIDED','current_review':{'status':'NOT_PROVIDED'}}),encoding='utf-8')
        code = 2 if 'scripts/validate_archive.py' in command else 1 if '--release' in command and '--reviews' not in command else 0
        return subprocess.CompletedProcess(command,code,b'',b'')

    def test_archive_relative_review_paths_are_absolute_in_child_command(self):
        archive,pin=self.archive()
        args=self.archive_args(archive,pin)
        args[args.index('--reviews')+1]=Path('relative-review.json')
        args += ['--review-required-paths','relative-scope.json','--expected-review-required-paths-sha256','1'*64]
        def stages(command, **kwargs):
            if '--reviews' in command:
                raise subprocess.TimeoutExpired(command,600)
            return self.synthetic_source_stage(command)
        with mock.patch.object(validate_archive,'review_source',return_value={'status':'PASS','synthetic':True}) as review, \
                mock.patch.object(validate_archive.subprocess,'run',side_effect=stages) as run, \
                redirect_stdout(io.StringIO()):
            code=validate_archive.main(list(map(str,args)))
        self.assertEqual(1,code)
        command=run.call_args.args[0]
        for flag in ('--reviews','--review-required-paths'):
            self.assertTrue(Path(command[command.index(flag)+1]).is_absolute())
        self.assertTrue(review.call_args.args[2].is_absolute())
        self.assertTrue(review.call_args.args[4].is_absolute())

    def test_git_validator_rejects_aliased_source_locations_before_outputs(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'owner.txt').write_bytes(b'Original\n')
        alias = source.parent / '..' / source.parent.name / source.name
        for work, report in [(alias / 'unused-work', self.root / 'report.json'),
                             (self.root / 'unused-work', alias / 'unused-report.json')]:
            before = snapshot(self.root)
            self.reject(ROOT / 'scripts/validate_git_checkout.py',
                        ['--source', source, '--expected-manifest-sha256', '0' * 64,
                         '--workdir', work, '--report', report], 'outside the release source')
            self.assertEqual(before, snapshot(self.root))
            self.assertFalse(work.exists())
            self.assertFalse(report.exists())

    def test_git_validator_invalid_pin_retains_only_explicit_failure_report(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'owner.txt').write_bytes(b'Original\n')
        before = snapshot(source)
        report, work = self.root / 'report.json', self.root / 'unused-work'
        self.reject(ROOT / 'scripts/validate_git_checkout.py',
                    ['--source', source, '--expected-manifest-sha256', 'bad', '--workdir', work, '--report', report])
        data = json.loads(report.read_text(encoding='utf-8'))
        self.assertEqual('FAILED', data['status'])
        self.assertIn('lowercase hexadecimal', data['error'])
        self.assertFalse(data['network_used'])
        self.assertFalse(work.exists())
        self.assertEqual(before, snapshot(source))

    def test_release_builder_rejects_ignored_local_residue_before_rewriting_manifest(self):
        source = self.root / 'source'
        shutil.copytree(ROOT, source,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc', '.tmp', 'tmp', '.tmp-tests'))
        residue = source / '.tmp' / 'legacy-provider.patch'
        residue.parent.mkdir()
        residue.write_text('historical provider adapter material\n', encoding='utf-8')
        manifests = {name: (source / name).read_bytes() for name in ('MANIFEST.json', 'MANIFEST.md')}
        archive = self.root / 'candidate.zip'
        done = subprocess.run([sys.executable, '-B', str(source / 'scripts/build_release.py'), '--output', str(archive)],
                              cwd=source, env=self.env, capture_output=True, timeout=30)
        output = (done.stdout + done.stderr).decode('utf-8', errors='replace')
        self.assertNotEqual(0, done.returncode, output)
        self.assertIn('runtime/build residue', output)
        self.assertFalse(archive.exists())
        self.assertEqual(manifests['MANIFEST.json'], (source / 'MANIFEST.json').read_bytes())
        self.assertEqual(manifests['MANIFEST.md'], (source / 'MANIFEST.md').read_bytes())


if __name__ == '__main__':
    unittest.main()
