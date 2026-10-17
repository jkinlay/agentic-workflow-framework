"""AWF-54B T11: controller-only batch Jira reconciliation (red contract)."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
NOW = "2026-10-17T16:00:00Z"


class JiraSpy:
    """In-memory provider callbacks; never import or contact a Jira client."""

    def __init__(self, mismatch=None):
        self.calls = []
        self.mismatch = mismatch
        self.writes = {}

    def read_current_status(self, binding):
        ticket = binding["issue_id"]
        self.calls.append(("read", ticket))
        return {"issue_id": ticket, "status_id": "In Review", "observed_at": NOW}

    def write_transition(self, record):
        ticket = record["binding"]["issue_id"]
        self.calls.append(("write", ticket))
        self.writes[ticket] = deepcopy(record)
        return {"issue_id": ticket, "status": "ATTEMPTED", "observed_at": NOW}

    def read_transition(self, record, operation):
        ticket = record["binding"]["issue_id"]
        self.calls.append(("read_back", ticket))
        return {
            "issue_id": ticket,
            "status": "In Progress" if ticket == self.mismatch else "Done",
            "actor": "external-automation" if ticket == self.mismatch else "controller-1",
            "observed_at": NOW,
        }


class MergeTrainControllerTests(unittest.TestCase):
    def test_only_controller_reconciles_constituent_jira(self):
        # Planned production entry point: resolve inside the test so standalone
        # discovery works on main and absence is a behavior-specific red error.
        from agentic import continuous_controller

        reconcile = continuous_controller.production_batch_jira_reconciliation

        # Q5 is answered by the owner: exact commit observed on main, merge
        # order, stop at mismatch, retain progress and resume without replay.
        # Doc 47's audit schema/location/retention and batch lifecycle fields
        # remain open owner questions. The dictionaries below describe only
        # T11's proposed Python seam, not a new canonical record schema.
        config = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text("utf-8"))
        config["jira"].update({
            "enabled": True,
            "cloud_id": "cloud-1",
            "site": "https://jira.example.invalid",
            "provider_project_id": "project-1",
            "project_key": "AWF",
            "controller_actor_id": "controller-1",
        })
        # Deliberately neither ticket nor PR sorted: preserve batch merge order.
        tickets = ["AWF-303", "AWF-101", "AWF-202"]
        constituents = [
            {
                "binding": {"issue_id": ticket},
                "pr_number": number,
                "reviewed_head_sha": digit * 40,
                "paths": [f"src/member-{number}.txt"],
                "closeout_kind": "merged-via-batch",
                "batch_id": "batch-1",
                "merge_commit_sha": "d" * 40,
            }
            for ticket, number, digit in zip(tickets, [33, 11, 22], "abc")
        ]
        batch = {
            "batch_id": "batch-1", "pr_number": 44,
            "merge_commit_sha": "d" * 40,
            "constituents": constituents,
        }
        observed_merge = {
            "batch_id": "batch-1", "target_branch": "main",
            "merge_commit_sha": "d" * 40, "observed_at": NOW,
        }

        def expected_calls(selected):
            return [(operation, ticket) for ticket in selected
                    for operation in ("read", "write", "read_back")]

        with tempfile.TemporaryDirectory(prefix="awf-batch-jira-") as temporary:
            external = Path(temporary)
            project = external / "project"
            project.mkdir()
            for member in constituents:
                path = project / member["paths"][0]
                path.parent.mkdir(exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")

            def store(name):
                # Controller state stays outside the candidate worktree.
                return continuous_controller.ContinuousControllerStore(
                    external / f"{name}.sqlite", ["B"], worktree_roots=(project,))

            def run(state, spy, *, role="controller", enabled=True):
                selected_config = deepcopy(config)
                selected_config["jira"]["enabled"] = enabled
                return reconcile(
                    state, config=selected_config, role=role,
                    batch=deepcopy(batch), observed_merge=deepcopy(observed_merge),
                    now=NOW, read_current_status=spy.read_current_status,
                    write_transition=spy.write_transition,
                    read_transition=spy.read_transition,
                )

            # Disabled Jira forbids all issue access through every role.
            for role in ("worker", "steward", "controller"):
                with self.subTest(enabled=False, role=role):
                    spy = JiraSpy()
                    run(store(f"disabled-{role}"), spy, role=role, enabled=False)
                    self.assertEqual([], spy.calls)
            for role in ("worker", "steward"):
                with self.subTest(enabled=True, role=role):
                    spy = JiraSpy()
                    run(store(role), spy, role=role)
                    self.assertEqual([], spy.calls)

            with self.subTest(enabled=True, role="controller", outcome="success"):
                spy = JiraSpy()
                result = run(store("success"), spy)
                self.assertEqual(expected_calls(tickets), spy.calls)
                self.assertEqual(tickets, result["reconciled"])
                self.assertEqual([], result["remaining"])
                for member in constituents:
                    record = spy.writes[member["binding"]["issue_id"]]
                    self.assertEqual("JIRA_RECONCILED", record["lifecycle_event"])
                    self.assertEqual("Done", record["to_status_id"])
                    self.assertEqual("merged-via-batch", record["closeout_kind"])
                    for key in ("batch_id", "merge_commit_sha", "pr_number", "reviewed_head_sha"):
                        self.assertEqual(member[key], record[key])
                # Reopen durable state: successful constituents cannot be replayed.
                replay = JiraSpy()
                repeated = run(store("success"), replay)
                self.assertEqual([], replay.calls)
                self.assertEqual(tickets, repeated["reconciled"])

            with self.subTest(enabled=True, role="controller", outcome="mismatch"):
                spy = JiraSpy(mismatch=tickets[1])
                result = run(store("partial"), spy)
                self.assertEqual(expected_calls(tickets[:2]), spy.calls)
                self.assertEqual(tickets[:1], result["reconciled"])
                self.assertEqual(tickets[2:], result["remaining"])
                self.assertEqual(tickets[1], result["conflict"]["issue_id"])
                self.assertEqual("In Progress", result["conflict"]["observed_status"])
                self.assertEqual("external-automation", result["conflict"]["observed_actor"])
                self.assertEqual(NOW, result["conflict"]["observed_at"])
                # Resume remaining constituents after restart. The mismatched
                # ticket stays stopped; neither it nor completed work is retried.
                resumed_spy = JiraSpy()
                resumed = run(store("partial"), resumed_spy)
                self.assertEqual(expected_calls(tickets[2:]), resumed_spy.calls)
                self.assertEqual([tickets[0], tickets[2]], resumed["reconciled"])
                self.assertEqual([], resumed["remaining"])
                self.assertEqual(result["conflict"], resumed["conflict"])
                final_spy = JiraSpy()
                run(store("partial"), final_spy)
                self.assertEqual([], final_spy.calls)


if __name__ == "__main__":
    unittest.main()
