"""AWF-50 backlog planning and lane-sizing regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint, load, sha256
from agentic.operating import operating_ceiling, stream_labels, validate_operating
from agentic import planning

NOW = "2026-10-12T12:00:00Z"
HOST_EVIDENCE = {}


def config(max_lanes=12):
    value = load(ROOT / ".agentic/PROJECT_CONFIG.yaml")
    value["execution"]["max_parallel_tickets"] = 12
    value["execution"]["max_lanes"] = max_lanes
    value["execution"]["host_broker"] = {"enabled": False, "max_workers": 12}
    value["merge_gate"]["trusted_owner_ids"] = [101, 202]
    value["github"]["expected_actor_id"] = 101
    value["github"]["repository_id"] = "123"
    value["project"]["id"] = "project-1"
    return value


def ticket(key, path, **extra):
    return {"key": key, "status": "open", "write_paths": [path], **extra}


def owner_source(current, mode, value, reason, actor=101, source_id="comment-1"):
    command = planning.owner_override_command(current, mode, value, reason)
    return {
        "channel": "github_pr_comment", "comment_id": 77,
        "actor_id": actor, "actor_login": "owner",
        "created_at": NOW, "updated_at": NOW,
        "binding": {"current_override_id": current},
        "raw_body": command, "raw_body_sha256": sha256(command.encode("utf-8")),
        "source_id": source_id,
    }


def verify_owner(source, expected):
    return {"status": "VERIFIED", "actor_id": source["actor_id"],
            "binding": source["binding"], "raw_body_sha256": source["raw_body_sha256"],
            "source_id": source["source_id"], "observed_at": NOW}


def operating_snapshot(cfg, count=None):
    count = count or operating_ceiling(cfg)["effective_ceiling"]
    value = load(ROOT / "OPERATING_CONFIG.yaml")
    route = deepcopy(value["streams"]["A"])
    value["streams"] = {"count": count, **{label: deepcopy(route) for label in stream_labels(count)}}
    return validate_operating(value, cfg)


def verify_host_observation(authentication, request):
    evidence = HOST_EVIDENCE.get(authentication.get("evidence_id"))
    if evidence is None:
        return {
            "status": "UNVERIFIED", **deepcopy(authentication),
            "observed_at": request["observed_at"], "host_capacity": 0,
            "resources": [{"name": "cpu", "capacity": 0}],
        }
    return {"status": "VERIFIED", **deepcopy(authentication), **deepcopy(evidence)}


def resource_observation(cfg, snapshot, host_capacity=12, resources=None):
    binding = {
        "project_id": cfg["project"]["id"],
        "repository_id": str(cfg["github"]["repository_id"]),
        "scope_sha256": "b" * 64,
        "repository_head_sha": "c" * 40,
        "repository_tree_sha": "d" * 40,
        "project_config_sha256": fingerprint("accepted-project-config", cfg),
        "operating_config_sha256": snapshot.operating_hash,
    }
    capacities = (resources if resources is not None
                  else [{"name": "cpu", "capacity": 12}])

    def observe_host_resources(request):
        claims = {
            "observed_at": NOW, "host_capacity": host_capacity,
            "resources": deepcopy(capacities),
        }
        evidence_sha256 = fingerprint("test_host_capacity_evidence", claims)
        evidence_id = "test-host-evidence-" + evidence_sha256[:16]
        HOST_EVIDENCE[evidence_id] = deepcopy(claims)
        return {
            "status": "OBSERVED", "producer_id": "test-host-adapter",
            "evidence_id": evidence_id, "evidence_sha256": evidence_sha256,
            "request_sha256": fingerprint("planning_resource_request", request),
            **claims,
        }

    return planning.produce_planning_resource_observation(
        binding=binding, observed_at=NOW,
        producer_implementation_sha256="a" * 64,
        observe_host_resources=observe_host_resources,
        verify_host_observation=verify_host_observation)


def capacity_kwargs(cfg, host_capacity=12, resources=None, stream_capacity=None):
    snapshot = operating_snapshot(cfg, stream_capacity)
    observation = resource_observation(cfg, snapshot, host_capacity, resources)
    return {
        "resource_observation": observation,
        "expected_resource_binding": observation["binding"],
        "expected_resource_producer_sha256": "a" * 64,
        "operating_snapshot": snapshot,
        "observed_at": NOW,
        "verify_host_observation": verify_host_observation,
    }


def plan(tickets, cfg, **capacity):
    return planning.plan_lanes(
        planning.build_backlog_graph(tickets, cfg), cfg, **capacity_kwargs(cfg, **capacity))


class ProviderSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.binding = {
            "project_id": "project-1", "repository_id": "123",
            "scope_sha256": "b" * 64, "repository_head_sha": "c" * 40,
            "project_config_sha256": fingerprint("accepted-project-config", self.cfg),
            "cycle_id": "cycle-1",
        }
        self.provider = {
            "provider_id": "reviewed-backlog-provider",
            "implementation_sha256": "a" * 64,
            "verifier_implementation_sha256": "d" * 64,
        }
        self.snapshot = {
            "format": "awf-backlog-provider-snapshot-1", "complete": True,
            "provider": deepcopy(self.provider), "binding": deepcopy(self.binding),
            "observed_at": NOW,
            "tickets": [{"ticket": {"key": "AWF-1", "status": "open"},
                         "components": [
                             {"kind": kind, "ticket_key": "AWF-1",
                              "binding": deepcopy(self.binding), "observed_at": NOW,
                              "files": [path]}
                             for kind, path in [("brief", "src/brief.py"),
                                                ("open_pr", "src/pr.py"),
                                                ("worktree_diff", "src/dirty.py")]]}],
            "authentication": {"evidence_id": "trusted-observation-1"},
        }
        self.trusted = self.body(self.snapshot)

    @staticmethod
    def body(snapshot):
        return {key: deepcopy(value) for key, value in snapshot.items()
                if key != "authentication"}

    def verify(self, authentication, request):
        # Independent host evidence, never a reflection of the supplied payload.
        self.assertEqual(self.provider, request["provider"])
        self.assertEqual(self.binding, request["binding"])
        self.assertEqual(NOW, request["observed_at"])
        if authentication != {"evidence_id": "trusted-observation-1"}:
            return {"status": "UNVERIFIED"}
        return {"status": "VERIFIED", "evidence_id": "trusted-observation-1",
                "snapshot": deepcopy(self.trusted)}

    def read(self, snapshot=None, verifier=None, **overrides):
        kwargs = {"expected_binding": self.binding,
                  "expected_provider": self.provider, "now": NOW,
                  "verify_provider_snapshot": verifier or self.verify}
        kwargs.update(overrides)
        return planning.tickets_from_provider_snapshot(
            self.snapshot if snapshot is None else snapshot, self.cfg, **kwargs)

    def test_authenticated_backlog_snapshot_collects_brief_pr_and_diff_paths(self):
        original = deepcopy(self.snapshot)
        tickets = self.read()
        graph = planning.build_backlog_graph(tickets, self.cfg)
        self.assertEqual({"src/brief.py": ["brief"], "src/pr.py": ["open_pr"],
                          "src/dirty.py": ["worktree_diff"]},
                         graph["tickets"]["AWF-1"]["path_sources"])
        self.assertEqual(original, self.snapshot)
        tickets[0]["write_paths"].append("src/later.py")
        self.assertEqual(original, self.snapshot)

    def test_unauthenticated_backlog_snapshot_fails_before_graph_build(self):
        for case in ("unsigned", "stale", "foreign-repository", "foreign-project",
                     "foreign-scope", "foreign-head", "old-cycle", "partial",
                     "wrong-attribution", "foreign-component", "stale-component",
                     "duplicate-component", "ticket-authored-pr", "forged-paths",
                     "unpinned-provider", "unpinned-verifier", "wrong-config"):
            with self.subTest(case=case):
                snapshot = deepcopy(self.snapshot)
                component = snapshot["tickets"][0]["components"][1]
                if case == "unsigned":
                    del snapshot["authentication"]
                elif case == "stale":
                    snapshot["observed_at"] = "2026-10-11T12:00:00Z"
                elif case.startswith("foreign-") and case != "foreign-component":
                    field = {"foreign-repository": "repository_id",
                             "foreign-project": "project_id", "foreign-scope": "scope_sha256",
                             "foreign-head": "repository_head_sha"}[case]
                    snapshot["binding"][field] = "e" * 64
                elif case == "old-cycle":
                    snapshot["binding"]["cycle_id"] = "cycle-0"
                elif case == "partial":
                    snapshot["tickets"][0]["components"].pop()
                elif case == "wrong-attribution":
                    component["ticket_key"] = "AWF-2"
                elif case == "foreign-component":
                    component["binding"]["repository_id"] = "456"
                elif case == "stale-component":
                    component["observed_at"] = "2026-10-11T12:00:00Z"
                elif case == "duplicate-component":
                    snapshot["tickets"][0]["components"].append(deepcopy(component))
                elif case == "ticket-authored-pr":
                    snapshot["tickets"][0]["ticket"]["open_pr_files"] = ["src/injected.py"]
                elif case == "forged-paths":
                    component["files"] = ["src/forged.py"]
                elif case.startswith("unpinned-"):
                    field = ("implementation_sha256" if case == "unpinned-provider"
                             else "verifier_implementation_sha256")
                    snapshot["provider"][field] = "f" * 64
                elif case == "wrong-config":
                    snapshot["binding"]["project_config_sha256"] = "f" * 64
                # Even authenticated malformed components must be refused.
                self.trusted = (self.body(self.snapshot) if case == "forged-paths"
                                else self.body(snapshot))
                with patch.object(planning, "build_backlog_graph") as build:
                    with self.assertRaises(ValidationError):
                        planning.build_backlog_graph(self.read(snapshot), self.cfg)
                    build.assert_not_called()

    def test_snapshot_requires_independent_complete_verification(self):
        for verifier in (None, lambda *_: True,
                         lambda *_: {"status": "VERIFIED"},
                         lambda *_: {"status": "VERIFIED", "snapshot_sha256":
                                     fingerprint("snapshot", self.trusted)}):
            with self.subTest(verifier=verifier), self.assertRaises(ValidationError):
                self.read(verify_provider_snapshot=verifier)

    def test_snapshot_rejects_unsafe_paths_and_duplicate_tickets(self):
        for files in (["../outside.py"], ["src/*.py"], ["src/"], "src/file.py"):
            with self.subTest(files=files):
                snapshot = deepcopy(self.snapshot)
                snapshot["tickets"][0]["components"][0]["files"] = files
                self.trusted = self.body(snapshot)
                with self.assertRaises(ValidationError):
                    self.read(snapshot)
        snapshot = deepcopy(self.snapshot)
        snapshot["tickets"].append(deepcopy(snapshot["tickets"][0]))
        self.trusted = self.body(snapshot)
        with self.assertRaises(ValidationError):
            self.read(snapshot)

    def test_snapshot_preserves_risk_metadata_and_empty_observed_sources(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["tickets"][0]["ticket"].update(
            summary="security change", description="reviewed scope", change_kind="release_pr")
        snapshot["tickets"][0]["components"][1]["files"] = []
        snapshot["tickets"][0]["components"][2]["files"] = ["MANIFEST.md"]
        self.trusted = self.body(snapshot)
        tickets = self.read(snapshot)
        self.assertEqual("security change", tickets[0]["summary"])
        graph = planning.build_backlog_graph(tickets, self.cfg)
        self.assertEqual(3, graph["tickets"]["AWF-1"]["risk_tier"])
        self.assertEqual({"src/brief.py": ["brief"]},
                         graph["tickets"]["AWF-1"]["path_sources"])

    def test_snapshot_rejects_verifier_errors_and_wrong_evidence_identity(self):
        def unavailable(*_):
            raise RuntimeError("provider unavailable")

        def wrong_identity(*_):
            return {"status": "VERIFIED", "evidence_id": "other-evidence",
                    "snapshot": deepcopy(self.trusted)}

        for verifier in (unavailable, wrong_identity):
            with self.subTest(verifier=verifier), self.assertRaises(ValidationError):
                self.read(verifier=verifier)

    def test_rejected_collector_isolates_empty_attribution_and_preserves_sibling(self):
        snapshot = deepcopy(self.snapshot)
        sibling = deepcopy(snapshot["tickets"][0])
        sibling["ticket"]["key"] = "AWF-2"
        for component in sibling["components"]:
            component["ticket_key"] = "AWF-2"
        snapshot["tickets"].append(sibling)
        for component in snapshot["tickets"][0]["components"]:
            component["files"] = []
        self.trusted = self.body(snapshot)
        original = deepcopy(snapshot)
        rejected, evidence = {}, {}
        tickets = self.read(snapshot, rejected=rejected, observed_pr_paths=evidence)
        self.assertEqual(["AWF-2"], [item["key"] for item in tickets])
        self.assertEqual({"AWF-1": "Backlog ticket AWF-1 has no observed exact write files"}, rejected)
        self.assertEqual({"paths": [], "complete": True}, evidence["AWF-1"])
        self.assertEqual(original, snapshot)
        tickets[0]["write_paths"].append("src/later.py")
        evidence["AWF-2"]["paths"].append("src/later.py")
        self.assertEqual(original, snapshot)

    def test_rejected_collector_isolates_inexact_component_paths(self):
        for field in (0, 2):  # brief and diff; the PR remains complete and safe.
            for invalid in ("src/*.py", "src/", "/outside.py", "../outside.py", "", None, 7, {}):
                with self.subTest(field=field, invalid=invalid):
                    snapshot = deepcopy(self.snapshot)
                    sibling = deepcopy(snapshot["tickets"][0])
                    sibling["ticket"]["key"] = "AWF-2"
                    for component in sibling["components"]:
                        component["ticket_key"] = "AWF-2"
                    snapshot["tickets"].append(sibling)
                    snapshot["tickets"][0]["components"][field]["files"] = [invalid]
                    self.trusted = self.body(snapshot)
                    with self.assertRaises(ValidationError) as strict:
                        self.read(snapshot)
                    rejected, evidence = {}, {}
                    tickets = self.read(snapshot, rejected=rejected, observed_pr_paths=evidence)
                    self.assertEqual(["AWF-2"], [item["key"] for item in tickets])
                    self.assertEqual({"AWF-1": str(strict.exception)}, rejected)
                    self.assertEqual({"paths": ["src/pr.py"], "complete": True}, evidence["AWF-1"])

    def test_rejected_collector_continues_validation_and_detects_later_duplicate_or_forgery(self):
        for case in ("duplicate", "wrong-attribution", "foreign", "stale", "missing",
                     "duplicate-component", "unknown-component", "non-list", "metadata", "forged"):
            with self.subTest(case=case):
                snapshot = deepcopy(self.snapshot)
                valid = deepcopy(snapshot["tickets"][0])
                valid["ticket"]["key"] = "AWF-2"
                for component in valid["components"]:
                    component["ticket_key"] = "AWF-2"
                later = deepcopy(valid)
                snapshot["tickets"].extend([valid, later])
                for component in snapshot["tickets"][0]["components"]:
                    component["files"] = []
                later["ticket"]["key"] = "AWF-1" if case == "duplicate" else "AWF-3"
                for component in later["components"]:
                    component["ticket_key"] = later["ticket"]["key"]
                component = later["components"][1]
                if case == "wrong-attribution":
                    component["ticket_key"] = "AWF-99"
                elif case == "foreign":
                    component["binding"]["repository_id"] = "456"
                elif case == "stale":
                    component["observed_at"] = "2026-10-11T12:00:00Z"
                elif case == "missing":
                    later["components"].pop()
                elif case == "duplicate-component":
                    component["kind"] = "brief"
                elif case == "unknown-component":
                    component["kind"] = "other"
                elif case == "non-list":
                    component["files"] = "src/pr.py"
                elif case == "metadata":
                    later["ticket"]["open_pr_files"] = ["src/injected.py"]
                self.trusted = self.body(snapshot)
                if case == "forged":
                    component["files"] = ["src/forged.py"]
                rejected = {"previous": "unchanged"}
                evidence = {"previous": {"paths": ["prior.py"], "complete": True}}
                original = deepcopy((snapshot, rejected, evidence))
                with self.assertRaises(ValidationError):
                    self.read(snapshot, rejected=rejected, observed_pr_paths=evidence)
                self.assertEqual(original, (snapshot, rejected, evidence))

    def test_pr_evidence_survives_rejected_brief_or_diff(self):
        paths = [f"src/pr-{index}.py" for index in range(15)]
        for field in (0, 2):
            with self.subTest(field=field):
                snapshot = deepcopy(self.snapshot)
                snapshot["tickets"][0]["components"][field]["files"] = ["../invalid.py"]
                snapshot["tickets"][0]["components"][1]["files"] = paths
                self.trusted = self.body(snapshot)
                rejected, evidence = {}, {}
                self.assertEqual([], self.read(snapshot, rejected=rejected, observed_pr_paths=evidence))
                self.assertEqual({"AWF-1"}, set(rejected))
                self.assertEqual({"AWF-1": {"paths": sorted(paths), "complete": True}}, evidence)

    def test_pr_evidence_retains_all_files_before_manifest_filtering(self):
        # Also covers the plan's unsafe-PR completeness acceptance row.
        paths = ["MANIFEST.json", "MANIFEST.md", "src/pr.py"]
        for extra in ([], ["src/*.py"], ["src/"], ["/outside.py"], ["../outside.py"],
                      [None], [7], [""], [{}]):
            with self.subTest(extra=extra):
                snapshot = deepcopy(self.snapshot)
                snapshot["tickets"][0]["components"][1]["files"] = paths[:1] + extra + paths[1:]
                self.trusted = self.body(snapshot)
                original = deepcopy(snapshot)
                rejected, evidence = {}, {}
                tickets = self.read(snapshot, rejected=rejected, observed_pr_paths=evidence)
                self.assertEqual({"AWF-1": {"paths": sorted(paths), "complete": not bool(extra)}}, evidence)
                if extra:
                    self.assertEqual([], tickets)
                    self.assertEqual({"AWF-1"}, set(rejected))
                else:
                    self.assertEqual(self.read(snapshot), tickets)
                    self.assertEqual(["src/pr.py"], tickets[0]["open_pr_files"])
                    self.assertEqual({}, rejected)
                self.assertEqual(original, snapshot)

    def test_snapshot_collectors_reject_invalid_types_and_shared_dictionary(self):
        for name in ("rejected", "observed_pr_paths"):
            for invalid in ([], "invalid", False, 1):
                with self.subTest(name=name, invalid=invalid):
                    with self.assertRaisesRegex(ValidationError, "collector.*dictionary"):
                        self.read(**{name: invalid})
        collector = {}
        with self.assertRaisesRegex(ValidationError, "distinct"):
            self.read(rejected=collector, observed_pr_paths=collector)
        self.assertEqual({}, collector)

    def test_snapshot_collectors_do_not_weaken_authentication(self):
        for case in ("unsigned", "partial", "stale", "foreign", "unverified", "unavailable"):
            with self.subTest(case=case):
                snapshot = deepcopy(self.snapshot)
                overrides = {}
                if case == "unsigned":
                    del snapshot["authentication"]
                elif case == "partial":
                    snapshot["complete"] = False
                elif case == "stale":
                    snapshot["observed_at"] = "2026-10-11T12:00:00Z"
                elif case == "foreign":
                    snapshot["binding"]["repository_id"] = "456"
                elif case == "unverified":
                    overrides["verify_provider_snapshot"] = lambda *_: {"status": "UNVERIFIED"}
                else:
                    def unavailable(*_):
                        raise RuntimeError("provider unavailable")
                    overrides["verify_provider_snapshot"] = unavailable
                rejected, evidence = {}, {}
                with self.assertRaises(ValidationError):
                    self.read(snapshot, rejected=rejected, observed_pr_paths=evidence, **overrides)
                self.assertEqual(({}, {}), (rejected, evidence))

    def test_snapshot_collectors_preserve_strict_default_and_manifest_only_rejection(self):
        for files in ([], ["MANIFEST.json", "MANIFEST.md"]):
            with self.subTest(files=files):
                snapshot = deepcopy(self.snapshot)
                for component in snapshot["tickets"][0]["components"]:
                    component["files"] = files
                self.trusted = self.body(snapshot)
                evidence = {}
                with self.assertRaisesRegex(ValidationError, "no observed exact write files"):
                    self.read(snapshot, observed_pr_paths=evidence)
                self.assertEqual({}, evidence)
                rejected = {}
                self.assertEqual([], self.read(snapshot, rejected=rejected, observed_pr_paths=evidence))
                self.assertEqual({"AWF-1": {"paths": sorted(files), "complete": True}}, evidence)
                self.assertEqual({"AWF-1": "Backlog ticket AWF-1 has no observed exact write files"}, rejected)
        # The rejection collector is also usable without the evidence collector.
        self.trusted = self.body(self.snapshot)
        self.assertEqual(self.read(), self.read(rejected={}))

    def test_operating_doc_covers_max_lanes_overrides_and_migration(self):
        doc = (ROOT / ".agentic/docs/29-OPERATING-CONFIGURATION.md").read_text(encoding="utf-8")
        for text in ("execution.max_lanes", "fixed", "max", "sealed safe capacity",
                     "round boundaries", "omitted", "six", "AWF-50 item 1",
                     "generated-schema limitation"):
            with self.subTest(text=text):
                self.assertIn(text, doc)
        self.assertLessEqual(len(doc.split()), 800)


class GraphTests(unittest.TestCase):
    def test_dependencies_file_sources_generator_ownership_and_manifest_exception(self):
        tickets = [
            ticket("AWF-1", "src/generator.py", generated_outputs=["src/generated.py"],
                   open_pr_files=["src/pr.py", "MANIFEST.json"]),
            ticket("AWF-2", "src/generated.py", declared_dependencies=["AWF-1"],
                   worktree_diff=["src/worktree.py", "MANIFEST.md"]),
            ticket("AWF-3", "src/pr.py", jira_links=[{"type": "Blocks", "direction": "outward", "key": "AWF-4"}]),
            ticket("AWF-4", "src/four.py"),
        ]
        graph = planning.build_backlog_graph(tickets, config())
        edges = {(edge["blocker"], edge["dependent"]): edge["sources"] for edge in graph["dependencies"]}
        self.assertIn(("AWF-1", "AWF-2"), edges)
        self.assertIn(("AWF-3", "AWF-4"), edges)
        overlaps = {(edge["left"], edge["right"]): edge for edge in graph["overlaps"]}
        self.assertEqual(["src/generated.py"], overlaps[("AWF-1", "AWF-2")]["paths"])
        self.assertEqual(["src/pr.py"], overlaps[("AWF-1", "AWF-3")]["paths"])
        self.assertNotIn("MANIFEST.json", str(graph["overlaps"]))
        self.assertNotIn("MANIFEST.md", str(graph["overlaps"]))
        self.assertEqual("AWF-1", graph["generator_owners"]["src/generated.py"])
        self.assertIn("worktree_diff", graph["tickets"]["AWF-2"]["path_sources"]["src/worktree.py"])

    def test_protected_paths_use_awf48_tiering_without_creating_glob_conflicts(self):
        graph = planning.build_backlog_graph([
            ticket("AWF-1", ".agentic/lib/one.py"),
            ticket("AWF-2", ".agentic/lib/two.py"),
        ], config())
        self.assertEqual([], graph["overlaps"])
        self.assertGreaterEqual(graph["tickets"]["AWF-1"]["protected_path_floor"], 2)
        self.assertGreaterEqual(graph["tickets"]["AWF-1"]["risk_tier"], 2)

    def test_ambiguous_paths_and_generator_owners_refuse(self):
        with self.assertRaises(ValidationError):
            planning.build_backlog_graph([ticket("AWF-1", "src/**")], config())
        with self.assertRaises(ValidationError):
            planning.build_backlog_graph([
                ticket("AWF-1", "a.py", generated_outputs=["out.py"]),
                ticket("AWF-2", "b.py", generated_outputs=["out.py"]),
            ], config())

    def test_case_aliased_file_identities_refuse_before_overlap_detection(self):
        with self.assertRaisesRegex(ValidationError, "case-aliased"):
            planning.build_backlog_graph([
                ticket("AWF-1", "src/Foo.py"),
                ticket("AWF-2", "src/two.py", open_pr_files=["src/foo.py"]),
            ], config())


class LaneTests(unittest.TestCase):
    def test_ready_lanes_are_conflict_free_block_dependents_and_rank_critical_path(self):
        tickets = [
            ticket("AWF-1", "shared.py", expected_rounds=1, round_time_minutes=10),
            ticket("AWF-2", "shared.py", expected_rounds=1, round_time_minutes=10),
            ticket("AWF-3", "three.py", expected_rounds=2, round_time_minutes=20,
                   declared_dependencies=["AWF-1"]),
            ticket("AWF-4", "four.py", expected_rounds=1, round_time_minutes=20),
        ]
        result = plan(tickets, config())
        scheduled = {key for lane in result["lanes"] for key in lane["ticket_keys"]}
        self.assertEqual({"AWF-1", "AWF-2", "AWF-4"}, scheduled)
        self.assertEqual("AWF-1", result["blocked"]["AWF-3"][0])
        self.assertEqual("AWF-1", result["lanes"][0]["ticket_keys"][0])
        lane_paths = [set(lane["write_paths"]) for lane in result["lanes"]]
        self.assertFalse(any(left & right for i, left in enumerate(lane_paths) for right in lane_paths[i + 1:]))

    def test_in_flight_dependent_remains_blocked_until_its_open_blocker_closes(self):
        tickets = [
            ticket("AWF-1", "one.py"),
            ticket("AWF-2", "two.py", status="in_flight", declared_dependencies=["AWF-1"]),
        ]
        result = plan(tickets, config())
        scheduled = {key for lane in result["lanes"] for key in lane["ticket_keys"]}
        self.assertEqual({"AWF-1"}, scheduled)
        self.assertEqual(["AWF-1"], result["blocked"]["AWF-2"])

    def test_step1_fixture_yields_ten_strict_lanes(self):
        fixture = json.loads((ROOT / ".agentic/tests/fixtures/planning/step1.json").read_text(encoding="utf-8"))
        result = plan(fixture["tickets"], config())
        self.assertEqual(10, result["estimate"]["independent_lane_count"])
        self.assertEqual(10, len(result["lanes"]))
        self.assertEqual(10, result["lane_count"])

    def test_estimate_names_authenticated_resource_binding_constraints(self):
        cfg = config(max_lanes=8)
        tickets = [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 11)]
        resources = [{"name": "cpu", "capacity": 7}, {"name": "ram", "capacity": 9},
                     {"name": "model_rate", "capacity": 7}]
        result = plan(tickets, cfg, resources=resources)
        self.assertEqual(7, result["lane_count"])
        self.assertEqual(10, result["estimate"]["independent_lane_count"])
        self.assertEqual(["resource:cpu", "resource:model_rate"], result["estimate"]["binding_constraints"])

    def test_estimate_reports_each_governance_constraint_at_its_configured_value(self):
        cfg = config(max_lanes=10)
        cfg["execution"]["max_parallel_tickets"] = 12
        cfg["execution"]["host_broker"] = {"enabled": True, "max_workers": 4}
        tickets = [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 11)]
        result = plan(tickets, cfg)
        self.assertEqual({
            "dependency_graph/file_overlap": 10,
            "configured_ceiling": 10,
            "max_parallel_tickets": 12,
            "host_broker": 4,
            "reviewed_operating_streams": 4,
            "observed_host_capacity": 12,
            "resource:cpu": 12,
        }, result["estimate"]["constraints"])
        self.assertEqual(["host_broker", "reviewed_operating_streams"], result["estimate"]["binding_constraints"])
        self.assertEqual(["$.execution.host_broker.max_workers"], result["estimate"]["governance_sources"])

    def test_zero_capacity_is_a_zero_safe_estimate(self):
        result = plan([ticket("AWF-1", "one.py")], config(), host_capacity=0)
        self.assertEqual(0, result["estimate"]["safe_lane_count"])
        self.assertEqual(0, result["lane_count"])

        resource_zero = plan(
            [ticket("AWF-1", "one.py")], config(), resources=[{"name": "cpu", "capacity": 0}])
        self.assertEqual(0, resource_zero["estimate"]["safe_lane_count"])

    def test_zero_host_capacity_is_a_zero_safe_estimate(self):
        result = plan([ticket("AWF-1", "one.py")], config(), host_capacity=0)
        self.assertEqual(0, result["estimate"]["safe_lane_count"])
        self.assertEqual(0, result["lane_count"])
        self.assertEqual(0, result["estimate"]["constraints"]["observed_host_capacity"])
        self.assertIn("observed_host_capacity", result["estimate"]["binding_constraints"])
        self.assertIn("safe count 0", result["estimate"]["reasoning"])

    def test_zero_resource_capacity_is_a_zero_safe_estimate(self):
        result = plan(
            [ticket("AWF-1", "one.py")], config(),
            resources=[{"name": "cpu", "capacity": 0}])
        self.assertEqual(0, result["estimate"]["safe_lane_count"])
        self.assertEqual(0, result["lane_count"])
        self.assertEqual(0, result["estimate"]["capacity"]["resource_capacity"])
        self.assertIn("resource:cpu", result["estimate"]["binding_constraints"])

    def test_defined_producer_emits_the_only_closed_observation_shape(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        self.assertEqual(planning.PLANNING_RESOURCE_KEYS, set(observation))
        self.assertEqual(planning.PLANNING_PRODUCER_KEYS, set(observation["producer"]))
        self.assertEqual(planning.PLANNING_BINDING_KEYS, set(observation["binding"]))
        self.assertEqual(planning.PLANNING_AUTHENTICATION_KEYS,
                         set(observation["authentication"]))
        self.assertEqual(planning.PLANNING_RESOURCE_ITEM_KEYS,
                         set(observation["resources"][0]))

    def test_operator_authored_capacity_dictionary_is_refused(self):
        graph = planning.build_backlog_graph([ticket("AWF-1", "one.py")], config())
        with self.assertRaisesRegex(ValidationError, "authenticated.*observation"):
            planning.plan_lanes(
                graph, config(), resources={"limits": {"cpu": 2}, "headroom": {}})

    def test_operator_selected_capacities_cannot_pass_through_producer_into_plan_lanes(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        authenticated = resource_observation(
            cfg, snapshot, host_capacity=4,
            resources=[{"name": "cpu", "capacity": 4}])
        operator_selected = deepcopy(authenticated)
        operator_selected["host_capacity"] = 0
        operator_selected["resources"] = [{"name": "cpu", "capacity": 0}]
        operator_selected["observation_sha256"] = fingerprint(
            "planning_resource_observation",
            {key: value for key, value in operator_selected.items()
             if key != "observation_sha256"})

        def verify_host_observation(authentication, request):
            return {
                "status": "VERIFIED",
                **deepcopy(authentication),
                "observed_at": NOW,
                "host_capacity": 4,
                "resources": [{"name": "cpu", "capacity": 4}],
            }

        def operator_observer(request):
            return {
                "status": "OBSERVED", **deepcopy(authenticated["authentication"]),
                "request_sha256": fingerprint("planning_resource_request", request),
                "observed_at": NOW, "host_capacity": 0,
                "resources": [{"name": "cpu", "capacity": 0}],
            }

        with self.assertRaisesRegex(ValidationError, "authenticated host evidence"):
            planning.produce_planning_resource_observation(
                binding=authenticated["binding"], observed_at=NOW,
                producer_implementation_sha256="a" * 64,
                observe_host_resources=operator_observer,
                verify_host_observation=verify_host_observation)

        with self.assertRaisesRegex(ValidationError, "authenticated host evidence"):
            planning.plan_lanes(
                planning.build_backlog_graph([ticket("AWF-1", "one.py")], cfg), cfg,
                resource_observation=operator_selected,
                expected_resource_binding=authenticated["binding"],
                expected_resource_producer_sha256="a" * 64,
                operating_snapshot=snapshot, observed_at=NOW,
                verify_host_observation=verify_host_observation)

    def test_closed_authenticated_planning_resource_observation_is_required(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        with self.assertRaisesRegex(ValidationError, "closed"):
            planning.validate_planning_resource_observation(
                {**observation, "extra": True}, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_unknown_observation_key_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        with self.assertRaisesRegex(ValidationError, "closed"):
            planning.validate_planning_resource_observation(
                {**observation, "operator_note": "two lanes"},
                expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_missing_producer_field_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        del observation["producer"]
        with self.assertRaisesRegex(ValidationError, "closed"):
            planning.validate_planning_resource_observation(
                observation, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_unauthenticated_or_unsigned_observation_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        observation["observation_sha256"] = None
        with self.assertRaisesRegex(ValidationError, "digest"):
            planning.validate_planning_resource_observation(
                observation, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_missing_host_evidence_authenticator_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        with self.assertRaisesRegex(ValidationError, "authenticated host evidence"):
            planning.validate_planning_resource_observation(
                observation, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW)

    def test_wrong_capacity_type_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        observation["host_capacity"] = True
        with self.assertRaisesRegex(ValidationError, "integer"):
            planning.validate_planning_resource_observation(
                observation, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_negative_capacity_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        observation["host_capacity"] = -1
        with self.assertRaisesRegex(ValidationError, "zero through 1000"):
            planning.validate_planning_resource_observation(
                observation, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_out_of_bound_capacity_is_refused(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        observation["host_capacity"] = 1001
        with self.assertRaisesRegex(ValidationError, "zero through 1000"):
            planning.validate_planning_resource_observation(
                observation, expected_binding=observation["binding"],
                expected_producer_sha256="a" * 64, now=NOW,
                verify_host_observation=verify_host_observation)

    def test_authenticated_observation_refuses_stale_digest_binding_and_producer(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        observation = resource_observation(cfg, snapshot)
        accepted = planning.validate_planning_resource_observation(
            observation, expected_binding=observation["binding"],
            expected_producer_sha256="a" * 64, now=NOW,
            verify_host_observation=verify_host_observation)
        self.assertEqual(observation, accepted)

        for name, changed, match in (
                ("digest", {**observation, "observation_sha256": "0" * 64}, "digest"),
                ("stale", {**observation, "observed_at": "2026-10-12T11:59:59Z"}, "cycle"),
                ("producer", observation, "producer"),
                ("binding", observation, "binding")):
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError, match):
                planning.validate_planning_resource_observation(
                    changed,
                    expected_binding=({**observation["binding"], "scope_sha256": "9" * 64}
                                      if name == "binding" else observation["binding"]),
                    expected_producer_sha256=("9" * 64 if name == "producer" else "a" * 64),
                    now=NOW, verify_host_observation=verify_host_observation)

    def test_observation_resource_inventory_is_closed_unique_and_bounded(self):
        cfg = config()
        snapshot = operating_snapshot(cfg)
        for name, resources in (
                ("empty", []),
                ("duplicate", [{"name": "cpu", "capacity": 1}, {"name": "cpu", "capacity": 2}]),
                ("negative", [{"name": "cpu", "capacity": -1}]),
                ("boolean", [{"name": "cpu", "capacity": True}]),
                ("over-cap", [{"name": "cpu", "capacity": 1001}]),
                ("extra", [{"name": "cpu", "capacity": 1, "extra": True}])):
            observation = resource_observation(cfg, snapshot)
            observation["resources"] = resources
            with self.subTest(name=name), self.assertRaises(ValidationError):
                planning.validate_planning_resource_observation(
                    observation, expected_binding=observation["binding"],
                    expected_producer_sha256="a" * 64, now=NOW,
                    verify_host_observation=verify_host_observation)


class OwnerOverrideTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.state = {}

    def change(self, mode, value, reason="owner choice", actor=101, source_id="comment-1"):
        current = self.state.get("planning_override", {}).get("override_id")
        return planning.change_owner_override(
            self.cfg, self.state, mode=mode, value=value, reason=reason, now=NOW,
            owner_source=owner_source(current, mode, value, reason, actor, source_id),
            verify_owner_source=verify_owner)

    def test_no_response_accepts_recommendation(self):
        result = plan([ticket("AWF-1", "one.py")], self.cfg)
        self.assertEqual({"kind": "accepted_default", "value": 1}, result["owner_decision"])

    def test_fixed_and_max_overrides_apply_change_and_audit_in_awf48_shape(self):
        fixed = self.change("fixed", 4)
        self.assertEqual(4, fixed["value"])
        self.assertEqual({"mode": None, "value": None}, fixed["change_audit"]["old"])
        self.assertEqual({"mode": "fixed", "value": 4}, fixed["change_audit"]["new"])
        changed = self.change("max", 2, "reduce capacity", source_id="comment-2")
        self.assertEqual(fixed["override_id"], changed["previous_override_id"])
        self.assertEqual(2, len(self.state["planning_override_history"]))
        tickets = [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 5)]
        result = planning.plan_lanes(
            planning.build_backlog_graph(tickets, self.cfg), self.cfg,
            controller_state=self.state, verify_owner_source=verify_owner,
            **capacity_kwargs(self.cfg))
        self.assertEqual(2, result["lane_count"])

    def test_unauthorized_and_over_ceiling_overrides_refuse(self):
        with self.assertRaisesRegex(ValidationError, "trusted owner"):
            self.change("fixed", 2, actor=999)
        with self.assertRaisesRegex(ValidationError, "ceiling"):
            self.change("fixed", 13)

    def test_fixed_override_cannot_exceed_sealed_safe_capacity(self):
        self.change("fixed", 4)
        with self.assertRaisesRegex(ValidationError, "safe.*capacity"):
            planning.plan_lanes(
                planning.build_backlog_graph([ticket("AWF-1", "one.py")], self.cfg), self.cfg,
                controller_state=self.state, verify_owner_source=verify_owner,
                **capacity_kwargs(self.cfg, resources=[{"name": "cpu", "capacity": 2}]))

    def test_direct_or_incomplete_self_hashed_override_cannot_authorize_scheduling(self):
        forged = {"schema_version": 1, "override_id": "forged-without-owner",
                  "created_at": NOW, "mode": "fixed", "value": 4,
                  "previous_override_id": None}
        forged["override_sha256"] = planning.fingerprint(
            "planning_override_record", forged)
        graph = planning.build_backlog_graph(
            [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 5)], self.cfg)
        with self.assertRaisesRegex(ValidationError, "authenticated controller context"):
            planning.verify_override_record(forged)
        with self.assertRaisesRegex(ValidationError, "controller-owned"):
            planning.plan_lanes(graph, self.cfg, override=forged, **capacity_kwargs(self.cfg))

        forged_state = {"planning_override": deepcopy(forged),
                        "planning_override_history": [deepcopy(forged)]}
        with self.assertRaisesRegex(ValidationError, "owner-source|audit"):
            planning.plan_lanes(graph, self.cfg, controller_state=forged_state,
                                verify_owner_source=verify_owner, **capacity_kwargs(self.cfg))

    def test_stored_override_chain_is_reauthenticated_before_each_plan(self):
        self.change("fixed", 2)
        graph = planning.build_backlog_graph(
            [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 3)], self.cfg)

        def reject_replay(source, expected):
            raise RuntimeError("provider observation no longer authenticates")

        with self.assertRaisesRegex(ValidationError, "authentication failed"):
            planning.plan_lanes(graph, self.cfg, controller_state=self.state,
                                verify_owner_source=reject_replay, **capacity_kwargs(self.cfg))


class RecomputeAndAuditTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.state = {}
        self.tickets = [ticket("AWF-1", "one.py")]

    def test_merge_file_intake_close_and_override_triggers_recompute(self):
        for trigger in ("pr_merged", "files_changed", "ticket_added", "ticket_closed", "override_changed"):
            with self.subTest(trigger=trigger):
                result = planning.recompute_plan(
                    self.tickets, self.cfg, self.state, now=NOW, trigger=trigger,
                    **capacity_kwargs(self.cfg))
                self.assertEqual("PLANNED", result["status"])
                self.assertEqual(trigger, result["record"]["trigger"])
        self.assertEqual(5, len(self.state["planning_history"]))

    def test_recompute_uses_only_reauthenticated_controller_override_state(self):
        planning.change_owner_override(
            self.cfg, self.state, mode="fixed", value=1, reason="owner choice", now=NOW,
            owner_source=owner_source(None, "fixed", 1, "owner choice"),
            verify_owner_source=verify_owner)
        result = planning.recompute_plan(
            self.tickets, self.cfg, self.state, now=NOW, trigger="override_changed",
            verify_owner_source=verify_owner, **capacity_kwargs(self.cfg))
        self.assertEqual("owner_override", result["record"]["owner_decision"]["kind"])

        forged = deepcopy(self.state["planning_override"])
        with self.assertRaisesRegex(ValidationError, "controller-owned"):
            planning.recompute_plan(
                self.tickets, self.cfg, self.state, now=NOW, trigger="override_changed",
                override=forged, verify_owner_source=verify_owner, **capacity_kwargs(self.cfg))

    def test_mid_round_reassignment_is_deferred(self):
        result = planning.recompute_plan(self.tickets, self.cfg, self.state, now=NOW,
                                         trigger="files_changed", round_active=True)
        self.assertEqual("DEFERRED_UNTIL_ROUND_END", result["status"])
        self.assertEqual([], self.state.get("planning_history", []))
        self.assertEqual(["files_changed"], self.state["pending_planning_triggers"])

    def test_plan_records_are_sealed_and_append_only(self):
        result = planning.recompute_plan(
            self.tickets, self.cfg, self.state, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg))
        record = result["record"]
        self.assertTrue(planning.verify_plan_record(record))
        mutated = deepcopy(record)
        mutated["lane_count"] = 99
        with self.assertRaises(ValidationError):
            planning.verify_plan_record(mutated)
        with self.assertRaises(ValidationError):
            planning.append_plan_record(self.state, record)

    def test_schema_v1_history_is_preserved_when_capacity_bound_plan_is_appended(self):
        current = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg))["record"]
        legacy = deepcopy(current)
        legacy["schema_version"] = 1
        legacy["inputs"]["capacity"].pop("capacity_binding_sha256")
        legacy["inputs"]["capacity"].pop("stream_capacity_source")
        legacy["estimate"]["capacity"].pop("capacity_binding_sha256")
        legacy["estimate"]["capacity"].pop("stream_capacity_source")
        legacy.pop("plan_id")
        legacy.pop("plan_sha256")
        legacy["plan_id"] = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            "awf-planning-record:" + fingerprint("planning_audit_seed", legacy)))
        legacy["plan_sha256"] = fingerprint("planning_audit_record", legacy)
        state = {"planning_history": [deepcopy(legacy)], "current_plan": deepcopy(legacy)}

        result = planning.recompute_plan(
            self.tickets, self.cfg, state, now=NOW, trigger="files_changed",
            **capacity_kwargs(self.cfg))

        self.assertEqual(1, legacy["schema_version"])
        self.assertNotIn("capacity_binding_sha256", legacy["inputs"]["capacity"])
        self.assertEqual(legacy, state["planning_history"][0])
        self.assertEqual(2, result["record"]["schema_version"])
        self.assertEqual(2, len(state["planning_history"]))
        self.assertTrue(planning.verify_plan_record(state["planning_history"][0]))
        self.assertTrue(planning.verify_plan_record(state["planning_history"][1]))

    def test_plan_record_seals_capacity_inputs(self):
        result = planning.recompute_plan(
            self.tickets, self.cfg, self.state, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, resources=[{"name": "cpu", "capacity": 1}]))
        capacity = result["record"]["inputs"]["capacity"]
        self.assertEqual(1, capacity["safe_lane_count"])
        self.assertEqual([{"name": "cpu", "capacity": 1}], capacity["resources"])
        self.assertEqual(1, capacity["resource_capacity"])
        self.assertEqual(12, capacity["host_capacity"])
        self.assertEqual(12, capacity["stream_capacity"])
        self.assertEqual("a" * 64, capacity["producer_implementation_sha256"])
        self.assertEqual(
            result["record"]["estimate"]["safe_lane_count"], capacity["safe_lane_count"])

    def test_sealed_plan_binds_host_capacity_input(self):
        first = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, host_capacity=5))
        second = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, host_capacity=4))
        first_capacity = first["record"]["inputs"]["capacity"]
        second_capacity = second["record"]["inputs"]["capacity"]
        self.assertEqual(5, first_capacity["host_capacity"])
        self.assertEqual(4, second_capacity["host_capacity"])
        self.assertNotEqual(first_capacity["capacity_binding_sha256"],
                            second_capacity["capacity_binding_sha256"])

    def test_sealed_plan_binds_stream_capacity_input(self):
        first = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, stream_capacity=5))
        second = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, stream_capacity=4))
        first_capacity = first["record"]["inputs"]["capacity"]
        second_capacity = second["record"]["inputs"]["capacity"]
        self.assertEqual(5, first_capacity["stream_capacity"])
        self.assertEqual(4, second_capacity["stream_capacity"])
        self.assertNotEqual(first_capacity["capacity_binding_sha256"],
                            second_capacity["capacity_binding_sha256"])

    def test_sealed_plan_binds_resource_capacity_input(self):
        first = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, resources=[{"name": "cpu", "capacity": 5}]))
        second = planning.recompute_plan(
            self.tickets, self.cfg, {}, now=NOW, trigger="project_start",
            **capacity_kwargs(self.cfg, resources=[{"name": "cpu", "capacity": 4}]))
        first_capacity = first["record"]["inputs"]["capacity"]
        second_capacity = second["record"]["inputs"]["capacity"]
        self.assertEqual(5, first_capacity["resource_capacity"])
        self.assertEqual(4, second_capacity["resource_capacity"])
        self.assertNotEqual(first_capacity["capacity_binding_sha256"],
                            second_capacity["capacity_binding_sha256"])

    def test_owner_override_is_applied_after_capacity_binding(self):
        tickets = [ticket(f"AWF-{index}", f"src/{index}.py") for index in range(1, 5)]
        planning.change_owner_override(
            self.cfg, self.state, mode="fixed", value=2, reason="owner choice", now=NOW,
            owner_source=owner_source(None, "fixed", 2, "owner choice"),
            verify_owner_source=verify_owner)
        result = planning.recompute_plan(
            tickets, self.cfg, self.state, now=NOW, trigger="override_changed",
            verify_owner_source=verify_owner,
            **capacity_kwargs(self.cfg, resources=[{"name": "cpu", "capacity": 2}]))
        record = result["record"]
        self.assertEqual(
            record["inputs"]["capacity"]["capacity_binding_sha256"],
            record["owner_decision"]["capacity_binding_sha256"])
        self.assertTrue(record["owner_decision"]["applied_after_capacity_binding"])
        self.assertLessEqual(record["lane_count"],
                             record["inputs"]["capacity"]["safe_lane_count"])

    def test_stream_slots_come_from_reviewed_operating_configuration(self):
        kwargs = capacity_kwargs(self.cfg, stream_capacity=3)
        result = planning.recompute_plan(
            self.tickets, self.cfg, self.state, now=NOW, trigger="project_start", **kwargs)
        capacity = result["record"]["inputs"]["capacity"]
        snapshot = kwargs["operating_snapshot"]
        self.assertEqual(snapshot.count, capacity["stream_capacity"])
        self.assertEqual({
            "kind": "validated_operating_snapshot",
            "operating_hash": snapshot.operating_hash,
            "governance_hash": snapshot.governance_hash,
            "source": snapshot.source,
            "last_change_id": snapshot.last_change_id,
        }, capacity["stream_capacity_source"])


if __name__ == "__main__":
    unittest.main()
