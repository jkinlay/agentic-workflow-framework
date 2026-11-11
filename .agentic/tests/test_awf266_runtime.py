"""Standalone runtime lock, fresh-stage construction and both publication sites."""
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import adoption_config as adoption, installer, ValidationError


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


if __name__ == '__main__':
    unittest.main()
