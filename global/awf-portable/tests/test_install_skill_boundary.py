"""Each portable rename checks its original source and vacant destination."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_install_skill as fixture

installer = fixture.installer


class PublicationTests(unittest.TestCase):
    setUp = fixture.InstallerTests.setUp
    pin_manifest = fixture.InstallerTests.pin_manifest
    run_install = fixture.InstallerTests.run_install
    old_skill = fixture.InstallerTests.old_skill

    def test_publication_keeps_its_parent_pinned_until_the_rename(self):
        parent = self.root / 'publication-parent'
        parent.mkdir()
        source = parent / 'stage'; source.mkdir()
        (source / 'awf').write_bytes(b'created stage')
        destination = parent / 'destination'
        parked = parent.with_name(parent.name + '-parked')
        publication = installer.Publication()
        try:
            expected = publication.identity(source)
            if os.name == 'nt':
                with self.assertRaises(PermissionError): parent.rename(parked)
                publication.replace(source, destination, expected)
                self.assertEqual(b'created stage', (destination / 'awf').read_bytes())
            else:
                parent.rename(parked); parent.mkdir()
                (parked / 'stage').rename(source)
                (parked / 'owner').write_bytes(b'owner bytes')
                with self.assertRaisesRegex(installer.InstallError, 'parent identity changed'):
                    publication.replace(source, destination, expected)
                self.assertEqual(b'created stage', (source / 'awf').read_bytes())
                self.assertEqual(b'owner bytes', (parked / 'owner').read_bytes())
                self.assertFalse(destination.exists())
        finally:
            publication.close()

    def replace_directory(self, target):
        if target.exists(): target.rename(target.with_name(target.name + '-original'))
        target.mkdir(); (target / 'owner').write_bytes(b'owner replacement')

    def test_backup_source_is_rechecked_after_last_inventory(self):
        self.old_skill()
        native = installer.inventory
        count = []
        def inventory(path):
            result = native(path)
            if Path(path) == self.dest:
                count.append(path)
                if len(count) == 3: self.replace_directory(self.dest)
            return result
        with patch.object(installer, 'inventory', inventory), self.assertRaises(installer.InstallError):
            self.run_install()
        self.assertEqual(3, len(count))
        self.assertEqual(b'owner replacement', (self.dest / 'owner').read_bytes())

    def test_backup_destination_occupancy_is_rechecked(self):
        self.old_skill()
        native = installer.inventory
        count, targets = [], []
        original_uuid = installer.uuid.uuid4
        backup_id = original_uuid().hex
        # Source verification can allocate no UUIDs; the install lock on POSIX
        # also allocates none. Observe the allocated backup at its real seam.
        def verify(root, *args, **kwargs):
            result = native_verify(root, *args, **kwargs)
            if Path(root).name.startswith('.awf-stage-'):
                count.append(root)
                if len(count) == 2:
                    self.backups.mkdir(exist_ok=True)
                    # Backup names use the third UUID allocated by install on
                    # Windows (the lock uses none); stage and backup are first/second.
                    target = self.backups / ('awf-1.5.0-' + backup_id)
                    target.mkdir(); (target / 'owner').write_bytes(b'owner replacement')
                    targets.append(target)
            return result
        native_verify = installer.verify_package
        # Capture allocations explicitly rather than depending on wall time.
        values = [__import__('uuid').UUID('1' * 32), __import__('uuid').UUID(backup_id)]
        with patch.object(installer.uuid, 'uuid4', side_effect=values), \
                patch.object(installer, 'verify_package', verify), self.assertRaises(installer.InstallError):
            self.run_install()
        self.assertEqual(1, len(targets))
        self.assertEqual(b'owner replacement', (targets[0] / 'owner').read_bytes())

    def test_stage_source_and_destination_are_rechecked_after_verification(self):
        for side in ('source', 'destination'):
            with self.subTest(side=side):
                # Each invocation has a distinct destination/old skill.
                self.dest = self.root / side / 'skills/awf'
                self.old_skill()
                native = installer.verify_package
                hits, count = [], []
                def verify(root, *args, **kwargs):
                    result = native(root, *args, **kwargs)
                    if Path(root).name.startswith('.awf-stage-'):
                        count.append(root)
                        if len(count) == 2:
                            target = Path(root) if side == 'source' else self.dest
                            self.replace_directory(target); hits.append(target)
                    return result
                with patch.object(installer, 'verify_package', verify), self.assertRaises(installer.InstallError):
                    self.run_install()
                self.assertEqual(1, len(hits))
                self.assertEqual(b'owner replacement', (hits[0] / 'owner').read_bytes())

    def test_restore_source_and_destination_are_rechecked_after_failed_publication(self):
        for side in ('source', 'destination', 'normal'):
            with self.subTest(side=side):
                self.dest = self.root / side / 'skills/awf'
                self.old_skill()
                before = installer.inventory(self.dest)
                native = os.replace
                hits = []
                def replace(source, destination, *args, **kwargs):
                    if Path(source).name.startswith('.awf-stage-'):
                        if side != 'normal':
                            target = next(self.backups.glob('awf-*')) if side == 'source' else self.dest
                            self.replace_directory(target); hits.append(target)
                        raise OSError('synthetic publication failure')
                    return native(source, destination, *args, **kwargs)
                expected = OSError if side == 'normal' else installer.InstallError
                # Previous subcases keep their backups for evidence. Use a
                # separate root so source substitution selects this transaction.
                self.backups = self.root / (side + '-backups')
                with patch.object(os, 'replace', replace), self.assertRaises(expected):
                    self.run_install()
                if side == 'normal':
                    self.assertEqual(before, installer.inventory(self.dest))
                else:
                    self.assertEqual(1, len(hits))
                    self.assertTrue(hits[0].exists())
                    self.assertEqual(b'owner replacement', (hits[0] / 'owner').read_bytes())


if __name__ == '__main__':
    unittest.main()
