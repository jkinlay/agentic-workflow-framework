"""AWF-115 generated ticket-plan contract regressions."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import generate_contracts  # noqa: E402
from agentic.publication import UNC, _detectors, _scan_text  # noqa: E402


class TicketPlanGenerationTests(unittest.TestCase):
    def test_awf115_ticket_plan_is_first_generated_contract(self):
        catalog = generate_contracts.render_catalog()
        self.assertEqual("ticket-plan", next(iter(catalog)))

    def test_ticket_plan_is_registered_in_generic_schema_catalog(self):
        self.assertIn("ticket-plan", generate_contracts.catalog())

    def test_ticket_plan_uses_the_release_schema_revision(self):
        schema = generate_contracts.catalog()["ticket-plan"]
        self.assertEqual(3, schema["properties"]["schema_version"]["const"])

    def test_exact_path_patterns_are_scan_safe_without_changing_semantics(self):
        schema = generate_contracts.catalog()["ticket-plan"]
        patterns = (
            schema["properties"]["tickets"]["items"]["properties"]
            ["write_paths"]["items"]["pattern"],
            schema["properties"]["edges"]["items"]["properties"]
            ["paths"]["items"]["pattern"],
        )
        separator = chr(92)
        invalid_paths = (
            "src" + separator + "file.py",
            separator * 2 + "server" + separator + "share" + separator + "file.py",
            "C" + chr(58) + "/repo/file.py",
            "../file.py",
            "src/../file.py",
            "src/./file.py",
            "src/*.py",
            "src/?.py",
            "src/[ab].py",
            "src/{a,b}.py",
            "src/" + chr(0) + "file.py",
            "src/" + chr(31) + "file.py",
            "src/" + chr(127) + "file.py",
        )

        for pattern in patterns:
            with self.subTest(pattern=pattern, check="publication-scan"):
                self.assertIsNone(UNC.search(json.dumps(pattern)))
            for path in (
                "src/file.py",
                ".agentic/schemas/ticket-plan.schema.json",
                "README",
                "docs/file name.md",
            ):
                with self.subTest(pattern=pattern, path=path, valid=True):
                    self.assertIsNotNone(re.fullmatch(pattern, path))
            for path in invalid_paths:
                with self.subTest(pattern=pattern, path=repr(path), valid=False):
                    self.assertIsNone(re.fullmatch(pattern, path))

    def test_checked_in_schema_is_generator_output(self):
        expected = generate_contracts.render_catalog()["ticket-plan"]
        self.assertEqual(
            expected,
            (ROOT / ".agentic/schemas/ticket-plan.schema.json").read_bytes(),
        )

    def test_generator_writes_ticket_plan_without_hand_authored_residue(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated_root = Path(temporary)
            with mock.patch.object(generate_contracts, "ROOT", generated_root):
                self.assertEqual(0, generate_contracts.main(()))
            actual = generated_root / ".agentic/schemas/ticket-plan.schema.json"
            self.assertTrue(actual.is_file())
            self.assertEqual(
                generate_contracts.render_catalog()["ticket-plan"],
                actual.read_bytes(),
            )
            self.assertEqual(
                "urn:awf:1.2:ticket-plan",
                json.loads(actual.read_text(encoding="utf-8"))["$id"],
            )


# Original patterns are retained literally so equivalence is checked against
# the previous contract rather than a second implementation of the new regex.
OLD_PATH = r"^(?!/)(?!.*[\\:*?\[\]\x00-\x1f\x7f])(?!.*(?:^|/)\.{1,2}(?:/|$))[^/]+(?:/[^/]+)*$"
OLD_TEXT = r"^\S(?:[^\x00-\x1f\x7f]*\S)?$"


class LargeFileAllowancePatternTests(unittest.TestCase):
    def setUp(self):
        self.fields = generate_contracts.catalog()["project-config"]["properties"]["publication"]["properties"]["large_file_allowances"]["items"]["properties"]

    def assert_equivalent(self, field, old):
        previous = re.compile(old)
        current = re.compile(self.fields[field]["pattern"])
        cases = ["", ".", "..", "...", "/", "/data/blob", "data/blob",
                 "data//blob", "data/./blob", "data/../blob", "../blob",
                 "data/", " data/blob", "data/blob ", "reason with spaces",
                 " reviewer", "reviewer ", "reviewer\n", "a\nb", "a\tb",
                 "a\x00b", "a\x7fb", "a\\b", "\\", "[blob]", "a*b", "a?b",
                 "a:b", "a\u00a0b", "\u00a0a", "a\u00a0", "\u2003", "a\u2003b"]
        # Include each byte alone and at endpoints/interiors of multi-char
        # values: negated classes and the legacy dollar anchor both matter.
        for number in range(256):
            char = chr(number)
            cases.extend((char, "a" + char, char + "a", "a" + char + "b",
                          "data/" + char + "/blob"))
        for value in cases:
            with self.subTest(field=field, value=repr(value)):
                self.assertEqual(bool(previous.search(value)), bool(current.search(value)))

    def test_path_pattern_is_equivalent(self):
        self.assert_equivalent("path", OLD_PATH)

    def test_reason_pattern_is_equivalent(self):
        self.assert_equivalent("reason", OLD_TEXT)

    def test_approver_pattern_is_equivalent(self):
        self.assert_equivalent("approver", OLD_TEXT)

    def test_generated_schema_has_no_blocking_publication_findings(self):
        detectors, allows = _detectors({}, {})
        rendered = generate_contracts.render_catalog()["project-config"].decode("utf-8")
        findings = _scan_text(rendered, commit="candidate", path=".agentic/schemas/project-config.schema.json",
                              source="head", detectors=detectors, allows=allows)
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
