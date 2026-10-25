"""AWF-88 AC4: independently reject exhaustion-level planning ceilings."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_contracts  # noqa: E402
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import operating as op  # noqa: E402
from agentic.canonical import load  # noqa: E402


class ResourceCeilingTests(unittest.TestCase):
    def check_ceiling(self, name, path, minimum, maximum=1000):
        # Validate each scalar in isolation: no unrelated fixture error can
        # accidentally make an excessive capacity appear to fail closed.
        sources = {
            "generated": generate_contracts.catalog()[name],
            "committed": json.loads(
                (ROOT / f".agentic/schemas/{name}.schema.json").read_bytes()),
        }
        for source, schema in sources.items():
            for key in path.split("/"):
                schema = schema[key]
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            for value in (maximum + 1, 9007199254740991, 10**100):
                with self.subTest(source=source, rejected=value):
                    self.assertFalse(validator.is_valid(value))
            for value in (minimum, maximum):
                with self.subTest(source=source, accepted=value):
                    self.assertTrue(validator.is_valid(value))
            for value in (minimum - 1, None, True, 1.5, "unbounded"):
                with self.subTest(source=source, malformed=value):
                    self.assertFalse(validator.is_valid(value))

    def test_ticket_plan_lane_cap(self):
        self.check_ceiling("ticket-plan", "properties/lane_cap", 1)

    def test_project_parallel_tickets(self):
        self.check_ceiling("project-config", "properties/execution/properties/max_parallel_tickets", 1)

    def test_project_parallel_tickets_per_stream(self):
        self.check_ceiling("project-config", "properties/execution/properties/max_parallel_tickets_per_stream", 1)

    def test_broker_workers(self):
        self.check_ceiling("project-config", "properties/execution/properties/host_broker/properties/max_workers", 1)

    def test_broker_heavy_jobs(self):
        self.check_ceiling("project-config", "properties/execution/properties/host_broker/properties/max_heavy_jobs", 0)

    def test_broker_gpu_jobs(self):
        self.check_ceiling("project-config", "properties/execution/properties/host_broker/properties/max_gpu_jobs", 0)

    def test_broker_named_resource_capacity(self):
        self.check_ceiling("project-config", "properties/execution/properties/host_broker/properties/resources/additionalProperties", 1)

    def test_project_independent_reviewer_count(self):
        self.check_ceiling("project-config", "properties/execution/properties/independent_reviewers/properties/count", 0)

    def test_lease_worker_slots(self):
        self.check_ceiling("host-lease", "properties/worker_slots", 1)

    def test_lease_heavy_slots(self):
        self.check_ceiling("host-lease", "properties/heavy_slots", 0)

    def test_lease_gpu_slots(self):
        self.check_ceiling("host-lease", "properties/gpu_slots", 0)

    def test_lease_named_resource_slots(self):
        self.check_ceiling("host-lease", "properties/resources_held/items/properties/slots", 1)

    def test_attestation_named_resource_slots(self):
        self.check_ceiling("run-attestation", "properties/resources_held/items/properties/slots", 1)

    def test_operating_stream_count_retains_runtime_default_and_configured_bounds(self):
        for configured, parallel, broker in ((None, 20, None), (1, 20, None),
                                             (6, 20, None), (12, 20, None),
                                             (12, 20, 4), (12, 5, None)):
            expected = min(6 if configured is None else configured, parallel,
                           broker if broker is not None else 12)
            with self.subTest(configured=configured, parallel=parallel, broker=broker), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                governance = load(ROOT / ".agentic/PROJECT_CONFIG.yaml")
                execution = governance["execution"]
                execution.update(max_parallel_tickets=parallel)
                execution["host_broker"]["enabled"] = broker is not None
                if broker is not None:
                    execution["host_broker"]["max_workers"] = broker
                execution["independent_reviewers"].pop("count", None)
                if configured is None:
                    execution.pop("max_lanes", None)
                else:
                    execution["max_lanes"] = configured
                value = op.default_operating(op._routing_policy(governance))
                route = value["streams"]["A"]
                value["streams"] = {"count": expected, **{
                    label: deepcopy(route) for label in op.stream_labels(expected)}}
                (root / ".agentic").mkdir()
                (root / op.GOVERNANCE).write_bytes(op._json(governance))
                (root / op.CONFIG).write_bytes(op._json(value))
                op.initialize_operating(root, governance)
                capacity = op.operating_ceiling(governance)
                source = (op.DEFAULT_MAX_LANES_SOURCE if configured is None
                          else "$.execution.host_broker.max_workers" if broker is not None
                          else "$.execution.max_parallel_tickets" if parallel < configured
                          else "$.execution.max_lanes")
                self.assertEqual([source], capacity["effective_ceiling_sources"])
                for loader in (op.validate_operating, op.load_operating, op.read_operating):
                    with self.subTest(loader=loader.__name__):
                        snapshot = loader(value if loader is op.validate_operating else root, governance)
                        self.assertEqual(expected, snapshot.count)
                        self.assertEqual(expected, snapshot.config["streams"]["count"])
                rejected = deepcopy(value)
                rejected["streams"]["count"] = expected + 1
                rejected["streams"][op.stream_labels(expected + 1)[-1]] = deepcopy(route)
                # Also prove default-seven refusal without a G route.
                candidates = [rejected]
                if configured is None:
                    missing_route = deepcopy(rejected)
                    del missing_route["streams"]["G"]
                    candidates.append(missing_route)
                for rejected in candidates:
                    (root / op.CONFIG).write_bytes(op._json(rejected))
                    before = {p.relative_to(root).as_posix(): p.read_bytes()
                              for p in root.rglob("*") if p.is_file()}
                    for loader in (op.validate_operating, op.load_operating, op.read_operating):
                        with self.subTest(loader=loader.__name__, rejected=expected + 1):
                            with self.assertRaises(op.OperatingError) as caught:
                                loader(rejected if loader is op.validate_operating else root, governance)
                            self.assertTrue(any(
                                item["path"] == "$.streams.count"
                                and item["governance_path"] == (None if configured is None else source)
                                and item["governance_value"] == expected
                                and source in item["reason"]
                                and "1.." + str(expected) in item["reason"]
                                for item in caught.exception.refusals), caught.exception.refusals)
                    inspection = op.inspect_operating(root, governance)
                    self.assertEqual("REJECTED", inspection["status"])
                    self.assertIsNone(inspection["hash"])
                    self.assertIsNone(inspection["streams"])
                    self.assertEqual(before, {p.relative_to(root).as_posix(): p.read_bytes()
                                             for p in root.rglob("*") if p.is_file()})
                    self.assertFalse((root / op.JOURNAL).exists())

    def test_project_max_lanes_capacity_input(self):
        self.check_ceiling("project-config", "properties/execution/properties/max_lanes", 1, 12)

    def test_committed_catalog_equals_fresh_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(generate_contracts, "ROOT", Path(temporary)):
                self.assertEqual(0, generate_contracts.main(()))
            generated = Path(temporary) / ".agentic/schemas"
            committed = ROOT / ".agentic/schemas"
            self.assertEqual(
                sorted(p.name for p in generated.glob("*.schema.json")),
                sorted(p.name for p in committed.glob("*.schema.json")),
            )
            for path in generated.glob("*.schema.json"):
                with self.subTest(schema=path.name):
                    self.assertEqual(path.read_bytes(), (committed / path.name).read_bytes())


if __name__ == "__main__":
    unittest.main()
