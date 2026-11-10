"""Qualification must name the exact clean, manifest-consistent source."""
from pathlib import Path
import sys
import os
import shutil
import subprocess
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
            source = Path(temporary) / 'source'
            # Copy committed source into a disposable repository; the caller's
            # checkout may be clean, dirty, or in a merge and stays untouched.
            from agentic.child_process import isolated_git_env
            git = [shutil.which('git'), '-c', 'safe.directory=' + ROOT.as_posix()]
            observed = subprocess.run([*git, '-C', str(ROOT), 'rev-parse', '--absolute-git-dir'],
                env=isolated_git_env(), capture_output=True, check=True)
            # A linked worktree's local clone validates its administrative path.
            # Trust only that observed path for this command, never global config.
            admin = Path(os.fsdecode(observed.stdout).strip())
            subprocess.run([*git, '-c', 'safe.directory=' + admin.as_posix(), 'clone', '--quiet',
                '--no-hardlinks', str(ROOT), str(source)],
                env=isolated_git_env(), capture_output=True, check=True)
            self.assertEqual(b'', subprocess.run([shutil.which('git'), '-C',
                str(source), 'status', '--porcelain'], env=isolated_git_env(),
                capture_output=True, check=True).stdout)
            readme = source / 'README.md'
            readme.write_bytes(readme.read_bytes() + b'\nDirty qualification fixture.\n')
            work = Path(temporary) / 'cohort'
            report = qualify_upgrade.qualify(source, work)
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
