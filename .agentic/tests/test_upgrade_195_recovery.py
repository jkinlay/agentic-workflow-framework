"""Fresh-process recovery from an authentic published baseline.

Transaction tests include stdlib venvs without locked dependencies.
The qualification runner separately requires canonical installed checks.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from agentic.installer import (CONFIG, INSTALLED, PROVENANCE, JOURNAL, MARKER,
                               RECOVERY_OBJECTS, install)
from agentic.safeio import Tree
from agentic.child_process import child_env
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import inventory


class CreatedDirectorySafetyTests(unittest.TestCase):
    def test_cleanup_preserves_preexisting_and_refuses_unowned_directories(self):
        from agentic import ValidationError
        from agentic.installer import _cleanup_transaction_directories, _directory_identity
        with tempfile.TemporaryDirectory() as temporary, Tree(temporary) as tree:
            root = Path(temporary)
            (root / 'owner').mkdir()
            (root / 'created').mkdir()
            row = {'path': 'created', 'identity': _directory_identity(tree, 'created')}
            (root / 'created/owner-empty-directory').mkdir()
            with self.assertRaisesRegex(ValidationError, 'safely empty'):
                _cleanup_transaction_directories(tree, {'created_directories': [row]})
            self.assertTrue((root / 'created/owner-empty-directory').is_dir())
            (root / 'created/owner-empty-directory').rmdir()
            for identity in (None, [0, 0]):
                with self.assertRaisesRegex(ValidationError, 'changed'):
                    _cleanup_transaction_directories(tree, {'created_directories': [dict(row, identity=identity)]})
            _cleanup_transaction_directories(tree, {'created_directories': [row]})
            _cleanup_transaction_directories(tree, {'created_directories': [row]})
            self.assertFalse((root / 'created').exists())
            self.assertTrue((root / 'owner').is_dir())


class SimulatedTermination(BaseException):
    pass


@skip_unless_source_repo('requires authentic historical Git bytes', 'scripts/publish_release.py', '.git')
class PublishedRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_published(ROOT, cls.base / 'published')
        cls.target = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.baseline = cls.base / 'baseline'
        install_baseline(cls.source, cls.baseline)
        (cls.baseline / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')
        cls.pin = hashlib.sha256((cls.target / 'MANIFEST.json').read_bytes()).hexdigest()

    def test_receipt_provenance_and_committed_cleanup_recover_in_fresh_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, baseline, pin = self.target, self.baseline, self.pin
            original = Tree.write
            for boundary in (CONFIG, INSTALLED, PROVENANCE, '.agentic/SPECIFICATION.md',
                             'object_staging', 'commit_cleanup'):
                with self.subTest(boundary=boundary):
                    project = base / ('copy-' + Path(boundary).name)
                    shutil.copytree(baseline, project)
                    before = inventory(project)
                    reached = []

                    def crash_after_write(tree, relative, data):
                        result = original(tree, relative, data)
                        if (relative == boundary or
                                boundary == 'object_staging' and relative.startswith(RECOVERY_OBJECTS + '/')):
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

    def test_missing_and_tampered_objects_refuse_without_mutation(self):
        for damage in ('missing', 'tampered', 'unknown_phase', 'external_directory_content'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / 'project'
                shutil.copytree(self.baseline, project)
                original = Tree.write

                def terminate(tree, relative, data):
                    result = original(tree, relative, data)
                    if relative == INSTALLED:
                        raise SimulatedTermination(relative)
                    return result

                with patch.object(Tree, 'write', new=terminate), self.assertRaises(SimulatedTermination):
                    install(self.target, project, self.pin, mode='upgrade', configure=True, discover=False)
                journal = json.loads((project / JOURNAL).read_bytes())
                if damage == 'unknown_phase':
                    journal['phase'] = 'unknown'
                    raw = json.dumps(journal).encode()
                    (project / JOURNAL).write_bytes(raw)
                    marker = json.loads((project / MARKER).read_bytes())
                    marker['journal_sha256'] = hashlib.sha256(raw).hexdigest()
                    (project / MARKER).write_text(json.dumps(marker), encoding='utf-8')
                elif damage == 'external_directory_content':
                    row = next(row for row in journal['created_directories']
                               if row['path'].startswith('.agentic-state/archive'))
                    (project / row['path'] / 'owner-note').write_bytes(b'owner bytes')
                else:
                    obj = next((project / RECOVERY_OBJECTS).iterdir())
                    if damage == 'missing':
                        obj.unlink()
                    else:
                        raw = obj.read_bytes()
                        obj.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                # Cleanup may restore the old endpoint before refusing an external
                # directory member. Subsequent refusals must leave that state exact.
                first = self.recover_refused(project)
                before = inventory(project)
                second = self.recover_refused(project)
                self.assertEqual(first, second)
                self.assertEqual(before, inventory(project))
                self.assertTrue((project / JOURNAL).exists())
                self.assertTrue((project / MARKER).exists())
                if damage == 'external_directory_content':
                    self.assertEqual(b'owner bytes', (project / row['path'] / 'owner-note').read_bytes())

    def test_real_venv_staging_swap_and_commit_recover_after_process_exit(self):
        # These are real stdlib venvs, deliberately without locked dependencies.
        # Only the separate qualification runner can establish CONFIGURED.
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            baseline = base / 'baseline'
            shutil.copytree(self.baseline, baseline)
            venv.EnvBuilder(with_pip=False).create(baseline / '.agentic/.venv')
            for boundary in ('before_stage', 'partial_stage', 'staged', 'backup_swap',
                             'runtime_swap', 'commit_cleanup'):
                with self.subTest(boundary=boundary):
                    project = base / boundary
                    shutil.copytree(baseline, project)
                    result = install(self.target, project, self.pin, mode='upgrade',
                                     configure=True, discover=False, defer_runtime=True)
                    code = """
