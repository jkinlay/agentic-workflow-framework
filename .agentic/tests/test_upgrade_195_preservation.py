"""Authentic published baseline; synthetic owner customization is not CMC evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import shutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from source_only import AWF_SOURCE_REPOSITORY, skip_unless_source_repo
from upgrade_195_fixtures import (materialize_published, verify_published, install_baseline,
                                  materialize_candidate, customize_preservation_project)
sys.path.insert(0, str(ROOT / 'scripts'))
# The qualification oracle/CLI is source tooling; installed runtime tests still run.
if AWF_SOURCE_REPOSITORY:
    from qualify_upgrade import inventory, plan
    import qualify_upgrade
import os
from agentic import ValidationError
from agentic.installer import install
import hashlib
import sqlite3
from unittest.mock import patch


@skip_unless_source_repo('requires published Git objects', 'scripts/publish_release.py', '.git')
class AuthenticBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_published(ROOT, cls.base / 'published')
        cls.candidate = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.candidate / 'MANIFEST.json').read_bytes()).hexdigest()

    def _lib64_upgrade(self, *, simulated):
        from agentic import installer
        from contextlib import nullcontext
        # Agentic-only CI discovery does not add the scripts test root.
        sys.path.insert(0, str(ROOT / 'scripts/tests'))
        from test_qualify_upgrade import simulated_runtime_links
        project = self.base / ('lib64-upgrade-simulated' if simulated else 'lib64-upgrade-real')
        install_baseline(self.source, project)
        runtime = project / installer.RUNTIME
        (runtime / 'lib').mkdir(parents=True)
        (runtime / 'lib/original').write_bytes(b'previous runtime bytes')
        if simulated:
            (runtime / 'lib64').write_bytes(b'lib')
        else:
            (runtime / 'lib64').symlink_to('lib', target_is_directory=True)
        roots = {runtime}
        with simulated_runtime_links(roots) if simulated else nullcontext():
            before = inventory(project)
            policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
            installed = install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                                discover=False, conflict='backup', defer_runtime=True)
            backup = project / ('.agentic/.venv.backup-' + installed['_runtime_transaction_id'])
            roots.add(backup)
            def build(transaction):
                transaction.stage.mkdir()
                (transaction.stage / 'new-runtime').write_bytes(b'new runtime bytes')
                transaction.record_staged_runtime()
                os.replace(transaction.runtime_root, transaction.backup)
                os.replace(transaction.stage, transaction.runtime_root)
                return {}
            completed = installer.complete_runtime_transaction(project, installed['_runtime_transaction_id'], build)
            self.assertEqual('COMMITTED_CLEANUP_PENDING', completed['transaction_cleanup'])
            after = inventory(project)
            self.assertEqual(before['excluded_paths'], after['excluded_paths'])
            accepted = qualify_upgrade.compare_upgrade(before, after, policy, project)
            self.assertTrue(accepted['preserved'], accepted['violations'])
            self.assertEqual(set(accepted['changes']), set(accepted['approved_changes']))
            link = backup.relative_to(project).as_posix() + '/lib64'
            self.assertEqual('lib', after['files'][link]['link'])
            # A fresh accepted control precedes every mutation of the SAME tree.
            for mutation in ('bytes', 'added', 'link-target'):
                with self.subTest(mutation=mutation):
                    accepted = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                    self.assertTrue(accepted['preserved'], accepted['violations'])
                    target = backup / ('extra' if mutation == 'added' else 'lib/original')
                    if mutation == 'link-target': target = backup / 'lib64'
                    original = target.read_bytes() if mutation != 'added' and (simulated or mutation != 'link-target') else None
                    quarantined_link = None
                    try:
                        if mutation == 'link-target' and not simulated:
                            quarantined_link = self.base / 'retained-original-lib64-link'
                            self.assertFalse(quarantined_link.exists() or quarantined_link.is_symlink())
                            target.rename(quarantined_link)
                            target.symlink_to('.', target_is_directory=True)
                        else:
                            target.write_bytes(b'.' if mutation == 'link-target' else b'tampered')
                        rejected = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                        self.assertFalse(rejected['preserved'])
                        self.assertTrue(any('Retained runtime bytes, membership or metadata differ' in value
                                            for value in rejected['violations']), rejected['violations'])
                    finally:
                        if quarantined_link:
                            target.rename(self.base / 'retained-mutated-link')
                            quarantined_link.rename(target)
                        elif original is None:
                            target.rename(self.base / ('retained-extra-' + str(simulated)))
                        else:
                            target.write_bytes(original)
                    self.assertEqual(after, inventory(project))

    def test_lib64_upgrade_preserves_authenticated_backup_simulated_posix(self):
        self._lib64_upgrade(simulated=True)

    @unittest.skipUnless(os.name == 'posix',
        'Windows venvs never contain lib64; Windows reparse points are refused by design '
        '(asserted by test_qualify_upgrade Windows refusal companions)')
    def test_lib64_upgrade_preserves_authenticated_backup_real_symlink(self):
        self._lib64_upgrade(simulated=False)

    def test_retained_runtime_upgrade_has_exact_preservation_policy(self):
        from agentic import installer
        project = self.base / 'retained-runtime-upgrade'
        install_baseline(self.source, project)
        runtime = project / installer.RUNTIME
        runtime.mkdir()
        (runtime / 'previous-runtime').write_bytes(b'previous runtime bytes')
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        installed = install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                            discover=False, conflict='backup', defer_runtime=True)
        def build(transaction):
            transaction.stage.mkdir()
            (transaction.stage / 'new-runtime').write_bytes(b'new runtime bytes')
            transaction.record_staged_runtime()
            os.replace(transaction.runtime_root, transaction.backup)
            os.replace(transaction.stage, transaction.runtime_root)
            return {}
        completed = installer.complete_runtime_transaction(project, installed['_runtime_transaction_id'], build)
        self.assertEqual('COMMITTED_CLEANUP_PENDING', completed['transaction_cleanup'])
        after = inventory(project)
        result = qualify_upgrade.compare_upgrade(before, after, policy, project)
        self.assertTrue(result['preserved'], result['violations'])
        self.assertEqual(set(result['changes']), set(result['approved_changes']))
        retained = completed['installation_recovery']['retained_directories'][0]
        self.assertEqual(b'previous runtime bytes', (project / retained / 'previous-runtime').read_bytes())
        for relative in (retained + '/previous-runtime', retained + '/unexpected'):
            with self.subTest(tamper=relative):
                accepted = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                self.assertTrue(accepted['preserved'], accepted['violations'])
                target = project / relative
                original = target.read_bytes() if target.exists() else None
                try:
                    target.write_bytes(b'tampered owner evidence')
                    rejected = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                    self.assertFalse(rejected['preserved'])
                    self.assertIn(relative, rejected['remaining_changes'])
                finally:
                    if original is None:
                        target.unlink()
                    else:
                        target.write_bytes(original)

    def test_adoption_evidence_is_an_exact_preservation_output(self):
        from agentic import installer
        from agentic.safeio import Tree
        import uuid
        project = self.base / 'adoption-evidence-upgrade'
        install_baseline(self.source, project)
        # Synthetic interrupted-directory diagnostics added only to this
        # disposable baseline; they assert no published/adopter provenance.
        path = '.agentic/lib/agentic'
        journal = dict(format='awf-install-journal-4', transaction_id=str(uuid.uuid4()),
            phase='rollback_cleanup', runtime=None, managed_before_sha256=hashlib.sha256(b'[]').hexdigest(),
            managed_after_sha256=None, created_directories=[dict(path=path, identity=None)],
            files=[dict(path=path + '/synthetic-previous.bin', old_sha256=None, old_size=None,
                old_mode=None, new_sha256='a' * 64, new_mode=0o600)])
        original = dict(format='awf-retained-journal-1', journal=journal, retained_directories=[path])
        with Tree(project) as tree:
            tree.write(installer.RETAINED_JOURNAL, installer.json_bytes(original))
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                discover=False, conflict='backup')
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertTrue(result['preserved'], result['violations'])
        self.assertEqual(set(result['changes']), set(result['approved_changes']))
        self.assertFalse((project / installer.RETAINED_JOURNAL).exists())
        audit_path = project / installer.DIRECTORY_ADOPTIONS
        audit = json.loads(audit_path.read_bytes())
        self.assertEqual(original, audit['transactions'][0]['evidence'])
        self.assertEqual([path], audit['transactions'][0]['directories'])
        audit['transactions'][0]['evidence']['journal']['files'][0]['new_sha256'] = 'b' * 64
        audit_path.write_bytes(installer.json_bytes(audit))
        rejected = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(rejected['preserved'])
        self.assertIn(installer.DIRECTORY_ADOPTIONS, rejected['remaining_changes'])

    def test_retained_journal_cannot_rebind_or_omit_baseline_evidence(self):
        from copy import deepcopy
        from agentic import installer
        import uuid
        project = self.base / 'retention-journal-authentication'
        install_baseline(self.source, project)
        runtime = project / installer.RUNTIME
        runtime.mkdir()
        (runtime / 'previous-runtime').write_bytes(b'current runtime')
        previous_id = str(uuid.uuid4())
        previous_backup = project / ('.agentic/.venv.backup-' + previous_id)
        previous_backup.mkdir()
        (previous_backup / 'previous-runtime').write_bytes(b'prior backup owner bytes')
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        installed = install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                            discover=False, conflict='backup', defer_runtime=True)

        def build(transaction):
            transaction.stage.mkdir()
            (transaction.stage / 'new-runtime').write_bytes(b'new runtime')
            transaction.record_staged_runtime()
            os.replace(transaction.runtime_root, transaction.backup)
            os.replace(transaction.stage, transaction.runtime_root)
            return {}

        installer.complete_runtime_transaction(project, installed['_runtime_transaction_id'], build)
        clean = inventory(project)
        accepted = qualify_upgrade.compare_upgrade(before, clean, policy, project)
        self.assertTrue(accepted['preserved'], accepted['violations'])
        record = json.loads((project / installer.RETAINED_JOURNAL).read_bytes())
        self.assertGreaterEqual(len(record['journal']['created_directories']), 25)
        raw = (project / installer.RETAINED_JOURNAL).read_bytes()
        for mutation in ('missing-file', 'metadata-reference', 'missing-directories',
                         'forged-runtime-proofs', 'existing-backup'):
            with self.subTest(mutation=mutation):
                accepted = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                self.assertTrue(accepted['preserved'], accepted['violations'])
                changed = deepcopy(record)
                journal = changed['journal']
                moved_runtime = moved_files = None
                prior_bytes = (previous_backup / 'previous-runtime').read_bytes()
                try:
                    if mutation == 'existing-backup':
                        identity = journal['transaction_id']
                        changed = json.loads(json.dumps(changed).replace(identity, previous_id))
                        old = project / record['journal']['runtime']['backup']
                        quarantine = self.base / 'retention-original-runtime'
                        self.assertTrue(old.resolve().is_relative_to(project.resolve()))
                        self.assertTrue(quarantine.resolve().is_relative_to(self.base.resolve()))
                        self.assertFalse(quarantine.exists())
                        (previous_backup / 'previous-runtime').write_bytes((old / 'previous-runtime').read_bytes())
                        old.rename(quarantine)
                        moved_runtime = (quarantine, old)
                        file_backups = project / '.agentic-backup' / identity
                        if file_backups.exists():
                            target = file_backups.with_name(previous_id)
                            self.assertTrue(file_backups.resolve().is_relative_to(project.resolve()))
                            self.assertTrue(target.resolve().is_relative_to(project.resolve()))
                            self.assertFalse(target.exists())
                            file_backups.rename(target)
                            moved_files = (target, file_backups)
                        self.assertNotEqual(b'prior backup owner bytes',
                                            (previous_backup / 'previous-runtime').read_bytes())
                    elif mutation == 'missing-file':
                        row = next(row for row in journal['files'] if row['old_sha256'] is None
                                   and row['path'].startswith('.agentic/lib/agentic/'))
                        journal['files'].remove(row)
                    elif mutation == 'metadata-reference':
                        references = journal.setdefault('windows_metadata', {})
                        path = next(iter(references), next(row['path'] for row in journal['files']
                                                         if row['old_sha256'] is not None))
                        references[path] = dict(sha256='f' * 64, size=123)
                    elif mutation == 'forged-runtime-proofs':
                        journal['runtime']['cleanup_identity'] = ['1' * 32, '2' * 32]
                        journal['runtime']['cleanup_members'] = [
                            ['nonexistent', 'file', 17, ['3' * 16, '4' * 16], 'f' * 64]]
                    else:
                        del journal['created_directories'][:25]
                    installer._retained_record_result(changed)
                    (project / installer.RETAINED_JOURNAL).write_bytes(installer.json_bytes(changed))
                    rejected = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                    self.assertFalse(rejected['preserved'], mutation)
                    self.assertIn(installer.RETAINED_JOURNAL, rejected['remaining_changes'])
                    self.assertFalse(any('directory identities differ' in value
                                         for value in rejected['violations']), rejected['violations'])
                finally:
                    if moved_files:
                        moved_files[0].rename(moved_files[1])
                    if moved_runtime:
                        moved_runtime[0].rename(moved_runtime[1])
                    (previous_backup / 'previous-runtime').write_bytes(prior_bytes)
                    (project / installer.RETAINED_JOURNAL).write_bytes(raw)
                self.assertEqual(clean, inventory(project))

    def test_adoption_audit_cannot_rebind_the_upgrade_transaction(self):
        from agentic import installer
        from agentic.safeio import Tree
        import uuid
        project = self.base / 'adoption-transaction-binding'
        install_baseline(self.source, project)
        path = '.agentic/lib/agentic'
        journal = dict(format='awf-install-journal-4', transaction_id=str(uuid.uuid4()),
            phase='rollback_cleanup', runtime=None, managed_before_sha256=hashlib.sha256(b'[]').hexdigest(),
            managed_after_sha256=None, created_directories=[dict(path=path, identity=None)],
            files=[dict(path=path + '/synthetic-previous.bin', old_sha256=None, old_size=None,
                old_mode=None, new_sha256='a' * 64, new_mode=0o600)])
        original = dict(format='awf-retained-journal-1', journal=journal, retained_directories=[path])
        with Tree(project) as tree:
            tree.write(installer.RETAINED_JOURNAL, installer.json_bytes(original))
        previous_id = str(uuid.uuid4())
        backup = project / '.agentic-backup' / previous_id / 'owner'
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b'prior owner backup')
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                discover=False, conflict='backup')
        clean = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertTrue(clean['preserved'], clean['violations'])
        audit_path = project / installer.DIRECTORY_ADOPTIONS
        audit = json.loads(audit_path.read_bytes())
        for identity in (previous_id, str(uuid.uuid4())):
            with self.subTest(transaction_id=identity):
                audit_path.write_bytes(installer.json_bytes(audit))
                accepted = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                self.assertTrue(accepted['preserved'], accepted['violations'])
                from copy import deepcopy
                changed = deepcopy(audit)
                event = changed['transactions'][0]
                event['transaction_id'] = identity
                event['resolution'] = 'adopted by transaction ' + identity
                audit_path.write_bytes(installer.json_bytes(changed))
                rejected = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
                self.assertFalse(rejected['preserved'])
                self.assertIn(installer.DIRECTORY_ADOPTIONS, rejected['remaining_changes'])
        self.assertEqual(b'prior owner backup', backup.read_bytes())

    def test_manifest_and_published_tag_identity(self):
        self.assertEqual('1.9.5', verify_published(self.source)['template_version'])
        with self.assertRaisesRegex(ValueError, 'published commit'):
            materialize_published(ROOT, self.base / 'wrong', commit='HEAD')
        self.assertFalse((self.base / 'wrong').exists())

    def test_corrupt_disposable_projection_is_rejected(self):
        # Corrupt only throwaway copies, never Git objects, the pinned baseline
        # or any installation receipt.
        for relative in ('MANIFEST.json', '.agentic/SPECIFICATION.md'):
            with self.subTest(relative=relative):
                copy = self.base / ('corrupt-' + Path(relative).name)
                shutil.copytree(self.source, copy)
                (copy / relative).write_bytes(b'corrupt disposable projection\n')
                with self.assertRaises((ValueError, AssertionError)):
                    verify_published(copy)
                self.assertEqual('1.9.5', verify_published(self.source)['template_version'])

    def test_newly_managed_owner_collision_cannot_be_approved_after_deletion(self):
        project = self.base / 'owner-managed-collision'
        install_baseline(self.source, project)
        relative = '.agentic/lib/agentic/lane_labels.py'
        owner = project / relative
        self.assertFalse(owner.exists())
        owner.write_bytes(b'Synthetic owner evidence at a future managed path.\n')
        before = inventory(project)
        try:
            policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        except ValueError as error:
            self.assertIn('owner collision', str(error))
            self.assertIn(relative, str(error))
            self.assertEqual(before, inventory(project))
            return
        # Simulate the faulty upgrade deleting owner evidence before installation.
        owner.unlink()
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(result['preserved'], result)

    def test_faulty_migration_cannot_approve_its_own_deleted_settlement(self):
        from upgrade_195_fixtures import seed_legacy_accounting
        from agentic import upgrade
        project = self.base / 'deleted-settlement'
        install_baseline(self.source, project)
        seed_legacy_accounting(self.source, project)
        ledger = project / '.agentic-state/routing/ledger.sqlite'

        def identities():
            connection = sqlite3.connect(':memory:')
            try:
                connection.deserialize(ledger.read_bytes())
                return connection.execute('SELECT run_id FROM model_runs ORDER BY run_id').fetchall()
            finally:
                connection.close()

        self.assertEqual([('synthetic-settled',), ('synthetic-unknown',)], identities())
        before = inventory(project)
        clean_project = self.base / 'clean-accounting'
        shutil.copytree(project, clean_project)
        clean_before = inventory(clean_project)
        clean_policy = qualify_upgrade.prepare_upgrade(self.candidate, clean_project, self.pin, clean_before)
        install(self.candidate, clean_project, self.pin, mode='upgrade', configure=True, discover=False,
                conflict='backup')
        clean = qualify_upgrade.compare_upgrade(clean_before, inventory(clean_project), clean_policy, clean_project)
        self.assertTrue(clean['preserved'], clean['violations'])
        self.assertEqual('PASS', clean['checks']['historical_state_continuity'])
        original = upgrade._migrate_routing_ledger

        def lose_settlement(raw):
            connection = sqlite3.connect(':memory:')
            try:
                connection.deserialize(original(raw))
                connection.execute("DELETE FROM model_runs WHERE run_id='synthetic-settled'")
                connection.commit()
                return connection.serialize()
            finally:
                connection.close()

        with patch.object(upgrade, '_migrate_routing_ledger', side_effect=lose_settlement):
            policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
            install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual([('synthetic-unknown',)], identities())
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(result['preserved'], result)
        self.assertTrue(any('continuity' in item for item in result['violations']), result)

    @unittest.skipUnless(os.name == 'nt', 'Windows named-stream preservation regression')
    def test_owner_named_stream_deleted_across_upgrade_is_rejected(self):
        project = self.base / 'deleted-stream'
        install_baseline(self.source, project)
        customize_preservation_project(project)
        stream = Path(str(project / 'owner.txt') + ':owner-receipt')
        stream.write_bytes(b'Synthetic owner stream evidence.\n')
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        stream.unlink()
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(result['preserved'], result)
        self.assertIn('owner.txt', result['changes'])

    def test_derived_delta_accepts_only_the_clean_authentic_upgrade(self):
        project = self.base / 'preservation-upgrade'
        install_baseline(self.source, project)
        customize_preservation_project(project)
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False,
                conflict='backup')
        clean = inventory(project)
        result = qualify_upgrade.compare_upgrade(before, clean, policy, project)
        self.assertTrue(result['preserved'], result['violations'])
        self.assertEqual(set(result['changes']), set(result['approved_changes']))
        self.assertEqual('target-manifest', result['approved_changes']['AGENTS.md']['rule'])
        self.assertEqual('project-config-version-only',
                         result['approved_changes']['.agentic/PROJECT_CONFIG.yaml']['rule'])
        mutations = {
            'owner deleted': ('owner.txt', None),
            'owner changed': ('owner.txt', b'changed owner bytes\n'),
            'instructions changed': ('PROJECT_INSTRUCTIONS.md', b'changed instructions\n'),
            'managed mismatch': ('AGENTS.md', b'not target manifest bytes\n'),
            'unexpected file': ('unexpected.txt', b'extra\n'),
            'unexpected archive': ('.agentic-state/archive/upgrade-to-1.9.6/files/extra', b'extra\n'),
            'unexpected object': ('.agentic-install/objects/' + '0' * 64, b'not an installer object\n'),
            'prior archive': ('.agentic-state/archive/prior/receipt.json', b'changed prior archive\n'),
            'archive contents': ('.agentic-state/archive/upgrade-to-1.9.6/files/owner-note.txt', b'wrong copy\n'),
            'receipt': ('.agentic/installed-manifest.json', b'{}\n'),
            'provenance': ('.agentic/workflow-version.yaml', b'{}\n'),
            'codeowners': ('.github/CODEOWNERS', b'* @unexpected-owner\n'),
            'routing': ('.agentic/routing.yaml', b'{}\n'),
            'operating': ('OPERATING_CONFIG.yaml', b'{}\n'),
        }
        for label, (relative, raw) in mutations.items():
            with self.subTest(mutation=label):
                copy = self.base / ('mutation-' + label.replace(' ', '-'))
                shutil.copytree(project, copy)
                path = copy / relative
                if raw is None:
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.chmod(0o600) if path.exists() else None
                    path.write_bytes(raw)
                rejected = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy)
                self.assertFalse(rejected['preserved'], label)
                self.assertTrue(any(relative in item for item in rejected['violations']), rejected)
                if label == 'owner changed':
                    # Even an externally supplied exact observed delta cannot waive ownership.
                    waived = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy,
                                                             allowed_delta=rejected['changes'])
                    self.assertFalse(waived['preserved'])
                    self.assertIn('external delta is not an approved migration: owner.txt', waived['violations'])
        copy = self.base / 'mutation-config'
        shutil.copytree(project, copy)
        config_path = copy / '.agentic/PROJECT_CONFIG.yaml'
        config = json.loads(config_path.read_bytes())
        config['execution']['max_parallel_tickets'] += 1
        config_path.write_text(json.dumps(config), encoding='utf-8')
        rejected = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy)
        self.assertFalse(rejected['preserved'])
        self.assertTrue(any('.agentic/PROJECT_CONFIG.yaml' in item for item in rejected['violations']))
        if os.name == 'nt':
            import subprocess
            copy = self.base / 'mutation-acl'
            shutil.copytree(project, copy)
            child = subprocess.run(['icacls', str(copy / 'owner.txt'), '/grant', '*S-1-1-0:(R)'],
                                   capture_output=True)
            self.assertEqual(0, child.returncode, 'Disposable ACL mutation failed')
            rejected = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy)
            self.assertFalse(rejected['preserved'])
            self.assertIn('unapproved delta: owner.txt', rejected['violations'])
        self.assertEqual(clean, inventory(project))

    def test_published_installer_generates_receipt_for_main_master_trunk(self):
        for branch in ('main', 'master', 'trunk'):
            with self.subTest(branch=branch):
                project = self.base / branch
                result = install_baseline(self.source, project, branch=branch)
                self.assertEqual('INSTALLED', result['status'])
                receipt = json.loads((project / '.agentic/installed-manifest.json').read_bytes())
                self.assertEqual('1.9.5', receipt['template_version'])
                config = json.loads((project / '.agentic/PROJECT_CONFIG.yaml').read_bytes())
                self.assertEqual(branch, config['github']['base_branch'])
                self.assertEqual(101, config['github']['repository_id'])
                self.assertFalse(result['live_automation_enabled'])
                owner = project / 'PROJECT_INSTRUCTIONS.md'
                owner.write_bytes(b'synthetic owner customization; not CMC evidence\n')
                before = inventory(project)
                planned = plan(self.candidate, project, self.pin)
                self.assertTrue(planned['preservation']['preserved'])
                self.assertEqual(before, inventory(project))
                if branch != 'main':
                    continue
                for override in ({'repository': 'another-org/another-repo'},
                                 {'repository_id': 202}, {'base_branch': 'another-branch'}):
                    with self.subTest(override=override), self.assertRaises(ValidationError):
                        install(self.candidate, project, self.pin, mode='upgrade',
                                configure=True, discover=False, overrides=override, dry_run=True)
                    self.assertEqual(before, inventory(project))
                note = project / '.agentic-state/unknown-owner-note.txt'
                note.write_bytes(b'synthetic retained owner note\n')
                collision = project / '.agentic-state/archive/upgrade-to-1.9.6/files/unknown-owner-note.txt'
                collision.parent.mkdir(parents=True)
                collision.write_bytes(b'conflicting retained archive\n')
                before_conflict = inventory(project)
                with self.assertRaisesRegex(ValidationError, 'State archive path already exists'):
                    install(self.candidate, project, self.pin, mode='upgrade',
                            configure=True, discover=False)
                self.assertEqual(before_conflict, inventory(project))


if __name__ == '__main__':
    unittest.main()
