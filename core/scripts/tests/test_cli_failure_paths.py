"""Offline subprocess argument/rejection regressions, not branch-coverage claims.

Each rejection uses real CLI parsing, source integrity and filesystem boundaries.
Synthetic sealed runtimes avoid depending on a mutable developer manifest.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_archive import PREFIX, RELEASE_LINE, VERSION, preflight


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class CliFailurePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in (entries or {PREFIX + 'MANIFEST.json': b'{}'}).items():
                entry = zipfile.ZipInfo(name)
                # ZipInfo normalizes backslashes on Windows; force raw inventory
                # to exercise the actual foreign/malformed ZIP boundary.
                entry.filename = name
                archive.writestr(entry, data)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def archive_args(self, path, pin, work=None, report=None):
        return ['--archive', path, '--expected-zip-sha256', pin,
                '--workdir', work or self.root / 'unused-work', '--report', report or self.root / 'report.json']

    def test_each_entry_point_rejects_missing_required_arguments_without_writes(self):
        for name in ['scripts/bootstrap_project.py', '.agentic/scripts/workflow.py',
                     '.agentic/scripts/project_status.py', 'scripts/validate_archive.py',
                     'scripts/validate_git_checkout.py']:
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
        self.assertEqual('.'.join(VERSION.split('.')[:2]), RELEASE_LINE)
        self.assertEqual(f'agentic-workflow-template-v{RELEASE_LINE}/', PREFIX)
        archive, pin = self.archive()
        before = snapshot(self.root)
        resolved = preflight(archive, pin, self.root / 'unused-work', self.root / 'report.json')
        self.assertEqual(archive.resolve(), resolved[0])
        self.assertEqual(before, snapshot(self.root))
        self.assertFalse(resolved[1].exists())

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


if __name__ == '__main__':
    unittest.main()
