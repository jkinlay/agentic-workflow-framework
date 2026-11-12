"""AWF-280: exact fixture identities and the release candidate's real history."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/tests"))
import source_only  # noqa: E402
from source_only import skip_unless_source_repo  # noqa: E402
from agentic import publication  # noqa: E402


# Audited full lines, not matched substrings; compute with the scanner's digest.
AUDITED_IDENTITIES = (
    (".agentic/tests/test_bootstrap_configuration.py", "builtin.windows_absolute",
     "e059da2a49d3c2859d5cb07446e7b79089da9e6439c7ea657d0feb4e231192fc"),
    (".agentic/tests/test_bootstrap_configuration.py", "builtin.windows_absolute",
     "ddf78f0200e1992ac4aed760dae42c6117a8e0d31a21acd8b6c986e7553e719e"),
    ("scripts/tests/test_installed_self_test_shards.py", "builtin.windows_absolute",
     "c82f1a1b6007051ea0852bc0abf4fa53883fbd6b7b6bd1e1aa403cd64d04cc7f"),
)


@skip_unless_source_repo()
class ReleaseFixtureIdentityTests(unittest.TestCase):
    def audited_lines(self):
        for path, detector, digest in AUDITED_IDENTITIES:
            lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
            matches = [line for line in lines
                       if publication._redacted(line) == "sha256:" + digest]
            self.assertEqual(1, len(matches), (path, detector, digest))
            yield path, detector, digest, matches[0]

    def scan(self, text, path, declarations=None):
        detectors, allows = publication._detectors({}, declarations or {})
        return publication._scan_text(
            text, commit="fixture", path=path, source="patch", change="added",
            detectors=detectors, allows=allows)

    def test_exact_audited_lines_are_registered(self):
        for path, detector, digest, line in self.audited_lines():
            with self.subTest(path=path, digest=digest):
                self.assertIn(digest, publication._HISTORICAL_SELF_REFERENCES.get(
                    (path, detector), ()))
                self.assertEqual([], self.scan(line, path))

    def test_changed_line_and_other_path_still_block(self):
        for path, detector, digest, line in self.audited_lines():
            for text, target in ((line + " ", path), (line, "other.py")):
                with self.subTest(path=target, digest=digest, text=text):
                    findings = self.scan(text, target)
                    self.assertTrue(any(item["detector_id"] == detector
                                        and item["classification"] == "BLOCKING"
                                        for item in findings), findings)

    def test_injected_path_on_new_line_and_other_detector_still_block(self):
        injected = "C:" + "/private/release-owner/release-key.txt"
        for path, detector, digest, line in self.audited_lines():
            with self.subTest(path=path, digest=digest):
                findings = self.scan(line + "\n" + injected, path)
                self.assertTrue(any(item["line"] == 2
                                    and item["detector_id"] == detector
                                    and item["classification"] == "BLOCKING"
                                    for item in findings), findings)
                other_detector = self.scan(line, path, {"deny_literals": [line]})
                self.assertTrue(any(item["detector_id"].startswith("declared.")
                                    and item["classification"] == "BLOCKING"
                                    for item in other_detector), other_detector)


if __name__ == "__main__":
    unittest.main()
