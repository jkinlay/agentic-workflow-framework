"""Round-one owner recovery and retained-probe publication regressions."""
import contextlib
import hashlib
import io
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
import qualify_upgrade as qualification
from agentic import installer, ValidationError


class RecoveryDocumentationTests(unittest.TestCase):
    def test_named_limitation_is_unchanged_and_parked_provisioning_is_executable(self):
        for relative in ('MIGRATION-v1.9.5-to-v1.9.6.md', '.agentic/docs/38-RELEASE-196-QUALIFICATION.md'):
            raw = (ROOT / relative).read_bytes()
            paragraph = re.search(rb'\*\*1\.9\.6 limitation.*?(?=\n\n)', raw, re.S).group()
            self.assertEqual('43d09674f9f5fb1b90db449972140df7d286e3032f8ce4c6e5f86f7fe6a0cb68',
                             hashlib.sha256(paragraph).hexdigest())
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        for clause in ('PROJECT/AWF-RECOVERY-PARKED.txt', 'Original stays quiescent/untouched',
                       'Digest snapshots cannot restore bytes', 'git clone --no-hardlinks',
                       'git -C FRESH checkout --detach PRE_UPGRADE_COMMIT', 'Run step 8 there',
                       'All-empty backup tree: retain deliberately', 'combined retained records=RETAINED_DIRECTORIES',
                       "PRE_UPGRADE_COMMIT's `.agentic/requirements.lock`", 'a superset is refused',
                       'WORK/baseline-wheels'):
            self.assertIn(clause, document)
        line = next(line for line in document.splitlines() if line.startswith('python -B -c '))
        from agentic import adoption_config
        with patch.object(adoption_config, 'ensure_installed_runtime') as provision:
            exec(compile(shlex.split(line)[3], '<documented parked provisioning>', 'exec'), {})
        provision.assert_called_once_with('.', wheelhouse='<hash-verified wheelhouse>')

    def test_shipped_plan_and_installed_examples_execute_with_real_parser(self):
        document = (ROOT / '.agentic/docs/38-RELEASE-196-QUALIFICATION.md').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for command, method, report in (
                    ('plan', 'plan', dict(status='PLAN')),
                    ('installed', 'qualify', dict(status='QUALIFIED_DISPOSABLE_UPGRADE'))):
                lines = [line for line in document.splitlines()
                         if line.startswith('PYTHON -B RELEASE/scripts/qualify_upgrade.py ' + command + ' ')]
                self.assertEqual(1, len(lines))
                replacements = dict(PROJECT=str(root / 'project'), RELEASE=str(ROOT), PIN='a' * 64,
                    WHEELS=str(root / 'wheels'), WORK=str(root / 'work'), PLAN=str(root / 'plan.json'),
                    REPORT=str(root / 'installed.json'), ID='123', BRANCH='main')
                arguments = [replacements.get(token, token) for token in shlex.split(lines[0])[3:]]
                with patch.object(qualification, method, return_value=report) as called, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, qualification.main(arguments))
                called.assert_called_once()

    def test_lock_primary_capture_preserves_bytes_size_digest_and_existing_evidence(self):
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        line = next(line for line in document.splitlines()
                    if line.startswith('PYTHON -c ') and 'hashlib' in line)
        code = shlex.split(line)[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for index, data in enumerate((b'', b'lock\x00\xff\r\n')):
                lock, output = root / ('lock' + str(index)), root / ('capture' + str(index))
                lock.write_bytes(data)
                arguments = [sys.executable, '-B', '-c', code, str(lock), str(output)]
                result = subprocess.run(arguments, capture_output=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(dict(hex=data.hex(), size=len(data), sha256=hashlib.sha256(data).hexdigest()),
                                 json.loads(output.read_bytes()))
                captured = output.read_bytes()
                result = subprocess.run(arguments, capture_output=True, timeout=30)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(captured, output.read_bytes())
                self.assertEqual(data, lock.read_bytes())

    def test_posix_capture_handles_absent_apis_without_attribute_error(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        line = next(line for line in document.splitlines() if line.startswith('PYTHON -c ') and 'listxattr' in line)
        code = shlex.split(line)[2]
        native_import = __import__
        for view, expected in ((SimpleNamespace(), {'capture': 'APIS_UNAVAILABLE'}),
                (SimpleNamespace(listxattr=Mock(return_value=[]), getxattr=Mock()), {}),
                (SimpleNamespace(listxattr=Mock(return_value=['user.awf-temporary-custody']),
                    getxattr=Mock(return_value=b'custody')), {'user.awf-temporary-custody': b'custody'.hex()})):
            output = io.StringIO()
            def imports(name, *args, **kwargs):
                return view if name == 'os' else native_import(name, *args, **kwargs)
            with patch('builtins.__import__', imports), patch.object(sys, 'argv', ['capture', 'LOCK']), \
                    contextlib.redirect_stdout(output):
                exec(compile(code, '<documented xattr capture>', 'exec'), {})
            self.assertEqual(expected, json.loads(output.getvalue()))
        unreadable = SimpleNamespace(listxattr=lambda path: ['user.awf-temporary-custody'],
                                     getxattr=Mock(side_effect=OSError('unreadable recorded custody')))
        with patch('builtins.__import__', lambda name, *a, **kw: unreadable if name == 'os' else native_import(name, *a, **kw)), \
                patch.object(sys, 'argv', ['capture', 'LOCK']), self.assertRaises(OSError):
            exec(compile(code, '<documented xattr capture>', 'exec'), {})

    @unittest.skipUnless(os.name == 'nt', 'WIN32_ADS: PowerShell stream capture is Windows-only')
    def test_windows_capture_preserves_ads_and_refuses_existing_output(self):
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        command = next(line for line in document.splitlines() if line.startswith('ConvertTo-Json -InputObject @(Get-Item '))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            lock = root / 'lock'
            lock.write_bytes(b'0')
            ads = str(lock) + ':awf-temporary-custody'
            with open(ads, 'xb') as stream:
                stream.write(b'custody\x00\xff')
            with open(str(lock) + ':empty-custody-fixture', 'xb'):
                pass
            output = root / 'ads.json'
            environment = dict(os.environ, AWF_CAPTURE_LOCK=str(lock), AWF_CAPTURE_OUTPUT=str(output))
            script = "$LOCK=$env:AWF_CAPTURE_LOCK\n$EXTERNAL_ADS=$env:AWF_CAPTURE_OUTPUT\n$ErrorActionPreference='Stop'\n" + command
            arguments = ['powershell', '-NoProfile', '-NonInteractive', '-Command', script]
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, env=environment)
            self.assertEqual(0, result.returncode, result.stderr)
            rows = json.loads(output.read_text(encoding='utf-8-sig'))
            if isinstance(rows, dict):
                rows = [rows]
            self.assertEqual([dict(name='awf-temporary-custody', hex=b'custody\x00\xff'.hex().upper()),
                              dict(name='empty-custody-fixture', hex='')], sorted(rows, key=lambda row: row['name']))
            previous = output.read_bytes()
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, env=environment)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(previous, output.read_bytes())
            self.assertEqual(b'0', lock.read_bytes())


class ReportPublicationTests(unittest.TestCase):
    def test_destination_occupied_after_probe_preparation_reports_actual_retained_replica(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ('project', 'source', 'baseline'):
                (root / name).mkdir()
            for name in ('before', 'after'):
                (root / name).write_text('{}', encoding='utf-8')
            output = root / 'report'
            probe = root / 'private-replica'
            def prepare(*args):
                probe.mkdir(mode=0o700)
                qualification._PERMISSION_PROBES.get().append(probe)
                output.write_bytes(b'owner report appeared after preflight')
                raise ValueError('injected preparation refusal after replica creation')
            stderr = io.StringIO()
            with patch.object(qualification, '_prepare_upgrade', prepare), \
                    contextlib.redirect_stderr(stderr), self.assertRaises(FileExistsError):
                qualification.main(['compare', '--project', str(root / 'project'),
                    '--source', str(root / 'source'), '--baseline-project', str(root / 'baseline'),
                    '--expected-manifest-sha256', '0' * 64, '--before', str(root / 'before'),
                    '--after', str(root / 'after'), '--output', str(output)])
            self.assertEqual(b'owner report appeared after preflight', output.read_bytes())
            self.assertTrue(probe.is_dir())
            self.assertEqual([str(probe)], json.loads(stderr.getvalue())['retained_probes'])

    def test_existing_output_refuses_before_probe_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ('project', 'source', 'baseline'):
                (root / name).mkdir()
            for name in ('before', 'after'):
                (root / name).write_text('{}', encoding='utf-8')
            output = root / 'report'
            output.write_bytes(b'previous report')
            with patch.object(qualification, 'prepare_upgrade', side_effect=ValueError('injected probe producer')) as prepare:
                with self.assertRaises(FileExistsError):
                    qualification.main(['compare', '--project', str(root / 'project'),
                        '--source', str(root / 'source'), '--baseline-project', str(root / 'baseline'),
                        '--expected-manifest-sha256', '0' * 64, '--before', str(root / 'before'),
                        '--after', str(root / 'after'), '--output', str(output)])
            prepare.assert_not_called()
            self.assertEqual(b'previous report', output.read_bytes())

    def test_publication_failure_prints_retained_probe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'project').mkdir()
            for name in ('before', 'after'):
                (root / name).write_text('{}', encoding='utf-8')
            report = dict(preserved=False, violations=[], retained_probes=[str(root / 'private/probe')],
                          retained_probe_roots=[str(root / 'private')])
            stderr = io.StringIO()
            with patch.object(qualification, 'compare', return_value=report), \
                    patch.object(qualification, 'write_report', side_effect=OSError('publication denied')), \
                    contextlib.redirect_stderr(stderr):
                with self.assertRaises(OSError):
                    qualification.main(['compare', '--project', str(root / 'project'),
                        '--before', str(root / 'before'), '--after', str(root / 'after'),
                        '--output', str(root / 'report')])
            observed = json.loads(stderr.getvalue())
            self.assertEqual(report['retained_probes'], observed['retained_probes'])
            self.assertEqual(report['retained_probe_roots'], observed['retained_probe_roots'])


class JournalAuthenticationTests(unittest.TestCase):
    def test_journal_copy_must_match_present_endpoint_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = root / '.agentic/.venv'
            runtime.mkdir(parents=True)
            (runtime / 'member').write_bytes(b'new runtime')
            transaction = str(uuid.uuid4())
            journal = dict(format='awf-install-journal-4', phase='active', files=[],
                transaction_id=transaction, managed_before_sha256='0' * 64,
                managed_after_sha256='0' * 64, runtime=installer._runtime_journal(root, transaction))
            digest = installer._runtime_tree_sha256(runtime)
            journal['runtime'].update(stage_identity=installer.runtime_creation_identity(runtime), new_sha256=digest)
            raw = installer.json_bytes(journal)
            copy = root / 'copy.json'
            copy.write_bytes(raw)
            marker = root / installer.MARKER
            marker.write_bytes(installer.json_bytes(installer._journal_marker(transaction, installer.sha256(raw))))
            arguments = ['runtime-hash', '--path', str(runtime), '--expected-sha256', digest, '--journal', str(copy)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, qualification.main(arguments))
            journal['managed_after_sha256'] = '1' * 64
            pending_raw = installer.json_bytes(journal)
            copy.write_bytes(pending_raw)
            with self.assertRaisesRegex(ValueError, 'bound marker'):
                qualification.main(arguments)
            marker.write_bytes(installer.json_bytes(installer._journal_marker(transaction,
                installer.sha256(raw), installer.sha256(pending_raw), 'managed_after')))
            with self.assertRaisesRegex(ValueError, 'normalize pending update'):
                qualification.main(arguments)

    def test_journal_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = root / 'runtime'
            runtime.mkdir()
            journal = root / 'journal'
            journal.write_bytes(b' ' * (installer.MAX_DOCUMENT_BYTES + 1))
            with patch.object(Path, 'read_text', side_effect=AssertionError('unbounded journal read')), \
                    self.assertRaisesRegex(ValueError, 'limit|large|SIZE|bound|exceeds|size'):
                qualification.main(['runtime-hash', '--path', str(runtime), '--expected-sha256',
                    installer._runtime_tree_sha256(runtime), '--journal', str(journal)])


if __name__ == '__main__':
    unittest.main()
