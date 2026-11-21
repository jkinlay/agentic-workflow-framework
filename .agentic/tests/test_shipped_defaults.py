"""AWF-305: shipped defaults that installed tests read instead of adopter files."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402
import shipped_defaults  # noqa: E402

from agentic.contracts import Contracts  # noqa: E402
from agentic.installer import OPERATOR_CONFIG_PATHS, SOURCE_CONFIG_PATHS, managed  # noqa: E402
from agentic.model_routing import load_routing  # noqa: E402

ROOT = shipped_defaults.ROOT
DEFAULT_PATHS = {
    ".agentic/defaults/PROJECT_CONFIG.yaml": ".agentic/PROJECT_CONFIG.yaml",
    ".agentic/defaults/routing.yaml": ".agentic/routing.yaml",
    ".agentic/defaults/OPERATING_CONFIG.yaml": "OPERATING_CONFIG.yaml",
}


class ShippedDefaultsTests(unittest.TestCase):
    def test_defaults_are_release_owned_while_live_configuration_is_preserved(self):
        preserved = SOURCE_CONFIG_PATHS | OPERATOR_CONFIG_PATHS
        for default, live in DEFAULT_PATHS.items():
            with self.subTest(default=default):
                self.assertTrue(managed(default))
                self.assertNotIn(default, preserved)
                self.assertTrue((ROOT / default).is_file())
                self.assertIn(live, preserved)

    def test_shipped_defaults_are_valid_and_carry_release_values(self):
        config = shipped_defaults.project_config()
        Contracts(ROOT / ".agentic/schemas").validate("project-config", config)
        self.assertEqual("Done", config["jira"]["status_map"]["done"])
        self.assertEqual(".agentic/routing.yaml", config["execution"]["model_routing"]["routing"])
        routing = load_routing(shipped_defaults.ROUTING)
        for role, settings in config["execution"]["roles"].items():
            with self.subTest(role=role):
                self.assertEqual(list(routing["catalog"]), settings["approved_model_ids"])
        self.assertIsNot(config, shipped_defaults.project_config())
        Contracts(ROOT / ".agentic/schemas").validate(
            "operating-config", shipped_defaults.operating_config())

    @source_only.skip_unless_source_repo(
        "The source template and live source configuration exist only in the AWF source repository",
        ".agentic/templates/source-config/PROJECT_CONFIG.yaml")
    def test_source_live_configuration_is_byte_identical_to_the_shipped_defaults(self):
        expected = {**DEFAULT_PATHS,
                    ".agentic/defaults/PROJECT_CONFIG.yaml": (
                        ".agentic/PROJECT_CONFIG.yaml",
                        ".agentic/templates/source-config/PROJECT_CONFIG.yaml")}
        for default, live in expected.items():
            for path in (live,) if isinstance(live, str) else live:
                with self.subTest(default=default, path=path):
                    self.assertEqual((ROOT / default).read_bytes(), (ROOT / path).read_bytes())


if __name__ == "__main__":
    unittest.main()
