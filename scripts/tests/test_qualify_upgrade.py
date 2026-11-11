"""Preservation evidence is read-only, exact and cannot waive target identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import (compare, inventory, write_report, plan, checked_bootstrap, qualify,
                             _creation_state, _json_identity, _historical_state, _check_state_continuity)
import os
from copy import deepcopy


from contextlib import contextmanager, nullcontext
import stat
from types import SimpleNamespace


@contextmanager
def simulated_runtime_links(roots, *, windows_reparse=False):
    """Observe lib64 placeholders as POSIX links or Windows symlink reparse points."""
    original_lstat, original_scandir, original_readlink = Path.lstat, os.scandir, os.readlink
    original_os_lstat = os.lstat

    def linked(path):
        path = Path(path)
        return (path.name == 'lib64' and os.path.normcase(os.path.realpath(path.parent)) in
                {os.path.normcase(os.path.realpath(root)) for root in roots})

    def link_stat(path):
        info = original_os_lstat(path)
        return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_nlink=1,
            st_file_attributes=0x400 if windows_reparse else 0,
            st_reparse_tag=0xA000000C if windows_reparse else 0, st_size=info.st_size,
            st_dev=info.st_dev, st_ino=info.st_ino)

    def lstat(path, *args, **kwargs):
        return link_stat(path) if linked(path) else original_lstat(path, *args, **kwargs)

    def os_lstat(path, *args, **kwargs):
        return link_stat(path) if linked(path) else original_os_lstat(path, *args, **kwargs)

    class Entry:
        def __init__(self, entry): self.entry = entry
        def __getattr__(self, name): return getattr(self.entry, name)
        def stat(self, *, follow_symlinks=True):
            if not follow_symlinks and linked(self.entry.path): return link_stat(self.entry.path)
            return self.entry.stat(follow_symlinks=follow_symlinks)
        def is_symlink(self): return linked(self.entry.path) or self.entry.is_symlink()
        def is_dir(self, *, follow_symlinks=True):
            if linked(self.entry.path): return follow_symlinks
            return self.entry.is_dir(follow_symlinks=follow_symlinks)

    class Scan:
        def __init__(self, path): self.scan = original_scandir(path)
        def __iter__(self): return self
        def __next__(self): return Entry(next(self.scan))
        def __enter__(self): return self
        def __exit__(self, *args): self.scan.close()
        def close(self): self.scan.close()

    def readlink(path, *args, **kwargs):
        return Path(path).read_text(encoding='utf-8') if linked(path) else original_readlink(path, *args, **kwargs)

    with patch.object(Path, 'lstat', lstat), patch.object(os, 'lstat', os_lstat), patch.object(os, 'scandir', Scan), \
            patch.object(os, 'readlink', readlink):
        yield


class PreservationReportTests(unittest.TestCase):
    def test_budget_import_continuity_requires_baseline_and_real_sources(self):
        from qualify_upgrade import prepare_budget_import, check_budget_import
        with self.assertRaises(ValueError):
            prepare_budget_import({}, project_id='project')
        result = check_budget_import(None, {}, self.root / 'missing.sqlite')
        self.assertFalse(result['preserved'])
        self.assertFalse((self.root / 'missing.sqlite').exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        (self.root / '.agentic').mkdir(parents=True)
        self.config = self.root / '.agentic/PROJECT_CONFIG.yaml'
        self.config.write_text(json.dumps({'github': {'repository': 'example-org/example-repo',
            'repository_id': 101, 'base_branch': 'trunk'}}), encoding='utf-8')
        (self.root / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')

    def test_retained_lib64_snapshot_is_authenticated_with_simulated_posix_links(self):
        backup = self.root / '.agentic/.venv.backup-11111111-1111-4111-8111-111111111111'
        (backup / 'lib').mkdir(parents=True)
        (backup / 'lib/runtime').write_bytes(b'original runtime')
        (backup / 'lib64').write_bytes(b'lib')
        with simulated_runtime_links({backup}):
            before = inventory(self.root)
            self.assertEqual('lib', before['files'][backup.relative_to(self.root).as_posix() + '/lib64']['link'])
            self.assertTrue(compare(before, inventory(self.root), {})['preserved'])
            for mutation in ('bytes', 'added', 'target'):
                with self.subTest(mutation=mutation):
                    self.assertTrue(compare(before, inventory(self.root), {})['preserved'])
                    target = backup / {'bytes': 'lib/runtime', 'added': 'extra', 'target': 'lib64'}[mutation]
                    original = target.read_bytes() if target.exists() else None
                    try:
                        target.write_bytes(b'.' if mutation == 'target' else b'tampered')
                        rejected = compare(before, inventory(self.root), {})
                        self.assertFalse(rejected['preserved'])
                        self.assertIn(target.relative_to(self.root).as_posix(), rejected['changes'])
                    finally:
                        if original is not None: target.write_bytes(original)
                        else: target.rename(Path(self.temporary.name) / 'retained-extra')
            (backup / 'lib64').write_bytes(b'../escape')
            with self.assertRaisesRegex(ValueError, 'contained independent'):
                inventory(self.root)

    def test_lib64_lookalikes_outside_runtime_still_refuse_simulated_links(self):
        for relative in ('.agentic/other', '.agentic/.venv.backup-unbound',
                         '.agentic/nested/.venv.backup-11111111-1111-4111-8111-111111111111'):
            with self.subTest(relative=relative):
                parent = self.root / relative
                parent.mkdir(parents=True)
                (parent / 'lib64').write_bytes(b'lib')
                with simulated_runtime_links({parent}):
                    with self.assertRaisesRegex(ValueError, 'Linked/reparse inventory member refused'):
                        inventory(self.root)

    @unittest.skipUnless(os.name == 'posix',
        'Windows venvs never contain lib64; Windows reparse points are refused by design '
        '(asserted by the Windows refusal companion and its privilege-independent simulation)')
    def test_retained_lib64_real_symlink_snapshot_and_tamper(self):
        backup = self.root / '.agentic/.venv.backup-22222222-2222-4222-8222-222222222222'
        (backup / 'lib').mkdir(parents=True)
        (backup / 'lib/runtime').write_bytes(b'original')
        (backup / 'lib64').symlink_to('lib', target_is_directory=True)
        before = inventory(self.root)
        self.assertTrue(compare(before, inventory(self.root), {})['preserved'])
        (backup / 'lib/runtime').write_bytes(b'changed')
        result = compare(before, inventory(self.root), {})
        self.assertFalse(result['preserved'])
        self.assertIn(backup.relative_to(self.root).as_posix() + '/lib/runtime', result['changes'])

    def _assert_reparse_refusal_preserves_inventory(self, backup, observations=None):
        from agentic.preservation_metadata import (runtime_tree_sha256, runtime_inventory,
                                                    _permissions, _named_streams)
        from qualify_upgrade import main

        def capture(root):
            # The production inventory deliberately refuses this tree. This
            # independent test inventory records links without traversing them.
            result = {}
            def visit(node, relative):
                info = node.lstat()
                metadata = {key: getattr(info, key, None) for key in (
                    'st_mode', 'st_dev', 'st_ino', 'st_nlink', 'st_size', 'st_uid',
                    'st_gid', 'st_mtime_ns', 'st_ctime_ns', 'st_file_attributes', 'st_reparse_tag')}
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    result[relative] = dict(metadata=metadata, link=os.readlink(node))
                else:
                    state = dict(metadata=metadata, permissions=_permissions(node),
                                 named_streams=_named_streams(node))
                    if stat.S_ISDIR(info.st_mode):
                        result[relative] = state
                        for child in sorted(node.iterdir()):
                            visit(child, relative + '/' + child.name)
                    else:
                        self.assertTrue(stat.S_ISREG(info.st_mode))
                        result[relative] = dict(state, contents=node.read_bytes())
            visit(root, '.')
            return result

        project_before, backup_before = capture(self.root), capture(backup)
        operations = (
            ('runtime hash', lambda: runtime_tree_sha256(backup)),
            ('runtime inventory', lambda: runtime_inventory(backup)),
            ('project snapshot', lambda: inventory(self.root)),
            ('runtime-hash command', lambda: main(['runtime-hash', '--path', str(backup),
                                                  '--expected-sha256', '0' * 64])),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                with observations() if observations else nullcontext():
                    with self.assertRaises(ValueError) as refused:
                        operation()
                    self.assertEqual('Runtime hash refuses reparse points: lib64; retain the runtime and '
                        'journal/marker; obtain an owner-reviewed recovery plan from an independently '
                        'authenticated regular runtime', str(refused.exception))
                self.assertEqual(backup_before, capture(backup))
                self.assertEqual(project_before, capture(self.root))

    @unittest.skipUnless(os.name == 'nt', 'Windows symlink reparse-point refusal; POSIX links have different semantics')
    def test_retained_lib64_windows_symlink_is_refused_without_mutation(self):
        backup = self.root / '.agentic/.venv.backup-33333333-3333-4333-8333-333333333333'
        (backup / 'lib').mkdir(parents=True)
        (backup / 'lib/runtime').write_bytes(b'original runtime')
        try:
            (backup / 'lib64').symlink_to('lib', target_is_directory=True)
        except OSError as exc:
            if exc.winerror == 1314:
                self.skipTest('Creating a real Windows directory symlink requires privilege (WinError 1314); '
                              'the reparse observation seam is exercised without privilege by the companion')
            raise
        self.assertTrue((backup / 'lib64').lstat().st_file_attributes & 0x400)
        self._assert_reparse_refusal_preserves_inventory(backup)

    def test_retained_lib64_windows_reparse_observations_refuse_without_mutation(self):
        backup = self.root / '.agentic/.venv.backup-44444444-4444-4444-8444-444444444444'
        (backup / 'lib').mkdir(parents=True)
        (backup / 'lib/runtime').write_bytes(b'original runtime')
        (backup / 'lib64').write_bytes(b'lib')
        # The same non-following lstat/DirEntry.stat observations as a privileged
        # Windows symlink, including FILE_ATTRIBUTE_REPARSE_POINT and its tag.
        self._assert_reparse_refusal_preserves_inventory(backup,
            lambda: simulated_runtime_links({backup}, windows_reparse=True))

    def test_runtime_hash_command_is_exact_read_only_and_checks_expected_digest(self):
        import io
        from contextlib import redirect_stdout
        from agentic import installer
        from qualify_upgrade import main
        runtime = self.root / '.agentic/.venv'
        (runtime / 'lib').mkdir(parents=True)
        (runtime / 'lib/file').write_bytes(b'runtime contents')
        expected = installer._runtime_tree_sha256(runtime)
        before = {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                  for p in self.root.rglob('*') if p.is_file()}
        for digest, code in ((expected, 0), ('f' * 64, 1)):
            output = io.StringIO()
            with redirect_stdout(output):
                actual = main(['runtime-hash', '--path', str(runtime), '--expected-sha256', digest])
            self.assertEqual(code, actual)
            self.assertEqual(dict(format='awf-runtime-authentication-1', sha256=expected,
                expected_sha256=digest, matches=code == 0, execution_authority=False), json.loads(output.getvalue()))
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_runtime_hash_reads_link_text_without_following_simulated_posix_link(self):
        from agentic.preservation_metadata import runtime_tree_sha256
        from agentic import installer
        runtime = self.root / '.agentic/.venv'
        runtime.mkdir()
        # Deliberately dangling external target: hashing observes text, never opens it.
        (runtime / 'lib64').write_bytes(b'../../outside-unreadable')
        original = Path.read_bytes
        def read(path):
            self.assertNotEqual('outside-unreadable', path.name)
            self.assertNotEqual(runtime / 'lib64', path)
            return original(path)
        with simulated_runtime_links({runtime}), patch.object(Path, 'read_bytes', read):
            self.assertEqual(installer._runtime_tree_sha256(runtime), runtime_tree_sha256(runtime))

    def test_runtime_hash_rejects_missing_roots_hardlinks_and_reparse_entries(self):
        from agentic.preservation_metadata import runtime_tree_sha256
        from agentic import ValidationError
        with self.assertRaises((OSError, ValidationError)):
            runtime_tree_sha256(self.root / 'absent')
        self.assertFalse((self.root / 'absent').exists())
        runtime = self.root / '.agentic/.venv'
        runtime.mkdir()
        target = runtime / 'file'
        target.write_bytes(b'original bytes')
        os.link(target, Path(self.temporary.name) / 'owner-hardlink')
        message = ('Refusing link, reparse point, or wrong file type' if os.name == 'nt' else
                   'Unsafe file type/link count/size')
        with self.assertRaisesRegex(ValidationError, message):
            runtime_tree_sha256(runtime)
        self.assertEqual(b'original bytes', target.read_bytes())
        target.rename(Path(self.temporary.name) / 'preserved-hardlinked-runtime-file')
        (runtime / 'lib64').write_bytes(b'lib')
        # A reparse directory is refused even if it carries a runtime-looking name.
        original = os.scandir
        class Reparse:
            def __init__(self, entry): self.entry = entry
            def __getattr__(self, key): return getattr(self.entry, key)
            def stat(self, *, follow_symlinks=True):
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)
        class Scan:
            def __init__(self, path): self.scan = original(path)
            def __enter__(self): return (Reparse(entry) for entry in self.scan)
            def __exit__(self, *args): self.scan.close()
        with patch.object(os, 'scandir', Scan):
            with self.assertRaisesRegex(ValueError, 'reparse points'):
                runtime_tree_sha256(runtime)
        self.assertEqual(b'lib', (runtime / 'lib64').read_bytes())

    def test_installed_receipts_require_canonical_commands_and_bound_outputs(self):
        # Unit validation inputs, not executed qualification evidence.
        pin, policy = 'a' * 64, 'b' * 64
        runtime = self.root / '.agentic/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        script = self.root / '.agentic/scripts/workflow.py'
        result = {'status': 'CONFIGURED', 'source_manifest_sha256': pin,
                  'configuration': {'policy_sha256': policy},
                  'pre_install_configuration': {'policy_sha256': policy}, 'post_install_checks': []}
        for action, output in [('verify-installation', {'integrity_valid': True, 'source_manifest_sha256': pin}),
                               ('validate-config', {'status': 'ACCEPTED', 'policy_sha256': policy})]:
            result['post_install_checks'].append({'command': [str(runtime), '-B', '-I', str(script),
                '--root', str(self.root), action], 'exit_code': 0, 'diagnostic': None, 'output': output})
        for name in ('installed-manifest.json', 'workflow-version.yaml'):
            (self.root / '.agentic' / name).write_bytes(b'unit validation input')
        self.assertEqual('CONFIGURED', checked_bootstrap(result, self.root, pin)['status'])
        mutations = [lambda r: r.update(status='ACTIVE'),
            lambda r: r['post_install_checks'][0].update(exit_code=1),
            lambda r: r['post_install_checks'][1].update(exit_code=None),
            lambda r: r['post_install_checks'][0]['command'].remove('-I'),
            lambda r: r['post_install_checks'][0]['output'].update(integrity_valid=False),
            lambda r: r['post_install_checks'][0]['output'].update(source_manifest_sha256='c' * 64),
            lambda r: r['post_install_checks'][1]['output'].update(status='REJECTED'),
            lambda r: r['post_install_checks'][1]['output'].update(policy_sha256='c' * 64),
            lambda r: r['pre_install_configuration'].update(policy_sha256='c' * 64),
            lambda r: r['post_install_checks'].pop()]
        for mutate in mutations:
            damaged = deepcopy(result)
            mutate(damaged)
            with self.assertRaises(ValueError):
                checked_bootstrap(damaged, self.root, pin)

    def test_inventory_is_nonwriting_and_contains_modes_and_history(self):
        state = self.root / '.agentic-state/operating/changes/record.json'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'synthetic history')
        before = {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                  for p in self.root.rglob('*') if p.is_file()}
        report = inventory(self.root)
        self.assertEqual('SYNTHETIC_OWNER_CUSTOMIZATION', report['evidence_kind'])
        self.assertIn('.agentic-state/operating/changes/record.json', report['files'])
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual([], compare(report, report, {})['violations'])

    def test_inventory_retains_canonical_owner_paths_and_managed_link_refusals(self):
        owners = ('.github/CODEOWNERS', '.github/PULL_REQUEST_TEMPLATE.md', 'docs/CODEOWNERS')
        for relative in owners:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'owner content\n')
        before = inventory(self.root)
        self.assertTrue(set(owners) <= before['files'].keys())
        for relative in owners:
            (self.root / relative).unlink()
        self.assertTrue(set(owners) <= compare(before, inventory(self.root), {})['changes'].keys())
        managed = self.root / 'AGENTS.md'
        managed.write_bytes(b'managed content\n')
        os.link(managed, Path(self.temporary.name) / 'alias')
        with self.assertRaisesRegex(ValueError, 'AGENTS.md; preserve'):
            inventory(self.root)

    def test_creation_permission_probes_match_installer_and_leave_no_delta(self):
        from agentic.safeio import Tree
        import stat
        before = inventory(self.root)
        file_path = '.agentic/new/nested/managed.txt'
        directory_path = '.agentic/new/nested/'
        file_state = _creation_state(self.root, file_path)
        directory_state = _creation_state(self.root, directory_path, directory=True)
        readonly_state = _creation_state(self.root, file_path, readonly=True)
        self.assertEqual(before, inventory(self.root))
        with Tree(self.root) as tree:
            tree.write(file_path, b'permission creation test\n')
        after = inventory(self.root)
        self.assertEqual(file_state, {key: after['files'][file_path][key]
                                     for key in ('mode', 'permissions', 'named_streams')})
        self.assertEqual(directory_state, after['directories'][directory_path])
        (self.root / file_path).chmod(stat.S_IREAD)
        readonly = inventory(self.root)['files'][file_path]
        self.assertEqual(readonly_state, {key: readonly[key] for key in ('mode', 'permissions', 'named_streams')})

    def test_config_value_identity_preserves_types(self):
        self.assertNotEqual(_json_identity({'enabled': True}), _json_identity({'enabled': 1}))
        self.assertNotEqual(_json_identity({'count': 12}), _json_identity({'count': 12.0}))
        self.assertEqual(_json_identity({'b': 2, 'a': 1}), _json_identity({'a': 1, 'b': 2}))

    def test_exact_delta_and_identity_cannot_be_waived(self):
        before = inventory(self.root)
        path = 'PROJECT_INSTRUCTIONS.md'
        (self.root / path).write_bytes(b'changed')
        after = inventory(self.root)
        delta = json.loads(json.dumps({path: {'before': before['files'][path], 'after': after['files'][path]}}))
        self.assertFalse(compare(before, after, {})['preserved'])
        self.assertTrue(compare(before, after, delta)['preserved'])
        after['identity']['base_branch'] = 'main'
        self.assertFalse(compare(before, after, delta)['preserved'])
        after['identity']['base_branch'] = 'trunk'
        after['files'][path]['sha256'] = '0' * 64
        self.assertFalse(compare(before, after, delta)['preserved'])

    def test_unused_delta_is_not_silently_accepted(self):
        report = inventory(self.root)
        self.assertFalse(compare(report, report, {'missing': {'before': None, 'after': None}})['preserved'])

    def test_empty_directory_creation_is_detected(self):
        before = inventory(self.root)
        (self.root / 'unexpected').mkdir()
        self.assertFalse(compare(before, inventory(self.root), {})['preserved'])

    @unittest.skipUnless(os.name == 'nt', 'Windows named-stream preservation regression')
    def test_inventory_detects_owner_stream_loss(self):
        stream = Path(str(self.root / 'PROJECT_INSTRUCTIONS.md') + ':owner-receipt')
        stream.write_bytes(b'synthetic stream evidence')
        before = inventory(self.root)
        stream.unlink()
        result = compare(before, inventory(self.root), {})
        self.assertFalse(result['preserved'], result)

    def test_missing_named_stream_evidence_cannot_pass(self):
        report = inventory(self.root)
        report['files']['PROJECT_INSTRUCTIONS.md'].pop('named_streams', None)
        self.assertFalse(compare(report, report, {})['preserved'])

    def test_unreadable_named_streams_cannot_be_waived(self):
        before = inventory(self.root)
        with patch('agentic.preservation_metadata._named_streams', return_value={
                'verified': False, 'method': 'windows-find-streams'}):
            after = inventory(self.root)
        delta = compare(before, after, {})['changes']
        self.assertFalse(compare(before, after, delta)['preserved'])

    def test_independent_state_oracle_preserves_all_historical_values(self):
        import sqlite3
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('''
            CREATE TABLE ledger (identity TEXT PRIMARY KEY, charge, settlement BLOB);
            INSERT INTO ledger VALUES ('reserved', 100, NULL);
            INSERT INTO ledger VALUES ('settled', 20, X'0001');
            INSERT INTO ledger VALUES ('unknown', NULL, NULL);
            CREATE TABLE audit (identity TEXT, value TEXT);
            INSERT INTO audit VALUES ('entry', 'unchanged');
            INSERT INTO audit VALUES ('entry', 'unchanged');
            CREATE TABLE empty_history (identity TEXT);
        ''')
        original = connection.serialize()
        path = '.agentic-state/routing/ledger.sqlite'
        state = {path: original, '.agentic-state/reservations/run.json': b'{"id":"reserved","charge":100}',
                 '.agentic-state/operating/changes/audit.json': b'{"id":"entry","value":"unchanged"}'}
        # This oracle must not invoke upgrade code, even for a clean result.
        with patch('agentic.upgrade.apply_chain', side_effect=AssertionError('migration called')), \
                patch('agentic.upgrade._migrate_routing_ledger', side_effect=AssertionError('migration called')):
            historical = _historical_state(state)
            self.assertEqual([], _check_state_continuity(historical, set(state), state.__getitem__))
            for sql in ("DELETE FROM ledger WHERE identity='settled'",
                        "UPDATE ledger SET identity='replacement' WHERE identity='reserved'",
                        "UPDATE ledger SET charge=0 WHERE identity='unknown'",
                        "UPDATE ledger SET settlement=X'0002' WHERE identity='settled'",
                        "UPDATE ledger SET charge='100' WHERE identity='reserved'",
                        "DELETE FROM audit WHERE rowid=2",
                        "UPDATE audit SET rowid=3 WHERE rowid=1",
                        "ALTER TABLE empty_history RENAME COLUMN identity TO replacement"):
                with self.subTest(sql=sql):
                    connection.deserialize(original)
                    connection.execute(sql)
                    connection.commit()
                    changed = {**state, path: connection.serialize()}
                    self.assertNotEqual(original, changed[path])
                    self.assertTrue(_check_state_continuity(historical, set(state), changed.__getitem__))
            for changed_path in set(state) - {path}:
                changed = {**state, changed_path: b'{}'}
                self.assertTrue(_check_state_continuity(historical, set(state), changed.__getitem__))
            self.assertTrue(_check_state_continuity(None, set(state), state.__getitem__))
            self.assertTrue(_check_state_continuity({}, set(state), state.__getitem__))
            connection.deserialize(original)
            connection.execute('ALTER TABLE ledger ADD COLUMN additive TEXT')
            connection.commit()
            changed = {**state, path: connection.serialize()}
            self.assertEqual([], _check_state_continuity(historical, set(state), changed.__getitem__))

    def test_archive_nested_runtime_git_and_cache_names_are_evidence(self):
        for name in ('.venv', '.git', '__pycache__'):
            with self.subTest(name=name):
                receipt = self.root / '.agentic-state/archive/owner-backup' / name / 'owner-receipt.json'
                receipt.parent.mkdir(parents=True)
                receipt.write_bytes(b'synthetic archived evidence')
                before = inventory(self.root)
                receipt.unlink()
                result = compare(before, inventory(self.root), {})
                self.assertFalse(result['preserved'])
                self.assertIn(receipt.relative_to(self.root).as_posix(), result['changes'])

    def test_missing_permission_evidence_cannot_be_accepted_by_delta(self):
        before = inventory(self.root)
        after = deepcopy(before)
        before['files']['PROJECT_INSTRUCTIONS.md'].pop('permissions', None)
        after['files']['PROJECT_INSTRUCTIONS.md'].pop('permissions', None)
        self.assertFalse(compare(before, after, {})['preserved'])

    def test_unavailable_permission_capture_blocks_even_an_exact_delta(self):
        before = inventory(self.root)
        with patch('agentic.preservation_metadata._permissions', return_value={
                'verified': False, 'method': 'windows-owner-group-dacl-sddl',
                'diagnostic': 'permission capture unavailable'}):
            after = inventory(self.root)
        unapproved = compare(before, after, {})
        accepted = compare(before, after, unapproved['changes'])
        self.assertFalse(accepted['preserved'])
        self.assertTrue(any('permissions unverified' in item for item in accepted['violations']))

    def test_only_actual_git_and_runtime_locations_are_excluded(self):
        for relative in ('.git/objects/ignored', '.agentic/.venv/runtime'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'outside preservation inventory')
        report = inventory(self.root)
        self.assertEqual(['.agentic/.venv', '.git'], report['excluded_paths'])
        self.assertFalse(any(path.startswith(('.git/', '.agentic/.venv/')) for path in report['files']))

    def test_unreadable_archive_cannot_silently_skip_preservation(self):
        archive = self.root / '.agentic-state/archive'
        archive.mkdir(parents=True)
        (archive / 'owner-receipt.json').write_bytes(b'synthetic archived evidence')
        before = inventory(self.root)
        original_bytes = (archive / 'owner-receipt.json').read_bytes()
        original = os.scandir
        archive_identity = archive.resolve()
        attempts = []

        def deny_archive(path):
            if Path(path).resolve() == archive_identity:
                attempts.append(path)
                raise PermissionError(13, 'Synthetic enumeration refusal', str(archive_identity))
            return original(path)

        with patch('qualify_upgrade.os.scandir', side_effect=deny_archive):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                inventory(self.root)
        self.assertEqual(1, len(attempts))
        self.assertEqual(archive_identity, Path(attempts[0]).resolve())
        self.assertEqual(original_bytes, (archive / 'owner-receipt.json').read_bytes())
        self.assertEqual(before, inventory(self.root))

    @unittest.skipUnless(os.name == 'nt', 'Windows ownership/DACL regression')
    def test_windows_added_acl_entry_is_detected_without_disclosing_sids(self):
        import subprocess
        before = inventory(self.root)
        child = subprocess.run(['icacls', str(self.root / 'PROJECT_INSTRUCTIONS.md'),
                                '/grant', '*S-1-1-0:(R)'], capture_output=True)
        self.assertEqual(0, child.returncode, 'Disposable ACL mutation failed')
        after = inventory(self.root)
        self.assertFalse(compare(before, after, {})['preserved'])
        rendered = json.dumps(after)
        self.assertNotIn('S-1-', rendered)
        self.assertNotIn(str(self.root), rendered)

    def qualification_with_unit_children(self, mutation, *, omit_checks=False, budget_preserved=True, omit_ledger=False):
        # Mocked child receipts exercise orchestration only, never installed evidence.
        from types import SimpleNamespace
        sys.path.insert(0, str(ROOT / '.agentic/tests'))
        import upgrade_195_fixtures
        work = Path(self.temporary.name) / 'qualification'

        def child(command, **kwargs):
            if '--manifest-only' in command:
                (work / 'candidate/MANIFEST.json').write_bytes(b'unit candidate manifest')
            elif '--mode' in command:
                project = work / 'project'
                if command[command.index('--mode') + 1] == 'install':
                    (project / '.agentic').mkdir(parents=True)
                    (project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(self.config.read_bytes())
                    (project / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')
                else:
                    self.assertIn('--on-conflict', command)
                    self.assertEqual('backup', command[command.index('--on-conflict') + 1])
                    mutation(project)
            return SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')

        def comparison(before, after, policy, project, **kwargs):
            result = compare(before, after, {})
            if not omit_checks:
                result['checks'] = {name: 'PASS' for name in
                    ('source_managed_membership', 'historical_state_continuity', 'named_streams')}
            if not omit_ledger:
                result['migration'] = {'state_migrations': [{'path': '.agentic-state/routing/ledger.sqlite',
                    'action': 'migrated_schema'}], 'historical_continuity': {
                    'sqlite_paths': ['.agentic-state/routing/ledger.sqlite']}}
            return result

        import hashlib
        with patch('qualify_upgrade.source_identity', return_value={'commit': 'a' * 40, 'tree': 'b' * 40,
                'manifest_sha256': hashlib.sha256(b'unit candidate manifest').hexdigest()}), \
                patch('build_release.release_paths', return_value=['MANIFEST.json']), \
                patch('build_release.release_bytes', return_value=b'unit candidate manifest'), \
                patch('build_release.SOURCE_CONFIGS', {}), \
                patch('qualify_upgrade.verify_release'), \
                patch.object(upgrade_195_fixtures, 'materialize_published', return_value=ROOT), \
                patch('qualify_upgrade.run_bounded', side_effect=child), \
                patch('qualify_upgrade.prepare_upgrade', return_value={'unit_policy': True, 'retained_probe_paths': ['unit retained probe']}) as prepare, \
                patch('qualify_upgrade.compare_upgrade', side_effect=comparison) as preservation, \
                patch('qualify_upgrade._prepare_qualification_budget', return_value='unit budget baseline', create=True), \
                patch('qualify_upgrade._seed_qualification_state'), \
                patch('qualify_upgrade._qualify_budget_import', return_value={'preserved': budget_preserved}, create=True), \
                patch('qualify_upgrade.checked_bootstrap', return_value={'status': 'CONFIGURED'}):
            result = qualify(ROOT, work)
            prepare.assert_called_once()
            preservation.assert_called_once()
            self.assertEqual({'unit_policy': True, 'retained_probe_paths': ['unit retained probe']}, preservation.call_args.args[2])
            self.assertEqual(['unit retained probe'], result['retained_probe_paths'])
            return result

    def test_qualification_rejects_deleted_owner_instructions_after_valid_bootstrap(self):
        result = self.qualification_with_unit_children(
            lambda project: (project / 'PROJECT_INSTRUCTIONS.md').unlink())
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('preservation', result['failed_stage'])
        self.assertIn('PROJECT_INSTRUCTIONS.md', result['preservation']['changes'])

    def test_qualification_requires_executed_accepted_preservation(self):
        result = self.qualification_with_unit_children(lambda project: None)
        self.assertEqual('QUALIFIED_DISPOSABLE_UPGRADE', result['status'])
        self.assertTrue(result['preservation']['preserved'])
        self.assertEqual(result['before'], result['after'])

    def test_qualification_cannot_pass_missing_oracle_checks(self):
        result = self.qualification_with_unit_children(lambda project: None, omit_checks=True)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('preservation', result['failed_stage'])

    def test_qualification_cannot_pass_a_lost_budget_import(self):
        result = self.qualification_with_unit_children(lambda project: None, budget_preserved=False)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('budget_import', result['failed_stage'])

    def test_qualification_cannot_pass_unexercised_ledger_migration(self):
        result = self.qualification_with_unit_children(lambda project: None, omit_ledger=True)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('ledger_migration_coverage', result['failed_stage'])

    def test_qualification_cannot_pass_unverified_permissions(self):
        with patch('agentic.preservation_metadata._permissions', return_value={
                'verified': False, 'method': 'windows-owner-group-dacl-sddl'}):
            result = self.qualification_with_unit_children(lambda project: None)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('preservation', result['failed_stage'])

    def test_plan_refuses_a_mutating_installer(self):
        def mutate(*args, **kwargs):
            (self.root / 'unexpected').mkdir()
            return {'source_manifest_sha256': '0' * 64}
        with patch('qualify_upgrade.install', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'mutated'):
                plan(ROOT, self.root, '0' * 64)

    def test_report_cannot_write_inside_source_or_project_or_overwrite(self):
        for path in (self.root / 'report.json', ROOT / 'report.json'):
            with self.assertRaises(ValueError):
                write_report(path, {}, project=self.root)
            self.assertFalse(path.exists())
        other_source = Path(self.temporary.name) / 'other-source'
        other_source.mkdir()
        with self.assertRaises(ValueError):
            write_report(other_source / 'report.json', {}, project=self.root, source=other_source)
        output = Path(self.temporary.name) / 'external.json'
        write_report(output, {'status': 'NOT_QUALIFIED'}, project=self.root)
        with self.assertRaises(FileExistsError):
            write_report(output, {}, project=self.root)


if __name__ == '__main__':
    unittest.main()
