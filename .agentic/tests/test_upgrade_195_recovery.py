"""Fresh-process recovery from an authentic published baseline.

Most transaction tests omit runtime dependencies. The temporary-file regression
also executes canonical checks in a fixture runtime with real dependency bytes;
this is not release qualification or adopter evidence.
"""
import hashlib
import io
import json
import os
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import venv
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from agentic.installer import (CONFIG, INSTALLED, PROVENANCE, JOURNAL, MARKER,
                               RECOVERY_OBJECTS, install)
from agentic.safeio import Tree
from agentic.child_process import child_env
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import PUBLISHED_PIN, materialize_published, materialize_candidate, install_baseline
from agentic.preservation_metadata import inventory


# Terminate the actual installer, bypassing finally/rollback, at deterministic
# points inside its real Tree.write implementation. No polling or sleep race.
TEMPORARY_CRASH = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agentic.installer as installer
import agentic.safeio as safeio
from unittest.mock import patch
boundary, stage, action = sys.argv[5:8]
original = safeio.Tree.write
def chosen(relative, data):
    if boundary == 'object': return relative.startswith(installer.RECOVERY_OBJECTS + '/')
    if boundary == 'backup': return relative.startswith('.agentic-backup/')
    if boundary == 'journal_update':
        return relative == installer.JOURNAL and json.loads(data)['phase'] == 'active'
    if boundary in ('marker_pending', 'marker_stable'):
        if relative != installer.MARKER: return False
        marker = json.loads(data)
        if boundary == 'marker_pending': return marker['pending_update_kind'] == 'activate'
        current_path = Path(sys.argv[3]) / installer.MARKER
        return (current_path.exists() and marker['pending_journal_sha256'] is None and
            json.loads(current_path.read_bytes())['pending_update_kind'] == 'activate')
    return relative == boundary
