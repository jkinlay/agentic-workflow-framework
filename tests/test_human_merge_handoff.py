import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs" / "HUMAN_MERGE_HANDOFF.md"


class HumanMergeHandoffContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = HANDOFF.read_text(encoding="utf-8")

    def test_only_terminal_event_may_send_email(self):
        self.assertIn(
            "`READY_FOR_HUMAN_MERGE` is the only AWF event permitted to send email",
            self.contract,
        )
        for prohibited in (
            "review-started",
            "finding",
            "CI-pending",
            "CI-pass",
            "remediation",
        ):
            self.assertIn(prohibited, self.contract)

    def test_external_browser_precedes_email(self):
        browser = self.contract.index("call `desktop.open_external_url`")
        email = self.contract.index("call `notification.send_email`")
        self.assertLess(browser, email)
        self.assertIn("external browser rather than an embedded or in-app browser", self.contract)

    def test_false_readiness_never_opens_browser(self):
        self.assertIn("When the predicate is false", self.contract)
        self.assertIn("must not call either terminal", self.contract)
        self.assertIn("open it in any browser", self.contract)

    def test_email_has_required_human_context(self):
        self.assertIn("Full authoritative Epic or Ticket title", self.contract)
        self.assertRegex(
            self.contract,
            re.escape("Ready to merge: <issue_key> — <full issue_title> — PR #<number>"),
        )
        self.assertIn("ready for human approval to merge", self.contract)

    def test_recipient_and_idempotency_are_not_repository_secrets(self):
        self.assertIn("notification.resolve_current_recipient()", self.contract)
        self.assertIn("does not send it again", self.contract)
        self.assertIn("AWF never records", self.contract)

    def test_normative_documents_link_the_contract(self):
        references = {
            ROOT / "README.md": "docs/HUMAN_MERGE_HANDOFF.md",
            ROOT / "ARCHITECTURE.md": "docs/HUMAN_MERGE_HANDOFF.md",
            ROOT / "docs" / "OPERATING_MODEL.md": "HUMAN_MERGE_HANDOFF.md",
            ROOT / "docs" / "GETTING_STARTED.md": "HUMAN_MERGE_HANDOFF.md",
        }
        for path, reference in references.items():
            with self.subTest(path=path):
                self.assertIn(reference, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
