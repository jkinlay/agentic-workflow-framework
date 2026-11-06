"""Twelve-lane routing and fresh-default regressions; no live execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load
from agentic.lane_labels import STREAM_LABELS
from agentic.model_routing import RoutingLedger, default_policy, policy_from_config, routing_context, select_route
from agentic.operating import initialize_operating, default_operating, plan_initialize_operating, validate_operating
from agentic.streams import LABELS


class TwelveStreamTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy(ROOT / ".agentic/defaults/routing.yaml")
        self.gov = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.gov["execution"].update(max_lanes=12, max_parallel_tickets=12)
        self.gov["execution"]["model_routing"] = deepcopy(self.policy)
        for role in self.policy["role_defaults"]:
            self.gov["execution"]["roles"][role]["approved_model_ids"] = list(self.policy["models"])
        self.policy = policy_from_config(self.gov)
        self.value = default_operating(self.policy, count=12)
        self.operating = validate_operating(self.value, self.gov)
        self.capabilities = {"models": {m: v["reasoning_efforts"] for m, v in self.policy["models"].items()}}

    def request(self, **updates):
        value = dict(ticket_id="FIX-1", role="worker", agent_id="writer", context_id="writer-context",
                     phase="implementation", task_class="unit-fix", complexity="medium", risk="low",
                     uncertainty="low", verification="strong", risk_flags=[], reservation_tokens=100,
                     reservation_cost_microusd=None, stream="L")
        value.update(updates)
        return value

    def route(self, request, operating=None):
        return select_route(self.policy, request, self.capabilities,
                            operating=operating or self.operating, governance=self.gov)

    def test_supported_operating_and_schema_labels_are_canonical(self):
        from agentic.operating import stream_labels
        for count in range(13):
            self.assertEqual(STREAM_LABELS[:count], stream_labels(count))
        self.assertEqual(("Z", "AA", "AB"), stream_labels(28)[25:])
        if not (ROOT / "scripts/generate_contracts.py").is_file():
            self.skipTest("source schema generator is not installed")
        spec = importlib.util.spec_from_file_location("lane_contracts", ROOT / "scripts/generate_contracts.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        streams = generator.catalog()["operating-config"]["properties"]["streams"]["properties"]
        self.assertEqual(set(STREAM_LABELS), set(streams) - {"count"})

    def test_planner_describes_all_supported_labels(self):
        from agentic.streams import render_markdown
        from test_operating_integration import OperatingPlanningTests
        planner = OperatingPlanningTests()
        planner.setUp()
        self.assertIn("streams A\u2013L", render_markdown(planner.plan()).decode("utf-8"))
        self.assertIn("A\u2013L", (ROOT / ".agentic/scripts/plan_streams.py").read_text(encoding="utf-8").splitlines()[1])

    def test_all_twelve_workers_and_critics_keep_safety_floors(self):
        self.assertEqual(tuple("ABCDEFGHIJKL"), STREAM_LABELS)
        self.assertEqual(STREAM_LABELS, LABELS)
        for stream in STREAM_LABELS:
            for role in ("worker", "fix", "critic"):
                with self.subTest(stream=stream, role=role):
                    request = self.request(stream=stream, role=role, worker_context_id="other-context")
                    route = self.route(request)
                    self.assertEqual("ready", route["status"])
                    self.assertEqual(("gpt-6.1-sol", "high"), (route["model"], route["reasoning_effort"]))

    def test_simple_route_requires_each_eligibility_condition(self):
        simple = self.request(complexity="low")
        selected = self.route(simple)
        self.assertEqual(("gpt-6-luna", "high"), (selected["model"], selected["reasoning_effort"]))
        for change in ({"risk":"medium"}, {"complexity":"medium"}, {"uncertainty":"medium"},
                       {"verification":"limited"}, {"risk_flags":["permissions"]}):
            with self.subTest(change=change):
                self.assertEqual("gpt-6.1-sol", self.route({**simple, **change})["model"])

    def test_invalid_labels_remain_closed(self):
        for stream in ("M", "AB", "", None, 12, True, False, [], {}, ["L"], ("L",)):
            with self.subTest(stream=stream), self.assertRaises(ValidationError):
                self.route(self.request(stream=stream))

    def test_observed_stream_is_required_for_operating_worker_fix_and_critic(self):
        for role in ("worker", "fix", "critic"):
            request = self.request(role=role, worker_context_id="independent")
            del request["stream"]
            with self.subTest(role=role), self.assertRaisesRegex(ValidationError, "observed stream"):
                self.route(request)

    def test_unavailable_fresh_model_or_effort_refuses_without_fallback(self):
        for observation in ([], ["low", "medium"], {"status": "unavailable"}):
            self.capabilities["models"]["gpt-6.1-sol"] = observation
            for role in ("worker", "fix", "critic"):
                with self.subTest(observation=observation, role=role):
                    result = self.route(self.request(role=role, worker_context_id="independent"))
                    self.assertEqual("unavailable", result["status"])
                    self.assertEqual({"model": "gpt-6.1-sol", "reasoning_effort": "high"}, result["requested"])
                    self.assertIn("no fallback", result["reason"])

    def test_explicit_reviewer_pin_below_fresh_floor_refuses(self):
        for stream in STREAM_LABELS:
            for pair in ({"model": "gpt-6-luna", "reasoning_effort": "high"},
                         {"model": "gpt-6.1-sol", "reasoning_effort": "medium"}):
                value = deepcopy(self.value)
                value["streams"][stream]["reviewer"] = {**pair, "pinned": True}
                with self.subTest(stream=stream, pair=pair), self.assertRaises(ValidationError):
                    snapshot = validate_operating(value, self.gov)
                    self.route(self.request(stream=stream, role="critic", worker_context_id="independent"), snapshot)

    def test_upper_lanes_are_draining_after_capacity_reduction(self):
        from test_model_routing import outcome

        reduced = deepcopy(self.value)
        reduced["streams"]["count"] = 3
        snapshot = validate_operating(reduced, self.gov)
        reduced["streams"]["count"] = 6
        six_streams = validate_operating(reduced, self.gov)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "routing.sqlite")
            cases = ((stream, first, continuation) for stream in "GHIJKL"
                     for first in ("worker", "fix") for continuation in ("worker", "fix"))
            for stream, first_role, role in cases:
                with self.subTest(stream=stream, first_role=first_role, role=role):
                    request = self.request(stream=stream)
                    self.assertTrue(self.route(request, snapshot)["draining"])
                    with self.assertRaisesRegex(ValidationError, "draining"):
                        ledger.reserve("project", self.policy, request, self.capabilities,
                                       operating=snapshot, governance=self.gov)
                    owned = self.request(stream=stream, role=first_role,
                                         ticket_id="FIX-" + stream + "-" + first_role + "-" + role, agent_id="agent-X")
                    first = ledger.reserve("project", self.policy, owned, self.capabilities,
                                           operating=self.operating, governance=self.gov)
                    self.assertEqual("reserved", first["status"])
                    settled = ledger.settle(first["run_id"], outcome(first, actual_context_id=owned["context_id"]))
                    self.assertTrue(settled["accepted"])
                    for changed in ({"ticket_id": "NEW-" + stream}, {"agent_id": "agent-Y"}):
                        with self.assertRaisesRegex(ValidationError, "[Dd]raining"):
                            ledger.reserve("project", self.policy, {**owned, **changed, "role": role},
                                           self.capabilities, operating=six_streams, governance=self.gov)
                    continued = ledger.reserve("project", self.policy, {**owned, "role": role},
                                               self.capabilities, operating=six_streams, governance=self.gov)
                    self.assertEqual("reserved", continued["status"])
                    self.assertTrue(continued["draining"])
                    self.assertEqual(six_streams.operating_hash, continued["operating_hash"])
                    settled = ledger.settle(continued["run_id"], outcome(continued, actual_context_id=owned["context_id"]))
                    self.assertTrue(settled["accepted"])

    def test_draining_new_tickets_refuse_every_role(self):
        reduced = deepcopy(self.value)
        reduced["streams"]["count"] = 6
        snapshot = validate_operating(reduced, self.gov)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "routing.sqlite")
            for stream in ("G", "L"):
                for role in ("worker", "fix", "critic", "specialist", "controller"):
                    with self.subTest(stream=stream, role=role):
                        request = self.request(stream=stream, role=role, worker_context_id="other-context")
                        with self.assertRaisesRegex(ValidationError, "no previously admitted ticket"):
                            ledger.reserve("project", self.policy, request, self.capabilities,
                                           operating=snapshot, governance=self.gov)

    def test_draining_review_history_never_grants_implementation_ownership(self):
        from test_model_routing import outcome

        reduced = deepcopy(self.value)
        reduced["streams"]["count"] = 6
        snapshot = validate_operating(reduced, self.gov)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "routing.sqlite")

            def settle(request, operating):
                reserved = ledger.reserve("project", self.policy, request, self.capabilities,
                                          operating=operating, governance=self.gov)
                self.assertEqual("reserved", reserved["status"])
                self.assertTrue(ledger.settle(reserved["run_id"], outcome(
                    reserved, actual_context_id=request["context_id"]))["accepted"])

            for stream in ("G", "L"):
                for review_role in ("critic", "specialist"):
                    with self.subTest(stream=stream, review_role=review_role):
                        review = self.request(stream=stream, ticket_id="REVIEW-" + stream + "-" + review_role,
                                              role=review_role, agent_id="agent-Y", context_id="review-context",
                                              worker_context_id="writer-context")
                        settle(review, self.operating)
                        # A retained ticket with review history alone has no implementation owner.
                        for role in ("worker", "fix"):
                            for agent in ("agent-X", "agent-Y"):
                                with self.subTest(role=role, agent=agent), self.assertRaisesRegex(
                                        ValidationError, "previously admitted implementation identity"):
                                    ledger.reserve("project", self.policy, {**review, "role": role, "agent_id": agent},
                                                   self.capabilities, operating=snapshot, governance=self.gov)
                        owned = {**review, "role": "fix", "agent_id": "agent-X", "context_id": "writer-context"}
                        settle(owned, self.operating)
                        # Later reviews must not replace that implementation owner either.
                        settle(review, snapshot)
                        for role in ("worker", "fix"):
                            with self.subTest(role=role):
                                with self.assertRaisesRegex(ValidationError, "previously admitted implementation identity"):
                                    ledger.reserve("project", self.policy, {**review, "role": role}, self.capabilities,
                                                   operating=snapshot, governance=self.gov)
                                settle({**owned, "role": role}, snapshot)

    def test_draining_keeps_only_latest_admitted_implementation_agent(self):
        from test_model_routing import outcome

        reduced = deepcopy(self.value)
        reduced["streams"]["count"] = 6
        snapshot = validate_operating(reduced, self.gov)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "routing.sqlite")
            for stream in ("G", "L"):
                for first_role in ("worker", "fix"):
                    for last_role in ("worker", "fix"):
                        with self.subTest(stream=stream, first_role=first_role, last_role=last_role):
                            request = self.request(stream=stream,
                                                   ticket_id="LATEST-" + stream + "-" + first_role + "-" + last_role)
                            for agent, role in (("agent-X", first_role), ("agent-Y", last_role)):
                                reserved = ledger.reserve("project", self.policy, {**request, "agent_id": agent, "role": role},
                                                          self.capabilities, operating=self.operating, governance=self.gov)
                                self.assertEqual("reserved", reserved["status"])
                                self.assertTrue(ledger.settle(reserved["run_id"], outcome(
                                    reserved, actual_context_id=request["context_id"]))["accepted"])
                            for role in ("worker", "fix"):
                                with self.subTest(role=role):
                                    with self.assertRaisesRegex(ValidationError, "previously admitted implementation identity"):
                                        ledger.reserve("project", self.policy, {**request, "agent_id": "agent-X", "role": role},
                                                       self.capabilities, operating=snapshot, governance=self.gov)
                                    continued = ledger.reserve("project", self.policy, {**request, "agent_id": "agent-Y", "role": role},
                                                               self.capabilities, operating=snapshot, governance=self.gov)
                                    self.assertEqual("reserved", continued["status"])
                                    self.assertTrue(continued["draining"])
                                    self.assertTrue(ledger.settle(continued["run_id"], outcome(
                                        continued, actual_context_id=request["context_id"]))["accepted"])

    def test_fresh_operating_template_and_legacy_missing_file_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            fresh = plan_initialize_operating(temporary, self.gov, initial_operating=self.value)
            self.assertEqual(12, fresh["inspection"]["streams"])
        legacy = deepcopy(self.gov)
        legacy["execution"].pop("max_lanes")
        legacy["execution"]["max_parallel_tickets"] = 6
        with tempfile.TemporaryDirectory() as temporary:
            old = plan_initialize_operating(temporary, legacy)
            self.assertEqual(3, old["inspection"]["streams"])
            self.assertEqual(6, old["inspection"]["effective_ceiling"])

    def test_missing_audited_operating_refuses_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".agentic").mkdir()
            (root / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(self.gov), encoding="utf-8")
            initialize_operating(root, self.gov)
            (root / "OPERATING_CONFIG.yaml").unlink()
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValidationError, "missing with retained audit history"):
                plan_initialize_operating(root, self.gov)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_planned_target_routing_is_scoped_and_explicit_paths_remain_independent(self):
        fresh_path = ROOT / ".agentic/defaults/routing.yaml"
        legacy_path = ROOT / ".agentic/tests/fixtures/routing/valid.yaml"
        legacy = default_policy(legacy_path)
        ambient = default_policy()
        with routing_context(legacy_path.read_bytes(), "preserved target routing"):
            self.assertEqual(legacy, default_policy())
            self.assertEqual(self.policy["role_defaults"], default_policy(fresh_path)["role_defaults"])
            with self.assertRaisesRegex(RuntimeError, "reset"):
                with routing_context(fresh_path.read_bytes(), "fresh target routing"):
                    self.assertEqual("gpt-6.1-sol", default_policy()["role_defaults"]["worker"]["model"])
                    raise RuntimeError("reset")
            self.assertEqual(legacy, default_policy())
        self.assertEqual(ambient, default_policy())

    def retained_default_route(self, routing, operating, **request):
        governance = deepcopy(self.gov)
        governance["execution"]["model_routing"] = {"routing": ".agentic/routing.yaml"}
        for role in governance["execution"]["roles"].values():
            role["approved_model_ids"] = list(routing["catalog"])
        # Use the production routing-file loader, governance binding and snapshot
        # validation, including the second policy lookup inside select_route.
        with routing_context(json.dumps(routing), "synthetic owner routing"):
            policy = policy_from_config(governance)
            snapshot = validate_operating(operating, governance)
            capabilities = {"models": {m: v["reasoning_efforts"] for m, v in policy["models"].items()}}
            return select_route(policy, self.request(stream="A", **request), capabilities,
                                operating=snapshot, governance=governance)

    def test_generated_worker_default_follows_owner_changes_independent_of_catalog(self):
        for source in ("defaults/routing.yaml", "tests/fixtures/routing/valid.yaml"):
            path = ROOT / ".agentic" / source
            original = load(path)
            operating = default_operating(default_policy(path))
            for change in ("added_model", "added_effort", "default_only", "other_release_default"):
                with self.subTest(source=source, change=change):
                    routing = deepcopy(original)
                    if change == "added_model":
                        routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
                        chosen = {"model": "owner-model", "effort": "high"}
                    elif change == "added_effort":
                        routing["catalog"]["gpt-6.1-sol"]["efforts"].append("xhigh")
                        chosen = {"model": "gpt-6.1-sol", "effort": "xhigh"}
                    elif change == "default_only":
                        chosen = {"model": "gpt-5.6-sol", "effort": "high"}
                    else:
                        chosen = {"model": "gpt-5.6-luna", "effort": "medium"} if source.startswith("defaults/") else {
                            "model": "gpt-6.1-sol", "effort": "high"}
                    routing["role_defaults"]["worker"] = chosen
                    for role in ("worker", "fix"):
                        route = self.retained_default_route(routing, operating, role=role)
                        self.assertEqual("ready", route["status"])
                        self.assertEqual((chosen["model"], chosen["effort"]),
                                         (route["model"], route["reasoning_effort"]))
                        self.assertFalse(route["pinned"])

    def test_matching_catalog_does_not_identify_edited_operating_defaults(self):
        routing = load(ROOT / ".agentic/defaults/routing.yaml")
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        routing["simple_worker"] = {"model": "gpt-5.6-luna", "effort": "high"}
        with routing_context(json.dumps(routing), "synthetic owner defaults"):
            operating = default_operating()
        operating["streams"]["A"]["worker"].update(model="gpt-6.1-sol", reasoning_effort="high")
        for protection in ("none", "pin", "user_source"):
            value = deepcopy(operating)
            if protection == "pin":
                value["streams"]["A"]["worker"]["pinned"] = True
            elif protection == "user_source":
                value["source"] = "user"
            for role in ("worker", "fix"):
                for complexity in ("low", "medium"):
                    with self.subTest(protection=protection, role=role, complexity=complexity):
                        route = self.retained_default_route(routing, value, role=role, complexity=complexity)
                        self.assertEqual("ready", route["status"])
                        self.assertEqual(("gpt-6.1-sol", "high"),
                                         (route["model"], route["reasoning_effort"]))
                        self.assertEqual(protection == "pin", route["pinned"])

    def test_semantically_unchanged_defaults_follow_owner_worker_choice(self):
        for source in ("defaults/routing.yaml", "tests/fixtures/routing/valid.yaml"):
            path = ROOT / ".agentic" / source
            routing = load(path)
            operating = default_operating(default_policy(path))
            routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
            routing["role_defaults"]["worker"] = {"model": "owner-model", "effort": "high"}
            for addition in ("none", "epic_overrides", "controller_pin", "worker_pin",
                             "all_false_pins", "key_order", "combined"):
                value = deepcopy(operating)
                if addition in ("epic_overrides", "combined"):
                    value["epic_overrides"] = {}
                if addition in ("controller_pin", "combined"):
                    value["controller"]["pinned"] = False
                if addition == "worker_pin":
                    value["streams"]["A"]["worker"]["pinned"] = False
                if addition in ("all_false_pins", "combined"):
                    for role in ("controller", "critic", "specialist", "simple_worker"):
                        value[role]["pinned"] = False
                    for label in "ABC":
                        value["streams"][label]["worker"]["pinned"] = False
                if addition in ("key_order", "combined"):
                    value = json.loads(json.dumps(value, sort_keys=True))
                for role in ("worker", "fix"):
                    with self.subTest(source=source, addition=addition, role=role):
                        route = self.retained_default_route(routing, value, role=role)
                        self.assertEqual("ready", route["status"])
                        self.assertEqual(("owner-model", "high"),
                                         (route["model"], route["reasoning_effort"]))
                        self.assertFalse(route["pinned"])

    def test_simple_worker_edits_with_changed_defaults_keep_stored_ordinary_routes(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        routing = load(path)
        routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
        routing["role_defaults"]["worker"] = {"model": "owner-model", "effort": "high"}
        operating = default_operating(default_policy(path))
        operating["simple_worker"].update(model="gpt-5.6-luna", reasoning_effort="low")
        operating["epic_overrides"] = {}
        operating["controller"]["pinned"] = False
        for role in ("worker", "fix"):
            # Editing simple work breaks the whole baseline match; the stored
            # ordinary route differs from policy and wins for both workloads.
            for complexity in ("medium", "low"):
                with self.subTest(role=role, complexity=complexity):
                    route = self.retained_default_route(routing, operating, role=role, complexity=complexity)
                    self.assertEqual("ready", route["status"])
                    self.assertEqual(("gpt-6.1-sol", "high"), (route["model"], route["reasoning_effort"]))

    def test_edit_in_another_ordinary_route_prevents_baseline_inference(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        routing = load(path)
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        for edited_role in ("controller", "other_stream"):
            operating = default_operating(default_policy(path))
            pair = operating["controller"] if edited_role == "controller" else operating["streams"]["B"]["worker"]
            pair["model"] = "gpt-5.6-sol"
            for role in ("worker", "fix"):
                with self.subTest(edited_role=edited_role, role=role):
                    route = self.retained_default_route(routing, operating, role=role)
                    self.assertEqual("ready", route["status"])
                    self.assertEqual(("gpt-6.1-sol", "high"),
                                     (route["model"], route["reasoning_effort"]))

    def test_catalog_changes_alone_preserve_generated_and_legacy_defaults(self):
        for source in ("defaults/routing.yaml", "tests/fixtures/routing/valid.yaml"):
            path = ROOT / ".agentic" / source
            original = load(path)
            operating = default_operating(default_policy(path))
            for change in ("none", "added_model", "added_effort"):
                routing = deepcopy(original)
                if change == "added_model":
                    routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
                elif change == "added_effort":
                    routing["catalog"]["gpt-6.1-sol"]["efforts"].append("xhigh")
                for role in ("worker", "fix", "controller", "critic", "specialist"):
                    with self.subTest(source=source, change=change, role=role):
                        route = self.retained_default_route(routing, operating, role=role,
                                                            worker_context_id="independent")
                        expected = original["role_defaults"]["worker" if role == "fix" else role]
                        self.assertEqual("ready", route["status"])
                        self.assertEqual((expected["model"], expected["effort"]),
                                         (route["model"], route["reasoning_effort"]))
                with self.subTest(source=source, change=change, simple=True):
                    route = self.retained_default_route(routing, operating, complexity="low")
                    expected = original["simple_worker"]
                    self.assertEqual("ready", route["status"])
                    self.assertEqual((expected["model"], expected["effort"]),
                                     (route["model"], route["reasoning_effort"]))

    def test_single_simple_worker_field_edits_prevent_baseline_substitution(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        routing = load(path)
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        for field, choice in (("model", "gpt-5.6-luna"), ("reasoning_effort", "medium")):
            operating = default_operating(default_policy(path))
            operating["simple_worker"][field] = choice
            for role in ("worker", "fix"):
                for simple in (False, True):
                    with self.subTest(field=field, role=role, simple=simple):
                        route = self.retained_default_route(
                            routing, operating, role=role, complexity="low" if simple else "medium")
                        # Even one field prevents a whole-content match, so
                        # neither workload may substitute the new policy route.
                        expected = operating["streams"]["A"]["worker"]
                        self.assertEqual("ready", route["status"])
                        self.assertEqual((expected["model"], expected["reasoning_effort"]),
                                         (route["model"], route["reasoning_effort"]))

    def test_owner_worker_choice_matching_an_older_default_is_preserved(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        for changed_catalog in (False, True):
            routing = load(path)
            if changed_catalog:
                routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
            operating = default_operating(default_policy(path))
            operating["simple_worker"].update(model="gpt-5.6-luna", reasoning_effort="low")
            operating["streams"]["A"]["worker"] = {
                "model": "gpt-5.6-luna", "reasoning_effort": "medium"}
            for role in ("worker", "fix"):
                for complexity in ("low", "medium"):
                    with self.subTest(changed_catalog=changed_catalog, role=role, complexity=complexity):
                        route = self.retained_default_route(routing, operating, role=role, complexity=complexity)
                        self.assertEqual("ready", route["status"])
                        self.assertEqual(("gpt-5.6-luna", "medium"),
                                         (route["model"], route["reasoning_effort"]))

    def test_historical_catalog_with_same_simple_pair_preserves_owner_choice(self):
        from agentic.canonical import load_yaml

        if not (ROOT / ".agentic/tests/fixtures/upgrades/versions.json").is_file():
            self.skipTest("historical upgrade fixtures are excluded from portable releases")
        from upgrade_fixtures import fixture_blob, fixture_manifest

        manifest = fixture_manifest("1.9.4")
        entry = manifest["owner_files"][".agentic/PROJECT_CONFIG.yaml"]
        governance = load_yaml(fixture_blob(entry["sha256"]))
        policy = policy_from_config(governance)
        legacy = default_policy(ROOT / ".agentic/tests/fixtures/routing/valid.yaml")
        self.assertEqual(legacy["simple_worker"], policy["simple_worker"])
        self.assertNotEqual(legacy["models"], policy["models"])
        operating = default_operating(policy)
        operating["streams"]["A"]["worker"] = {
            "model": "gpt-5.6-luna", "reasoning_effort": "medium"}
        snapshot = validate_operating(operating, governance)
        capabilities = {"models": {m: v["reasoning_efforts"] for m, v in policy["models"].items()}}
        for role in ("worker", "fix"):
            for complexity in ("low", "medium"):
                with self.subTest(role=role, complexity=complexity):
                    route = select_route(policy, self.request(stream="A", role=role, complexity=complexity),
                                         capabilities, operating=snapshot, governance=governance)
                    self.assertEqual("ready", route["status"])
                    self.assertEqual(("gpt-5.6-luna", "medium"),
                                     (route["model"], route["reasoning_effort"]))

    def test_single_operating_route_field_edits_are_preserved(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        routing = load(path)
        for role in ("worker", "controller", "critic", "specialist"):
            for field, choice in (("model", "gpt-6-luna"), ("reasoning_effort", "xhigh")):
                # These choices satisfy the governing floor, so a floor cannot
                # conceal replacement of an accepted single-field edit.
                changed = deepcopy(routing)
                changed["catalog"]["gpt-6.1-sol"]["efforts"].append("xhigh")
                changed["review_floor"] = {"model": "gpt-6-luna", "effort": "high"}
                operating = default_operating(default_policy(path))
                pair = operating["streams"]["A"]["worker"] if role == "worker" else operating[role]
                pair[field] = choice
                with self.subTest(role=role, field=field):
                    route = self.retained_default_route(changed, operating, role=role,
                                                        worker_context_id="independent")
                    self.assertEqual("ready", route["status"])
                    self.assertEqual((pair["model"], pair["reasoning_effort"]),
                                     (route["model"], route["reasoning_effort"]))

    def test_unidentified_generated_origin_preserves_stored_choices(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        routing = load(path)
        routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
        routing["role_defaults"]["worker"] = {"model": "owner-model", "effort": "high"}
        operating = default_operating(default_policy(path))
        operating["controller"]["model"] = "gpt-5.6-sol"
        for role in ("worker", "fix", "controller", "critic", "specialist"):
            with self.subTest(role=role):
                route = self.retained_default_route(routing, operating, role=role,
                                                    worker_context_id="independent")
                expected = operating["streams"]["A"]["worker"] if role in ("worker", "fix") else operating[role]
                self.assertEqual("ready", route["status"])
                self.assertEqual((expected["model"], expected["reasoning_effort"]),
                                 (route["model"], route["reasoning_effort"]))

    def test_exact_legacy_content_follows_policy_despite_conflicting_catalog(self):
        legacy_path = ROOT / ".agentic/tests/fixtures/routing/valid.yaml"
        operating = default_operating(default_policy(legacy_path))
        routing = load(ROOT / ".agentic/defaults/routing.yaml")
        # Whole content exactly matches the legacy generator. A fresh catalog
        # cannot veto that match: unpinned defaults follow the current policy.
        routing["review_floor"] = {"model": "gpt-6.1-sol", "effort": "medium"}
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        for role in ("worker", "fix", "controller", "critic", "specialist"):
            for complexity in ("low", "medium"):
                with self.subTest(role=role, complexity=complexity):
                    route = self.retained_default_route(routing, operating, role=role, complexity=complexity,
                                                        worker_context_id="independent")
                    expected = (routing["simple_worker"] if role in ("worker", "fix") and complexity == "low"
                                else routing["role_defaults"]["worker" if role == "fix" else role])
                    self.assertEqual("ready", route["status"])
                    self.assertEqual((expected["model"], expected["effort"]),
                                     (route["model"], route["reasoning_effort"]))

    def test_missing_simple_worker_keys_still_refuse(self):
        routing = load(ROOT / ".agentic/defaults/routing.yaml")
        for field in ("simple_worker", "model", "reasoning_effort", "enabled"):
            operating = deepcopy(self.value)
            if field == "simple_worker":
                operating.pop(field)
            else:
                operating["simple_worker"].pop(field)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.retained_default_route(routing, operating)

    def test_whole_baseline_simple_edits_keep_stored_ordinary_routes(self):
        path = ROOT / ".agentic/defaults/routing.yaml"
        routing = load(path)
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        for field, choice in (("model", "gpt-5.6-luna"), ("reasoning_effort", "medium")):
            operating = default_operating(default_policy(path), count=12)
            operating["simple_worker"][field] = choice
            for role in ("worker", "fix"):
                for complexity in ("low", "medium"):
                    with self.subTest(field=field, role=role, complexity=complexity):
                        route = self.retained_default_route(routing, operating, role=role, complexity=complexity)
                        # Stored ordinary routes win even for simple work when
                        # changed policy defaults differ and no baseline matches.
                        expected = operating["streams"]["A"]["worker"]
                        self.assertEqual("ready", route["status"])
                        self.assertEqual((expected["model"], expected["reasoning_effort"]),
                                         (route["model"], route["reasoning_effort"]))

    def test_simple_worker_edits_are_honoured_when_routing_defaults_are_untouched(self):
        for source in ("defaults/routing.yaml", "tests/fixtures/routing/valid.yaml"):
            path = ROOT / ".agentic" / source
            routing = load(path)
            for edit in ("model", "reasoning_effort", "both", "disabled"):
                operating = default_operating(default_policy(path))
                if edit in ("model", "both"):
                    operating["simple_worker"]["model"] = "gpt-5.6-sol"
                if edit in ("reasoning_effort", "both"):
                    operating["simple_worker"]["reasoning_effort"] = "medium"
                if edit == "disabled":
                    operating["simple_worker"]["enabled"] = False
                for role in ("worker", "fix"):
                    for complexity in ("low", "medium"):
                        with self.subTest(source=source, edit=edit, role=role, complexity=complexity):
                            route = self.retained_default_route(routing, operating, role=role, complexity=complexity)
                            expected = (operating["simple_worker"] if complexity == "low" and edit != "disabled"
                                        else operating["streams"]["A"]["worker"])
                            self.assertEqual("ready", route["status"])
                            self.assertEqual((expected["model"], expected["reasoning_effort"]),
                                             (route["model"], route["reasoning_effort"]))

    def test_whole_baseline_one_stream_owner_choice_is_preserved(self):
        routing = load(ROOT / ".agentic/defaults/routing.yaml")
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        routing["simple_worker"] = {"model": "gpt-5.6-luna", "effort": "high"}
        with routing_context(json.dumps(routing), "synthetic one-stream defaults"):
            operating = default_operating(count=1)
        operating["streams"]["A"]["worker"].update(model="gpt-6.1-sol", reasoning_effort="high")
        for role in ("worker", "fix"):
            for complexity in ("low", "medium"):
                with self.subTest(role=role, complexity=complexity):
                    route = self.retained_default_route(routing, operating, role=role, complexity=complexity)
                    self.assertEqual("ready", route["status"])
                    self.assertEqual(("gpt-6.1-sol", "high"), (route["model"], route["reasoning_effort"]))

    def test_whole_baseline_redundant_route_and_count_edits_keep_stored_choices(self):
        for source in ("defaults/routing.yaml", "tests/fixtures/routing/valid.yaml"):
            path = ROOT / ".agentic" / source
            routing = load(path)
            original_policy = default_policy(path)
            routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
            routing["role_defaults"]["worker"] = {"model": "owner-model", "effort": "high"}
            for edit in ("redundant_reviewer", "reduced_count"):
                operating = default_operating(original_policy, count=12)
                if edit == "redundant_reviewer":
                    operating["streams"]["A"]["reviewer"] = deepcopy(operating["critic"])
                else:
                    operating["streams"]["count"] = 3
                for role in ("worker", "fix"):
                    with self.subTest(source=source, edit=edit, role=role):
                        route = self.retained_default_route(routing, operating, role=role)
                        expected = operating["streams"]["A"]["worker"]
                        self.assertEqual("ready", route["status"])
                        self.assertEqual((expected["model"], expected["reasoning_effort"]),
                                         (route["model"], route["reasoning_effort"]))

    def test_whole_legacy_baseline_follows_policy_even_with_a_fresh_catalog(self):
        legacy = default_policy(ROOT / ".agentic/tests/fixtures/routing/valid.yaml")
        operating = default_operating(legacy)
        routing = load(ROOT / ".agentic/defaults/routing.yaml")
        routing["review_floor"] = {"model": "gpt-6.1-sol", "effort": "medium"}
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        for role in ("worker", "fix", "controller", "critic", "specialist"):
            for complexity in ("low", "medium"):
                with self.subTest(role=role, complexity=complexity):
                    route = self.retained_default_route(routing, operating, role=role, complexity=complexity,
                                                        worker_context_id="independent")
                    expected = (routing["simple_worker"] if role in ("worker", "fix") and complexity == "low"
                                else routing["role_defaults"]["worker" if role == "fix" else role])
                    self.assertEqual("ready", route["status"])
                    self.assertEqual((expected["model"], expected["effort"]),
                                     (route["model"], route["reasoning_effort"]))

    def test_changed_catalog_defaults_preserve_pins_explicit_choices_and_floors(self):
        path = ROOT / ".agentic/tests/fixtures/routing/valid.yaml"
        routing = load(path)
        operating = default_operating(default_policy(path))
        routing["catalog"]["owner-model"] = {"efforts": ["medium", "high"]}
        routing["role_defaults"]["worker"] = {"model": "gpt-5.6-sol", "effort": "high"}
        for choice in ("pin", "explicit_route", "user_source"):
            value = deepcopy(operating)
            if choice == "pin":
                value["streams"]["A"]["worker"]["pinned"] = True
            elif choice == "explicit_route":
                # A custom legacy-project route may equal the fresh default.
                value["streams"]["A"]["worker"] = {"model": "gpt-6.1-sol", "reasoning_effort": "high"}
            else:
                value["source"] = "user"
            with self.subTest(choice=choice):
                route = self.retained_default_route(routing, value)
                expected = value["streams"]["A"]["worker"]
                self.assertEqual("ready", route["status"])
                self.assertEqual((expected["model"], expected["reasoning_effort"]),
                                 (route["model"], route["reasoning_effort"]))
                risky = self.retained_default_route(routing, value, risk="high")
                self.assertEqual("ready", risky["status"])
                self.assertEqual(("gpt-6.1-sol", expected["reasoning_effort"]),
                                 (risky["model"], risky["reasoning_effort"]))
        routing["role_defaults"]["critic"] = {"model": "gpt-5.6-luna", "effort": "low"}
        reviewed = self.retained_default_route(routing, operating, role="critic", worker_context_id="independent")
        self.assertEqual("ready", reviewed["status"])
        self.assertEqual(("gpt-6.1-sol", "medium"), (reviewed["model"], reviewed["reasoning_effort"]))

    def test_published_195_runtime_retains_original_upper_label_refusal(self):
        import hashlib
        import io
        import subprocess
        import zipfile
        from agentic.child_process import child_env
        from source_only import AWF_SOURCE_REPOSITORY
        if not AWF_SOURCE_REPOSITORY or not (ROOT / ".git").exists():
            self.skipTest("published historical Git source is absent from closed release archives")
        commit = "8ad399943c8f5e4f17db72f3e321b9365ee27782"
        pin = "1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7"
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", commit,
                                  ".agentic", "MANIFEST.json"], capture_output=True, timeout=30, env=child_env())
        self.assertEqual(0, archive.returncode, archive.stderr.decode("utf-8", errors="replace"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(archive.stdout)) as historical:
                historical.extractall(root)
            self.assertEqual(pin, hashlib.sha256((root / "MANIFEST.json").read_bytes()).hexdigest())
            script = """
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / '.agentic/lib'))
from agentic import ValidationError
from agentic.model_routing import default_policy, select_route
policy = default_policy(root / '.agentic/routing.yaml')
request = json.loads(sys.argv[2])
caps = {'models': {m: v['reasoning_efforts'] for m, v in policy['models'].items()}}
results = {}
for label in 'ABCDEFGHIJKL':
    try:
        results[label] = select_route(policy, {**request, 'stream': label}, caps)['status']
    except ValidationError as error:
        results[label] = str(error)