def write(tree, relative, data, **kwargs):
    if not chosen(relative, data): return original(tree, relative, data, **kwargs)
    fdopen, fsync, replace = os.fdopen, os.fsync, os.replace
    class Partial:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def write(self, raw):
            self.stream.write(raw[:max(1, len(raw) // 2)])
            self.stream.flush()
            os._exit(73)
    def opened(fd, *args, **kwargs):
        if 'w' not in (args[0] if args else kwargs.get('mode', 'r')):
            return fdopen(fd, *args, **kwargs)
        if stage == 'created': os._exit(73)
        stream = fdopen(fd, *args, **kwargs)
        return Partial(stream) if stage == 'partial' else stream
    def synced(fd):
        result = fsync(fd)
        if stage == 'flushed': os._exit(73)
        return result
    def replacing(*args, **kwargs):
        if stage == 'metadata': raise AssertionError('Windows metadata hook was not reached')
        if stage == 'replaced': replace(*args, **kwargs)
        os._exit(73)
    with patch.object(os, 'fdopen', opened), patch.object(os, 'fsync', synced), \
            patch.object(os, 'replace', replacing):
        if stage == 'metadata' and os.name == 'nt':
            apply = safeio._apply_windows_metadata
            def applied(*args):
                apply(*args)
                os._exit(73)
            with patch.object(safeio, '_apply_windows_metadata', applied):
                return original(tree, relative, data, **kwargs)
        return original(tree, relative, data, **kwargs)
with patch.object(safeio.Tree, 'write', write):
    if action == 'recover': installer.recover(sys.argv[3])
    else: installer.install(sys.argv[2], sys.argv[3], sys.argv[4], mode='upgrade',
        conflict='backup', configure=True, discover=False)
"""


class CreatedDirectorySafetyTests(unittest.TestCase):
    def test_temporary_names_bind_scheme_target_and_transaction_without_claiming_legacy_files(self):
        from agentic import ValidationError
        from agentic.installer import (_transaction_temporary_paths, _bind_transaction_temporaries,
                                       _managed_file_inventory)
        target = '.agentic/target.py'
        journal = {'format': 'awf-install-journal-4', 'transaction_id': str(uuid.uuid4()),
                   'files': [{'path': target, 'old_sha256': None}]}
        self.assertEqual({}, _transaction_temporary_paths(journal))
        journal['temporary_files'] = 'sibling-uuid5-v1'
        paths = _transaction_temporary_paths(journal)
        expected = uuid.uuid5(uuid.UUID(journal['transaction_id']), target).hex
        self.assertEqual('.agentic/.awf-' + expected + '.tmp', paths[target])
        self.assertEqual({target, JOURNAL, MARKER}, set(paths))
        other = dict(journal, transaction_id=str(uuid.uuid4()))
        self.assertNotEqual(paths, _transaction_temporary_paths(other))
        for invalid in ('unknown', {target: '.agentic/.awf-' + 'f' * 32 + '.tmp'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                _transaction_temporary_paths(dict(journal, temporary_files=invalid))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / paths[target]
            existing.parent.mkdir()
            existing.write_bytes(b'synthetic preexisting adopter file\n')
            with Tree(root) as tree:
                with self.assertRaisesRegex(ValidationError, 'already exists'):
                    _bind_transaction_temporaries(tree, journal, initial=True)
                self.assertEqual(b'synthetic preexisting adopter file\n', existing.read_bytes())
                legacy = dict(journal)
                del legacy['temporary_files']
                _bind_transaction_temporaries(tree, legacy)
                self.assertEqual([paths[target]], [row['path'] for row in _managed_file_inventory(tree)])

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
            if os.name == 'nt':
                _cleanup_transaction_directories(tree, {'created_directories': [row]})
                _cleanup_transaction_directories(tree, {'created_directories': [row]})
                self.assertFalse((root / 'created').exists())
            else:
                # A path lookup is not creating-handle proof on POSIX. Keep the
                # refusal assertions above and exercise repeated refusal here.
                for _ in range(2):
                    with self.assertRaisesRegex(ValidationError, 'cannot prove'):
                        _cleanup_transaction_directories(tree, {'created_directories': [row]})
                self.assertTrue((root / 'created').is_dir())
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

    def test_atomic_write_temporary_recovers_in_fresh_bootstrap(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            shutil.copytree(self.baseline, project)
            self.provision_fixture_runtime(project)
            from agentic.installer import _runtime_tree_sha256
            runtime_before = _runtime_tree_sha256(project / '.agentic/.venv')
            before = inventory(project)
            code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentic.installer import install
original = os.replace
def interrupt(source, destination, *args, **kwargs):
    if Path(destination).name == 'operating_status.py':
        os._exit(73)
    return original(source, destination, *args, **kwargs)
os.replace = interrupt
install(sys.argv[2], sys.argv[3], sys.argv[4], mode='upgrade', configure=True, discover=False)
"""
            stopped = subprocess.run([sys.executable, '-B', '-c', code,
                str(ROOT / '.agentic/lib'), str(self.target), str(project), self.pin],
                capture_output=True, text=True, env=child_env(), timeout=120)
            self.assertEqual(73, stopped.returncode, stopped.stdout + stopped.stderr)
            relative = '.agentic/lib/agentic/operating.py'
            self.assertEqual((self.target / relative).read_bytes(), (project / relative).read_bytes())
            self.assertTrue((project / MARKER).is_file())
            self.assertTrue((project / JOURNAL).is_file())
            self.assertEqual(1, len(list(project.rglob('.awf-*.tmp'))))
            from agentic.installer import _transaction_temporary_paths
            journal, prepared = self.recovery_inventory_evidence(project)
            recorded = _transaction_temporary_paths(journal)
            self.assertEqual([project / recorded['.agentic/lib/agentic/operating_status.py']],
                             list(project.rglob('.awf-*.tmp')))
            recovered = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/bootstrap_project.py'),
                '--dest', str(project), '--recover'], capture_output=True, text=True,
                env=child_env(), timeout=120)
            self.assertEqual(0, recovered.returncode, recovered.stdout + recovered.stderr)
            self.assertEqual('ROLLED_BACK', json.loads(recovered.stdout)['status'])
            self.assert_recovered_inventory(project, before, json.loads(recovered.stdout),
                                            journal, prepared)
            self.assertEqual(runtime_before, _runtime_tree_sha256(project / '.agentic/.venv'))
            self.assertFalse(list(project.rglob('.awf-*.tmp')))
            self.assertEqual('NO_PENDING_INSTALL', self.recover_cli(project)['status'])
            self.assert_canonical_restored(project)

    def test_temporary_creation_write_and_coordination_boundaries(self):
        cases = [(boundary, 'replace') for boundary in (
            JOURNAL, MARKER, 'object', 'backup', 'journal_update',
            'marker_pending', 'marker_stable', '.agentic/lib/agentic/operating_status.py')]
        cases += [('.agentic/lib/agentic/operating_status.py', stage)
                  for stage in ('created', 'partial', 'flushed')]
        if os.name == 'nt':
            cases.append(('.agentic/lib/agentic/operating_status.py', 'metadata'))
        for boundary, stage in cases:
            with self.subTest(boundary=boundary, stage=stage), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / 'project'
                shutil.copytree(self.baseline, project)
                before = inventory(project)
                sentinel = Path(temporary) / 'outside.txt'
                sentinel.write_bytes(b'synthetic outside sentinel\n')
                self.interrupt_temporary(project, boundary, stage)
                leftovers = list(project.rglob('.awf-*.tmp'))
                self.assertEqual(1, len(leftovers))
                expected = deepcopy(before)
                if boundary == JOURNAL:
                    relative = leftovers[0].relative_to(project).as_posix()
                    expected['files'][relative] = inventory(project)['files'][relative]
                journal, prepared = self.recovery_inventory_evidence(project)
                recovered = self.recover_cli(project)
                self.assertEqual('NO_PENDING_INSTALL' if boundary == JOURNAL else 'ROLLED_BACK',
                                 recovered['status'])
                self.assert_recovered_inventory(project, expected, recovered, journal, prepared)
                self.assertEqual(b'synthetic outside sentinel\n', sentinel.read_bytes())
                self.assertEqual('NO_PENDING_INSTALL', self.recover_cli(project)['status'])
                # An interrupted first journal publication leaves only unbound
                # diagnostic residue outside managed paths; recovery cannot claim it.
                if boundary == JOURNAL:
                    self.assertEqual(leftovers, list(project.rglob('.awf-*.tmp')))
                    self.assertTrue(leftovers[0].is_relative_to(project / '.agentic-install'))
                else:
                    self.assertFalse(list(project.rglob('.awf-*.tmp')))

    def test_unrecorded_lookalike_and_unknown_extra_refuse_without_mutation(self):
        for name in ('.awf-' + 'f' * 32 + '.tmp', 'owner-note.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / 'project'
                shutil.copytree(self.baseline, project)
                self.interrupt_temporary(project, '.agentic/lib/agentic/operating_status.py')
                extra = project / '.agentic/lib/agentic' / name
                self.assertFalse(extra.exists())
                extra.write_bytes(b'synthetic adopter bytes\n')
                before = inventory(project)
                for _ in range(2):
                    self.recover_cli(project, expected=2)
                    self.assertEqual(before, inventory(project))
                    self.assertEqual(b'synthetic adopter bytes\n', extra.read_bytes())
                    self.assertTrue((project / MARKER).is_file())

    def test_recovery_temporary_interruption_is_repeatable(self):
        for stage in ('created', 'partial', 'flushed', 'replace', 'replaced'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / 'project'
                shutil.copytree(self.baseline, project)
                before = inventory(project)
                self.interrupt_temporary(project, '.agentic/lib/agentic/operating_status.py')
                self.interrupt_temporary(project, '.agentic/lib/agentic/operating.py', stage, 'recover')
                self.assertEqual(0 if stage == 'replaced' else 1,
                                 len(list(project.rglob('.awf-*.tmp'))))
                journal, prepared = self.recovery_inventory_evidence(project)
                recovered = self.recover_cli(project)
                self.assertEqual('ROLLED_BACK', recovered['status'])
                self.assert_recovered_inventory(project, before, recovered, journal, prepared)
                self.assertFalse(list(project.rglob('.awf-*.tmp')))
                self.assertEqual('NO_PENDING_INSTALL', self.recover_cli(project)['status'])

    def recovery_inventory_evidence(self, project):
        path = project / JOURNAL
        journal = json.loads(path.read_bytes()) if path.exists() else {}
        # Capture metadata before recovery, independently of the final inventory.
        return journal, inventory(project)['directories']

    def assert_recovered_inventory(self, project, before, recovery, journal, prepared):
        after = inventory(project)
        reported = recovery.get('retained_directories', [])
        self.assertIsInstance(reported, list)
        self.assertEqual(len(reported), len(set(reported)), 'duplicate retained paths')
        retained = {path + '/' for path in reported}
        extra = set(after['directories']) - set(before['directories'])
        self.assertEqual(retained, extra, 'directory residue must exactly match recovery reporting')
        unproved = {row['path'] + '/' for row in journal.get('created_directories', [])
                    if row['identity'] is None}
        self.assertLessEqual(retained, unproved, 'retention requires recorded unproved creation')
        expected = deepcopy(before)
        for relative in retained:
            directory = project / relative
            # Retained parents can contain retained children. Leaves are empty;
            # every other entry must itself be a reported, unproved directory.
            members = list(directory.iterdir())
            children = {child.relative_to(project).as_posix() + '/' for child in members}
            self.assertTrue(all(child.is_dir() and not child.is_symlink() for child in members))
            self.assertLessEqual(children, retained, 'retained directory contains an unreported entry')
            expected_children = {path for path in retained
                                 if (project / path).parent == directory}
            self.assertEqual(expected_children, children)
            if not expected_children:
                self.assertEqual([], members, 'retained leaf must be empty')
            expected['directories'][relative] = prepared[relative]
        # With no reported retention this is the original exact assertion,
        # including the Windows creating-handle case. No files or metadata differ.
        if not reported:
            self.assertEqual(set(), extra)
            self.assertEqual(before, after)
        self.assertEqual(expected, after)

    def interrupt_temporary(self, project, boundary, stage='replace', action='install'):
        stopped = subprocess.run([sys.executable, '-B', '-c', TEMPORARY_CRASH,
            str(ROOT / '.agentic/lib'), str(self.target), str(project), self.pin,
            boundary, stage, action], capture_output=True, text=True, env=child_env(), timeout=120)
        self.assertEqual(73, stopped.returncode, stopped.stdout + stopped.stderr)

    def recover_cli(self, project, expected=0):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/bootstrap_project.py'),
            '--dest', str(project), '--recover'], capture_output=True, text=True,
            env=child_env(), timeout=120)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        if expected:
            self.assertIn('Managed-file inventory changed', result.stderr)
            return
        return json.loads(result.stdout)

    def provision_fixture_runtime(self, project):
        from test_bootstrap_configuration import installed_wheel
        runtime = project / '.agentic/.venv'
        venv.EnvBuilder(with_pip=False).create(runtime)
        packages = runtime / ('Lib/site-packages' if os.name == 'nt' else
                              f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
        for name in ('PyYAML', 'jsonschema', 'attrs', 'jsonschema-specifications',
                     'referencing', 'rpds-py', 'typing-extensions', 'tzdata'):
            _version, raw = installed_wheel(name)
            with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
                wheel.extractall(packages)

    def assert_canonical_restored(self, project):
        python = project / '.agentic/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        for command in ('verify-installation', 'validate-config'):
            checked = subprocess.run([str(python), '-B', '-I', str(project / '.agentic/scripts/workflow.py'),
                '--root', str(project), command], capture_output=True, text=True,
                env=child_env(), timeout=60)
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
            report = json.loads(checked.stdout)
            if command == 'verify-installation':
                self.assertTrue(report['integrity_valid'])
                self.assertEqual(PUBLISHED_PIN, report['source_manifest_sha256'])
            else:
                self.assertEqual('ACCEPTED', report['status'])

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
                    journal, prepared = self.recovery_inventory_evidence(project)
                    observed = self.recover_fresh(project)
                    self.assertNotIn(observed['recovery']['status'], ('CONFIGURED', 'ACTIVE'))
                    self.assertEqual('NO_PENDING_INSTALL', self.recover_fresh(project)['recovery']['status'])
                    if boundary != 'commit_cleanup':
                        self.assertEqual('ROLLED_BACK', observed['recovery']['status'])
                        self.assert_recovered_inventory(project, before, observed['recovery'],
                                                        journal, prepared)
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
