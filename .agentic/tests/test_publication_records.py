"""Red contracts for durable publication records (IN-47 / B23)."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.canonical import canonical


# Open design points: record location, on-disk schema and public API are not
# specified yet. PublicationRecordStore is a proposed behaviour-facing seam:
# write/read/recover return canonical_bytes and sha256; path_for exposes the
# stored artifact for corruption testing, without fixing its name or schema.
# before_commit receives the pending artifact Path after writing and before
# commit, allowing deterministic interruption without killing any process.
# The store root is caller-selected, and all member keys below are POSIX paths.


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


class PublicationRecordTests(unittest.TestCase):
    def test_publication_record_is_append_only(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            key = "publication/record-1"
            payload = canonical({"operation": "publish", "sequence": 1})
            changed = canonical({"operation": "publish", "sequence": 2})
            first = store.write(key, payload)
            original = snapshot(root)
            self.assertTrue(original, "publication record must be durable")
            self.assertEqual(store.write(key, payload), first)
            self.assertEqual(snapshot(root), original)
            with self.assertRaises(ValidationError):
                store.write(key, changed)
            self.assertEqual(snapshot(root), original)
            with self.assertRaises(ValidationError):
                store.rewrite(key, changed)
            self.assertEqual(snapshot(root), original)
            with self.assertRaises(ValidationError):
                store.delete(key)
            self.assertEqual(snapshot(root), original)
            restarted = publication.PublicationRecordStore(root)
            self.assertEqual(restarted.read(key), first)
            self.assertEqual(restarted.read(key)["canonical_bytes"], payload)

    def test_publication_record_is_digest_bound(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            key = "publication/record-1"
            payload = canonical({"operation": "publish", "sequence": 1})
            written = store.write(key, payload)
            self.assertEqual(written["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(written["canonical_bytes"], payload)
            self.assertEqual(store.read(key), written)
            artifact = store.path_for(key)
            original = artifact.read_bytes()
            self.assertTrue(original)
            # Flip stored bytes directly; no serialization format is assumed.
            artifact.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            restarted = publication.PublicationRecordStore(root)
            with self.assertRaises(ValidationError):
                restarted.read(key)

    def test_publication_records_recover_after_crash(self):
        from agentic import publication

        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            key = "publication/record-1"
            payload = canonical({"operation": "publish", "sequence": 1})
            interrupted = []

            def crash_before_commit(pending):
                data = pending.read_bytes()
                self.assertGreater(len(data), 1)
                partial = data[:len(data) // 2]
                pending.write_bytes(partial)
                interrupted.append((pending, partial))
                raise SimulatedCrash()

            with self.assertRaises(SimulatedCrash):
                store.write(key, payload, before_commit=crash_before_commit)
            self.assertEqual(len(interrupted), 1)
            pending, partial = interrupted[0]
            self.assertEqual(pending.read_bytes(), partial)

            # Recovery is explicit in this proposed API. Both permitted outcomes
            # are checked across another restart: exact-once completion or
            # fail-closed refusal. Partial bytes must never become a valid read.
            restarted = publication.PublicationRecordStore(root)
            try:
                recovered = restarted.recover(key)
            except ValidationError:
                with self.assertRaises(ValidationError):
                    restarted.read(key)
                again = publication.PublicationRecordStore(root)
                with self.assertRaises(ValidationError):
                    again.recover(key)
                with self.assertRaises(ValidationError):
                    again.read(key)
            else:
                self.assertEqual(recovered["canonical_bytes"], payload)
                self.assertEqual(recovered["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(restarted.read(key), recovered)
                committed = snapshot(root)
                again = publication.PublicationRecordStore(root)
                self.assertEqual(again.recover(key), recovered)
                self.assertEqual(again.write(key, payload), recovered)
                self.assertEqual(again.read(key), recovered)
                self.assertEqual(snapshot(root), committed)


if __name__ == "__main__":
    unittest.main()
