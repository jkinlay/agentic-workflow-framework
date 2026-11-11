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
import stat
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
                               RECOVERY_OBJECTS, RETAINED_JOURNAL, DIRECTORY_ADOPTIONS, install, json_bytes)
from agentic.safeio import Tree
from agentic.child_process import child_env
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import PUBLISHED_PIN, materialize_published, materialize_candidate, install_baseline
from agentic.preservation_metadata import inventory


from rollback_refusal_expectations import (REFUSAL, inventory as refusal_inventory,
                                           cleanup_record, assert_retained)
from agentic import installer


def repeated_recovery(result):
    return result if result.get('retained_directories') else {'status': 'NO_PENDING_INSTALL'}


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
        self.assertEqual({target, JOURNAL, MARKER, RETAINED_JOURNAL}, set(paths))
        adopting = dict(journal, directory_adoptions=[{'path': '.agentic'}])
        self.assertEqual({target, JOURNAL, MARKER, RETAINED_JOURNAL, DIRECTORY_ADOPTIONS},
                         set(_transaction_temporary_paths(adopting)))
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
    def test_conditional_runtime_recovery_and_diagnostic_retirement_are_consistent(self):
        required = (
            'No backup/canonical `.agentic/.venv`=previous hash: quarantine stage only.',
            'No retirement CLI;',
            'move only resolved `retained-journal.json` externally',
            'Legacy `.agentic-backup/<txid>`',
            'runtime-hash --path ENDPOINT --expected-sha256 DIGEST',
            'verified journal-writing release',
            'never automatically adopted',
            'authenticated restoration/quarantine or deliberate retention',
            'Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE.',
            '`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`)',
            '`.agentic/scripts/workflow.py`',
            'Existing stage/null new_sha256: unauthenticated; retain diagnostics.',
            'Quarantine needs independent owner-verified creation evidence; otherwise stop.',
            'file-hash --path FILE --expected-sha256 DIGEST',
            'unrecorded/unprovable metadata stops',
            'owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership',
            "owner stops/checks every host's installers/agents/editors/schedulers/processes/leases",
            'stop for 1=mismatch or 2=malformed/refused',
            'Fresh-install rollback: confirm runtime/workflow/receipt absent;',
            'archive evidence/report uninstalled; checks unavailable.',
            'Retry requires recovery/resolution/owner authorization.',
        )
        procedures = []
        for relative in ('MIGRATION-v1.9.5-to-v1.9.6.md',
                         '.agentic/docs/38-RELEASE-196-QUALIFICATION.md',
                         'docs/runbook/19-runbook-journal-recovery.md'):
            with self.subTest(document=relative):
                text = (ROOT / relative).read_text(encoding='utf-8')
                for clause in required:
                    self.assertIn(clause, text)
                procedures.append(text[text.index('1. No exclusivity-proof CLI:'):].split('\n\n', 1)[0])
        self.assertEqual([procedures[0]] * 3, procedures)


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

    def test_documented_staged_owner_recovery_runs_steps_one_through_eight(self):
        from agentic import ValidationError
        from agentic.preservation_metadata import runtime_tree_sha256
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / 'staged-project'
            shutil.copytree(self.baseline, project)
            # Step 1: a sole test writer owns this disposable project.
            self.provision_fixture_runtime(project)
            previous = runtime_tree_sha256(project / installer.RUNTIME)
            originals = {path: (project / path).read_bytes() for path in (INSTALLED, PROVENANCE, CONFIG)}
            result = install(self.target, project, self.pin, mode='upgrade', configure=True,
                             discover=False, defer_runtime=True)
            def builder(transaction):
                venv.EnvBuilder(with_pip=False).create(transaction.stage)
                transaction.record_staged_runtime()
                raise PermissionError('Simulated in-use canonical runtime: swap never started')
            with self.assertRaises(ValidationError) as failure:
                installer.complete_runtime_transaction(project, result['_runtime_transaction_id'], builder)
            self.assertEqual(
                'Bootstrap runtime failed and exact managed/runtime rollback cannot be proved; transaction '
                + result['_runtime_transaction_id'] + ' and backup .agentic/.venv.backup-'
                + result['_runtime_transaction_id'] + ' were retained', str(failure.exception))
            self.assertEqual(REFUSAL, str(failure.exception.__cause__))
            journal = json.loads((project / JOURNAL).read_bytes())
            runtime = journal['runtime']
            self.assertFalse((project / runtime['backup']).exists())
            self.assertEqual(previous, runtime['previous_sha256'])
            preserved = refusal_inventory(project)
            archive = base / 'owner-archive'
            shutil.copytree(project, archive / 'refused-project', symlinks=True)
            # Steps 2-3: public command authenticates both endpoints; keep GOOD canonical.
            for endpoint, digest in ((runtime['path'], previous),
                                     (runtime['stage'], runtime['new_sha256'])):
                checked = subprocess.run([sys.executable, '-B', str(self.target / 'scripts/qualify_upgrade.py'),
                    'runtime-hash', '--path', str(project / endpoint), '--expected-sha256', digest],
                    capture_output=True, text=True, env=child_env(), timeout=60)
                self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
                self.assertEqual(dict(format='awf-runtime-authentication-1', sha256=digest,
                    expected_sha256=digest, matches=True, execution_authority=False), json.loads(checked.stdout))
            self.assertEqual(preserved, refusal_inventory(project))
            stage = project / runtime['stage']
            stage.rename(archive / 'quarantined-stage')
            self.assertEqual(previous, runtime_tree_sha256(project / installer.RUNTIME))
            self.assertFalse((project / runtime['backup']).exists())
            # Step 4 uses the candidate release that wrote this journal, externally.
            recover_command = [sys.executable, '-B', str(self.target / 'scripts/bootstrap_project.py'),
                               '--dest', str(project), '--recover']
            recovered = subprocess.run(recover_command, capture_output=True, text=True,
                                       env=child_env(), timeout=120)
            self.assertEqual(0, recovered.returncode, recovered.stdout + recovered.stderr)
            report = json.loads(recovered.stdout)
            self.assertEqual('ROLLED_BACK', report['status'])
            self.assertFalse((project / JOURNAL).exists())
            self.assertFalse((project / MARKER).exists())
            for path, raw in originals.items(): self.assertEqual(raw, (project / path).read_bytes())
            self.assertEqual(previous, runtime_tree_sha256(project / installer.RUNTIME))
            # Step 5 is conditional: this compact journal has no legacy refusal.
            self.assertIn('created_directories', journal)
            # Steps 6-7: resolve every inventory row by authenticated quarantine or
            # deliberate retention, archive all references, then move ONLY the index.
            status = installer.retained_directory_status(project)
            self.assertIn(runtime['stage'], status['retained_directories'])
            resolutions = {path: ('authenticated stage quarantined' if path == runtime['stage'] else
                                  'deliberately retained directory') for path in status['retained_directories']}
            self.assertEqual(set(status['retained_directories']), set(resolutions))
            shutil.copytree(project / '.agentic-install', archive / 'resolved-install-evidence')
            (archive / 'resolutions.json').write_bytes(json_bytes(resolutions))
            (project / RETAINED_JOURNAL).rename(archive / 'resolved-retained-journal.json')
            self.assertEqual(previous, runtime_tree_sha256(project / installer.RUNTIME))
            repeated = subprocess.run(recover_command, capture_output=True, text=True,
                                      env=child_env(), timeout=120)
            self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
            self.assertEqual({'status': 'NO_PENDING_INSTALL'}, json.loads(repeated.stdout))
            # Step 8 actually runs all three commands through restored 1.9.5 tooling.
            self.assert_canonical_restored(project)
            python = project / installer.RUNTIME / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            observed = subprocess.run([str(python), '-B', '-I', str(project / '.agentic/scripts/workflow.py'),
                '--root', str(project), 'status'], capture_output=True, text=True, env=child_env(), timeout=60)
            self.assertEqual(0, observed.returncode, observed.stdout + observed.stderr)
            self.assertIn('CONFIGURED', observed.stdout)

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
            self.assertEqual(repeated_recovery(json.loads(recovered.stdout)), self.recover_cli(project))
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
                if boundary == 'backup':
                    rows = [row for row in journal['files']
                            if row['path'].startswith('.agentic-backup/')]
                    self.assertTrue(rows)
                    self.assertTrue(all(row['new_mode'] is None for row in rows))
                    # An early backup interruption has no recorded creation mode.
                    # Step 5 must STOP; it cannot infer metadata from the live file.
                    preserved = refusal_inventory(project)
                    with self.assertRaisesRegex(AssertionError, 'absent|unrecorded'):
                        self.authenticate_backup_rows(project, journal, {})
                    self.assertEqual(preserved, refusal_inventory(project))
                recovered = self.recover_cli(project)
                self.assertEqual('NO_PENDING_INSTALL' if boundary == JOURNAL else 'ROLLED_BACK',
                                 recovered['status'])
                self.assert_recovered_inventory(project, expected, recovered, journal, prepared)
                self.assertEqual(b'synthetic outside sentinel\n', sentinel.read_bytes())
                self.assertEqual(repeated_recovery(recovered), self.recover_cli(project))
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
                self.assertEqual(repeated_recovery(recovered), self.recover_cli(project))

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
        if reported:
            # The retained journal is an exact declared recovery output, never
            # a prefix waiver. Directory residue must still equal R exactly.
            terminal = deepcopy(journal)
            if terminal['phase'] == 'active':
                terminal['phase'] = 'rollback_cleanup'
            raw = json_bytes(dict(format='awf-retained-journal-1', journal=terminal,
                                  retained_directories=reported))
            self.assertEqual(raw, (project / RETAINED_JOURNAL).read_bytes())
            probe = '.agentic-install/diagnostic-metadata-probe'
            with Tree(project) as tree:
                tree.write(probe, raw)
                try:
                    expected['files'][RETAINED_JOURNAL] = inventory(project)['files'][probe]
                finally:
                    tree.unlink(probe)
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
                    self.assertEqual(repeated_recovery(observed['recovery']),
                                     self.recover_fresh(project)['recovery'])
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

    def test_real_venv_staging_swap_and_commit_preserve_endpoints_after_process_exit(self):
        # Real stdlib stages intentionally omit dependencies. On Windows the
        # previous runtime has real fixture dependencies for owner step 8;
        # these disposable checks are not release qualification.
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            baseline = base / 'baseline'
            shutil.copytree(self.baseline, baseline)
            if os.name == 'nt':
                self.provision_fixture_runtime(baseline)
            else:
                venv.EnvBuilder(with_pip=False).create(baseline / '.agentic/.venv')
            for boundary in ('before_stage', 'partial_stage', 'staged', 'backup_swap',
                             'runtime_swap', 'commit_cleanup'):
                with self.subTest(boundary=boundary):
                    project = base / boundary
                    shutil.copytree(baseline, project)
                    originals = {path: (project / path).read_bytes()
                                 for path in (INSTALLED, PROVENANCE, CONFIG, 'PROJECT_INSTRUCTIONS.md')}
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
                    before = refusal_inventory(project)
                    journal = json.loads((project / JOURNAL).read_bytes())
                    runtime = journal['runtime']
                    if boundary in ('staged', 'backup_swap', 'runtime_swap'):
                        blocked = [runtime['path'] if boundary == 'runtime_swap' else runtime['stage']]
                        self.assertEqual(REFUSAL, self.recover_refused(project, forbid_mutation=True)['diagnostic'])
                        expected = assert_retained(self, project, before, blocked, journal=journal)
                        # A repeated fresh process must refuse identically, with
                        # neither runtime nor the exact evidence changing.
                        retained_before = refusal_inventory(project)
                        self.assertEqual(REFUSAL, self.recover_refused(project, forbid_mutation=True)['diagnostic'])
                        self.assertEqual(retained_before, refusal_inventory(project))
                        if boundary == 'staged':
                            self.assertFalse((project / runtime['backup']).exists())
                        old_path = runtime['path'] if boundary == 'staged' else runtime['backup']
                        self.assertEqual(runtime['previous_sha256'],
                                         installer._runtime_tree_sha256(project / old_path))
                        new_path = runtime['path'] if boundary == 'runtime_swap' else runtime['stage']
                        self.assertEqual(runtime['new_sha256'],
                                         installer._runtime_tree_sha256(project / new_path))
                        self.assertEqual(expected, installer.retained_directory_status(project))
                        # Managed bytes are still the upgraded endpoint, including
                        # the receipt and provenance, while rollback is refused.
                        receipt = json.loads((project / INSTALLED).read_bytes())
                        provenance = json.loads((project / PROVENANCE).read_bytes())
                        self.assertEqual(self.pin, receipt['source_manifest_sha256'])
                        self.assertEqual(self.pin, provenance['installation']['source_manifest_sha256'])
                        for path, digest in receipt['immutable_files'].items():
                            self.assertEqual(digest, hashlib.sha256((project / path).read_bytes()).hexdigest())
                        from agentic import ValidationError
                        with self.assertRaises(ValidationError) as incomplete:
                            installer.verify_installed(project)
                        self.assertEqual('Installation is incomplete; use bootstrap --recover before running tools',
                                         str(incomplete.exception))
                        if os.name == 'nt' and boundary in ('backup_swap', 'runtime_swap'):
                            self.finish_swapped_owner_recovery(project, journal, originals,
                                                               base / (boundary + '-archive'))
                        # Pending owner decision: POSIX Tree.unlink currently refuses.
                        # Linux must retain the refusal checks above, then execute
                        # this same owner procedure for BOTH swap boundaries with
                        # a dependency-provisioned previous runtime, and
                        # prove ROLLED_BACK -> NO_PENDING_INSTALL -> CONFIGURED
                        # after the owner settles that policy. No path is skipped.
                        continue
                    observed = self.recover_fresh(project)
                    self.assertNotIn(observed['recovery']['status'], ('CONFIGURED', 'ACTIVE'))
                    if boundary == 'commit_cleanup':
                        record = cleanup_record(before, [runtime['backup']],
                                                status='COMMITTED_CLEANUP_PENDING', journal=journal)
                        retained = {key: value for key, value in record.items()
                                    if key not in {'format', 'journal'}}
                        # Successful recovery includes the current unresolved
                        # inventory via recover -> _with_retained_inventory.
                        expected = dict(status='COMMITTED',
                            transaction_cleanup='COMMITTED_CLEANUP_PENDING',
                            transaction_cleanup_failures=record['cleanup_pending'],
                            retained_directories=record['retained_directories'],
                            installation_recovery=retained)
                        self.assertEqual(expected, observed['recovery'])
                        self.assertEqual(json_bytes(record), (project / RETAINED_JOURNAL).read_bytes())
                        self.assertEqual(retained, self.recover_fresh(project)['recovery'])
                        for relative in (runtime['path'], runtime['backup']):
                            self.assertEqual({p: v for p, v in before.items()
                                              if p == relative or p.startswith(relative + '/')},
                                             {p: v for p, v in refusal_inventory(project).items()
                                              if p == relative or p.startswith(relative + '/')})
                        self.assertEqual(runtime['previous_sha256'],
                                         installer._runtime_tree_sha256(project / runtime['backup']))
                        self.assertFalse((project / JOURNAL).exists())
                        self.assertFalse((project / MARKER).exists())
                        from agentic.providers.github_status import project_status, render_status
                        from unittest.mock import Mock
                        status = project_status(project, gh=Mock())
                        self.assertEqual(retained, status['installation_recovery'])
                        self.assertIn('Cleanup pending: ' + runtime['backup'], render_status(status))
                    else:
                        self.assertEqual('ROLLED_BACK', observed['recovery']['status'])
                        self.assertEqual(repeated_recovery(observed['recovery']),
                                         self.recover_fresh(project)['recovery'])
                    self.assertEqual(self.pin if boundary == 'commit_cleanup' else
                                     hashlib.sha256((self.source / 'MANIFEST.json').read_bytes()).hexdigest(),
                                     observed['integrity'])
                    if boundary == 'partial_stage':
                        self.assertTrue(list((project / '_to_delete').iterdir()))

    def owner_command(self, script, *arguments):
        result = subprocess.run([sys.executable, '-B', str(self.target / 'scripts' / script),
                                 *map(str, arguments)], capture_output=True, text=True,
                                env=child_env(), timeout=60 if script == 'qualify_upgrade.py' else 120)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def finish_swapped_owner_recovery(self, project, journal, originals, archive):
        runtime = journal['runtime']
        # Steps 1-2: sole fixture writer; archive the refused endpoints, journal,
        # evidence objects and metadata before any owner-controlled rename.
        shutil.copytree(project, archive / 'refused-project', symlinks=True)
        preserved = refusal_inventory(project)
        (archive / 'preserved-inventory.json').write_bytes(json_bytes(inventory(project)))
        new_path = runtime['path'] if (project / runtime['path']).exists() else runtime['stage']
        for endpoint, digest in ((runtime['backup'], runtime['previous_sha256']),
                                 (new_path, runtime['new_sha256'])):
            checked = self.owner_command('qualify_upgrade.py', 'runtime-hash',
                '--path', project / endpoint, '--expected-sha256', digest)
            self.assertEqual(dict(format='awf-runtime-authentication-1', sha256=digest,
                expected_sha256=digest, matches=True, execution_authority=False), checked)
        self.assertEqual(preserved, refusal_inventory(project))
        # Step 3: backup is authenticated; quarantine ONLY the new endpoint,
        # preserving metadata with a same-filesystem rename, never copy/delete.
        from agentic.preservation_metadata import runtime_inventory
        new_inventory = runtime_inventory(project / new_path)
        (project / new_path).rename(archive / 'quarantined-new-runtime')
        self.assertEqual(new_inventory, runtime_inventory(archive / 'quarantined-new-runtime'))
        self.assertFalse((project / runtime['path']).exists())
        # Step 4: use the external journal-writing release, not the swapped tools.
        recovered = self.owner_command('bootstrap_project.py', '--dest', project, '--recover')
        self.assertEqual('ROLLED_BACK', recovered['status'])
        for relative in (JOURNAL, MARKER):
            self.assertFalse((project / relative).exists())
        for relative, raw in originals.items():
            self.assertEqual(raw, (project / relative).read_bytes())
        self.assertEqual(runtime['previous_sha256'], installer._runtime_tree_sha256(project / runtime['path']))
        # Steps 6-7: explicitly resolve every retained row, preserve references
        # and resolutions, and move only the resolved diagnostic index externally.
        status = installer.retained_directory_status(project)
        self.assertTrue(status['retained_directories'])
        resolutions = {relative: ('authenticated original runtime restored' if relative in (runtime['path'], runtime['backup'])
            else 'authenticated new runtime quarantined' if relative == new_path
            else 'deliberately retained directory') for relative in status['retained_directories']}
        self.assertEqual(set(status['retained_directories']), set(resolutions))
        shutil.copytree(project / '.agentic-install', archive / 'resolved-install-evidence', symlinks=True)
        (archive / 'resolutions.json').write_bytes(json_bytes(resolutions))
        (project / RETAINED_JOURNAL).rename(archive / 'resolved-retained-journal.json')
        self.assertEqual({'status': 'NO_PENDING_INSTALL'}, self.owner_command(
            'bootstrap_project.py', '--dest', project, '--recover'))
        # Step 8: all commands execute with the RESTORED isolated interpreter.
        self.assert_canonical_restored(project)
        python = project / installer.RUNTIME / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        observed = subprocess.run([str(python), '-B', '-I', str(project / '.agentic/scripts/workflow.py'),
            '--root', str(project), 'status'], capture_output=True, text=True, env=child_env(), timeout=60)
        self.assertEqual(0, observed.returncode, observed.stdout + observed.stderr)
        self.assertIn('CONFIGURED', observed.stdout)

    def authenticate_backup_rows(self, project, journal, creation):
        from agentic.preservation_metadata import _permissions, _named_streams
        rows = [row for row in journal['files'] if row['path'].startswith('.agentic-backup/')]
        self.assertTrue(rows, 'backup boundary must have journal rows')
        actual = {path.relative_to(project).as_posix()
                  for path in (project / '.agentic-backup').rglob('*') if path.is_file()}
        self.assertEqual({row['path'] for row in rows}, actual, 'extra or absent backup file stops')
        records = {}
        for row in rows:
            relative = row['path']
            self.assertIsNotNone(row['new_sha256'], 'unrecorded backup digest stops')
            self.assertIsNotNone(row['new_mode'], 'unrecorded backup mode stops')
            checked = self.owner_command('qualify_upgrade.py', 'file-hash', '--path', project / relative,
                                         '--expected-sha256', row['new_sha256'])
            self.assertTrue(checked['matches'])
            self.assertEqual(row['new_sha256'], checked['sha256'])
            self.assertEqual(row['new_mode'], checked['mode'], 'backup mode differs; stop')
            self.assertFalse(checked['execution_authority'])
            self.assertIn(relative, creation, 'unrecorded copy creation metadata stops')
            expected = creation[relative]
            self.assertEqual(expected['permissions'], checked['permissions'])
            self.assertEqual(expected['named_streams'], checked['named_streams'])
            self.assertEqual((_permissions(project / relative), _named_streams(project / relative)),
                             (checked['permissions'], checked['named_streams']))
            if os.name != 'nt':
                # Original ownership is not copy ownership. These values were
                # observed before creation while this sole writer held the tree.
                self.assertNotIn(relative, journal.get('posix_ownership', {}))
                info = (project / relative).stat()
                expected_gid = (expected['parent_gid'] if expected['parent_mode'] & stat.S_ISGID
                                else expected['effective_gid'])
                self.assertEqual((expected['effective_uid'], expected_gid), (info.st_uid, info.st_gid))
            records[relative] = checked
        self.assertEqual(set(actual), set(records))
        return records

    def test_backup_owner_authentication_requires_every_row_and_rejects_extras_and_modes(self):
        # Early 'backup' interruption (including POSIX) is covered above and
        # must stop for absent/unrecorded metadata. The fully journaled endpoint
        # below needs deletion during upgrade. Pending Tree.unlink decision:
        # Linux must run the full positive/extra/mode path once policy permits
        # it, checking copy UID=installing effective UID and BOTH GID cases:
        # setgid-parent inheritance and installing effective GID otherwise.
        # The early-boundary assertions remain executable without that decision.
        if os.name != 'nt':
            # Execute the reachable refusal path rather than reporting an
            # unexecuted positive path as a skip. Full-path Linux work is above.
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / 'backup-project'
                shutil.copytree(self.baseline, project)
                self.interrupt_temporary(project, 'backup')
                journal = json.loads((project / JOURNAL).read_bytes())
                preserved = refusal_inventory(project)
                with self.assertRaisesRegex(AssertionError, 'absent|unrecorded'):
                    self.authenticate_backup_rows(project, journal, {})
                self.assertEqual(preserved, refusal_inventory(project))
            return
        from agentic.preservation_metadata import _permissions, _named_streams
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'backup-project'
            shutil.copytree(self.baseline, project)
            creation = {}
            original = Tree.write
            def write(tree, relative, data, **kwargs):
                evidence = None
                if relative.startswith('.agentic-backup/'):
                    parent = (project / relative).parent.stat()
                    evidence = dict(parent_mode=stat.S_IMODE(parent.st_mode), parent_gid=parent.st_gid)
                    if os.name != 'nt':
                        evidence.update(effective_uid=os.geteuid(), effective_gid=os.getegid())
                result = original(tree, relative, data, **kwargs)
                if evidence is not None:
                    # Contemporaneous accepted creation baseline; this is not
                    # original-file ownership copied out of posix_ownership.
                    evidence.update(permissions=_permissions(project / relative),
                                    named_streams=_named_streams(project / relative))
                    creation[relative] = evidence
                return result
            with patch.object(Tree, 'write', write):
                install(self.target, project, self.pin, mode='upgrade', conflict='backup',
                        configure=True, discover=False, defer_runtime=True)
            journal = json.loads((project / JOURNAL).read_bytes())
            preserved = refusal_inventory(project)
            records = self.authenticate_backup_rows(project, journal, creation)
            self.assertGreater(len(records), 1)
            self.assertEqual(set(creation), set(records))
            self.assertEqual(preserved, refusal_inventory(project))
            extra = project / '.agentic-backup' / journal['transaction_id'] / 'owner-extra.txt'
            extra.write_bytes(b'synthetic owner extra; must stop quarantine')
            extra_inventory = refusal_inventory(project)
            with self.assertRaisesRegex(AssertionError, 'extra or absent'):
                self.authenticate_backup_rows(project, journal, creation)
            self.assertEqual(b'synthetic owner extra; must stop quarantine', extra.read_bytes())
            self.assertEqual(extra_inventory, refusal_inventory(project))
            extra.unlink()
            first = project / next(iter(records))
            mode = stat.S_IMODE(first.stat().st_mode)
            try:
                first.chmod(stat.S_IREAD if mode & stat.S_IWRITE else stat.S_IREAD | stat.S_IWRITE)
                self.assertNotEqual(mode, stat.S_IMODE(first.stat().st_mode))
                edited = refusal_inventory(project)
                with self.assertRaisesRegex(AssertionError, 'backup mode differs'):
                    self.authenticate_backup_rows(project, journal, creation)
                self.assertEqual(edited, refusal_inventory(project))
            finally:
                first.chmod(mode)
            self.assertEqual(preserved, refusal_inventory(project))

    def recover_refused(self, project, *, forbid_mutation=False):
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from agentic.installer import recover
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent / 'tests'))
from rollback_refusal_expectations import forbid_managed_mutation
from contextlib import nullcontext
try:
    with (forbid_managed_mutation(sys.argv[2]) if sys.argv[3] == 'guard' else nullcontext()):
        result = recover(sys.argv[2])
except Exception as error:
    print(json.dumps({'status': 'REFUSED', 'diagnostic': str(error)}))
    sys.exit(19)
print(json.dumps(result))
"""
        result = subprocess.run([sys.executable, '-B', '-c', code,
                                 str(ROOT / '.agentic/lib'), str(project),
                                 'guard' if forbid_mutation else 'plain'],
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
