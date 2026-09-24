"""Offline subprocess argument/rejection regressions, not branch-coverage claims.

Each rejection uses real CLI parsing, source integrity and filesystem boundaries.
Synthetic sealed runtimes avoid depending on a mutable developer manifest.
"""
import hashlib
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
                '--reviews',self.root/'independent-review.json','--expected-reviews-sha256','0'*64,
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
                self.assertEqual(['PASS','FAILED'],[check['status'] for check in data['checks']])
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


if __name__ == '__main__':
    unittest.main()
