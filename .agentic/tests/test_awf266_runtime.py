"""Standalone runtime lock, fresh-stage construction and both publication sites."""
from contextlib import ExitStack
import json
import shutil
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import adoption_config as adoption, installer, safeio, ValidationError
from source_only import skip_unless_source_repo


@unittest.skipUnless(os.name == 'nt', 'WIN32_83_TEMP: real Windows short-name aliases required')
class ShortTempPathTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        self.temporary = tempfile.TemporaryDirectory(prefix='awf266-long-temp-directory-')
        self.addCleanup(self.temporary.cleanup)
        self.long = Path(self.temporary.name).resolve()
        get_short = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        get_short.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short(str(self.long), buffer, len(buffer))
        self.assertTrue(0 < length < len(buffer), 'Real 8.3 TEMP alias must be available on Windows')
        self.short = Path(buffer.value)
        self.assertNotEqual(self.long, self.short, 'Windows short-path coverage must exercise a real alias')
        self.assertTrue(os.path.samefile(self.long, self.short))
        self.environment = patch.dict(os.environ, TEMP=str(self.short), TMP=str(self.short))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.tempdir = patch.object(tempfile, 'tempdir', str(self.short))
        self.tempdir.start()
        self.addCleanup(self.tempdir.stop)

    def test_cleanup_evidence_accepts_both_alias_directions(self):
        for tree_type in (safeio.Tree, installer.Tree):
            for root, child_root in ((self.short, self.long), (self.long, self.short)):
                with self.subTest(tree_type=tree_type, root=root), tempfile.TemporaryDirectory() as temporary:
                    # The retained endpoint's spelling deliberately differs from Tree.root.
                    name = Path(temporary).name
                    (child_root / name / 'retained').mkdir()
                    options = {'mutation_authority': 'transaction'} if tree_type is safeio.Tree else {}
                    with tree_type(root / name, **options) as tree:
                        rows = installer._record_cleanup_pending(tree, [child_root / name / 'retained'],
                            status='RECOVERY_REFUSED')
                    self.assertEqual(['retained'], [row['path'] for row in rows])

    def test_overlapping_install_aliases_refuse_before_source_access(self):
        for source, destination in ((self.short, self.long / 'child'),
                                    (self.long, self.short / 'child')):
            with self.subTest(source=source), patch.object(installer, 'verify_release') as verify:
                with self.assertRaisesRegex(ValidationError, 'must not overlap'):
                    installer.install(source, destination, 'a' * 64, dry_run=True)
                verify.assert_not_called()

    def test_runtime_stage_and_backup_accept_both_alias_directions(self):
        for root, child_root in ((self.short, self.long), (self.long, self.short)):
            with self.subTest(root=root), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                name = Path(temporary).name
                project = root / name
                (project / '.agentic/scripts').mkdir(parents=True)
                (project / '.agentic/scripts/workflow.py').write_bytes(b'# fixture\n')
                stage = child_root / name / '.agentic/.venv.staging-fixture'
                backup = child_root / name / '.agentic/.venv.backup-fixture'
                (project / '.agentic/.venv').mkdir()
                (project / '.agentic/.venv/previous').write_bytes(b'previous')
                def create(builder, path):
                    interpreter = adoption._runtime_interpreter(Path(path))
                    interpreter.parent.mkdir(exist_ok=True)
                    interpreter.write_bytes(b'fixture interpreter')
                dependencies = {key: {'name': key, 'version': 'fixture'} for key in adoption._RUNTIME_IMPORTS}
                stack.enter_context(patch.object(adoption, '_locked_dependencies', return_value=(dependencies, 'fixture')))
                stack.enter_context(patch.object(adoption, '_load_locked_wheels', return_value={}))
                stack.enter_context(patch.object(adoption, '_transaction_sibling',
                    side_effect=lambda runtime, kind: stage if kind == 'staging' else backup))
                stack.enter_context(patch.object(adoption.venv.EnvBuilder, 'create', create))
                stack.enter_context(patch.object(adoption, '_runtime_site_packages',
                    side_effect=lambda exe, project, runtime: Path(runtime) / 'site-packages'))
                stack.enter_context(patch.object(adoption, '_extract_locked_wheels', return_value={}))
                stack.enter_context(patch.object(adoption, '_validate_runtime'))
                result = adoption.ensure_installed_runtime(project)
                self.assertEqual('COMMITTED_CLEANUP_PENDING', result['transaction_cleanup'])
                self.assertEqual(b'previous', (backup / 'previous').read_bytes())
                self.assertTrue(adoption._runtime_interpreter(project / '.agentic/.venv').is_file())


class RuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / '.agentic/scripts').mkdir(parents=True)
        (self.root / '.agentic/scripts/workflow.py').write_bytes(b'# fixture entry point\n')
        tx = uuid.uuid4().hex
        self.stage = self.root / ('.agentic/.venv.staging-' + tx)
        self.backup = self.root / ('.agentic/.venv.backup-' + tx)
        self.canonical = self.root / '.agentic/.venv'

    def inputs(self, stack):
        dependencies = {name: {'name': name, 'version': 'fixture'} for name in adoption._RUNTIME_IMPORTS}
        stack.enter_context(patch.object(adoption, '_locked_dependencies', return_value=(dependencies, 'fixture')))
        stack.enter_context(patch.object(adoption, '_load_locked_wheels', return_value={}))
        stack.enter_context(patch.object(adoption, '_transaction_sibling', side_effect=lambda root, kind: self.stage if kind == 'staging' else self.backup))

    def test_runtime_stage_outside_root_is_refused_before_creation(self):
        with tempfile.TemporaryDirectory() as outside, ExitStack() as stack:
            self.stage = Path(outside) / 'foreign-stage'
            self.inputs(stack)
            create = stack.enter_context(patch.object(adoption.venv.EnvBuilder, 'create'))
            with self.assertRaises((ValidationError, ValueError)):
                adoption.ensure_installed_runtime(self.root)
            create.assert_not_called()
            self.assertFalse(self.stage.exists())

    def test_cleanup_evidence_outside_root_is_refused(self):
        with tempfile.TemporaryDirectory() as outside, installer.Tree(self.root) as tree:
            with self.assertRaises((ValidationError, ValueError)):
                installer._record_cleanup_pending(tree, [Path(outside)], status='RECOVERY_REFUSED')
            self.assertFalse((self.root / installer.RETAINED_JOURNAL).exists())

    def test_configuration_creation_never_overwrites_injected_owner_hardlink(self):
        # pre-allow/reproduce_builder.py, injected at the real creating seam.
        owner = self.root / 'owner'
        owner.write_bytes(b'owner original content')
        original = adoption.venv.EnvBuilder.ensure_directories
        hits = []
        def directories(builder, stage):
            context = original(builder, stage)
            os.link(owner, Path(stage) / 'pyvenv.cfg')
            hits.append(Path(stage))
            return context
        with ExitStack() as stack:
            self.inputs(stack)
            stack.enter_context(patch.object(adoption.venv.EnvBuilder, 'ensure_directories', directories))
            stack.enter_context(patch.object(adoption, '_runtime_site_packages', side_effect=ValidationError('stop after native builder')))
            with self.assertRaises((ValidationError, FileExistsError)):
                adoption.ensure_installed_runtime(self.root)
        self.assertEqual([self.stage], hits)
        self.assertEqual(b'owner original content', owner.read_bytes())
        self.assertEqual(b'owner original content', (self.stage / 'pyvenv.cfg').read_bytes())
        self.assertFalse(self.canonical.exists())

    def test_substituted_stage_is_refused_before_venv_directory_creation(self):
        native = adoption.venv.EnvBuilder.create
        hits = []
        def replace(builder, stage):
            stage = Path(stage)
            if stage.exists():
                stage.rename(stage.with_name(stage.name + '-original'))
            stage.mkdir(); (stage / 'owner').write_bytes(b'owner bytes')
            hits.append(stage)
            return native(builder, stage)
        with ExitStack() as stack:
            self.inputs(stack)
            stack.enter_context(patch.object(adoption.venv.EnvBuilder, 'create', replace))
            stack.enter_context(patch.object(adoption, '_runtime_site_packages', side_effect=ValidationError('stop after native builder')))
            with self.assertRaises(ValidationError): adoption.ensure_installed_runtime(self.root)
        self.assertEqual([self.stage], hits)
        self.assertEqual(['owner'], sorted(path.name for path in self.stage.iterdir()))
        self.assertEqual(b'owner bytes', (self.stage / 'owner').read_bytes())

    def test_both_runtime_renames_recheck_sources_and_destinations(self):
        for site in ('backup', 'stage'):
            for side in ('source', 'destination', 'normal'):
                with self.subTest(site=site, side=side), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    prior = (self.root, self.stage, self.backup, self.canonical)
                    self.root = Path(temporary)
                    (self.root / '.agentic/scripts').mkdir(parents=True)
                    (self.root / '.agentic/scripts/workflow.py').write_bytes(b'# fixture\n')
                    self.stage = self.root / prior[1].relative_to(prior[0])
                    self.backup = self.root / prior[2].relative_to(prior[0])
                    self.canonical = self.root / '.agentic/.venv'
                    if site == 'backup':
                        self.canonical.mkdir(); (self.canonical / 'previous').write_bytes(b'previous')
                    hits = []
                    def create(builder, stage):
                        stage = Path(stage); stage.mkdir(exist_ok=True)
                        interpreter = adoption._runtime_interpreter(stage)
                        interpreter.parent.mkdir(exist_ok=True)
                        interpreter.write_bytes(b'fixture interpreter')
                    def validate(interpreter, root, stage, *args):
                        if Path(stage) != self.stage or side == 'normal' or hits:
                            return
                        target = ((self.canonical if side == 'source' else self.backup) if site == 'backup'
                                  else (self.stage if side == 'source' else self.canonical))
                        if target.exists(): target.rename(target.with_name(target.name + '-original'))
                        target.mkdir(); (target / 'owner').write_bytes(b'owner')
                        hits.append(target)
                    try:
                        with ExitStack() as stack:
                            self.inputs(stack)
                            stack.enter_context(patch.object(adoption.venv.EnvBuilder, 'create', create))
                            stack.enter_context(patch.object(adoption, '_runtime_site_packages', side_effect=lambda exe, root, stage: Path(stage) / 'site-packages'))
                            stack.enter_context(patch.object(adoption, '_extract_locked_wheels', return_value={}))
                            stack.enter_context(patch.object(adoption, '_validate_runtime', validate))
                            if side == 'normal':
                                adoption.ensure_installed_runtime(self.root)
                                self.assertTrue(adoption._runtime_interpreter(self.canonical).exists())
                            else:
                                with self.assertRaises(ValidationError): adoption.ensure_installed_runtime(self.root)
                                self.assertEqual(1, len(hits))
                                self.assertEqual(b'owner', (hits[0] / 'owner').read_bytes())
                    finally:
                        self.root, self.stage, self.backup, self.canonical = prior

    def test_standalone_runtime_obeys_the_installer_exclusive_lock(self):
        with installer.Tree(self.root) as tree, installer.install_lock(tree), \
                patch.object(adoption.venv.EnvBuilder, 'create') as create:
            with self.assertRaisesRegex(ValidationError, 'Another installer'):
                adoption.ensure_installed_runtime(self.root)
            create.assert_not_called()

    def test_runtime_publication_uses_the_still_open_locked_tree(self):
        native = installer.Tree.rename_checked
        hits = []
        def publish(tree, *args, **kwargs):
            self.assertTrue(callable(getattr(tree, 'transaction_check', None)))
            self.assertIn(tree.root, tree._dirs)
            hits.append(args[:2])
            return native(tree, *args, **kwargs)
        def create(builder, stage):
            interpreter = adoption._runtime_interpreter(Path(stage))
            interpreter.parent.mkdir(exist_ok=True)
            interpreter.write_bytes(b'fixture interpreter')
        with ExitStack() as stack:
            self.inputs(stack)
            stack.enter_context(patch.object(adoption.venv.EnvBuilder, 'create', create))
            stack.enter_context(patch.object(adoption, '_runtime_site_packages',
                side_effect=lambda exe, root, stage: Path(stage) / 'site-packages'))
            stack.enter_context(patch.object(adoption, '_extract_locked_wheels', return_value={}))
            stack.enter_context(patch.object(adoption, '_validate_runtime'))
            stack.enter_context(patch.object(installer.Tree, 'rename_checked', publish))
            adoption.ensure_installed_runtime(self.root)
        self.assertEqual(1, len(hits))


