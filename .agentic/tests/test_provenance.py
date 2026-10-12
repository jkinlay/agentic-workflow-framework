"""Writer/critic provenance and conservative mechanical-commit regressions."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.interaction import writer_commit_message
from agentic.provenance import (
    check_commit_range,
    parse_writer_trailer,
    record_owner_override,
    render_owner_override,
    validate_critic_run_binding,
    verify_owner_override,
)


RUN_A = "11111111-1111-4111-8111-111111111111"
RUN_B = "22222222-2222-4222-8222-222222222222"
NOW = "2026-10-12T12:00:00Z"


class TrailerParsingTests(unittest.TestCase):
    def test_tagged_and_untagged_messages(self):
        parsed = parse_writer_trailer(f"Implement change\n\nWriter: codex/{RUN_A}\n")
        self.assertEqual(("codex", RUN_A), (parsed["kind"], parsed["identity"]))
        self.assertIsNone(parse_writer_trailer("Human-authored commit\n"))

    def test_duplicate_or_non_trailer_writer_lines_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "exactly one"):
            parse_writer_trailer(
                f"Change\n\nWriter: codex/{RUN_A}\nWriter: script/manifest-regen\n"
            )
        with self.assertRaisesRegex(ValidationError, "final trailer"):
            parse_writer_trailer(f"Writer: codex/{RUN_A}\n\nMore prose\n")

    def test_writer_may_precede_other_lines_in_the_final_git_trailer_block(self):
        parsed = parse_writer_trailer(
            f"Change\n\nWriter: codex/{RUN_A}\nSigned-off-by: Fixture <fixture@example.invalid>\n"
        )
        self.assertEqual(RUN_A, parsed["identity"])

    def test_interaction_emits_one_writer_trailer_and_requires_codex_for_code(self):
        message = writer_commit_message("Implement change", codex_run_id=RUN_A)
        self.assertEqual(f"Implement change\n\nWriter: codex/{RUN_A}", message)
        mechanical = writer_commit_message(
            "Regenerate manifest", mechanical=True, script_name="manifest-regen"
        )
        self.assertTrue(mechanical.endswith("Writer: script/manifest-regen"))
        with self.assertRaisesRegex(ValidationError, "Codex run id"):
            writer_commit_message("Implement change")
        with self.assertRaisesRegex(ValidationError, "non-mechanical"):
            writer_commit_message(
                "Implement change", script_name="publisher", mechanical=False
            )


class CriticBindingTests(unittest.TestCase):
    def test_verdict_binds_critic_run_to_reviewed_head(self):
        head = "a" * 40
        binding = validate_critic_run_binding(
            {"run_id": RUN_A, "head_sha": head}, reviewed_head=head
        )
        self.assertEqual(
            {"critic_run_id": RUN_A, "head_sha": head,
             "trailer": f"Critic run: {RUN_A}"},
            binding,
        )

    def test_verdict_without_run_id_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "critic run id"):
            validate_critic_run_binding({"head_sha": "a" * 40})
        with self.assertRaisesRegex(ValidationError, "reviewed head"):
            validate_critic_run_binding(
                {"run_id": RUN_A, "head_sha": "a" * 40}, reviewed_head="b" * 40
            )


class OwnerOverrideTests(unittest.TestCase):
    def test_override_is_recorded_sealed_and_renderable_for_the_pr(self):
        state = {}
        sha = "a" * 40
        record = record_owner_override(
            state,
            who="owner-1",
            when=NOW,
            why="Fallback bot commit predates provenance enforcement",
            pr=49,
            covered_shas=[sha],
            trusted_owner_ids=["owner-1", "owner-2"],
        )
        self.assertEqual(record, state["provenance_overrides"]["49"])
        self.assertEqual(record, verify_owner_override(
            record, trusted_owner_ids=["owner-1"], pr=49
        ))
        rendered = render_owner_override(record)
        for value in ("owner-1", NOW, "#49", sha, record["override_sha256"]):
            self.assertIn(value, rendered)

    def test_unauthorised_override_is_rejected_without_state_change(self):
        state = {}
        with self.assertRaisesRegex(ValidationError, "trusted owner"):
            record_owner_override(
                state,
                who="outsider",
                when=NOW,
                why="Attempted bypass",
                pr=49,
                covered_shas=["a" * 40],
                trusted_owner_ids=["owner-1"],
            )
        self.assertEqual({}, state)

    def test_override_reason_cannot_inject_a_pr_body_line(self):
        with self.assertRaisesRegex(ValidationError, "bounded reason"):
            record_owner_override(
                {}, who="owner-1", when=NOW, why="reason\nInjected: value", pr=49,
                covered_shas=["a" * 40], trusted_owner_ids=["owner-1"],
            )


class RepositoryRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git = "git"
        subprocess.run([cls.git, "--version"], check=True, capture_output=True, timeout=20)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-provenance-")
        self.root = Path(self.temporary.name)
        self.environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Fixture Writer",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture Writer",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        self.git_run("init", "--quiet", "--initial-branch=main")
        self.write("README.md", "base\n")
        self.base = self.commit("base", f"Writer: codex/{RUN_A}")

    def tearDown(self):
        self.temporary.cleanup()

    def git_run(self, *args):
        result = subprocess.run(
            [self.git, "-C", str(self.root), *args],
            text=True,
            capture_output=True,
            env=self.environment,
            timeout=30,
        )
        if result.returncode:
            self.fail(f"git {' '.join(args)} failed: {result.stderr}")
        return result.stdout.strip()

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, subject, trailer=None):
        self.git_run("add", "--all")
        message = subject if trailer is None else f"{subject}\n\n{trailer}"
        self.git_run("commit", "--quiet", "--message", message)
        return self.git_run("rev-parse", "HEAD")

    def test_tagged_commit_passes_and_untagged_commit_has_per_commit_reason(self):
        self.write("src/feature.py", "value = 1\n")
        tagged = self.commit("feature", f"Writer: codex/{RUN_B}")
        passed = check_commit_range(self.root, self.base, tagged)
        self.assertEqual(("PASS", []), (passed["status"], passed["refusals"]))

        self.write("src/feature.py", "value = 2\n")
        untagged = self.commit("untagged")
        refused = check_commit_range(self.root, tagged, untagged)
        self.assertEqual("REFUSE", refused["status"])
        self.assertEqual(untagged, refused["refusals"][0]["sha"])
        self.assertIn("missing Writer trailer", refused["refusals"][0]["reason"])

    def test_script_tag_on_non_mechanical_diff_is_rejected(self):
        self.write("src/feature.py", "value = 1\n")
        head = self.commit("pretend generator", "Writer: script/manifest-regen")
        result = check_commit_range(self.root, self.base, head)
        self.assertEqual("REFUSE", result["status"])
        self.assertIn("not reproducibly mechanical", result["refusals"][0]["reason"])
        self.assertIn("src/feature.py", result["refusals"][0]["reason"])

    def test_manifest_commit_requires_byte_for_byte_reproduction(self):
        content = '{"version": 1}\n'
        self.write("MANIFEST.json", content)
        head = self.commit("manifest", "Writer: script/manifest-regen")

        def exact(_sha, paths):
            self.assertEqual(["MANIFEST.json"], paths)
            return {"MANIFEST.json": content.encode("utf-8")}

        passed = check_commit_range(
            self.root, self.base, head, reproduce_generated=exact
        )
        self.assertEqual("PASS", passed["status"])
        refused = check_commit_range(
            self.root, self.base, head,
            reproduce_generated=lambda _sha, _paths: {"MANIFEST.json": b"wrong\n"},
        )
        self.assertEqual("REFUSE", refused["status"])
        self.assertIn("byte-for-byte", refused["refusals"][0]["reason"])

    def test_optional_run_logs_are_checked_when_available_or_required(self):
        self.write("src/feature.py", "value = 1\n")
        head = self.commit("feature", f"Writer: codex/{RUN_B}")
        absent = check_commit_range(
            self.root, self.base, head,
            run_log_verifier=lambda _run, _sha, _paths: None,
        )
        self.assertEqual("PASS", absent["status"])
        required = check_commit_range(
            self.root, self.base, head,
            run_log_verifier=lambda _run, _sha, _paths: None,
            require_run_logs=True,
        )
        self.assertEqual("REFUSE", required["status"])
        mismatch = check_commit_range(
            self.root, self.base, head,
            run_log_verifier=lambda _run, _sha, _paths: False,
        )
        self.assertIn("does not match", mismatch["refusals"][0]["reason"])

    def test_range_accepts_reproducible_origin_main_merge(self):
        self.git_run("checkout", "--quiet", "-b", "feature")
        self.write("feature.txt", "feature\n")
        self.commit("feature", f"Writer: codex/{RUN_A}")

        self.git_run("checkout", "--quiet", "main")
        self.write("main.txt", "main\n")
        main_head = self.commit("main change", f"Writer: codex/{RUN_B}")
        self.git_run("update-ref", "refs/remotes/origin/main", main_head)

        self.git_run("checkout", "--quiet", "feature")
        self.git_run(
            "merge", "--quiet", "--no-ff", "origin/main", "--message",
            "Sync origin/main\n\nWriter: script/sync-merge",
        )
        head = self.git_run("rev-parse", "HEAD")
        result = check_commit_range(
            self.root, self.base, head, origin_main="origin/main"
        )
        self.assertEqual("PASS", result["status"], result["refusals"])
        merge = next(item for item in result["commits"] if item["sha"] == head)
        self.assertEqual((True, "origin/main merge"),
                         (merge["mechanical"], merge["mechanical_reason"]))

    def test_valid_labelled_override_covers_only_named_commit(self):
        self.write("legacy.txt", "legacy\n")
        head = self.commit("legacy bot commit")
        state = {}
        override = record_owner_override(
            state,
            who="owner-1",
            when=NOW,
            why="Known fallback bot commit",
            pr=49,
            covered_shas=[head],
            trusted_owner_ids=["owner-1"],
        )
        result = check_commit_range(
            self.root, self.base, head,
            override=override,
            trusted_owner_ids=["owner-1"],
            pr_number=49,
            pr_labels=["fallback: bot-written"],
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual("OVERRIDDEN", result["commits"][0]["status"])
        with self.assertRaisesRegex(ValidationError, "fallback"):
            check_commit_range(
                self.root, self.base, head,
                override=override,
                trusted_owner_ids=["owner-1"],
                pr_number=49,
                pr_labels=[],
            )

    def test_override_cannot_claim_a_commit_that_did_not_need_it(self):
        self.write("tagged.txt", "tagged\n")
        head = self.commit("tagged", f"Writer: codex/{RUN_A}")
        override = record_owner_override(
            {}, who="owner-1", when=NOW, why="Over-broad record", pr=49,
            covered_shas=[head], trusted_owner_ids=["owner-1"],
        )
        with self.assertRaisesRegex(ValidationError, "needed no override"):
            check_commit_range(
                self.root, self.base, head, override=override,
                trusted_owner_ids=["owner-1"], pr_number=49,
                pr_labels=["fallback: bot-written"],
            )


if __name__ == "__main__":
    unittest.main()
