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
    # AWF-266: synthetic escaped-newline call fingerprints (full JSON lines).
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "5e57f3a5d73e7cb3bb6bb9be63bdd3eb96cca8ae53ab2e066e5025e10e189a6e"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "e86018120f4ef62f0518ec117cc1e64051a6641dc14f4fcc18c1112f96993aa9"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "04d0e31cc33f85925daaa33475254f83400e9c9784877adbf3d98c1461340823"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "ef8a3c4eeedb07a3fe5729e26e239601bca427bd43ab6441d28ec719927087ee"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "e06006434b1997c0c851df19712cb95c79e1157910ac78a27e767d13cf0be12a"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "1e0306fa0d67dd7a9bd9cd4eaa94bdcbcaeb88a7b5c01af267c84dadb9f97177"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "fbec46d8eef7f325ae3f127cec53d91a9c56506d9afc9f53b905d18f0b6591e4"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "70873cc8e32ef2593b8015b7fff7fc28b29e537042bbac59f51791f961a9b9bd"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "510ea9a59ede9e42111bec69346aca9a2cd1edea4c4a804c9921e0b44ea8783a"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "5c077e2f4d92f86da36b16073c381862f2ee9ec867f10a816a56417f38131912"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "b42e7ee590d6be8db043d389a96f1e40695ddef9ff5a925360c75e7f5a3305b8"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "d3f4460f94902e9668f5ff98b6809b2fb104e5b73791a07c7d70334f631fba8f"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "dba53e096f34796a8b0962b422f56423be0f97b8378d2626e3b0f0f73d239679"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "3bc6ecfcc6c03a21bc21f8e3218f4ae23d1dcba872ca75970879ce63c832228c"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "d73d3371915702cc9a39aa958298913d4c4703feafda889d6c37eea38f2b6b38"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "e5197bf7b06f3bb460b5ea10cca76591f068f3f4ac87f8cfb4786a254d6cb394"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "ea5c29fff19b3b7d9d8cc9d2294be41f89f0f53be4ee5d2ecb45dd2f5dc82648"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "7c129d4878ad82e1fd584b056fa349e720f13ade67b3ce53eacb81a780e79892"),
    ("scripts/tests/path_mutation_allowlist.json", "builtin.unc_path",
     "410126ed9da9c7049076e0a20d94fefb4757e9297a30faf947a7d1b6f30ad4e4"),
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
        for path, detector, digest, line in self.audited_lines():
            injected = (chr(92) * 2 + "synthetic/share/fixture"
                        if detector == "builtin.unc_path"
                        else "C:" + "/private/release-owner/release-key.txt")
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
