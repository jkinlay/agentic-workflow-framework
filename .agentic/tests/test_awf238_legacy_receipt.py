"""Registered legacy receipts need no invented embedded source manifest."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests')]
from agentic.installer import install, verify_installed
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import materialize_candidate
from upgrade_fixtures import materialize, verify_materialized


@skip_unless_source_repo('requires registered historical fixture bytes',
                         '.agentic/tests/fixtures/upgrades/versions.json')
class LegacyReceiptTests(unittest.TestCase):
    def test_registered_183_without_embedded_manifest_still_upgrades(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / 'project'
            project.mkdir()
            materialize('1.8.3', project)
            verify_materialized('1.8.3', project)
            receipt = json.loads((project / '.agentic/installed-manifest.json').read_bytes())
            self.assertNotIn('source_manifest_json', receipt)
            candidate = materialize_candidate(ROOT, base / 'candidate')
            pin = hashlib.sha256((candidate / 'MANIFEST.json').read_bytes()).hexdigest()
            result = install(candidate, project, pin, mode='upgrade', configure=True, discover=False)
            self.assertEqual('UPGRADED', result['status'])
            self.assertEqual('1.8.3', result['upgrade']['detected_version'])
            self.assertEqual('REJECTED', result['configuration']['status'])
            self.assertEqual(pin, verify_installed(project))


class ImportMembershipTests(unittest.TestCase):
    def test_absent_undeclared_import_directories_are_empty(self):
        from agentic.adoption_config import verify_import_membership
        with tempfile.TemporaryDirectory() as temporary:
            verify_import_membership(Path(temporary), {'files': {}})

    def test_undeclared_import_files_still_refuse(self):
        from agentic import ValidationError
        from agentic.adoption_config import verify_import_membership
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.agentic/lib').mkdir(parents=True)
            (root / '.agentic/lib/unexpected.py').write_bytes(b'# Synthetic untrusted import\n')
            with self.assertRaisesRegex(ValidationError, 'Unexpected installed runtime import files'):
                verify_import_membership(root, {'files': {}})

    def test_declared_missing_directory_still_refuses(self):
        from agentic.adoption_config import verify_import_membership
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                verify_import_membership(Path(temporary),
                    {'files': {'.agentic/lib/pinned.py': 'a' * 64}})


if __name__ == '__main__':
    unittest.main()