class RuntimeCreationIdentityTests(unittest.TestCase):
    @skip_unless_source_repo('owner CLI is shipped in source releases', 'scripts/qualify_upgrade.py')
    def test_malformed_runtime_journal_is_refused_before_comparison(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import qualify_upgrade
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / 'runtime'
            runtime.mkdir()
            (runtime / 'member').write_bytes(b'synthetic runtime')
            digest = installer._runtime_tree_sha256(runtime)
            journal = root / 'journal.json'
            transaction = str(uuid.uuid4())
            invalid_phase = dict(format='awf-install-journal-4', transaction_id=transaction,
                phase=[], files=[], runtime=installer._runtime_journal(root, transaction),
                managed_before_sha256='0' * 64, managed_after_sha256='0' * 64)
            for malformed in ([], {}, {'format': 'awf-install-journal-4'}, invalid_phase):
                with self.subTest(journal=malformed):
                    journal.write_text(json.dumps(malformed), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        qualify_upgrade.main(['runtime-hash', '--path', str(runtime),
                            '--expected-sha256', digest, '--journal', str(journal)])

    def test_creation_update_crashes_bind_both_sides_and_refuse_replacement(self):
        class Crash(BaseException):
            pass
        for boundary in ('marker', 'journal'):
            for substitute in (False, True):
                with self.subTest(boundary=boundary, substitute=substitute), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    (root / '.agentic').mkdir()
                    journal = dict(format='awf-install-journal-4', phase='active',
                        transaction_id=str(uuid.uuid4()), files=[],
                        managed_before_sha256=installer.sha256(installer.json_bytes([])),
                        managed_after_sha256=installer.sha256(installer.json_bytes([])))
                    journal['runtime'] = installer._runtime_journal(root, journal['transaction_id'])
                    with installer.Tree(root) as tree, installer.install_lock(tree):
                        installer._publish_initial_intent(tree, journal)
                        tx = installer._RuntimeInstallTransaction(tree, journal)
                        tx.stage.mkdir()
                        native = tree.write
                        selected = False
                        def write(relative, data, **options):
                            nonlocal selected
                            result = native(relative, data, **options)
                            if relative == installer.MARKER and json.loads(data).get('pending_update_kind') == 'runtime_creation':
                                selected = True
                                if boundary == 'marker':
                                    raise Crash
                            if selected and relative == installer.JOURNAL and boundary == 'journal':
                                raise Crash
                            return result
                        with patch.object(tree, 'write', write), self.assertRaises(Crash):
                            tx.record_runtime_creation()
                        expected = journal['runtime']['stage_identity']
                        if substitute:
                            tx.stage.rename(root / 'original-stage')
                            tx.stage.mkdir()
                        current, raw, marker = installer._read_bound_journal(tree)
                        before = [(root / path).read_bytes() for path in (installer.JOURNAL, installer.MARKER)]
                        if substitute:
                            with self.assertRaises(ValidationError):
                                installer._recover_pending_journal_update(tree, current, raw, marker, publish=False)
                        else:
                            recovered = installer._recover_pending_journal_update(tree, current, raw, marker)
                            self.assertEqual(expected, recovered['runtime']['stage_identity'])
                            self.assertIsNone(recovered['runtime']['new_sha256'])
                        if substitute:
                            self.assertEqual(before, [(root / path).read_bytes()
                                for path in (installer.JOURNAL, installer.MARKER)])

    @skip_unless_source_repo('owner CLI is shipped in source releases', 'scripts/qualify_upgrade.py')
    def test_active_post_swap_journal_identifies_canonical_and_refuses_substitute(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import qualify_upgrade
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.agentic/.venv').mkdir(parents=True)
            (root / '.agentic/.venv/previous').write_bytes(b'old')
            journal = dict(format='awf-install-journal-4', phase='active',
                transaction_id=str(uuid.uuid4()), files=[],
                managed_before_sha256='a' * 64, managed_after_sha256='b' * 64)
            journal['runtime'] = installer._runtime_journal(root, journal['transaction_id'])
            with installer.Tree(root) as tree:
                tx = installer._RuntimeInstallTransaction(tree, journal)
                tx.stage.mkdir()
                # Model the durable journal seam; the published integration test
                # also exercises the real marker/digest protocol and os._exit.
                def publish(tree, current, kind):
                    (root / 'journal-copy.json').write_bytes(installer.json_bytes(current))
                with patch.object(installer, '_write_bound_journal_update', publish):
                    tx.record_runtime_creation()
                    (tx.stage / 'new').write_bytes(b'new')
                    tx.record_staged_runtime()
                tx.runtime_root.rename(tx.backup)
                tx.stage.rename(tx.runtime_root)
            recorded = json.loads((root / 'journal-copy.json').read_bytes())
            self.assertEqual('active', recorded['phase'])
            self.assertIsNotNone(recorded['runtime'].get('stage_identity'),
                                 'T88: active post-swap journal has no runtime creation identity')
            self.assertEqual(0, qualify_upgrade.main(['runtime-hash', '--path', str(tx.runtime_root),
                '--expected-sha256', recorded['runtime']['new_sha256'],
                '--journal', str(root / 'journal-copy.json')]))
            tx.runtime_root.rename(root / 'real-new')
            shutil.copytree(root / 'real-new', tx.runtime_root)
            self.assertEqual(1, qualify_upgrade.main(['runtime-hash', '--path', str(tx.runtime_root),
                '--expected-sha256', recorded['runtime']['new_sha256'],
                '--journal', str(root / 'journal-copy.json')]))

    def test_marker_without_journal_recover_refuses_without_payload_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.agentic').mkdir()
            marker = root / installer.MARKER
            marker.write_bytes(b'owner-preserved orphan marker\n')
            owner = root / 'owner'
            owner.write_bytes(b'owner bytes')
            with self.assertRaisesRegex(ValidationError, 'marker has no journal; recovery failed closed'):
                installer.recover(root)
            self.assertEqual(b'owner-preserved orphan marker\n', marker.read_bytes())
            self.assertEqual(b'owner bytes', owner.read_bytes())
            self.assertFalse((root / installer.JOURNAL).exists())


if __name__ == '__main__':
    unittest.main()
