"""Private, operation-bound retained permission replica reporting."""
import os
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import qualify_upgrade as qualification


class RetentionTests(unittest.TestCase):
    def test_missing_xattr_apis_refuse_only_exact_qualification_before_creating_probes(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            owner = parent / 'owner'
            owner.write_bytes(b'OWNER DATA')
            before = (owner.stat().st_ino, owner.stat().st_mode, owner.read_bytes())
            for substitution in (False, True):
                with self.subTest(substitution=substitution):
                    view = SimpleNamespace(**vars(os))
                    view.name = 'posix'
                    for name in ('getxattr', 'setxattr', 'removexattr', 'listxattr'):
                        if hasattr(view, name):
                            delattr(view, name)
                    def substitute(*args):
                        owner.write_bytes(b'forbidden substitution')
                        self.fail('missing API refusal must precede every replica operation')
                    with patch.object(qualification, 'os', view), \
                            patch.object(qualification, '_remember_permission_probe', side_effect=substitute if substitution else None) as remembered, \
                            patch.object(qualification.tempfile, 'mkdtemp', side_effect=AssertionError('probe created')):
                        with self.assertRaisesRegex(qualification.safeio.ValidationError, 'lacks xattr APIs.*xattr-capable'):
                            qualification._posix_probe_parent(parent)
                    remembered.assert_not_called()
                    self.assertEqual(before, (owner.stat().st_ino, owner.stat().st_mode, owner.read_bytes()))

    def test_compare_preparation_refusal_durably_reports_created_probes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('project', 'source', 'baseline'):
                (root / name).mkdir()
            for name in ('before.json', 'after.json'):
                (root / name).write_text('{}', encoding='utf-8')
            retained = []
            def refuse(*args):
                probe = Path(tempfile.mkdtemp(prefix='awf-permission-probe-', dir=root))
                qualification._PERMISSION_PROBES.get().append(probe)
                retained.append(str(probe))
                raise ValueError('synthetic preservation refusal')
            with patch.object(qualification, '_prepare_upgrade', refuse):
                result = qualification.main(['compare', '--project', str(root / 'project'),
                    '--before', str(root / 'before.json'), '--after', str(root / 'after.json'),
                    '--source', str(root / 'source'), '--expected-manifest-sha256', '0' * 64,
                    '--baseline-project', str(root / 'baseline'), '--output', str(root / 'report.json')])
            self.assertEqual(1, result)
            report = json.loads((root / 'report.json').read_bytes())
            self.assertFalse(report['preserved'])
            self.assertEqual(retained, report['retained_probes'])
            self.assertEqual(retained, report['retained_probe_roots'])
            self.assertFalse(report['execution_authority'])

    def test_retained_report_uses_creation_records_even_if_enumeration_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with qualification._permission_probe_collection() as roots:
                roots.append(root)
                with patch.object(Path, 'rglob', side_effect=PermissionError('copied ACL denies enumeration')):
                    report = qualification._retained_probe_report(roots)
            self.assertEqual([str(root)], report['retained_probes'])

    def test_recovery_docs_define_original_and_backup_creation_ownership(self):
        root = Path(__file__).resolve().parents[2]
        blocks = []
        for relative in ('MIGRATION-v1.9.5-to-v1.9.6.md',
                         '.agentic/docs/38-RELEASE-196-QUALIFICATION.md',
                         'docs/runbook/19-runbook-journal-recovery.md'):
            text = (root / relative).read_text(encoding='utf-8')
            for required in ('step-2 journal copy', '`posix_ownership` records ORIGINALS only',
                             'installing effective UID', 'otherwise installing effective GID',
                             'contemporaneous trusted evidence binding stage creation/identity to this transaction',
                             'Require new_sha256/new_mode', 'Unknown/differing/unrecorded/unprovable metadata stops'):
                self.assertIn(required, text)
            blocks.append(text[text.index('1. No exclusivity-proof CLI:'):].split('\n\n', 1)[0])
        self.assertEqual([blocks[0]] * 3, blocks)


    def test_every_posix_retained_path_is_reported_under_private_wrapper(self):
        self.assertTrue(callable(qualification._permission_probe_collection))
        self.assertTrue(callable(qualification._retained_probe_report))
        if os.name == 'nt':
            self.assertTrue(callable(qualification._windows_probe_directory))
            return
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / 'parent'
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            if not all(hasattr(os, name) for name in ('listxattr', 'getxattr', 'setxattr', 'removexattr')):
                before = parent.stat()
                with qualification._permission_probe_collection() as roots:
                    with self.assertRaisesRegex(qualification.safeio.ValidationError, 'lacks xattr APIs.*xattr-capable'):
                        qualification._creation_state(parent, 'new/file')
                    self.assertEqual([], roots)
                self.assertEqual([], list(parent.iterdir()))
                self.assertEqual((before.st_ino, before.st_mode), (parent.stat().st_ino, parent.stat().st_mode))
                return  # Missing-API POSIX platforms refuse exact replication.
            if hasattr(os, 'setxattr'):
                os.setxattr(parent, 'user.awf-test-private', b'private synthetic value')
            with patch.object(qualification.tempfile, 'tempdir', str(root)):
                with qualification._permission_probe_collection() as roots:
                    for _ in range(2):
                        qualification._creation_state(parent, 'new/file')
                    report = qualification._retained_probe_report(roots)
            self.assertEqual(2, len(roots))
            actual = set()
            for wrapper in roots:
                wrapper = Path(wrapper)
                self.assertEqual(0o700, stat.S_IMODE(wrapper.stat().st_mode))
                actual.add(str(wrapper))
                actual.update(str(path) for path in wrapper.rglob('*'))
                self.assertEqual(0o755, stat.S_IMODE((wrapper / 'replica').stat().st_mode))
            self.assertEqual(actual, set(report['retained_probes']))
            self.assertEqual(len(actual), len(report['retained_probes']))
