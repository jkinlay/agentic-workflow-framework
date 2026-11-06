"""Qualification must name the exact clean, manifest-consistent source."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import qualify_upgrade


class SourceIdentityTests(unittest.TestCase):
    def test_dirty_source_refuses_before_creating_cohort(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / 'cohort'
            report = qualify_upgrade.qualify(ROOT, work)
            self.assertEqual('NOT_QUALIFIED', report['status'])
            self.assertEqual('source_identity', report['failed_stage'])
            self.assertIn('dirty', report['diagnostic'].lower())
            self.assertFalse(work.exists())

    def test_clean_source_with_stale_manifest_refuses(self):
        # Unit-only Git observations; this is never qualification evidence.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            (root / 'MANIFEST.json').write_text('{}\n', encoding='utf-8')
            def git(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout=b'' if 'status' in command else b'a' * 40, stderr=b'')
            with patch('qualify_upgrade.run_bounded', side_effect=git), self.assertRaisesRegex(ValueError, 'manifest'):
                qualify_upgrade.source_identity(root)


if __name__ == '__main__':
    unittest.main()
