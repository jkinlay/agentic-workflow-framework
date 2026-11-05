"""Fresh-process recovery from an authentic published, runtime-free baseline.

These are installer transaction tests, not configured-installed qualification.
The existing real-runtime test remains the canonical installed-check cohort.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from agentic.installer import CONFIG, INSTALLED, PROVENANCE, install
from agentic.safeio import Tree
from agentic.child_process import child_env
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import inventory


class SimulatedTermination(BaseException):
    pass


@skip_unless_source_repo('requires authentic historical Git bytes', 'scripts/publish_release.py', '.git')
class PublishedRecoveryTests(unittest.TestCase):
    def test_receipt_provenance_and_committed_cleanup_recover_in_fresh_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = materialize_published(ROOT, base / 'published')
            target = materialize_candidate(ROOT, base / 'candidate')
            baseline = base / 'baseline'
            install_baseline(source, baseline)
            (baseline / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')
            pin = hashlib.sha256((target / 'MANIFEST.json').read_bytes()).hexdigest()
            original = Tree.write
            for boundary in (CONFIG, INSTALLED, PROVENANCE, 'commit_cleanup'):
                with self.subTest(boundary=boundary):
                    project = base / ('copy-' + Path(boundary).name)
                    shutil.copytree(baseline, project)
                    before = inventory(project)
                    reached = []

                    def crash_after_write(tree, relative, data):
                        result = original(tree, relative, data)
                        if relative == boundary:
                            reached.append(relative)
                            raise SimulatedTermination(relative)
                        return result

                    def crash_cleanup(*args, **kwargs):
                        reached.append('commit_cleanup')
                        raise SimulatedTermination('commit_cleanup')

                    hook = (patch('agentic.installer._finalize_committed', side_effect=crash_cleanup)
                            if boundary == 'commit_cleanup' else patch.object(Tree, 'write', new=crash_after_write))
                    with hook, self.assertRaises(SimulatedTermination):
                        install(target, project, pin, mode='upgrade', configure=True, discover=False)
                    self.assertTrue(reached)
                    observed = self.recover_fresh(project)
                    self.assertNotIn(observed['recovery']['status'], ('CONFIGURED', 'ACTIVE'))
                    self.assertEqual('NO_PENDING_INSTALL', self.recover_fresh(project)['recovery']['status'])
                    if boundary != 'commit_cleanup':
                        self.assertEqual('ROLLED_BACK', observed['recovery']['status'])
                        # Installer lock and empty transaction directories are
                        # coordination residue, not adopted project content.
                        after = inventory(project)
                        self.assertEqual(before, after)
                    else:
                        self.assertEqual(pin, observed['integrity'])

    def recover_fresh(self, project):
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from agentic.installer import recover
print(json.dumps(recover(sys.argv[2])))
"""
        result = subprocess.run([sys.executable, '-B', '-c', code,
                                 str(ROOT / '.agentic/lib'), str(project)],
                                capture_output=True, text=True, env=child_env(), timeout=120)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        recovery = json.loads(result.stdout)
        # Verify with the recovered endpoint's own version of the library.
        # No canonical runtime exists in this deliberately runtime-free cohort.
        verify_code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from agentic.installer import verify_installed
print(json.dumps(verify_installed(sys.argv[2])))
"""
        verified = subprocess.run([sys.executable, '-B', '-c', verify_code,
                                   str(project / '.agentic/lib'), str(project)],
                                  capture_output=True, text=True, env=child_env(), timeout=120)
        self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
        return {'recovery': recovery, 'integrity': json.loads(verified.stdout)}


if __name__ == '__main__':
    unittest.main()
