"""Generated operating-config schemas enforce their governed lane ceiling."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_only import skip_unless_source_repo  # noqa: E402
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError  # noqa: E402
from agentic.contracts import Contracts  # noqa: E402


def route():
    return {"worker": {"model": "gpt-5.6-luna", "reasoning_effort": "medium"}}


def operating(count):
    labels = generate_contracts.stream_labels(count)
    return {
        "version": 1,
        "source": "default",
        "streams": {"count": count, **{label: route() for label in labels}},
        "critic": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        "controller": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        "specialist": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        "simple_worker": {
            "enabled": True,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "low",
        },
    }


@skip_unless_source_repo(
    "needs the source-only contract generator",
    "scripts/generate_contracts.py",
)
class OperatingConfigGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        global generate_contracts
        import generate_contracts  # noqa: E402

    def contracts(self, schema):
        temporary = tempfile.TemporaryDirectory(prefix="awf-operating-schema-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "operating-config.schema.json"
        path.write_text(json.dumps(schema), encoding="utf-8")
        return Contracts(path.parent)

    def test_default_schema_enforces_default_six_lane_ceiling(self):
        schema = generate_contracts.catalog()["operating-config"]
        count = schema["properties"]["streams"]["properties"]["count"]
        self.assertEqual(6, count["maximum"])
        self.assertEqual(6, count["x-awf-governed-maximum"]["default"])
        self.assertEqual(
            list(generate_contracts.stream_labels(6)),
            [key for key in schema["properties"]["streams"]["properties"]
             if key != "count"],
        )
        generated = self.contracts(schema)
        shipped = Contracts(ROOT / ".agentic/schemas")
        generated.validate("operating-config", operating(6))
        shipped.validate("operating-config", operating(6))

        above_ceiling_without_unknown_route = operating(6)
        above_ceiling_without_unknown_route["streams"]["count"] = 7
        with self.assertRaises(ValidationError):
            shipped.validate("operating-config", above_ceiling_without_unknown_route)

        missing = operating(6)
        for label in ("D", "E", "F"):
            del missing["streams"][label]
        with self.assertRaises(ValidationError):
            shipped.validate("operating-config", missing)

    def test_configured_schema_requires_and_accepts_eight_active_routes(self):
        schema = generate_contracts.catalog(max_lanes=8)["operating-config"]
        count = schema["properties"]["streams"]["properties"]["count"]
        self.assertEqual(8, count["maximum"])
        self.assertEqual(6, count["x-awf-governed-maximum"]["default"])
        self.assertEqual(
            list(generate_contracts.stream_labels(8)),
            [key for key in schema["properties"]["streams"]["properties"]
             if key != "count"],
        )
        contracts = self.contracts(schema)
        contracts.validate("operating-config", operating(8))

        above_ceiling_without_unknown_route = operating(8)
        above_ceiling_without_unknown_route["streams"]["count"] = 9
        with self.assertRaises(ValidationError):
            contracts.validate("operating-config", above_ceiling_without_unknown_route)

        missing = operating(8)
        for label in generate_contracts.stream_labels(8)[3:]:
            del missing["streams"][label]
        with self.assertRaises(ValidationError):
            contracts.validate("operating-config", missing)

        unknown = operating(8)
        unknown["streams"]["I"] = route()
        with self.assertRaises(ValidationError):
            contracts.validate("operating-config", unknown)

    def test_generator_rejects_invalid_ceiling(self):
        for value in (0, -1, True, "8", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                generate_contracts.catalog(max_lanes=value)


if __name__ == "__main__":
    unittest.main()
