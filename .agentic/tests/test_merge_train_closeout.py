import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.closeout import (close_batch_constituents,
                              ordinary_closeout_guard)


class MergeTrainCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-batch-closeout-")
        self.progress = Path(self.temporary.name) / "progress.json"
        self.batch_commit = hashlib.sha1(b"batch").hexdigest()
        self.calls = []
        self.statuses = {"AWF-1": "In Review", "AWF-2": "In Review"}
        self.record = {
            "format": "awf-merge-train-closeout-1",
            "batch_id": "1.9.5-20261017-1000",
            "generation": 1,
            "batch_branch": "batch/1.9.5-20261017-1000",
            "target_branch": "main",
            "batch_commit_sha": self.batch_commit,
            "controller_actor_id": "controller-1",
            "constituents": [
                {"ticket": "AWF-1", "pr_number": 101, "head_sha": "1" * 40},
                {"ticket": "AWF-2", "pr_number": 102, "head_sha": "2" * 40},
            ],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _jira(self, mismatch_ticket=None):
        def read(ticket):
            self.calls.append(("read", ticket))
            return {"ticket": ticket, "status": self.statuses[ticket],
                    "batch_id": (self.record["batch_id"]
                                 if self.statuses[ticket] == "Done" else None),
                    "merge_commit_sha": (self.batch_commit
                                         if self.statuses[ticket] == "Done" else None)}

        def write(ticket, transition):
            self.calls.append(("write", ticket, copy.deepcopy(transition)))
            self.statuses[ticket] = ("In Review" if ticket == mismatch_ticket
                                     else "Done")
            return {"status": "SUBMITTED"}

        return {"actor_id": "controller-1", "read": read, "write": write}

    def test_ordinary_closeout_refuses_batch_constituent(self):
        jira_calls = []
        constituent = {
            "closeout_kind": "merged-via-batch",
            "batch_id": self.record["batch_id"],
            "ticket": "AWF-1",
        }
        with self.assertRaisesRegex(ValidationError, "batch constituent"):
            ordinary_closeout_guard(constituent,
                                    before_jira=lambda: jira_calls.append("jira"))
        self.assertEqual([], jira_calls)

    def test_batch_closeout_waits_for_commit_on_main_then_marks_merged_via_batch(self):
        jira = self._jira()
        for observation in [
                None,
                {"target_branch": "main", "commit_sha": "f" * 40},
                {"target_branch": "release", "commit_sha": self.batch_commit}]:
            with self.subTest(observation=observation):
                with self.assertRaisesRegex(ValidationError, "exact batch commit"):
                    close_batch_constituents(
                        self.record, observe_target=lambda: observation,
                        jira=jira, progress_path=self.progress)
                self.assertEqual([], self.calls)

        result = close_batch_constituents(
            self.record,
            observe_target=lambda: {"target_branch": "main",
                                    "commit_sha": self.batch_commit},
            jira=jira, progress_path=self.progress)
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual(["AWF-1", "AWF-2"],
                         [item["ticket"] for item in result["closed"]])
        self.assertTrue(all(item["closeout_kind"] == "merged-via-batch"
                            for item in result["closed"]))
        self.assertEqual(["read", "write", "read", "read", "write", "read"],
                         [call[0] for call in self.calls])

        calls = len(self.calls)
        resumed = close_batch_constituents(
            self.record,
            observe_target=lambda: {"target_branch": "main",
                                    "commit_sha": self.batch_commit},
            jira=jira, progress_path=self.progress)
        self.assertEqual("COMPLETE", resumed["status"])
        self.assertEqual(calls, len(self.calls))

    def test_batch_closeout_stops_on_mismatch_and_resumes_remaining_constituents(self):
        stopped = close_batch_constituents(
            self.record,
            observe_target=lambda: {"target_branch": "main",
                                    "commit_sha": self.batch_commit},
            jira=self._jira(mismatch_ticket="AWF-1"),
            progress_path=self.progress)
        self.assertEqual("STOPPED_MISMATCH", stopped["status"])
        self.assertEqual("AWF-1", stopped["ticket"])
        self.assertNotIn("AWF-2", [call[1] for call in self.calls])

        # An external controller/operator resolves the mismatch.  Recovery may
        # re-read this ticket but must never reissue its uncertain write.
        self.statuses["AWF-1"] = "Done"
        completed = close_batch_constituents(
            self.record,
            observe_target=lambda: {"target_branch": "main",
                                    "commit_sha": self.batch_commit},
            jira=self._jira(), progress_path=self.progress)
        self.assertEqual("COMPLETE", completed["status"])
        self.assertEqual(1, len([call for call in self.calls
                                if call[0] == "write" and call[1] == "AWF-1"]))
        saved = json.loads(self.progress.read_text("utf-8"))
        self.assertEqual(["AWF-1", "AWF-2"], saved["completed_tickets"])

    def test_batch_closeout_refuses_non_controller_writer(self):
        jira = self._jira()
        jira["actor_id"] = "worker-1"
        with self.assertRaisesRegex(ValidationError, "controller"):
            close_batch_constituents(
                self.record,
                observe_target=lambda: {"target_branch": "main",
                                        "commit_sha": self.batch_commit},
                jira=jira, progress_path=self.progress)
        self.assertEqual([], self.calls)

    def test_batch_closeout_lost_write_response_is_observed_without_replay(self):
        self.record["constituents"] = self.record["constituents"][:1]
        writes = {"count": 0}

        def read(ticket):
            self.calls.append(("read", ticket))
            return {"ticket": ticket, "status": self.statuses[ticket],
                    "batch_id": (self.record["batch_id"]
                                 if self.statuses[ticket] == "Done" else None),
                    "merge_commit_sha": (self.batch_commit
                                         if self.statuses[ticket] == "Done" else None)}

        def lost_write(ticket, _transition):
            writes["count"] += 1
            self.statuses[ticket] = "Done"
            raise TimeoutError("response lost")

        jira = {"actor_id": "controller-1", "read": read,
                "write": lost_write}
        stopped = close_batch_constituents(
            self.record,
            observe_target=lambda: {"target_branch": "main",
                                    "commit_sha": self.batch_commit},
            jira=jira, progress_path=self.progress)
        self.assertEqual("STOPPED_UNKNOWN", stopped["status"])
        self.assertEqual(1, writes["count"])

        jira["write"] = lambda _ticket, _transition: self.fail(
            "unknown Jira write was replayed")
        completed = close_batch_constituents(
            self.record,
            observe_target=lambda: {"target_branch": "main",
                                    "commit_sha": self.batch_commit},
            jira=jira, progress_path=self.progress)
        self.assertEqual("COMPLETE", completed["status"])
        self.assertEqual(1, writes["count"])


if __name__ == "__main__":
    unittest.main()
