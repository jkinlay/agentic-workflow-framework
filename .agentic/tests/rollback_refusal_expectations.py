"""Independent, exact inventories for the 1.9.6 refusal contract."""
import json
import os
from pathlib import Path
import stat
from contextlib import contextmanager
from unittest.mock import Mock, patch

from agentic import installer
from agentic.canonical import sha256


REFUSAL = 'Rollback refused before managed-file mutation; cleanup pending'
REASON = 'Pathname recursive deletion refused; object continuity cannot be guaranteed'
OWNER_ACTION = ('A later transaction may adopt directories it reuses; inspect unadopted '
                'paths and journal under exclusive owner control, preserve needed bytes '
                'and perform owner recovery for residue nobody adopts')


@contextmanager
def forbid_managed_mutation(root):
    """Detect even a write later undone, before the filesystem operation."""
    root = Path(root).resolve()
    journal = json.loads((root / installer.JOURNAL).read_bytes())
    protected = {row['path'] for row in journal['files']}
    original_write, original_unlink = installer.Tree.write, installer.Tree.unlink

    def write(tree, relative, *args, **kwargs):
        if tree.root.resolve() == root and relative in protected:
            raise AssertionError('Managed-file write before refusal: ' + relative)
        return original_write(tree, relative, *args, **kwargs)

    def unlink(tree, relative, *args, **kwargs):
        if tree.root.resolve() == root and relative in protected:
            raise AssertionError('Managed-file unlink before refusal: ' + relative)
        return original_unlink(tree, relative, *args, **kwargs)

    with patch.object(installer.Tree, 'write', write), patch.object(installer.Tree, 'unlink', unlink):
        yield


def inventory(root):
    """Bind membership, bytes, modes and object identities; never follow links."""
    root = Path(root)
    result = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            path = Path(directory) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            # The coordination lock is byte-locked while the builder runs on
            # Windows. Check its bytes after recovery releases it instead.
            if relative == installer.LOCK:
                continue
            identity = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))
            if path.is_symlink():
                result[relative] = ('link', identity, os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ('directory', identity)
            else:
                result[relative] = ('file', identity, path.read_bytes())
    return result


def cleanup_record(before, paths, *, status='RECOVERY_REFUSED', journal=None):
    rows = []
    for path in paths:
        entries = [value for name, value in before.items() if name.startswith(path + '/')]
        size = (None if any(value[0] == 'link' for value in entries) else
                sum(len(value[2]) for value in entries if value[0] == 'file'))
        rows.append(dict(path=path, size_bytes=size, reason=REASON, owner_action=OWNER_ACTION))
    record = dict(format='awf-retained-cleanup-1', status=status,
                  retained_directories=paths, cleanup_pending=rows)
    if journal is not None:
        record.update(format='awf-retained-cleanup-2', journal=journal)
    return record


def assert_retained(test, root, before, paths, *, journal=None, coordination_changed=False):
    """Only diagnostics may change; every runtime/directory/managed byte survives."""
    record = cleanup_record(before, paths)
    test.assertEqual(installer.json_bytes(record), (root / installer.RETAINED_JOURNAL).read_bytes())
    after = inventory(root)
    # Match install_lock's platform branch, including POSIX's empty lock.
    expected_lock = b'0' if installer.os.name == 'nt' else b''
    test.assertEqual(expected_lock, (root / installer.LOCK).read_bytes())
    ignored = {installer.RETAINED_JOURNAL}
    if coordination_changed:
        ignored.update((installer.JOURNAL, installer.MARKER))
    test.assertEqual({p: v for p, v in before.items() if p not in ignored},
                     {p: v for p, v in after.items() if p not in ignored})
    expected = {key: value for key, value in record.items() if key != 'format'}
    test.assertEqual(expected, installer.retained_directory_status(root))
    if journal is not None:
        test.assertEqual('active', journal['phase'])
        test.assertEqual(journal, json.loads((root / installer.JOURNAL).read_bytes()))
        marker = json.loads((root / installer.MARKER).read_bytes())
        test.assertEqual(installer._journal_marker(journal['transaction_id'],
                         sha256(installer.json_bytes(journal))), marker)
    from agentic.providers.github_status import project_status, render_status
    report = project_status(root, gh=Mock())
    test.assertEqual(expected, report['installation_recovery'])
    rendered = render_status(report)
    for path in paths:
        test.assertIn('Cleanup pending: ' + path, rendered)
    return expected
