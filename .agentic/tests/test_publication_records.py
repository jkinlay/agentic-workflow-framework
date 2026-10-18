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


class PublicationRecordRecoveryTests(unittest.TestCase):
    def test_partial_commit_seal_is_not_repaired(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            key = "publication/partial-seal"
            payload = canonical({"operation": "publish"})
            store.write(key, payload)
            artifacts = [p for p in root.rglob("*")
                         if p.is_file() and p != store.path_for(key)]
            self.assertTrue(artifacts, "commit evidence must be durable")
            for artifact in artifacts:
                artifact.write_bytes(artifact.read_bytes()[:1])
            broken = snapshot(root)
            restarted = publication.PublicationRecordStore(root)
            for operation in (restarted.read, restarted.recover):
                with self.assertRaises(ValidationError):
                    operation(key)
            with self.assertRaises(ValidationError):
                restarted.write(key, payload)
            self.assertEqual(snapshot(root), broken)

    def test_missing_record_recovery_does_not_create_artifacts(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            for operation in (store.read, store.recover):
                with self.assertRaises(ValidationError):
                    operation("publication/missing")
            self.assertEqual(list(root.iterdir()), [])

    def test_intact_pending_record_recovers_once(self):
        from agentic import publication

        class Crash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            store = publication.PublicationRecordStore(Path(directory))
            payload = canonical({"operation": "publish"})

            def crash(pending):
                raise Crash()

            with self.assertRaises(Crash):
                store.write("publication/intact", payload, before_commit=crash)
            with self.assertRaises(ValidationError):
                store.read("publication/intact")
            restarted = publication.PublicationRecordStore(Path(directory))
            recovered = restarted.recover("publication/intact")
            self.assertEqual(recovered["canonical_bytes"], payload)
            committed = snapshot(Path(directory))
            self.assertEqual(restarted.recover("publication/intact"), recovered)
            self.assertEqual(snapshot(Path(directory)), committed)

    def test_record_for_another_key_is_refused(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            store = publication.PublicationRecordStore(Path(directory))
            payload = canonical({"operation": "publish"})
            store.write("publication/one", payload)
            store.write("publication/two", payload)
            store.path_for("publication/two").write_bytes(
                store.path_for("publication/one").read_bytes())
            for operation in (store.read, store.recover):
                with self.assertRaises(ValidationError):
                    operation("publication/two")

    def test_noncanonical_payload_and_unsafe_keys_leave_no_artifacts(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            for payload in (b'{ "x": 1}', b'{"x":1,"x":2}', b'{', b'1.2', "text"):
                with self.assertRaises(ValidationError):
                    store.write("publication/invalid", payload)
            for key in ("../escape", "/absolute", "a//b", "a/./b", "a\\b", ""):
                with self.assertRaises(ValidationError):
                    store.write(key, canonical({"x": 1}))
            self.assertEqual(snapshot(root), {})

    def test_hook_corruption_cannot_commit_or_be_overwritten(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            payload = canonical({"operation": "publish"})

            def corrupt(pending):
                pending.write_bytes(b'{}')

            with self.assertRaises(ValidationError):
                store.write("publication/corrupt", payload, before_commit=corrupt)
            broken = snapshot(root)
            for operation in (store.read, store.recover):
                with self.assertRaises(ValidationError):
                    operation("publication/corrupt")
            with self.assertRaises(ValidationError):
                store.write("publication/corrupt", payload)
            self.assertEqual(snapshot(root), broken)

    def test_second_writer_cannot_replace_pending_record(self):
        from agentic import publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = publication.PublicationRecordStore(root)
            payload = canonical({"sequence": 1})

            def competing_write(pending):
                other = publication.PublicationRecordStore(root)
                before = snapshot(root)
                with self.assertRaises(ValidationError):
                    other.write("publication/race", canonical({"sequence": 2}))
                self.assertEqual(snapshot(root), before)

            result = store.write("publication/race", payload,
                                 before_commit=competing_write)
            self.assertEqual(store.read("publication/race"), result)


if __name__ == "__main__":
    unittest.main()
