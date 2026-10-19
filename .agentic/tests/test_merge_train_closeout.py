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


class OperationalCloseoutTests(unittest.TestCase):
    def fixture(self):
        from test_merge_train_controller import OperationalControllerTests
        fixture = OperationalControllerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_operational_closeout_requires_authenticated_exact_main_commit(self):
        from unittest.mock import patch
        fixture = self.fixture()
        from agentic.continuous_controller import production_batch_closeout
        from test_merge_train_controller import JiraSpy
        for field, value in [('target_branch','other'), ('head','0'*40), ('tree','0'*40),
                ('base','0'*40), ('generation',2), ('repository_id',202), ('pr',99),
                ('merge_commit_sha','0'*40), ('observed_at','2020-01-01T00:00:00Z')]:
            spy = JiraSpy()
            inputs = fixture.operational_inputs(spy)
            fixture.merge_response[field] = value
            with self.assertRaises(ValidationError):
                production_batch_closeout(fixture.store, **inputs)
            self.assertEqual([], spy.calls)
        spy = JiraSpy()
        inputs = fixture.operational_inputs(spy)
        inputs['merge_observer_registry'] = {}
        with self.assertRaises(ValidationError):
            production_batch_closeout(fixture.store, **inputs)
        self.assertEqual([], spy.calls)
        for state in ['ABANDONED', 'DRAFT', 'READY_AUTHORIZED', 'CI_GREEN']:
            inputs = fixture.operational_inputs(spy)
            fixture.coordinator.candidate['state'] = state
            fixture.coordinator.events.clear()
            fixture.coordinator.append_audit(fixture.coordinator.candidate)
            with self.assertRaises(ValidationError):
                production_batch_closeout(fixture.store, **inputs)
            self.assertEqual([], spy.calls)

    def test_operational_closeout_uses_single_t11_writer_and_preserves_order(self):
        from unittest.mock import patch
        from agentic.continuous_controller import production_batch_closeout
        from test_merge_train_controller import JiraSpy
        fixture = self.fixture()
        spy = JiraSpy()
        inputs = fixture.operational_inputs(spy)
        fixture.coordinator.candidate['sources'].reverse()
        fixture.coordinator.events.clear()
        fixture.coordinator.append_audit(fixture.coordinator.candidate)
        with patch('agentic.closeout.close_batch_constituents', side_effect=AssertionError('independent writer')):
            result = production_batch_closeout(fixture.store, **inputs)
        self.assertEqual([('read','AWF-2'),('write','AWF-2'),('read_back','AWF-2'),
                          ('read','AWF-1'),('write','AWF-1'),('read_back','AWF-1')], spy.calls)
        self.assertEqual(['AWF-2','AWF-1'], [x['binding']['issue_id'] for x in result['constituents']])
        self.assertTrue(all(x['closeout_kind']=='merged-via-batch' for x in result['constituents']))
        self.assertTrue(result['jira']['complete'])
        self.assertFalse(result['count_allowed'])
        replay = JiraSpy()
        inputs.update(read_current_status=replay.read_current_status,
            write_transition=replay.write_transition, read_transition=replay.read_transition)
        production_batch_closeout(fixture.store, **inputs)
        self.assertEqual([], replay.calls)

    def test_batch_counting_refused_for_held_or_unknown_constituents(self):
        from agentic.closeout import require_jira_reconciliation_before_counts
        for status in ['HELD','UNKNOWN','PENDING','SUCCEEDED']:
            with self.assertRaisesRegex(ValidationError, 'batch'):
                require_jira_reconciliation_before_counts(
                    {'batch_id':'batch-1','closeout_kind':'merged-via-batch',
                     'status':status,'complete':status=='SUCCEEDED'},
                    repository=Path('.'),project_config={},contracts=None)
        # An assembled batch envelope is not an ordinary count record either.
        with self.assertRaisesRegex(ValidationError, 'batch'):
            require_jira_reconciliation_before_counts(
                {'batch_id':'batch-1','constituents':[], 'complete':True},
                repository=Path('.'),project_config={},contracts=None)


if __name__ == "__main__":
    unittest.main()