print(json.dumps(results))
"""
            done = subprocess.run([sys.executable, "-B", "-I", "-c", script, str(root), json.dumps(self.request())],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30, env=child_env())
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            results = json.loads(done.stdout)
            self.assertEqual({label: "ready" for label in STREAM_LABELS[:6]}, {label: results[label] for label in STREAM_LABELS[:6]})
            for label in STREAM_LABELS[6:]:
                self.assertIn("stream must be A through F", results[label])

    def test_release_routing_and_generated_new_project_defaults_agree(self):
        if not (ROOT / "scripts/generate_examples.py").is_file():
            self.skipTest("source generator is not installed")
        spec = importlib.util.spec_from_file_location("fresh_examples", ROOT / "scripts/generate_examples.py")
        if spec is None or spec.loader is None:
            self.skipTest("source generator is not installed")
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            generator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(generator)
            config = generator.config(False, self.policy, fresh_defaults=True)
        finally:
            sys.path.remove(str(ROOT / "scripts"))
        self.assertEqual(12, config["execution"]["max_lanes"])
        self.assertEqual(12, config["execution"]["max_parallel_tickets"])
        for role in self.policy["role_defaults"]:
            self.assertEqual("gpt-6.1-sol", config["execution"]["roles"][role]["model"])
            self.assertEqual("high", config["execution"]["roles"][role]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
