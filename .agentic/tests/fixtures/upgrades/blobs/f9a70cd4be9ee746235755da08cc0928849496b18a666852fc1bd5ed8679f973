"""Offline operating choices, deterministic recommendations and interrupted writes."""
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic import operating as op
from agentic.canonical import canonical, load, sha256
from agentic.contracts import Contracts
from agentic.model_routing import default_policy


def governance():
    value = load(ROOT / ".agentic/examples/unconfigured-project.yaml")
    value["execution"]["max_parallel_tickets"] = 6
    value["execution"]["independent_reviewers"].pop("count", None)
    value["execution"]["model_routing"] = default_policy()
    for role in ("worker", "critic", "controller", "specialist"):
        value["execution"]["roles"][role]["approved_model_ids"] = list(default_policy()["models"])
    return value


def epic(number, scope, **extra):
    return {"id": "FIX-" + str(number), "title": "Bounded component " + str(number), "scope": scope, **extra}


def epic_bytes(epics):
    return canonical({"schema_version": 1, "epics": epics})


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.gov = governance()

    def refuse(self, value, path, binding):
        with self.assertRaises(op.OperatingError) as caught:
            op.validate_operating(value, self.gov)
        self.assertTrue(any(r["path"] == path and r["governance_path"] == binding for r in caught.exception.refusals), caught.exception.refusals)

    def test_exact_balanced_defaults_and_separate_fingerprint(self):
        value = op.default_operating()
        self.assertEqual(3, value["streams"]["count"])
        self.assertEqual({"count", "A", "B", "C"}, set(value["streams"]))
        snapshot = op.validate_operating(value, self.gov)
        current = canonical(self.gov)
        value["streams"]["count"] = 2
        self.assertNotEqual(snapshot.operating_hash, op.validate_operating(value, self.gov).operating_hash)
        self.assertEqual(current, canonical(self.gov))
        copied = snapshot.config
        copied["streams"]["count"] = 1
        self.assertEqual(3, snapshot.count)

    def test_count_ceiling_and_legacy_reviewer_disagreement(self):
        value = op.default_operating()
        value["streams"]["count"] = 7
        self.refuse(value, "$.streams.count", "$.execution.max_parallel_tickets")
        self.gov["execution"]["max_parallel_tickets"] = 2
        self.refuse(op.default_operating(), "$.streams.count", "$.execution.max_parallel_tickets")
        self.gov["execution"]["max_parallel_tickets"] = 6
        self.gov["execution"]["independent_reviewers"]["count"] = 2
        self.refuse(op.default_operating(), "$.streams.count", "$.execution.independent_reviewers.count")

    def test_both_model_allowlists_bind_exact_path(self):
        value = op.default_operating()
        self.gov["execution"]["model_routing"]["role_allowed_models"]["worker"].remove("gpt-5.6-terra")
        self.refuse(value, "$.streams.C.worker.model", "$.execution.model_routing.role_allowed_models.worker")
        self.gov = governance()
        self.gov["execution"]["roles"]["worker"]["approved_model_ids"].remove("gpt-5.6-terra")
        self.refuse(value, "$.streams.C.worker.model", "$.execution.roles.worker.approved_model_ids")

    def test_effective_ceiling_ignores_disabled_broker_and_binds_enabled_workers(self):
        current = op.default_operating()
        current["streams"].update(count=4, D=op._stream())
        self.gov["execution"]["host_broker"].update(enabled=False, max_workers=3)
        before = canonical(self.gov)
        op.validate_operating(current, self.gov)
        self.assertEqual(6, op.operating_ceiling(self.gov)["effective_ceiling"])
        self.assertEqual(before, canonical(self.gov))
        self.gov["execution"]["host_broker"]["enabled"] = True
        self.refuse(current, "$.streams.count", "$.execution.host_broker.max_workers")
        self.assertEqual(3, op.operating_ceiling(self.gov)["effective_ceiling"])

    def test_ceiling_ties_structure_and_malformed_shapes_are_explicit(self):
        self.gov["execution"].update(max_parallel_tickets=3, host_broker={"enabled": True, "max_workers": 3})
        capacity = op.operating_ceiling(self.gov)
        self.assertEqual("$.execution.max_parallel_tickets", capacity["effective_ceiling_governance_path"])
        self.assertEqual(["$.execution.max_parallel_tickets", "$.execution.host_broker.max_workers"], capacity["effective_ceiling_sources"])
        self.gov["execution"].update(max_parallel_tickets=9, host_broker={"enabled": False, "max_workers": "ignored"})
        capacity = op.operating_ceiling(self.gov)
        self.assertEqual(6, capacity["effective_ceiling"])
        self.assertIsNone(capacity["effective_ceiling_governance_path"])
        self.assertIn("operating-config.schema.json#", capacity["effective_ceiling_sources"][0])
        for broker in (None, [], {"enabled": 1}, {"enabled": True}, {"enabled": True, "max_workers": True}):
            self.gov["execution"]["host_broker"] = broker
            with self.subTest(broker=broker), self.assertRaises(op.OperatingError):
                op.operating_ceiling(self.gov)

    def test_effort_and_review_floor_bind(self):
        value = op.default_operating()
        value["streams"]["C"]["worker"]["reasoning_effort"] = "invalid"
        self.refuse(value, "$.streams.C.worker.reasoning_effort", "$.execution.model_routing.models.gpt-5.6-terra.reasoning_efforts")
        value = op.default_operating()
        value["streams"]["C"]["reviewer"] = op._pair("gpt-5.6-terra", "medium")
        self.refuse(value, "$.streams.C.reviewer", "$.execution.model_routing.review_floor")

    def test_all_routes_support_explicit_pin_and_inactive_routes_survive(self):
        value = op.default_operating()
        for role in ("controller", "specialist", "simple_worker"):
            value[role]["pinned"] = True
        value["streams"]["F"] = op._stream()
        checked = op.validate_operating(value, self.gov)
        self.assertIn("F", checked.config["streams"])

    def test_missing_active_route_and_unknown_scoped_paths_refuse(self):
        value = op.default_operating()
        value["streams"]["count"] = 4
        self.refuse(value, "$.streams.D", None)
        for path in ("epics.FIX-1.worker", "execution.max_parallel_tickets", "streams.G.worker"):
            with self.subTest(path=path), self.assertRaises(op.OperatingError):
                op._path(path)

    def test_malformed_operating_structures_are_typed_refusals(self):
        for value in (None, [], {"streams": None}, {**op.default_operating(), "streams": []}):
            with self.subTest(value=value), self.assertRaises(op.OperatingError):
                op.validate_operating(value, self.gov)

    def test_epic_routes_obey_both_allowlists_and_review_floor(self):
        value = op.default_operating()
        value["epic_overrides"] = {"FIX-1": {"streams": {"B": {"worker": op._pair("gpt-6-astra", "xhigh")}},
                                            "specialist": op._pair("gpt-6-astra", "high")}}
        op.validate_operating(value, self.gov)
        for section, binding in ((self.gov["execution"]["model_routing"]["role_allowed_models"],
                                  "$.execution.model_routing.role_allowed_models.worker"),
                                 (self.gov["execution"]["roles"], "$.execution.roles.worker.approved_model_ids")):
            allowed = section["worker"] if isinstance(section["worker"], list) else section["worker"]["approved_model_ids"]
            allowed.remove("gpt-6-astra")
            self.refuse(value, "$.epic_overrides.FIX-1.streams.B.worker.model", binding)
            allowed.append("gpt-6-astra")
        value["epic_overrides"]["FIX-1"]["streams"]["B"]["reviewer"] = op._pair("gpt-5.6-terra", "medium")
        self.refuse(value, "$.epic_overrides.FIX-1.streams.B.reviewer", "$.execution.model_routing.review_floor")
        value["epic_overrides"]["FIX-1"]["streams"]["B"].pop("reviewer")
        value["epic_overrides"]["FIX-1"]["specialist"] = op._pair("gpt-5.6-terra", "medium")
        self.refuse(value, "$.epic_overrides.FIX-1.specialist", "$.execution.model_routing.review_floor")

    def test_epic_schema_refuses_ambiguous_id_partial_routes_and_extra_scope(self):
        cases = [("this Epic", {"controller": op._pair("gpt-5.6-sol", "high")}),
                 ("FIX-1", {"streams": {"B": {"worker": {"pinned": True}}}}),
                 ("FIX-1", {"streams": {"count": 4}}),
                 ("FIX-1", {"simple_worker": {"enabled": False}}),
                 ("FIX-1", {})]
        for identifier, entry in cases:
            value = op.default_operating()
            value["epic_overrides"] = {identifier: entry}
            with self.subTest(identifier=identifier, entry=entry), self.assertRaises(op.OperatingError):
                op.validate_operating(value, self.gov)

    def test_snapshot_hash_and_governance_forgery_refuse(self):
        original = op.validate_operating(op.default_operating(), self.gov)
        forged = op.replace(original, operating_hash="0" * 64)
        with self.assertRaises(op.OperatingError):
            op.validate_snapshot(forged, self.gov)
        other = deepcopy(self.gov)
        other["execution"]["max_parallel_tickets"] = 5
        with self.assertRaises(op.OperatingError):
            op.validate_snapshot(original, other)

    def test_all_schemas_closed_and_registered(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("operating-config", op.default_operating())
        value = op.default_operating()
        value["governance"] = {"ceiling": 100}
        with self.assertRaises(ValidationError):
            contracts.validate("operating-config", value)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.gov = governance()
        self.snapshot = op.validate_operating(op.default_operating(), self.gov)

    def recommend(self, epics):
        return op.recommend_operating(epic_bytes(epics), self.snapshot, self.gov, now="2026-09-15T12:00:00Z")

    def test_four_disjoint_epics_deterministic_flagged_routes(self):
        epics = [epic(1, ["data/"]), epic(2, ["features/"], risk_flags=["concurrency"]),
                 epic(3, ["backtest/"]), epic(4, ["tooling/"])]
        first = self.recommend(epics)
        self.assertEqual(first, self.recommend(epics))
        rows = {r["path"]: r["recommended"] for r in first["rows"]}
        self.assertEqual(4, rows["streams.count"])
        self.assertEqual("gpt-6-astra", rows["streams.B.worker"]["model"])
        self.assertEqual("gpt-6-astra", rows["streams.B.reviewer"]["model"])
        self.assertIn("streams.D.worker", rows)
        self.assertFalse(first["execution_authority"])

    def test_component_boundaries_and_conservative_unknown_scope(self):
        record = self.recommend([epic(1, ["data/"]), epic(2, ["database/"])])
        self.assertEqual(2, record["rows"][0]["recommended"])
        for scope in (["data/raw/"], ["../escape"], ["data/**"], [], None):
            with self.subTest(scope=scope):
                record = self.recommend([epic(1, ["data/"]), epic(2, scope)])
                self.assertEqual(1, record["rows"][0]["recommended"])

    def test_directory_glob_suffixes_match_prefix_grouping_and_risk_routes(self):
        prefixes = [epic(1, ["data/"]), epic(2, ["features/"], risk_flags=["concurrency"]),
                    epic(3, ["backtest/"]), epic(4, ["infra/"])]
        expected = self.recommend(prefixes)["rows"]
        for suffix in ("*", "**"):
            values = deepcopy(prefixes)
            for item in values:
                item["scope"][0] += suffix
            with self.subTest(suffix=suffix):
                self.assertEqual(expected, self.recommend(values)["rows"])
        rows = {row["path"]: row["recommended"] for row in expected}
        self.assertEqual(4, rows["streams.count"])
        self.assertEqual("gpt-6-astra", rows["streams.B.reviewer"]["model"])
        self.assertEqual("gpt-6-astra", rows["streams.D.worker"]["model"])

    def test_unknown_scope_reason_identifies_epic_and_value(self):
        for value in ("../escape", "", "/root", "C:/root", "data//nested"):
            with self.subTest(value=value):
                record = self.recommend([epic(1, ["data/**"]), epic(2, [value])])
                reason = record["rows"][0]["reason"]
                self.assertIn("FIX-2", reason)
                self.assertIn(repr(value), reason)
                self.assertIn("Unknown Epic scope", reason)
        for value in (None, []):
            record = self.recommend([epic(2, value)])
            self.assertIn("FIX-2 value " + repr(value), record["rows"][0]["reason"])
        current = self.snapshot.config
        current["streams"]["count"] = 1
        self.snapshot = op.validate_operating(current, self.gov)
        record = self.recommend([epic(2, ["../escape"])])
        self.assertEqual(1, record["rows"][0]["recommended"])
        self.assertIn("FIX-2 value '../escape'", op.render_recommendation(record))

    def test_unsupported_globs_refuse_with_epic_and_exact_value(self):
        for value in ("data/**/*.py", "data*/", "data/?", "data/[ab]", "data/{a,b}", "data/**/nested/*"):
            with self.subTest(value=value), self.assertRaises(op.OperatingError) as caught:
                self.recommend([epic(1, ["../unknown", value])])
            self.assertEqual("$.epics.FIX-1.scope", caught.exception.refusals[0]["path"])
            self.assertIn(repr(value), str(caught.exception))
            self.assertIn("unsupported glob", str(caught.exception))

    def test_route_metadata_alone_never_creates_a_default_recommendation(self):
        current = self.snapshot.config
        current["source"] = "user"
        for label in "ABC":
            for route in current["streams"][label].values():
                route["pinned"] = False
        current["controller"]["pinned"] = False
        current["specialist"]["pinned"] = False
        self.snapshot = op.validate_operating(current, self.gov)
        record = self.recommend([epic(i, [f"component{i}/**"]) for i in range(1, 4)])
        self.assertEqual([], record["rows"])

    def test_defaults_follow_reviewed_policy_without_pins_or_weaker_floors(self):
        policy = self.gov["execution"]["model_routing"]
        policy["role_defaults"]["worker"] = op._pair("gpt-5.6-sol", "medium")
        policy["role_defaults"]["controller"] = op._pair("gpt-5.6-terra", "high")
        policy["role_defaults"]["specialist"] = op._pair("gpt-5.6-sol", "high")
        policy["role_defaults"]["critic"] = op._pair("gpt-5.6-sol", "xhigh")
        self.snapshot = op.validate_operating(op.default_operating(), self.gov)
        rows = {row["path"]: row for row in self.recommend([epic(1, ["data/**"])])["rows"]}
        for path, role in (("streams.A.worker", "worker"), ("streams.A.reviewer", "critic"),
                           ("controller", "controller"), ("specialist", "specialist")):
            self.assertEqual({**policy["role_defaults"][role], "pinned": False}, rows[path]["recommended"])
        risky = self.recommend([epic(1, ["data/**"], risk_flags=["security"])])
        reviewer = next(row["recommended"] for row in risky["rows"] if row["path"] == "streams.A.reviewer")
        self.assertEqual(op._pair("gpt-6-astra", "xhigh"), {k: reviewer[k] for k in ("model", "reasoning_effort")})

    def test_explicit_stream_role_and_epic_pins_are_displayed_and_preserved(self):
        current = self.snapshot.config
        current["streams"]["A"]["worker"] = {**op._pair("gpt-5.6-sol", "high"), "pinned": True}
        current["controller"]["pinned"] = True
        scoped = {**op._pair("gpt-6-astra", "xhigh"), "pinned": True}
        current["epic_overrides"] = {"FIX-1": {"streams": {"A": {"worker": scoped}}}}
        self.snapshot = op.validate_operating(current, self.gov)
        record = self.recommend([epic(1, ["data/**"])])
        rows = {row["path"]: row for row in record["rows"]}
        for path in ("streams.A.worker", "controller", "epic_overrides.FIX-1.streams.A.worker"):
            self.assertEqual(rows[path]["current"], rows[path]["recommended"])
            self.assertTrue(rows[path]["recommended"]["pinned"])
            self.assertIn("Keeps your pin unless you say otherwise", rows[path]["reason"])
        self.assertIn("epic_overrides.FIX-1.streams.A.worker", op.render_recommendation(record))

    def test_scoped_pin_rows_fit_schema_beyond_the_original_global_only_limit(self):
        current = self.snapshot.config
        entry = {"controller": {**current["controller"], "pinned": True},
                 "specialist": {**current["specialist"], "pinned": True},
                 "streams": {label: {role: {**pair, "pinned": True} for role, pair in op._stream().items()}
                             for label in "ABCDEF"}}
        current["epic_overrides"] = {f"FIX-{i}": deepcopy(entry) for i in range(1, 6)}
        self.snapshot = op.validate_operating(current, self.gov)
        record = self.recommend([epic(i, [f"component{i}/**"]) for i in range(1, 6)])
        pins = [row for row in record["rows"] if row["path"].startswith("epic_overrides.")]
        self.assertEqual(70, len(pins))
        self.assertTrue(all(row["current"] == row["recommended"] for row in pins))
        self.assertLess(len(op._json(record)), op.MAX_BYTES)

    def test_empty_epics_and_duplicate_ids(self):
        record = self.recommend([])
        self.assertEqual([], record["rows"])
        self.assertIn("No Epics", op.render_recommendation(record))
        with self.assertRaises(op.OperatingError):
            self.recommend([epic(1, ["a"]), epic(1, ["b"])])

    def test_six_streams_controller_high_and_specialist_trigger(self):
        record = self.recommend([epic(i, ["infra/" if i == 1 else f"component{i}/"]) for i in range(1, 7)])
        rows = {r["path"]: r["recommended"] for r in record["rows"]}
        self.assertEqual(6, rows["streams.count"])
        self.assertEqual("high", rows["controller"]["reasoning_effort"])
        self.assertEqual("gpt-6-astra", rows["streams.A.worker"]["model"])

    def test_rendered_state_is_supplied_not_invented(self):
        table = op.render_operating(self.snapshot, self.gov, state="UNVERIFIED", version="1.8.9")
        self.assertTrue(table.startswith("AWF 1.8.9: UNVERIFIED"))
        self.assertNotIn(": CONFIGURED", table)
        self.assertIn("3 of ceiling 6", table)

    def test_real_planner_ticket_risk_metadata_and_complete_inventory_pins(self):
        value = load(ROOT / ".agentic/examples/stream-input.json")
        self.gov["github"]["repository"] = value["project"]["repository"]
        value["tickets"][0]["risk_flags"] = ["concurrency"]
        snapshot = op.validate_operating(op.default_operating(), self.gov)
        epics = [{"id": e["id"], "title": e["title"], "scope": [f"component{i}/"]} for i, e in enumerate(value["epics"])]
        raw = canonical(value)
        record = op.recommend_operating(epic_bytes(epics), snapshot, self.gov, inventory_raw=raw,
                                        now=value["inventory"]["captured_at"])
        self.assertEqual(sha256(raw), record["inputs"]["inventory"])
        self.assertEqual(4, record["ticket_count"])
        self.assertTrue(any(row["path"].endswith("worker") and row["recommended"]["model"] == "gpt-6-astra" for row in record["rows"]))
        value["project"]["repository"] = "other/repository"
        with self.assertRaises(op.OperatingError):
            op.recommend_operating(epic_bytes(epics), snapshot, self.gov, inventory_raw=canonical(value),
                                   now=value["inventory"]["captured_at"])

    def test_inventory_epic_flags_survive_sparse_separate_epic_snapshot(self):
        value = load(ROOT / ".agentic/examples/stream-input.json")
        self.gov["github"]["repository"] = value["project"]["repository"]
        epics = [{"id": e["id"], "title": e["title"], "scope": [f"component{i}/"]}
                 for i, e in enumerate(value["epics"])]
        for item in value["epics"]:
            item["risk_flags"] = ["concurrency"]
        record = op.recommend_operating(epic_bytes(epics), op.validate_operating(op.default_operating(), self.gov), self.gov,
                                        inventory_raw=canonical(value), now=value["inventory"]["captured_at"])
        for role in ("worker", "reviewer"):
            self.assertTrue(any(row["path"].endswith(role) and row["recommended"]["model"] == "gpt-6-astra"
                                for row in record["rows"]), record["rows"])

    def test_sparse_inventory_epics_preserve_observed_simple_markers(self):
        value = load(ROOT / ".agentic/examples/stream-input.json")
        self.gov["github"]["repository"] = value["project"]["repository"]
        markers = {"complexity": "low", "uncertainty": "low", "verification": "strong"}
        epics = [{"id": e["id"], "title": "Bounded component", "scope": [f"component{i}/"], **markers}
                 for i, e in enumerate(value["epics"])]
        for item in value["tickets"]:
            item.update(markers)
            item["risk_flags"] = []
        current = op.default_operating()
        current["simple_worker"]["enabled"] = False
        record = op.recommend_operating(epic_bytes(epics), op.validate_operating(current, self.gov), self.gov,
                                        inventory_raw=canonical(value), now=value["inventory"]["captured_at"])
        self.assertTrue(any(row["path"] == "simple_worker.enabled" and row["recommended"] is True
                            for row in record["rows"]), record["rows"])

    def test_inventory_epic_and_ticket_scope_keywords_trigger_specialists(self):
        for source in ("epics", "tickets"):
            value = load(ROOT / ".agentic/examples/stream-input.json")
            self.gov["github"]["repository"] = value["project"]["repository"]
            epics = [{"id": e["id"], "title": "Bounded component", "scope": [f"component{i}/"]}
                     for i, e in enumerate(value["epics"])]
            for item in value[source]:
                item["scope"] = "Rotate authentication credentials and validate secrets handling."
            record = op.recommend_operating(epic_bytes(epics), op.validate_operating(op.default_operating(), self.gov), self.gov,
                                            inventory_raw=canonical(value), now=value["inventory"]["captured_at"])
            with self.subTest(source=source):
                self.assertTrue(any(row["path"].endswith("worker") and row["recommended"]["model"] == "gpt-6-astra"
                                    and "Specialist triggers" in row["reason"] for row in record["rows"]), record["rows"])

    def test_planner_completed_inventory_recommends_no_new_work(self):
        value = load(ROOT / ".agentic/examples/stream-input.json")
        self.gov["github"]["repository"] = value["project"]["repository"]
        for ticket in value["tickets"]:
            ticket["status"] = "done"
            ticket["history"] = [{"at": value["inventory"]["captured_at"], "actor": "synthetic", "summary": "Synthetic completed fixture"}]
            ticket["dependencies"] = []
        epics = [{"id": e["id"], "title": e["title"], "scope": []} for e in value["epics"]]
        record = op.recommend_operating(epic_bytes(epics), op.validate_operating(op.default_operating(), self.gov), self.gov,
                                        inventory_raw=canonical(value), now=value["inventory"]["captured_at"])
        self.assertEqual([], record["rows"])

    def test_higher_review_floor_and_invalid_recommendations_are_not_clamped(self):
        self.gov["execution"]["model_routing"]["review_floor"]["reasoning_effort"] = "xhigh"
        self.gov["execution"]["model_routing"]["risk_route"]["reasoning_effort"] = "xhigh"
        current = op.default_operating()
        for label in "ABC":
            current["streams"][label]["reviewer"]["reasoning_effort"] = "xhigh"
        current["specialist"]["reasoning_effort"] = "xhigh"
        self.snapshot = op.validate_operating(current, self.gov)
        record = self.recommend([epic(1, ["data/"], risk_flags=["concurrency"])])
        rows = {row["path"]: row["recommended"] for row in record["rows"]}
        self.assertEqual("xhigh", rows["streams.A.reviewer"]["reasoning_effort"])
        self.assertNotIn("specialist", rows)
        # A governed recommendation still refuses an unavailable risk route;
        # it cannot substitute a weaker allowed model to produce an offer.
        self.gov["execution"]["roles"]["worker"]["approved_model_ids"].remove("gpt-6-astra")
        self.snapshot = op.validate_operating(current, self.gov)
        with self.assertRaises(op.OperatingError) as caught:
            self.recommend([epic(1, ["data/"], risk_flags=["concurrency"])])
        self.assertTrue(any(r["governance_path"] == "$.execution.roles.worker.approved_model_ids" for r in caught.exception.refusals))


class OperatingFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-op-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gov = governance()
        (self.root / ".agentic").mkdir()
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        self.initial = op.initialize_operating(self.root, self.gov)

    def events(self):
        return [json.loads(p.read_text()) for p in sorted((self.root / op.STATE / "changes").glob("*.json"))]

    def test_show_and_status_report_shared_ceiling_and_enabled_broker_repair(self):
        current = op.set_operating(self.root, self.gov, "Use four streams", ["streams.count=4"])
        for command in ("show",):
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(0, op.main(["--root", str(self.root), command, "--json"]))
            result = json.loads(output.getvalue())
            self.assertEqual(6, result["effective_ceiling"])
            self.assertEqual("$.execution.max_parallel_tickets", result["effective_ceiling_governance_path"])
            self.assertIn("4 of ceiling 6", result["table"])
        self.gov["execution"]["host_broker"].update(enabled=True, broker_id="synthetic-broker", max_workers=3)
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        before = (self.root / op.CONFIG).read_bytes()
        self.assertEqual("REJECTED", op.inspect_operating(self.root, self.gov)["status"])
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        current = op.set_operating(self.root, self.gov, "Use three streams within enabled broker", ["streams.count=3"])
        status = op.inspect_operating(self.root, self.gov)
        self.assertEqual(3, status["effective_ceiling"])
        self.assertEqual("$.execution.host_broker.max_workers", status["effective_ceiling_governance_path"])
        self.assertIn("$.execution.host_broker.max_workers", op.render_operating(current, self.gov, state="UNVERIFIED"))

    def test_repeated_recommendation_compacts_kept_pins_without_changing_adoption(self):
        op.set_operating(self.root, self.gov, "Pin A to Sol/high", ["A.worker=gpt-5.6-sol/high"])
        path = self.root / "epics.json"
        path.write_bytes(epic_bytes([epic(1, ["data/**"]), epic(2, ["features/**"], risk_flags=["concurrency"]),
                                     epic(3, ["backtest/**"]), epic(4, ["infra/**"])]))
        first = op.save_recommendation(self.root, self.gov, path, now="2026-09-16T12:00:00Z")
        op.set_operating(self.root, self.gov, "adopt all", recommendation_id=first["id"], accept="all")
        current = op.set_operating(self.root, self.gov, "Use three streams", ["streams.count=3"])
        record = op.save_recommendation(self.root, self.gov, path, now="2026-09-16T12:01:00Z")
        before = canonical(record)
        table = op.render_recommendation(record)
        self.assertEqual(before, canonical(record))
        self.assertIn("| streams.count | 3 | 4 |", table)
        self.assertNotIn("| streams.A.worker |", table)
        self.assertEqual(1, sum(line.startswith("Keeps your pins") for line in table.splitlines()))
        self.assertIn("Mandatory risk/review floors still apply", table)
        adopted = op.set_operating(self.root, self.gov, "adopt all", recommendation_id=record["id"], accept="all")
        for label in "ABCD":
            self.assertEqual(current.config["streams"][label], adopted.config["streams"][label])
        with self.assertRaisesRegex(op.OperatingError, "stale"):
            op.set_operating(self.root, self.gov, "adopt again", recommendation_id=record["id"], accept="all")

    def test_initialization_honest_and_idempotent(self):
        event = self.events()[0]
        self.assertIsNone(event["instruction"])
        self.assertIsNone(event["before_hash"])
        self.assertEqual("bootstrap", event["applied_from"])
        self.assertEqual(self.initial, op.initialize_operating(self.root, self.gov))
        self.assertEqual(1, len(self.events()))

    def test_explicit_missing_file_initialization_respects_lower_ceiling(self):
        with tempfile.TemporaryDirectory(prefix="awf-op-lower-") as directory:
            root = Path(directory)
            (root / ".agentic").mkdir()
            gov = deepcopy(self.gov)
            gov["execution"]["max_parallel_tickets"] = 2
            raw = op._json(gov)
            (root / op.GOVERNANCE).write_bytes(raw)
            with self.assertRaises(op.OperatingError):
                op.initialize_operating(root, gov)
            self.assertFalse((root / op.CONFIG).exists())
            result = op.set_operating(root, gov, "use two streams", ["streams.count=2"], initialize=True)
            self.assertEqual(2, result.count)
            self.assertEqual(raw, (root / op.GOVERNANCE).read_bytes())
            with self.assertRaises(op.OperatingError):
                op.set_operating(root, gov, "initialize again", ["streams.count=2"], initialize=True)

    def test_governance_invalid_audited_choices_can_be_repaired(self):
        gov = deepcopy(self.gov)
        gov["execution"]["max_parallel_tickets"] = 2
        raw = op._json(gov)
        (self.root / op.GOVERNANCE).write_bytes(raw)
        with self.assertRaises(op.OperatingError):
            op.load_operating(self.root, gov)
        result = op.set_operating(self.root, gov, "two under the new ceiling", ["streams.count=2"])
        self.assertEqual(2, result.count)
        self.assertEqual(raw, (self.root / op.GOVERNANCE).read_bytes())

    def test_initializing_existing_valid_choices_preserves_raw_bytes(self):
        with tempfile.TemporaryDirectory(prefix="awf-op-existing-") as directory:
            root = Path(directory)
            (root / ".agentic").mkdir()
            (root / op.GOVERNANCE).write_bytes(op._json(self.gov))
            raw = json.dumps(op.default_operating(), separators=(",", ":")).encode()
            (root / op.CONFIG).write_bytes(raw)
            op.initialize_operating(root, self.gov)
            self.assertEqual(raw, (root / op.CONFIG).read_bytes())

    def test_untracked_invalid_operating_file_is_not_force_repaired(self):
        with tempfile.TemporaryDirectory(prefix="awf-op-untracked-") as directory:
            root = Path(directory)
            (root / ".agentic").mkdir()
            (root / op.GOVERNANCE).write_bytes(op._json(self.gov))
            value = op.default_operating()
            value["streams"]["count"] = 7
            raw = op._json(value)
            (root / op.CONFIG).write_bytes(raw)
            with self.assertRaises(op.OperatingError):
                op.set_operating(root, self.gov, "two", ["streams.count=2"])
            self.assertEqual(raw, (root / op.CONFIG).read_bytes())

    def test_roundtrip_verbatim_chat_and_no_governance_write(self):
        before = (self.root / op.GOVERNANCE).read_bytes()
        instruction = '  Four streams, and B on Sol/high.\nKeep $() and `text` verbatim.  '
        result = op.set_operating(self.root, self.gov, instruction, ["streams.count=4", "streams.B.worker=gpt-5.6-sol/high"])
        self.assertEqual(4, result.count)
        self.assertTrue(result.config["streams"]["B"]["worker"]["pinned"])
        self.assertNotIn("pinned", result.config["streams"]["D"]["worker"])
        event = next(e for e in self.events() if e["id"] == result.last_change_id)
        self.assertEqual(instruction, event["instruction"])
        self.assertEqual(self.initial.operating_hash, event["before_hash"])
        self.assertEqual(result.operating_hash, event["after_hash"])
        self.assertEqual(before, (self.root / op.GOVERNANCE).read_bytes())

    def test_epic_cli_roundtrip_preserves_global_routes_and_records_exact_scope(self):
        instruction = "  Stream B's worker on Astra/xhigh for this Epic.\nKeep scope FIX-2.  "
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = op.main(["--root", str(self.root), "set", "--instruction", instruction,
                            "--epic", "FIX-2", "--set", "B.worker=gpt-6-astra/xhigh", "--json"])
        self.assertEqual(0, code, output.getvalue())
        result = json.loads(output.getvalue())
        current = op.load_operating(self.root, self.gov)
        scoped = current.config["epic_overrides"]["FIX-2"]
        self.assertEqual({"streams": {"B": {"worker": {**op._pair("gpt-6-astra", "xhigh"), "pinned": True}}}}, scoped)
        self.assertEqual(self.initial.config["streams"], current.config["streams"])
        self.assertNotEqual(self.initial.operating_hash, current.operating_hash)
        self.assertIn("Epic FIX-2 overrides", result["table"])
        self.assertIn("gpt-6-astra / xhigh (pinned)", result["table"])
        event = next(e for e in self.events() if e["id"] == current.last_change_id)
        self.assertEqual(instruction, event["instruction"])
        self.assertEqual(self.initial.operating_hash, event["before_hash"])
        self.assertEqual(current.operating_hash, event["after_hash"])
        self.assertIn("epic_overrides.FIX-2.streams.B.worker", {change["path"] for change in event["changes"]})

    def test_scoped_roles_pin_only_existing_routes_and_do_not_broaden(self):
        result = op.set_operating(self.root, self.gov, "FIX-1 controller and specialist", ["controller=gpt-6-astra/high",
                                 "specialist=gpt-6-astra/xhigh"], epic_id="FIX-1")
        self.assertTrue(result.config["epic_overrides"]["FIX-1"]["controller"]["pinned"])
        self.assertEqual(self.initial.config["controller"], result.config["controller"])
        result = op.set_operating(self.root, self.gov, "Unpin FIX-1 controller", ["controller.pinned=false"], epic_id="FIX-1")
        self.assertFalse(result.config["epic_overrides"]["FIX-1"]["controller"]["pinned"])
        before, events = (self.root / op.CONFIG).read_bytes(), self.events()
        for identifier, assignment in (("this Epic", "B.worker=gpt-6-astra/xhigh"), ("FIX-2", "B.worker.pinned=false"),
                                       ("FIX-1", "streams.count=4"), ("FIX-1", "simple_worker.enabled=false"),
                                       ("FIX-1", "B.reviewer=gpt-5.6-terra/medium")):
            with self.subTest(identifier=identifier, assignment=assignment), self.assertRaises(op.OperatingError):
                op.set_operating(self.root, self.gov, "scoped instruction", [assignment], epic_id=identifier)
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(events, self.events())

    def test_atomic_repair_of_global_and_epic_routes_after_governance_change(self):
        op.set_operating(self.root, self.gov, "FIX-1 worker Terra", ["B.worker=gpt-5.6-terra/medium"], epic_id="FIX-1")
        gov = deepcopy(self.gov)
        gov["execution"]["roles"]["worker"]["approved_model_ids"].remove("gpt-5.6-terra")
        (self.root / op.GOVERNANCE).write_bytes(op._json(gov))
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, gov, "Fix Epic", ["B.worker=gpt-5.6-sol/high"], epic_id="FIX-1")
        assignments = ["streams." + label + ".worker=gpt-5.6-sol/high" for label in "ABC"]
        assignments.append("epic_overrides.FIX-1.streams.B.worker=gpt-5.6-sol/high")
        result = op.set_operating(self.root, gov, "Repair global and FIX-1 workers under the accepted policy", assignments)
        self.assertEqual("gpt-5.6-sol", result.config["epic_overrides"]["FIX-1"]["streams"]["B"]["worker"]["model"])
        for label in "ABC":
            self.assertEqual("gpt-5.6-sol", result.config["streams"][label]["worker"]["model"])
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, gov, "Ambiguous scopes", [assignments[-1]], epic_id="FIX-2")

    def test_reduction_retains_route_and_unpin_is_explicit(self):
        op.set_operating(self.root, self.gov, "D Astra high", ["streams.count=4", "streams.D.worker=gpt-6-astra/high"])
        result = op.set_operating(self.root, self.gov, "drop to two", ["streams.count=2"])
        self.assertTrue(result.config["streams"]["D"]["worker"]["pinned"])
        result = op.set_operating(self.root, self.gov, "unpin D", ["streams.D.worker.pinned=false"])
        self.assertFalse(result.config["streams"]["D"]["worker"]["pinned"])

    def test_mixed_refusal_leaves_config_and_audit_unchanged(self):
        raw = (self.root / op.CONFIG).read_bytes()
        events = self.events()
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, self.gov, "four and cheap reviewer", ["streams.count=4", "streams.C.reviewer=gpt-5.6-terra/medium"])
        self.assertEqual(raw, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(events, self.events())
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_external_governance_change_and_file_edit_refuse(self):
        other = deepcopy(self.gov)
        other["execution"]["max_parallel_tickets"] = 5
        (self.root / op.GOVERNANCE).write_bytes(op._json(other))
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, self.gov, "two", ["streams.count=2"])
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        value = self.initial.config
        value["streams"]["count"] = 2
        (self.root / op.CONFIG).write_bytes(op._json(value))
        with self.assertRaises(op.OperatingError):
            op.load_operating(self.root, self.gov)

    def test_failed_event_write_retains_journal_then_exact_recovery(self):
        original = op._new
        def fail_event(tree, name, raw):
            if "/changes/" in name:
                raise OSError("injected event write failure")
            return original(tree, name, raw)
        with patch.object(op, "_new", fail_event), self.assertRaises(OSError):
            op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        pending = (self.root / op.JOURNAL).read_bytes()
        with self.assertRaises(op.OperatingError):
            op.load_operating(self.root, self.gov)
        with self.assertRaises(op.OperatingError):
            op.recover_operating(self.root, self.gov, "0" * 64)
        restored = op.recover_operating(self.root, self.gov, sha256(pending))
        self.assertEqual(4, restored.count)
        self.assertEqual(2, len(self.events()))
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_partial_staging_write_never_publishes_a_truncated_event(self):
        original = op.os.fdopen
        class PartialWrite:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def write(self, raw):
                if b'"sequence": 2' in raw and b'"format"' not in raw:
                    self.stream.write(raw[:64])
                    self.stream.flush()
                    raise OSError("injected partial event write")
                return self.stream.write(raw)
        def fdopen(fd, mode):
            stream = original(fd, mode)
            return PartialWrite(stream) if mode == "wb" else stream
        with patch.object(op.os, "fdopen", fdopen), self.assertRaisesRegex(OSError, "partial event"):
            op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        pending = (self.root / op.JOURNAL).read_bytes()
        self.assertEqual(1, len(self.events()))
        restored = op.recover_operating(self.root, self.gov, sha256(pending))
        self.assertEqual(4, restored.count)
        self.assertEqual(2, len(self.events()))

    def test_journal_fsync_failure_leaves_no_published_intent_or_changes(self):
        original = (self.root / op.CONFIG).read_bytes()
        with patch.object(op.os, "fsync", side_effect=OSError("injected staging fsync failure")), self.assertRaises(OSError):
            op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        self.assertFalse((self.root / op.JOURNAL).exists())
        self.assertEqual(original, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(1, len(self.events()))
        result = op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        self.assertEqual(4, result.count)

    def test_serialized_event_and_escaped_journal_bounds_refuse_before_mutation(self):
        before, events = (self.root / op.CONFIG).read_bytes(), self.events()
        # A reduced test limit keeps the governing file readable and exercises
        # actual serialization, including the journal's config-string escaping.
        for line_count, expected in ((20000, "$.event"), (15300, "$.journal")):
            with self.subTest(member=expected), patch.object(op, "MAX_BYTES", 32768):
                with self.assertRaises(op.OperatingError) as caught:
                    op.set_operating(self.root, self.gov, "four" + "\n" * line_count, ["streams.count=4"])
                self.assertEqual(expected, caught.exception.refusals[0]["path"])
            self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
            self.assertEqual(events, self.events())
            self.assertFalse((self.root / op.JOURNAL).exists())

    def test_large_valid_scoped_configuration_refuses_before_publication(self):
        before, events = (self.root / op.CONFIG).read_bytes(), self.events()
        value = self.initial.config
        value["epic_overrides"] = {"FIX-" + str(i): {"streams": {label: op._stream() for label in op.LABELS}}
                                   for i in range(1, 1001)}
        self.assertGreater(len(op._json(value)), op.MAX_BYTES)
        with op.Tree(self.root) as tree, op._lock(tree), self.assertRaises(op.OperatingError) as caught:
            op._commit(tree, self.gov, before, value, "Synthetic oversized scoped fixture", [], "chat")
        self.assertEqual("$.configuration", caught.exception.refusals[0]["path"])
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(events, self.events())
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_abrupt_interruption_staging_residue_is_retained_and_recoverable(self):
        original = op._publish_new
        residue = None
        def interrupted(parent, handle, temp, leaf):
            nonlocal residue
            if parent.name == "changes":
                # Model process death after a partial staging write: its file
                # survives without any published event or finally cleanup.
                residue = parent / (".awf-operating-" + "f" * 32 + ".tmp")
                residue.write_bytes((parent / temp).read_bytes()[:64])
                raise OSError("injected abrupt interruption")
            return original(parent, handle, temp, leaf)
        with patch.object(op, "_publish_new", interrupted), self.assertRaises(OSError):
            op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        pending, evidence = (self.root / op.JOURNAL).read_bytes(), residue.read_bytes()
        restored = op.recover_operating(self.root, self.gov, sha256(pending))
        self.assertEqual(4, restored.count)
        self.assertEqual(evidence, residue.read_bytes())
        self.assertEqual(2, len(self.events()))

    def test_publication_conflict_never_replaces_different_final_bytes(self):
        original = op._publish_new
        conflict = None
        def race(parent, handle, temp, leaf):
            nonlocal conflict
            if parent.name == "changes":
                conflict = parent / leaf
                conflict.write_bytes(b"conflicting external record\n")
            return original(parent, handle, temp, leaf)
        with patch.object(op, "_publish_new", race), self.assertRaises(FileExistsError):
            op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        pending = (self.root / op.JOURNAL).read_bytes()
        self.assertEqual(b"conflicting external record\n", conflict.read_bytes())
        with self.assertRaises(ValidationError):
            op.recover_operating(self.root, self.gov, sha256(pending))
        self.assertEqual(pending, (self.root / op.JOURNAL).read_bytes())
        self.assertEqual(b"conflicting external record\n", conflict.read_bytes())

    def test_before_config_failure_and_after_event_failure_recover(self):
        for phase in ("before_config", "after_event"):
            with self.subTest(phase=phase):
                original_write, original_unlink = op.Tree.write, op.Tree.unlink
                def write(tree, name, raw):
                    if phase == "before_config" and name == op.CONFIG:
                        raise OSError("injected")
                    return original_write(tree, name, raw)
                def unlink(tree, name):
                    if phase == "after_event" and name == op.JOURNAL:
                        raise OSError("injected")
                    return original_unlink(tree, name)
                with patch.object(op.Tree, "write", write), patch.object(op.Tree, "unlink", unlink), self.assertRaises(OSError):
                    op.set_operating(self.root, self.gov, "two", ["streams.count=2"])
                pending = (self.root / op.JOURNAL).read_bytes()
                result = op.recover_operating(self.root, self.gov, sha256(pending))
                self.assertEqual(2, result.count)

    def test_mismatched_recovery_never_overwrites_external_edits(self):
        original = op._new
        def fail_event(tree, name, raw):
            if "/changes/" in name:
                raise OSError("injected")
            return original(tree, name, raw)
        with patch.object(op, "_new", fail_event), self.assertRaises(OSError):
            op.set_operating(self.root, self.gov, "four", ["streams.count=4"])
        pending = (self.root / op.JOURNAL).read_bytes()
        (self.root / op.CONFIG).write_bytes(b'external edit\n')
        with self.assertRaises(op.OperatingError):
            op.recover_operating(self.root, self.gov, sha256(pending))
        self.assertEqual(b'external edit\n', (self.root / op.CONFIG).read_bytes())
        self.assertEqual(pending, (self.root / op.JOURNAL).read_bytes())

    def test_reader_and_writer_share_lock(self):
        with op.locked_operating(self.root, self.gov):
            with self.assertRaises((op.OperatingError, OSError)):
                op.set_operating(self.root, self.gov, "two", ["streams.count=2"])

    def test_recommendation_applies_only_selected_rows_and_rejects_stale_inputs(self):
        path = self.root / "epics.json"
        path.write_bytes(epic_bytes([epic(i, [f"component{i}/"]) for i in range(1, 5)]))
        record = op.save_recommendation(self.root, self.gov, path, now="2026-09-15T12:00:00Z")
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, self.gov, "adopt all for FIX-1", recommendation_id=record["id"], accept="all", epic_id="FIX-1")
        self.assertEqual(self.initial.operating_hash, op.load_operating(self.root, self.gov).operating_hash)
        result = op.set_operating(self.root, self.gov, "adopt streams only", recommendation_id=record["id"], accept="streams.count")
        self.assertEqual(4, result.count)
        self.assertIn("D", result.config["streams"])
        self.assertEqual("recommendation:" + record["id"], result.source)
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, self.gov, "adopt all", recommendation_id=record["id"], accept="all")
        record = op.save_recommendation(self.root, self.gov, path, now="2026-09-15T12:01:00Z")
        path.write_bytes(epic_bytes([]))
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, self.gov, "adopt all", recommendation_id=record["id"], accept="all")

    def test_recommendation_content_tampering_and_audit_overwrite_rejected(self):
        path = self.root / "epics.json"
        path.write_bytes(epic_bytes([epic(1, ["data/"])]))
        record = op.save_recommendation(self.root, self.gov, path, now="2026-09-15T12:00:00Z")
        record["rows"][0]["recommended"] = 2
        (self.root / op.STATE / "recommendations" / (record["id"] + ".json")).write_bytes(op._json(record))
        with self.assertRaises(op.OperatingError):
            op.set_operating(self.root, self.gov, "adopt all", recommendation_id=record["id"], accept="all")
        with op.Tree(self.root) as tree, self.assertRaises(FileExistsError):
            op._new(tree, op.STATE + "/changes/" + self.initial.last_change_id + ".json", b'{}')

    def test_cli_refusal_exit_two_and_clean_io_residue(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            code = op.main(["--root", str(self.root), "set", "--instruction", "seven", "--set", "streams.count=7"])
        self.assertEqual(2, code)
        self.assertEqual("$.streams.count", json.loads(stream.getvalue())["refusals"][0]["path"])
        with patch.object(op, "load_operating", side_effect=OSError("private raw detail")):
            report = op.inspect_operating(self.root, self.gov)
        self.assertNotIn("private raw detail", report["refusals"][0]["reason"])
        self.assertEqual("private raw detail", report["diagnostic"])

    def test_actual_workflow_and_standalone_entrypoints_do_not_claim_installed(self):
        for entry, commands in (("workflow.py", ["operating", "show", "--json"]), ("operating.py", ["show", "--json"])):
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, "-B", "-I", str(ROOT / ".agentic/scripts" / entry),
                                         "--root", str(self.root), *commands], capture_output=True, timeout=30)
                self.assertEqual(0, result.returncode, (result.stdout + result.stderr).decode())
                report = json.loads(result.stdout)
                self.assertEqual("UNVERIFIED", report["project_state"])
                self.assertEqual(self.initial.operating_hash, report["operating_hash"])
                self.assertFalse(report["execution_authority"])


if __name__ == "__main__":
    unittest.main()
