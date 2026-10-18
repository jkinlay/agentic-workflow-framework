"""AWF-88 AC4: independently reject exhaustion-level planning ceilings."""
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

    def test_operating_stream_count_retains_six_stream_bound(self):
        self.check_ceiling("operating-config", "properties/streams/properties/count", 1, 6)

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
