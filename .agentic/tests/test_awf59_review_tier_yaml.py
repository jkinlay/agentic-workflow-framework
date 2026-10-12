"""AWF-59 integration regressions for lossless review-tier migration."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.canonical import load_yaml
from agentic.upgrade import (MigrationBundle, migrate_1_9_4_to_1_9_5,
                             review_tier_defaults)


class AWF59ReviewTierYamlTests(unittest.TestCase):
    def test_awf59_c195_r2_001_cr_only_version_step_reparses_and_is_idempotent(self):
        before = (
            b"template:\r  expected_workflow_version: 1.9.4\r"
            b"execution:\r"
            b"  risk_tiers:\r"
            b"    tier1_review: {findings: advisory}\r"
            b"  sibling: retained\r"
        )
        bundle = MigrationBundle(
            before,
            None,
            b"{}\n",
            b"{}\n",
            MappingProxyType({}),
        )
        target = {
            "source_manifest_sha256": "0" * 64,
            "_immutable_files": {},
        }
        expected = (
            b"template:\r  expected_workflow_version: 1.9.5\r"
            b"execution:\r"
            b"  risk_tiers:\r"
            b"    tier1_review: {findings: advisory}\r"
            b"    tier3_review:\r"
            b"      roles: [critic, specialist]\r"
            b"      findings: blocking\r"
            b"      max_rounds: 3\r"
            b"  sibling: retained\r"
        )

        after = migrate_1_9_4_to_1_9_5(bundle, target).project_config

        self.assertEqual(expected, after)
        self.assertEqual({
            "template": {"expected_workflow_version": "1.9.5"},
            "execution": {
                "risk_tiers": {
                    "tier1_review": {"findings": "advisory"},
                    "tier3_review": {
                        "roles": ["critic", "specialist"],
                        "findings": "blocking",
                        "max_rounds": 3,
                    },
                },
                "sibling": "retained",
            },
        }, load_yaml(after))
        self.assertEqual(1, after.count(b"tier3_review"))
        self.assertEqual(after, review_tier_defaults(after))

    def test_awf59_c195_r1_001_nested_block_preserves_following_execution_key(self):
        before = (
            b"template:\n  expected_workflow_version: 1.9.4\n"
            b"execution:\n"
            b"  risk_tiers:\n"
            b"    tier1_review: {findings: advisory}\n"
            b"  sibling: retained\n"
        )
        added = (
            b"    tier3_review:\n"
            b"      roles: [critic, specialist]\n"
            b"      findings: blocking\n"
            b"      max_rounds: 3\n"
        )
        expected = before.replace(b"  sibling: retained\n", added + b"  sibling: retained\n")

        after = review_tier_defaults(before)

        self.assertEqual(expected, after)
        self.assertEqual("retained", load_yaml(after)["execution"]["sibling"])
        self.assertEqual(1, after.count(b"tier3_review"))
        self.assertEqual(after, review_tier_defaults(after))

    def test_crlf_multiline_flow_comments_and_anchors_migrate_together(self):
        before = (
            b"template:\r\n  expected_workflow_version: 1.9.4\r\n"
            b"execution: &execution_defaults {\r\n"
            b"  mode: manual, # retained execution comment\r\n"
            b"  risk_tiers: &risk_defaults {\r\n"
            b"    tier1_review: {} # retained tier comment\r\n"
            b"  }\r\n}\r\n"
        )
        after = review_tier_defaults(before)

        self.assertEqual({
            "template": {"expected_workflow_version": "1.9.4"},
            "execution": {
                "mode": "manual",
                "risk_tiers": {
                    "tier1_review": {},
                    "tier3_review": {
                        "roles": ["critic", "specialist"],
                        "findings": "blocking",
                        "max_rounds": 3,
                    },
                },
            },
        }, load_yaml(after))
        self.assertIn(b"# retained execution comment\r\n", after)
        self.assertIn(b"# retained tier comment\r\n", after)
        self.assertEqual(1, after.count(b"tier3_review"))
        self.assertEqual(after, review_tier_defaults(after))


if __name__ == "__main__":
    unittest.main()
