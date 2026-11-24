"""AWF-312: real Windows junctions cannot redirect protected state."""
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import ValidationError, publication
from agentic.budgeting import ControllerBudgeting
from agentic.continuous_controller import (ContinuousControllerStore, jira_assignment_state,
                                           prune_production_planning_state)
from agentic.controller_state import protected_state_path
from agentic.handoff import _completed_tickets
from agentic.owner_publication import OwnerPublicationStore
from agentic.path_containment import is_reparse_point
from agentic.reporting.observations import read_database
from agentic.review_completion import ReviewCompletionStore
from agentic.status_report_store import ReportStore
from agentic.store import Store

PROJECT = "00000000-0000-0000-0000-000000000312"


@contextmanager
def without_path_is_junction():
    """Exercise Python 3.11's actual missing API, rather than a False return."""
    originals = [(cls, cls.__dict__["is_junction"]) for cls in Path.__mro__
                 if "is_junction" in cls.__dict__]
    for owner, _ in originals:
        delattr(owner, "is_junction")
    try:
        yield
    finally:
        for owner, original in originals:
            setattr(owner, "is_junction", original)


@unittest.skipUnless(os.name == "nt", "real Windows directory junction required")
class JunctionStateTests(unittest.TestCase):
    def setUp(self):
        import _winapi

        temporary = tempfile.TemporaryDirectory(prefix="awf312-junction-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.target = self.root / "target"
        self.target.mkdir()
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.junction = self.root / "junction"
        _winapi.CreateJunction(str(self.target), str(self.junction))
        self.addCleanup(os.rmdir, self.junction)
        self.assertTrue(is_reparse_point(self.junction))
        self.assertFalse(self.junction.is_symlink())
        self.assertEqual(self.target, self.junction.resolve())

    def modes(self):
        return (("native", nullcontext), ("python311_absent_api", without_path_is_junction))

    def test_controller_state_rejects_junction_at_every_depth(self):
        for mode, context in self.modes():
            for tail in ((), ("state.sqlite3",), ("missing", "state.sqlite3")):
                with self.subTest(mode=mode, tail=tail), context():
                    if mode == "python311_absent_api":
                        self.assertFalse(hasattr(Path, "is_junction"))
                    with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                        protected_state_path(self.junction.joinpath(*tail), [self.worktree])
                    self.assertEqual([], list(self.target.iterdir()))

    def test_budget_recovery_rejects_junction(self):
        actual = self.target / "recovery.sqlite3"
        sqlite3.connect(actual).close()
        budgeting = object.__new__(ControllerBudgeting)
        budgeting.database = SimpleNamespace(path=self.junction / actual.name,
                                             worktree_roots=[self.worktree])
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                    database = budgeting._recovery_database()
                    if database is not None:
                        database.close()

    def test_handoff_rejects_junction(self):
        actual = self.target / "coordinator.sqlite3"
        Store(actual, PROJECT, [self.worktree])
        self.assertEqual([], _completed_tickets(actual, PROJECT, self.worktree))
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                self.assertIsNone(_completed_tickets(self.junction / actual.name,
                                                    PROJECT, self.worktree))

    def test_coordinator_store_rejects_junction(self):
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                    Store(self.junction / "coordinator.sqlite3", PROJECT, [self.worktree])
                self.assertEqual([], list(self.target.iterdir()))

    def test_report_store_rejects_junction(self):
        actual = self.target / "coordinator.sqlite3"
        Store(actual, PROJECT, [self.worktree])
        ReportStore(actual, PROJECT, [self.worktree])
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                    ReportStore(self.junction / actual.name, PROJECT, [self.worktree])

    def test_shared_controller_ledger_callers_reject_junction(self):
        constructors = (
            ("continuous", lambda p: ContinuousControllerStore(p, ["A"], worktree_roots=[self.worktree])),
            ("owner", lambda p: OwnerPublicationStore(p, worktree_roots=[self.worktree])),
            ("review", lambda p: ReviewCompletionStore(p, worktree_roots=[self.worktree])),
        )
        for mode, context in self.modes():
            for name, constructor in constructors:
                with self.subTest(mode=mode, caller=name), context():
                    with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                        constructor(self.junction / (name + ".sqlite3"))
                    self.assertEqual([], list(self.target.iterdir()))

    def test_reporting_observations_reject_junction(self):
        actual = self.target / "report.sqlite3"
        sqlite3.connect(actual).close()
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                    with read_database(self.junction / actual.name, [self.worktree]):
                        pass


@unittest.skipUnless(os.name == "nt", "real Windows directory junction required")
class MissingTargetJunctionTests(unittest.TestCase):
    """A junction whose target is gone is still a link, not a missing ancestor."""

    def setUp(self):
        import _winapi

        temporary = tempfile.TemporaryDirectory(prefix="awf312-dangling-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.target = self.root / "deleted-target"
        self.target.mkdir()
        self.junction = self.root / "ab"
        _winapi.CreateJunction(str(self.target), str(self.junction))
        self.addCleanup(os.rmdir, self.junction)
        self.target.rmdir()
        self.assertTrue(is_reparse_point(self.junction))
        self.assertFalse(self.junction.exists())

    def modes(self):
        return (("native", nullcontext), ("python311_absent_api", without_path_is_junction))

    def assert_refused(self, operation, message="link/reparse point"):
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                if mode == "python311_absent_api":
                    self.assertFalse(hasattr(Path, "is_junction"))
                with self.assertRaisesRegex(ValidationError, message):
                    operation()
                self.assertFalse(self.target.exists(), "the deleted target was recreated")

    def test_controller_state_rejects_missing_target_at_every_depth(self):
        for tail in ((), ("state.sqlite3",), ("missing", "state.sqlite3")):
            with self.subTest(tail=tail):
                self.assert_refused(lambda: protected_state_path(
                    self.junction.joinpath(*tail), [self.worktree]))

    def test_coordinator_store_rejects_missing_target(self):
        self.assert_refused(lambda: Store(self.junction / "state.sqlite3", PROJECT, [self.worktree]))

    def test_report_store_rejects_missing_target(self):
        self.assert_refused(lambda: ReportStore(
            self.junction / "state.sqlite3", PROJECT, [self.worktree]))

    def test_budget_recovery_rejects_missing_target(self):
        budgeting = object.__new__(ControllerBudgeting)
        budgeting.database = SimpleNamespace(path=self.junction / "recovery.sqlite3",
                                             worktree_roots=[self.worktree])
        self.assert_refused(budgeting._recovery_database)

    def test_handoff_missing_target_is_unavailable(self):
        for mode, context in self.modes():
            with self.subTest(mode=mode), context():
                self.assertIsNone(_completed_tickets(
                    self.junction / "state.sqlite3", PROJECT, self.worktree))

    def test_shared_controller_ledger_callers_reject_missing_target(self):
        state = self.junction / "state.sqlite3"
        reopened = object.__new__(ContinuousControllerStore)
        reopened.path, reopened.worktree_roots = state, ()
        for name, operation in (
                ("continuous", lambda: ContinuousControllerStore(
                    state, ["A"], worktree_roots=[self.worktree])),
                ("assignment", lambda: jira_assignment_state(reopened)),
                ("owner", lambda: OwnerPublicationStore(state, worktree_roots=[self.worktree])),
                ("review", lambda: ReviewCompletionStore(state, worktree_roots=[self.worktree]))):
            with self.subTest(caller=name):
                self.assert_refused(operation)

    def test_existing_file_callers_still_refuse_missing_target(self):
        # These callers require an existing regular file before the shared check.
        state = self.junction / "state.sqlite3"

        def observe():
            with read_database(state, [self.worktree]):
                pass
        self.assert_refused(lambda: prune_production_planning_state(state),
                            "requires an existing controller database")
        self.assert_refused(observe, "REPORT_STATE_UNAVAILABLE")

    def test_publication_mapping_rejects_missing_target(self):
        mapping = self.root / "mapping.yaml"
        mapping.write_text("{}", encoding="utf-8")
        with patch.object(publication, "_git", return_value=SimpleNamespace(stdout=b"")):
            # Windows collapses ".." lexically, so strict resolution still succeeds.
            self.assert_refused(lambda: publication._require_operator_local_mapping(
                self.worktree, self.junction / ".." / mapping.name), "must not traverse")
            publication._require_operator_local_mapping(
                self.worktree, self.root / "never-created" / ".." / mapping.name)

    def test_publication_rewrite_file_rejects_missing_target(self):
        self.assert_refused(lambda: publication._file_bytes(self.junction),
                            "canonical regular file")

    def test_publication_fanout_rejects_missing_target(self):
        self.assert_refused(lambda: publication._loose_path(
            self.root, "ab" + "0" * 38, "sha1"), "fanout directory is aliased")


class UninspectableComponentTests(unittest.TestCase):
    """Missing, file and denied ancestors keep each site's 1.9.6 final outcome."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf312-components-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.regular = self.root / "regular"
        self.regular.write_bytes(b"file")

    def budgeting(self, path):
        value = object.__new__(ControllerBudgeting)
        value.database = SimpleNamespace(path=path, worktree_roots=[self.worktree])
        return value

    def test_missing_ancestors_are_still_allowed(self):
        state = self.root / "one" / "two" / "state.sqlite3"
        self.assertEqual(state, protected_state_path(state, [self.worktree]))
        self.assertTrue(state.parent.is_dir())
        stored = self.root / "three" / "four" / "coordinator.sqlite3"
        Store(stored, PROJECT, [self.worktree])
        self.assertTrue(stored.is_file())
        absent = self.root / "absent" / "state.sqlite3"
        self.assertIsNone(self.budgeting(absent)._recovery_database())
        self.assertIsNone(_completed_tickets(absent, PROJECT, self.worktree))
        with self.assertRaisesRegex(ValidationError, "Existing report store required"):
            ReportStore(absent, PROJECT, [self.worktree])

    def test_regular_file_ancestor_outcomes_are_unchanged(self):
        for tail in (("state.sqlite3",), ("missing", "state.sqlite3")):
            state = self.regular.joinpath(*tail)
            with self.subTest(tail=tail):
                with self.assertRaises(OSError):
                    protected_state_path(state, [self.worktree])
                with self.assertRaises(OSError):
                    Store(state, PROJECT, [self.worktree])
                self.assertIsNone(self.budgeting(state)._recovery_database())
                self.assertIsNone(_completed_tickets(state, PROJECT, self.worktree))
                self.assertIsNone(publication._file_bytes(state))
                with self.assertRaisesRegex(ValidationError, "Existing report store required"):
                    ReportStore(state, PROJECT, [self.worktree])

    def test_not_a_directory_component_is_not_a_link(self):
        # POSIX reports ENOTDIR here; Windows reports a missing path.
        with patch("agentic.controller_state.is_reparse_point",
                   side_effect=NotADirectoryError(20, "not a directory")):
            state = self.regular / "state.sqlite3"
            self.assertIsNone(self.budgeting(state)._recovery_database())
            with self.assertRaises(OSError):
                protected_state_path(state, [self.worktree])
            with self.assertRaisesRegex(ValidationError, "Existing report store required"):
                ReportStore(state, PROJECT, [self.worktree])

    def test_denied_component_never_becomes_acceptance(self):
        actual = self.root / "coordinator.sqlite3"
        Store(actual, PROJECT, [self.worktree])
        with patch("agentic.controller_state.is_reparse_point",
                   side_effect=PermissionError(13, "denied")):
            for operation in (lambda: protected_state_path(actual, [self.worktree]),
                              lambda: Store(actual, PROJECT, [self.worktree]),
                              lambda: ReportStore(actual, PROJECT, [self.worktree]),
                              self.budgeting(actual)._recovery_database):
                with self.assertRaises(PermissionError):
                    operation()
            self.assertIsNone(_completed_tickets(actual, PROJECT, self.worktree))

    def test_other_uninspectable_component_uses_the_existing_refusal(self):
        actual = self.root / "coordinator.sqlite3"
        Store(actual, PROJECT, [self.worktree])
        for error in (OSError(40, "link loop"), ValueError("unrepresentable")):
            with self.subTest(error=type(error).__name__), patch(
                    "agentic.controller_state.is_reparse_point", side_effect=error):
                for operation in (lambda: protected_state_path(actual, [self.worktree]),
                                  lambda: Store(actual, PROJECT, [self.worktree]),
                                  lambda: ReportStore(actual, PROJECT, [self.worktree]),
                                  self.budgeting(actual)._recovery_database):
                    with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                        operation()
                self.assertIsNone(_completed_tickets(actual, PROJECT, self.worktree))

    def test_dangling_link_ancestor_is_refused(self):
        # The native unprivileged directory link: a junction or a POSIX symlink.
        link = self.root / "dangling"
        if os.name == "nt":
            import _winapi
            (self.root / "gone").mkdir()
            _winapi.CreateJunction(str(self.root / "gone"), str(link))
            self.addCleanup(os.rmdir, link)
            (self.root / "gone").rmdir()
        else:
            link.symlink_to(self.root / "gone", target_is_directory=True)
        state = link / "state.sqlite3"
        for operation in (lambda: protected_state_path(state, [self.worktree]),
                          lambda: Store(state, PROJECT, [self.worktree]),
                          lambda: ReportStore(state, PROJECT, [self.worktree]),
                          self.budgeting(state)._recovery_database):
            with self.assertRaisesRegex(ValidationError, "link/reparse point"):
                operation()
        self.assertIsNone(_completed_tickets(state, PROJECT, self.worktree))
        self.assertFalse((self.root / "gone").exists())


if __name__ == "__main__":
    unittest.main()