import os, sys, venv
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agentic.installer as installer
boundary = sys.argv[4]
def stop():
    os._exit(73)
def builder(transaction):
    if boundary == 'before_stage': stop()
    venv.EnvBuilder(with_pip=False).create(transaction.stage)
    if boundary == 'partial_stage': stop()
    transaction.record_staged_runtime()
    if boundary == 'staged': stop()
    os.replace(transaction.runtime_root, transaction.backup)
    if boundary == 'backup_swap': stop()
    os.replace(transaction.stage, transaction.runtime_root)
    if boundary == 'runtime_swap': stop()
    return {}
if boundary == 'commit_cleanup':
    installer._cleanup_committed_runtime = lambda *args: stop()
installer.complete_runtime_transaction(sys.argv[2], sys.argv[3], builder)
"""
                    stopped = subprocess.run([sys.executable, '-B', '-c', code,
                        str(ROOT / '.agentic/lib'), str(project),
                        result['_runtime_transaction_id'], boundary], capture_output=True,
                        text=True, env=child_env(), timeout=120)
                    self.assertEqual(73, stopped.returncode, stopped.stdout + stopped.stderr)
                    observed = self.recover_fresh(project)
                    self.assertNotIn(observed['recovery']['status'], ('CONFIGURED', 'ACTIVE'))
                    self.assertEqual('COMMITTED' if boundary == 'commit_cleanup' else 'ROLLED_BACK',
                                     observed['recovery']['status'])
                    self.assertEqual(self.pin if boundary == 'commit_cleanup' else
                                     hashlib.sha256((self.source / 'MANIFEST.json').read_bytes()).hexdigest(),
                                     observed['integrity'])
                    self.assertEqual('NO_PENDING_INSTALL', self.recover_fresh(project)['recovery']['status'])
                    if boundary == 'partial_stage':
                        self.assertTrue(list((project / '_to_delete').iterdir()))

    def recover_refused(self, project):
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from agentic.installer import recover
try:
    result = recover(sys.argv[2])
except Exception as error:
    print(json.dumps({'status': 'REFUSED', 'diagnostic': str(error)}))
    sys.exit(19)
print(json.dumps(result))
"""
        result = subprocess.run([sys.executable, '-B', '-c', code,
                                 str(ROOT / '.agentic/lib'), str(project)],
                                capture_output=True, text=True, env=child_env(), timeout=120)
        self.assertEqual(19, result.returncode, result.stdout + result.stderr)
        observation = json.loads(result.stdout)
        self.assertEqual('REFUSED', observation['status'])
        self.assertTrue(observation['diagnostic'])
        return observation

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
