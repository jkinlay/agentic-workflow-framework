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
import shutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
import qualify_upgrade as qualification
from agentic import installer, ValidationError


class RecoveryDocumentationTests(unittest.TestCase):
    def test_ads_capture_selects_the_supported_byte_switch(self):
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        command = next(line for line in document.splitlines() if line.startswith('ConvertTo-Json -InputObject @(Get-Item '))
        self.assertIn('if ($PSVersionTable.PSVersion.Major -ge 6)', command)
        self.assertIn('-AsByteStream -Raw -ErrorAction Stop', command)
        self.assertIn('else { Get-Content -LiteralPath $LOCK -Stream $_.Stream -Encoding Byte -Raw -ErrorAction Stop }', command)

    def test_committed_cleanup_pending_skips_quarantine_and_checks_current_release(self):
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        step2 = next(line for line in document.splitlines() if line.startswith('2. '))
        self.assertIn('ROLLED_BACK/COMMITTED/COMMITTED_CLEANUP_PENDING: skip quarantine', step2)
        step5 = next(line for line in document.splitlines() if line.startswith('5. '))
        self.assertIn('ROLLED_BACK/COMMITTED/COMMITTED_CLEANUP_PENDING from step 2 or 4', step5)
        self.assertIn('transaction_cleanup_failures/retained_directories/installation_recovery', step5)
        self.assertIn('Committed endpoints: continue at step 6, then 7-8, ending at committed-1.9.6 checks', step5)
        step6 = next(line for line in document.splitlines() if line.startswith('6. '))
        self.assertIn('ROLLED_BACK/COMMITTED/COMMITTED_CLEANUP_PENDING OR backup refusal, resolve applicable rows', step6)
        self.assertIn('never restore over committed runtime', step6)
        step8 = next(line for line in document.splitlines() if line.startswith('8. '))
        self.assertIn('Committed 1.9.6: require exit 0/integrity_valid=true/status=ACCEPTED; status is CONFIGURED unless ACTIVE is independently proved', step8)

    def test_parked_wheel_download_names_index_or_verified_mirror(self):
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        self.assertIn('Download requires a reachable package index or hash-verified mirror, including on air-gapped hosts.', document)

    @unittest.skipUnless(any(shutil.which(shell) for shell in ('bash', 'zsh', 'dash')),
                         'POSIX_SHELL: shell evidence capture requires bash, zsh or dash')
    def test_xattr_shell_capture_refuses_existing_evidence(self):
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        block = next(block for block in re.findall(r'```(?:text|sh)\n(.*?)```', document, re.S)
                     if 'listxattr' in block)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            lock, output = root / 'lock', root / 'xattrs.json'
            lock.write_bytes(b'lock bytes')
            script = block.replace('PYTHON', shlex.quote(Path(sys.executable).as_posix()))
            script = script.replace('EXTERNAL_XATTRS', shlex.quote(output.as_posix()))
            script = script.replace('LOCK', shlex.quote(lock.as_posix()))
            for shell in ('bash', 'zsh', 'dash'):
                if not shutil.which(shell):
                    continue
                with self.subTest(shell=shell):
                    result = subprocess.run([shutil.which(shell), '-c', script], capture_output=True, timeout=30)
                    self.assertEqual(0, result.returncode, result.stderr)
                    json.loads(output.read_bytes())
                    output.write_bytes(b'previous owner evidence')
                    result = subprocess.run([shutil.which(shell), '-c', script], capture_output=True, timeout=30)
                    self.assertNotEqual(0, result.returncode, result.stderr)
                    self.assertEqual(b'previous owner evidence', output.read_bytes())
                    self.assertEqual(b'lock bytes', lock.read_bytes())
                    output.unlink()
                    result = subprocess.run([shutil.which(shell), '-c', script + '\ncase $- in *C*) exit 19;; esac'],
                                            capture_output=True, timeout=30)
                    self.assertEqual(0, result.returncode, result.stderr)
                    output.unlink()

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
                capture = json.loads(output.read_bytes())
                self.assertEqual(dict(hex=data.hex(), size=len(data), sha256=hashlib.sha256(data).hexdigest()),
                                 {key: value for key, value in capture.items() if key != 'path'})
                self.assertEqual(str(lock.absolute()), capture['path'])
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
        for view, expected in ((SimpleNamespace(), 'APIS_UNAVAILABLE'),
                (SimpleNamespace(listxattr=Mock(return_value=[]), getxattr=Mock()), {}),
                (SimpleNamespace(listxattr=Mock(return_value=['user.awf-temporary-custody']),
                    getxattr=Mock(return_value=b'custody')), {'user.awf-temporary-custody': b'custody'.hex()}),
                (SimpleNamespace(listxattr=Mock(return_value=['user.awf-temporary-custody']),
                    getxattr=Mock(return_value=b'custody\x00\xff')), {'user.awf-temporary-custody': b'custody\x00\xff'.hex()})):
            view.lstat = os.lstat
            view.path = os.path
            def imports(name, *args, **kwargs):
                return view if name == 'os' else native_import(name, *args, **kwargs)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                lock, output = root / 'lock', root / 'xattrs.json'
                lock.write_bytes(b'lock bytes')
                with patch('builtins.__import__', imports), patch.object(sys, 'argv', ['capture', str(lock), str(output)]):
                    exec(compile(code, '<documented xattr capture>', 'exec'), {})
                    capture = json.loads(output.read_bytes())
                    self.assertEqual(expected, capture['capture'])
                    self.assertEqual(os.path.abspath(lock), capture['path'])
                    self.assertEqual({'path', 'capture'}, set(capture))
                    previous = output.read_bytes()
                    with self.assertRaises(FileExistsError):
                        exec(compile(code, '<documented xattr capture>', 'exec'), {})
                    self.assertEqual(previous, output.read_bytes())
        unreadable = SimpleNamespace(lstat=os.lstat, path=os.path,
                                     listxattr=lambda path: ['user.awf-temporary-custody'],
                                     getxattr=Mock(side_effect=OSError('unreadable recorded custody')))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            lock, output = root / 'lock', root / 'xattrs.json'
            lock.write_bytes(b'lock bytes')
            with patch('builtins.__import__', lambda name, *a, **kw: unreadable if name == 'os' else native_import(name, *a, **kw)), \
                    patch.object(sys, 'argv', ['capture', str(lock), str(output)]), self.assertRaises(OSError):
                exec(compile(code, '<documented xattr capture>', 'exec'), {})
            self.assertFalse(output.exists())
            unreadable.getxattr.side_effect = None
            unreadable.getxattr.return_value = b'corrected\x00bytes'
            with patch('builtins.__import__', lambda name, *a, **kw: unreadable if name == 'os' else native_import(name, *a, **kw)), \
                    patch.object(sys, 'argv', ['capture', str(lock), str(output)]):
                exec(compile(code, '<documented xattr capture>', 'exec'), {})
            capture = json.loads(output.read_bytes())
            self.assertEqual({'user.awf-temporary-custody': b'corrected\x00bytes'.hex()}, capture['capture'])
            self.assertEqual(os.path.abspath(lock), capture['path'])
            self.assertEqual({'path', 'capture'}, set(capture))

    def test_posix_capture_refuses_missing_lock_without_output_even_without_apis(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        document = (ROOT / 'docs/runbook/19-runbook-journal-recovery.md').read_text(encoding='utf-8')
        line = next(line for line in document.splitlines() if line.startswith('PYTHON -c ') and 'listxattr' in line)
        code = shlex.split(line)[2]
        native_import = __import__
        for available in (False, True):
            with self.subTest(xattr_apis_available=available), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                lock, output = root / 'missing-lock', root / 'xattrs.json'
                view = SimpleNamespace(lstat=os.lstat, path=os.path)
                if available:
                    view.listxattr = Mock(return_value=[])
                    view.getxattr = Mock()
                with patch('builtins.__import__', lambda name, *a, **kw: view if name == 'os' else native_import(name, *a, **kw)), \
                        patch.object(sys, 'argv', ['capture', str(lock), str(output)]), self.assertRaises(FileNotFoundError):
                    exec(compile(code, '<documented xattr capture>', 'exec'), {})
                self.assertFalse(output.exists())
                if available:
                    view.listxattr.assert_not_called()
                    view.getxattr.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'WIN32_ADS: PowerShell stream capture is Windows-only')
    def test_windows_capture_preserves_ads_and_refuses_existing_output(self):
        self.assertIsNotNone(shutil.which('powershell'))
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
            for shell in ('powershell', 'pwsh'):
                if not shutil.which(shell):
                    continue
                with self.subTest(shell=shell):
                    shell_output = output.with_name(shell + '-ads.json')
                    environment['AWF_CAPTURE_OUTPUT'] = str(shell_output)
                    arguments = [shutil.which(shell), '-NoProfile', '-NonInteractive', '-Command', script]
                    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, env=environment)
                    self.assertEqual(0, result.returncode, result.stderr)
                    rows = json.loads(shell_output.read_text(encoding='utf-8-sig'))
                    if isinstance(rows, dict):
                        rows = [rows]
                    self.assertEqual([dict(name='awf-temporary-custody', hex=b'custody\x00\xff'.hex().upper()),
                                      dict(name='empty-custody-fixture', hex='')], sorted(rows, key=lambda row: row['name']))
                    previous = shell_output.read_bytes()
                    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, env=environment)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(previous, shell_output.read_bytes())
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
