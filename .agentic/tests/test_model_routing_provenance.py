"""Adversarial routing provenance and review-route policy regressions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.model_routing import (RoutingLedger, default_policy,
                                   require_reviewer_route,
                                   require_routing_switch_provenance,
                                   review_route_requires_fresh_review)


def provenance(**updates):
    record = {
        "run_id": "run-1",
        "role": "writer",
        "ticket": "AWF-80",
        "pr": 134,
        "head": "0123456789abcdef0123456789abcdef01234567",
        "tier": 3,
        "escalation_step": 0,
        "requested_model": "gpt-5.6-sol",
        "requested_effort": "high",
        "observed_model": "gpt-5.6-sol",
        "observed_effort": "high",
        "codex_cli_version": "codex-cli 0.153.0",
        "tokens": "unknown",
        "outcome": "PASS",
    }
    record.update(updates)
    return record


class ModelRoutingProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy()
        self.temp = tempfile.TemporaryDirectory(prefix="awf-routing-provenance-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "routing.sqlite"
        self.ledger = RoutingLedger(self.path)

    def test_every_run_kind_records_requested_observed_cli_and_unknown_tokens(self):
        for index, run_kind in enumerate(("writer", "critic", "design", "spec_review", "specialist")):
            recorded = self.ledger.record_run_provenance(
                "project-1", provenance(run_id=f"run-{index}", role=run_kind))
            self.assertEqual(recorded["role"], run_kind)
            self.assertEqual(recorded["requested_model"], "gpt-5.6-sol")
            self.assertEqual(recorded["observed_effort"], "high")
            self.assertEqual(recorded["codex_cli_version"], "codex-cli 0.153.0")
            self.assertEqual(recorded["tokens"], "unknown")
            self.assertNotEqual(recorded["tokens"], 0)
        history = self.ledger.provenance_history("project-1", "AWF-80")
        self.assertEqual([row["role"] for row in history["runs"]],
                         ["writer", "critic", "design", "spec_review", "specialist"])

    def test_cli_version_is_caller_supplied_and_missing_or_blank_fails_closed(self):
        for value in (None, "", "   "):
            record = provenance()
            if value is None:
                del record["codex_cli_version"]
            else:
                record["codex_cli_version"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValidationError, "codex_cli_version"):
                self.ledger.record_run_provenance("project-1", record)

    def test_observation_and_token_unknown_literals_are_preserved_not_coerced(self):
        recorded = self.ledger.record_run_provenance(
            "project-1", provenance(observed_model="unobserved", observed_effort="unobserved"))
        self.assertEqual(recorded["observed_model"], "unobserved")
        self.assertEqual(recorded["observed_effort"], "unobserved")
        self.assertEqual(recorded["tokens"], "unknown")
        with self.assertRaisesRegex(ValidationError, "tokens"):
            self.ledger.record_run_provenance("project-1", provenance(run_id="missing-tokens", tokens=None))

    def test_section_8_1_binding_fields_are_mandatory(self):
        for field in ("role", "ticket", "pr", "head", "tier", "escalation_step", "outcome"):
            record = provenance(run_id="missing-" + field)
            del record[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, field):
                self.ledger.record_run_provenance("project-1", record)

    def test_r1_reviewer_cannot_be_weaker_and_equal_route_is_allowed(self):
        writer = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
        self.assertEqual(require_reviewer_route(self.policy, writer, deepcopy(writer)), writer)
        for reviewer in ({"model": "gpt-5.6-terra", "reasoning_effort": "high"},
                         {"model": "gpt-5.6-sol", "reasoning_effort": "medium"}):
            with self.subTest(reviewer=reviewer), self.assertRaisesRegex(ValidationError, "weaker"):
                require_reviewer_route(self.policy, writer, reviewer)

    def test_r1_unknown_models_and_efforts_fail_closed(self):
        known = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
        for writer, reviewer in (({"model": "future-model", "reasoning_effort": "high"}, known),
                                 (known, {"model": "gpt-5.6-sol", "reasoning_effort": "future-effort"})):
            with self.subTest(writer=writer, reviewer=reviewer), self.assertRaises(ValidationError):
                require_reviewer_route(self.policy, writer, reviewer)

    def test_r2_model_or_effort_change_requires_full_fresh_review(self):
        prior = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
        self.assertFalse(review_route_requires_fresh_review(self.policy, prior, deepcopy(prior)))
        self.assertTrue(review_route_requires_fresh_review(
            self.policy, prior, {"model": "gpt-6-sol", "reasoning_effort": "high"}))
        self.assertTrue(review_route_requires_fresh_review(
            self.policy, prior, {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"}))

    def test_r3_route_switch_refuses_absent_unrecorded_or_mismatched_provenance(self):
        prior = {"model": "gpt-5.6-luna", "reasoning_effort": "medium"}
        new = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
        with self.assertRaisesRegex(ValidationError, "provenance"):
            require_routing_switch_provenance(self.ledger, self.policy, prior, new, None)
        with self.assertRaisesRegex(ValidationError, "ledger"):
            require_routing_switch_provenance(self.ledger, self.policy, prior, new, provenance())
        forged = {
            **provenance(run_id="forged"),
            "project_id": "project-1",
            "recorded_at": "2026-10-13T06:30:00+00:00",
            "ledger_recorded": True,
        }
        forged["provenance_sha256"] = hashlib.sha256(json.dumps(
            forged, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaisesRegex(ValidationError, "present"):
            require_routing_switch_provenance(self.ledger, self.policy, prior, new, forged)
        recorded = self.ledger.record_run_provenance("project-1", provenance())
        self.assertTrue(require_routing_switch_provenance(
            self.ledger, self.policy, prior, new, recorded))
        self.assertTrue(self.ledger.require_routing_switch_provenance(
            "project-1", self.policy, prior, new, "run-1"))
        with self.assertRaisesRegex(ValidationError, "present"):
            self.ledger.require_routing_switch_provenance(
                "project-1", self.policy, prior, new, "absent-run")
        mismatch = self.ledger.record_run_provenance(
            "project-1", provenance(run_id="mismatch", requested_model="gpt-5.6-terra"))
        with self.assertRaisesRegex(ValidationError, "requested route"):
            require_routing_switch_provenance(
                self.ledger, self.policy, prior, new, mismatch)

    def test_existing_model_run_rows_remain_readable_after_provenance_migration(self):
        legacy = sqlite3.connect(self.path)
        try:
            legacy.execute("""INSERT INTO model_runs
              (run_id,project_id,ticket_id,role,agent_id,phase,created_at,day,policy_hash,
               status,reserved_tokens,reserved_cost,actual_tokens,actual_cost,escalated,request,route,outcome)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("legacy-run", "project-1", "LEGACY-1", "worker", "worker-1", "implementation",
               "2026-10-01T00:00:00Z", "2026-10-01", "legacy-policy", "reserved", 10,
               None, None, None, 0, json.dumps({"legacy": True}), json.dumps({"legacy": True}), None))
            legacy.commit()
        finally:
            legacy.close()
        reopened = RoutingLedger(self.path)
        summary = reopened.summary("project-1", "LEGACY-1")
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["tokens"], 10)
        self.assertEqual(reopened.provenance_history("project-1", "LEGACY-1")["runs"], [])


if __name__ == "__main__":
    unittest.main()
