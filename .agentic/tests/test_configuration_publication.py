import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.configuration import (
    extract_large_file_allowances,
    publication_project_config_binding,
    validate_large_file_allowances,
    validate_publication_project_config,
)
from agentic.contracts import Contracts


ROOT = Path(__file__).resolve().parents[2]


def project_config():
    return json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))


def allowance(path="artifacts/existing.dat", digest="1" * 64):
    return {
        "path": path,
        "sha256": digest,
        "reason": "Reviewed historical artifact",
        "approver": "fixture-owner",
    }


class ConfigurationPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")

    def test_legacy_detector_only_and_empty_allowance_shapes(self):
        for publication in (
            None,
            {"deny_literals": [], "deny_regexes": [], "internal_hostnames": []},
            {"deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
             "large_file_allowances": []},
        ):
            with self.subTest(publication=publication):
                config = project_config()
                if publication is not None:
                    config["publication"] = publication
                validate_publication_project_config(config, self.contracts)
                self.assertEqual((), extract_large_file_allowances(config))

    def test_ordered_defensive_snapshot_and_every_field_binding(self):
        first = allowance("artifacts/first.dat", "1" * 64)
        second = allowance("artifacts/second.dat", "2" * 64)
        config = project_config()
        config["publication"] = {
            "deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
            "large_file_allowances": [first, second],
        }
        snapshot = extract_large_file_allowances(config)
        binding = publication_project_config_binding(config)
        first["reason"] = "mutated after extraction"
        config["publication"]["large_file_allowances"].reverse()
        self.assertEqual("artifacts/first.dat", snapshot[0]["path"])
        self.assertEqual("Reviewed historical artifact", snapshot[0]["reason"])
        self.assertNotEqual(binding, publication_project_config_binding(config))

        baseline = project_config()
        baseline["publication"] = {
            "deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
            "large_file_allowances": [allowance()],
        }
        original = publication_project_config_binding(baseline)
        for field, replacement in (
            ("path", "artifacts/renamed.dat"),
            ("sha256", "2" * 64),
            ("reason", "Different reviewed reason"),
            ("approver", "different-owner"),
        ):
            changed = copy.deepcopy(baseline)
            changed["publication"]["large_file_allowances"][0][field] = replacement
            self.assertNotEqual(original, publication_project_config_binding(changed), field)

    def test_schema_and_runtime_boundary_corpus_is_portable_total_and_redacted(self):
        config = project_config()
        config["publication"] = {
            "deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
            "large_file_allowances": [allowance()],
        }
        validate_publication_project_config(config, self.contracts)
        self.assertEqual((allowance(),), validate_large_file_allowances([allowance()]))

        invalid = (
            {**allowance(), "path": "../outside.dat"},
            {**allowance(), "path": "artifacts\\existing.dat"},
            {**allowance(), "path": "artifacts/name:stream.dat"},
            {**allowance(), "path": "artifacts/AUX.txt"},
            {**allowance(), "path": "artifacts/trailing. "},
            {**allowance(), "path": "artifacts/\ud800.dat"},
            {**allowance(), "reason": " padded "},
            {**allowance(), "approver": "owner\x7f"},
            {**allowance(), "unknown": "must-fail-closed"},
        )
        for entry in invalid:
            with self.subTest(entry=repr(entry)), self.assertRaises(ValidationError) as caught:
                validate_large_file_allowances([entry])
            self.assertNotIn("must-fail-closed", str(caught.exception))
            self.assertNotIn("padded", str(caught.exception))

        redacted = project_config()
        redacted_entry = allowance()
        redacted_entry["private-note"] = "metadata-must-not-echo"
        redacted["publication"] = {
            "deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
            "large_file_allowances": [redacted_entry],
        }
        with self.assertRaises(ValidationError) as caught:
            validate_publication_project_config(redacted, self.contracts)
        self.assertIn("$.publication.large_file_allowances[0]", str(caught.exception))
        self.assertNotIn("metadata-must-not-echo", str(caught.exception))
        self.assertNotIn("private-note", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
