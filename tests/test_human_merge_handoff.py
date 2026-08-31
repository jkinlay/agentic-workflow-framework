import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs" / "HUMAN_MERGE_HANDOFF.md"


class HumanMergeHandoffContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = HANDOFF.read_text(encoding="utf-8")

    def test_only_terminal_event_may_emit_terminal_notification(self):
        self.assertIn("posts exactly one issue comment", self.contract)
        for prohibited in (
            "review-started",
            "finding",
            "CI-pending",
            "CI-pass",
            "remediation",
        ):
            self.assertIn(prohibited, self.contract)

    def test_complete_readiness_precedes_notification_and_browser(self):
        predicate = self.contract.index("## Readiness predicate")
        notification = self.contract.index("## Deterministic GitHub notification")
        browser = self.contract.index("## External-browser action")
        self.assertLess(predicate, notification)
        self.assertLess(notification, browser)
        self.assertIn("Only after the GitHub notification has succeeded", self.contract)
        self.assertIn("browser closed", self.contract)

    def test_false_readiness_never_notifies_or_opens_browser(self):
        self.assertIn("When the predicate is false", self.contract)
        self.assertIn("must not call either terminal", self.contract)
        self.assertIn("open the pull\nrequest in any browser", self.contract)

    def test_comment_is_deterministic_and_mentions_configured_owner(self):
        self.assertIn('"notification_target": "github:jkinlay"', self.contract)
        self.assertIn("producing\nthe literal mention `@jkinlay`", self.contract)
        self.assertIn("@<github_login> READY TO MERGE", self.contract)
        self.assertIn("This pull request is ready for your approval to merge.", self.contract)
        self.assertIn("awf-ready-for-human-merge:<repository>:<number>:<full_head_sha>", self.contract)

    def test_full_title_comes_only_from_trusted_task_contract(self):
        self.assertIn("mandatory trusted fields in the approved task\ncontract", self.contract)
        self.assertIn("complete authoritative Jira Epic or Ticket title", self.contract)
        self.assertIn("must not abbreviate it", self.contract)
        self.assertIn("replacement from PR text", self.contract)

    def test_same_head_notification_is_idempotent(self):
        self.assertIn("paginates existing PR comments", self.contract)
        self.assertIn("does not post again", self.contract)
        self.assertIn("(repository, pull_request_number, head_sha, event)", self.contract)

    def test_notification_target_is_protected_project_configuration(self):
        template = (ROOT / "scaffolds" / "project" / ".agentic" / "project.yaml.template").read_text(
            encoding="utf-8"
        )
        dogfood = (ROOT / ".agentic" / "project.yaml").read_text(encoding="utf-8")
        self.assertIn('notification_target: "github:__AWF_HUMAN_MERGE_GITHUB_LOGIN__"', template)
        self.assertIn("notification_target: github:jkinlay", dogfood)
        self.assertIn("notification_mode: github_mention", template)

    def test_email_adapter_is_absent(self):
        self.assertNotIn("notification.send_email", self.contract)
        self.assertNotIn("resolve_current_recipient", self.contract)
        self.assertIn("does not store an email address", self.contract)

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
