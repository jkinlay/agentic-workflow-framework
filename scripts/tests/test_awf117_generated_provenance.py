"""AWF-117 AC6: generated contracts and docs describe the enforced gate."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / "scripts"))


class GeneratedProvenanceTests(unittest.TestCase):
    def test_ac6_generated_provenance_contracts_are_current(self):
        import generate_contracts
        import generate_examples

        with tempfile.TemporaryDirectory(prefix="awf117-generation-") as raw:
            generated = Path(raw)
            with patch.object(generate_contracts, "ROOT", generated):
                self.assertEqual(0, generate_contracts.main([]))
                self.assertEqual(0, generate_contracts.main(["--check"]))
            generate_examples.generate(generated, ROOT)
            for path in sorted(generated.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(generated)
                    with self.subTest(path=relative.as_posix()):
                        self.assertTrue((ROOT / relative).is_file())
                        self.assertEqual(path.read_bytes(), (ROOT / relative).read_bytes())
            # Both public check modes must remain clean, without regenerating
            # anything in the checkout being tested.
            self.assertEqual(0, generate_contracts.main(["--check"]))
            self.assertEqual(0, generate_examples.main(["--check"]))

            # Doc 45 owner question 2 leaves the field/record shape open. Look
            # for the commit-provenance contract in the three named surfaces,
            # rather than prescribing a new schema name or carrier location.
            for name in ("project-config", "evidence-bundle", "final-gate"):
                schema = json.loads((generated / ".agentic/schemas" /
                                     (name + ".schema.json")).read_text(encoding="utf-8"))
                with self.subTest(contract=name):
                    self.assertIsNotNone(re.search(
                        r"commit.{0,160}provenance|provenance.{0,160}commit",
                        json.dumps(schema).lower()),
                        "AWF-117 missing generated commit-provenance contract: " + name)

        for name in ("30-REVIEW-TIERS-AND-CLOSEOUT.md",
                     "41-WRITER-CRITIC-PROVENANCE.md"):
            text = (ROOT / ".agentic/docs" / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                # Assert the rule, without selecting the open range-bound,
                # override-chain, record-shape or critic-carrier decisions.
                for rule in (
                    r"(?s)(?:final|merge)[ -]gate.{0,500}(?:check_commit_range|commit.provenance)",
                    r"(?s)(?:refus|reject|fail|not_ready).{0,300}(?:untagged|missing writer|writer trailer)",
                    r"(?s)(?:refus|reject|fail|not_ready).{0,300}(?:non.mechanical|not reproducibly mechanical)",
                ):
                    self.assertIsNotNone(re.search(rule, text.lower()),
                        "AWF-117 missing enforced-gate documentation: " + name + " / " + rule)


if __name__ == "__main__":
    unittest.main()
