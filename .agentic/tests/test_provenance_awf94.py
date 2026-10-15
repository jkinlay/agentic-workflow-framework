"""AWF-94 focused acceptance tests for the shared provenance grammar."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError  # noqa: E402
from agentic.interaction import writer_commit_message  # noqa: E402
from agentic import provenance  # noqa: E402
from agentic import publication  # noqa: E402
from agentic import review_first_draft  # noqa: E402


RUN_ID = "12345678-1234-4234-8234-123456789abc"
HEAD = "a" * 40
DIGEST = "b" * 64


def complete_critic_verdict(**changes):
    value = {
        "run_id": RUN_ID,
        "head_sha": HEAD,
        "result_sha256": DIGEST,
        "critic_run_carrier": f"Critic run: {RUN_ID}",
        "candidate_binding": {"head_sha": HEAD},
        "critic_artifact_binding": {
            "critic_run_id": RUN_ID,
            "head_sha": HEAD,
            "result_sha256": DIGEST,
        },
        "critic_review": {
            "run_id": RUN_ID,
            "head_sha": HEAD,
            "result_sha256": DIGEST,
        },
    }
    value.update(changes)
    return value


class AWF94MessageGrammarTests(unittest.TestCase):
    def test_in_05_whole_message_rejects_every_fixed_format_control(self):
        with self.assertRaisesRegex(ValidationError, r"^MESSAGE_CONTROL:"):
            provenance.validate_commit_message("subject\u00adbody")

    def test_in_06_unicode_line_separator_has_stable_separator_code(self):
        with self.assertRaisesRegex(ValidationError, r"^MESSAGE_SEPARATOR:"):
            provenance.validate_commit_message("subject\u2028body")

    def test_in_07_non_ascii_trailer_key_lookalike_is_ambiguous(self):
        # The second character is CYRILLIC SMALL LETTER ER, not ASCII r.
        lookalike = f"subject\n\nW\u0440iter: codex/{RUN_ID}"
        with self.assertRaisesRegex(ValidationError, r"^WRITER_LOOKALIKE:"):
            provenance.parse_writer_trailer(lookalike)

    def test_in_08_canonical_non_nil_codex_uuid_regression(self):
        emitted = writer_commit_message("subject", codex_run_id=RUN_ID)
        self.assertEqual(RUN_ID, provenance.parse_writer_trailer(emitted)["identity"])
        with self.assertRaises(ValidationError):
            writer_commit_message("subject", codex_run_id="00000000-0000-0000-0000-000000000000")

    def test_in_09_portable_script_identity_regression(self):
        emitted = writer_commit_message(
            "subject", mechanical=True, script_name="manifest-regen")
        self.assertEqual("manifest-regen",
                         provenance.parse_writer_trailer(emitted)["identity"])
        with self.assertRaises(ValidationError):
            writer_commit_message("subject", mechanical=True, script_name="Manifest Regen")

    def test_in_10_mechanical_selector_requires_script_identity(self):
        with self.assertRaisesRegex(ValidationError, r"^EMITTER_IDENTITY:"):
            writer_commit_message("subject", mechanical=True, codex_run_id=RUN_ID)

    def test_in_11_commit_boundary_reparses_writer_message_before_mutation(self):
        class GitMustNotMutate:
            oid_length = 40

            def resolve(self, value, field):
                return HEAD

            def text(self, *args):
                raise AssertionError("write-tree reached for parser-refused message")

        with patch.object(provenance, "_Git", return_value=GitMustNotMutate()):
            with self.assertRaisesRegex(ValidationError, r"^PUBLISHED_MESSAGE_INVALID:"):
                provenance.create_canonical_commit(".", HEAD, "topic", "subject")


class AWF94CriticBindingTests(unittest.TestCase):
    def test_in_24_result_digest_and_nested_bindings_are_mandatory(self):
        for missing in ("result_sha256", "candidate_binding",
                        "critic_artifact_binding", "critic_review"):
            verdict = complete_critic_verdict()
            verdict.pop(missing)
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    ValidationError, r"^CRITIC_BINDING_INVALID:"):
                provenance.validate_critic_run_binding(verdict, reviewed_head=HEAD)

    def test_in_25_carrier_is_explicit_never_synthesized(self):
        verdict = complete_critic_verdict()
        verdict.pop("critic_run_carrier")
        with self.assertRaisesRegex(ValidationError, r"^CRITIC_LINE_INVALID:"):
            provenance.validate_critic_run_binding(verdict, reviewed_head=HEAD)
        schema = json.loads((Path(__file__).resolve().parents[1] /
                             "review-loop/critic-result.schema.json").read_text(
                                 encoding="utf-8"))
        self.assertEqual("#/$defs/criticBinding/properties/critic_run_carrier",
                         schema["x-awf-sole-critic-run-carrier"])


class AWF94FirstDraftTests(unittest.TestCase):
    def test_in_37_title_has_one_shared_canonical_entry_point(self):
        validator = getattr(review_first_draft, "canonical_first_draft_title")
        self.assertEqual("subject", validator("subject"))
        with self.assertRaisesRegex(ValidationError, r"^TITLE_INVALID:"):
            validator("subject\r\n")

    def test_in_38_scalar_task_data_rejects_unicode_structure_controls(self):
        with self.assertRaisesRegex(ValidationError, r"^BODY_SCALAR_INVALID:"):
            review_first_draft.render_first_draft_body(
                "trusted contract", risk_tier="Tier 3",
                worker_model="model\u2028injected", reasoning_effort="high",
                branch="topic", tested_tree=HEAD)


class AWF94RewriteTests(unittest.TestCase):
    def test_in_39_rewrite_message_refusal_starts_with_stable_code(self):
        with self.assertRaisesRegex(ValidationError, r"^REWRITE_MESSAGE_INVALID:"):
            publication._writer_emitted_rewrite_message(b"\xef\xbb\xbfsubject")

    def test_in_40_rewrite_branch_is_validated_before_repository_reads(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                publication, "_repository_commit_identity",
                side_effect=AssertionError("identity read reached")):
            with self.assertRaisesRegex(ValidationError, r"^REWRITE_REF_INVALID:"):
                publication.rewrite_unpublished(
                    temporary, HEAD, "topic.lock", 1, Path(temporary) / "message")

    def test_in_49_boolean_commit_count_is_not_the_integer_one(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                publication, "_repository_commit_identity",
                side_effect=AssertionError("identity read reached")):
            with self.assertRaisesRegex(ValidationError, r"^REWRITE_ARGUMENT_INVALID:"):
                publication.rewrite_unpublished(
                    temporary, HEAD, "topic", True, Path(temporary) / "message")


if __name__ == "__main__":
    unittest.main()
