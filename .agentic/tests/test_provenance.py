"""Writer/critic provenance and conservative mechanical-commit regressions."""
from __future__ import annotations

import os
import copy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
import agentic.provenance as provenance
from agentic.canonical import fingerprint
from agentic.gittree import candidate_tree
from agentic.interaction import writer_commit_message
from agentic.provenance import (
    check_commit_range,
    parse_writer_trailer,
    record_owner_override,
    render_owner_override,
    supersede_owner_override,
    validate_critic_run_binding,
    verify_owner_override,
)
from agentic.review_first_draft import publish_codex_tested_tree


RUN_A = "11111111-1111-4111-8111-111111111111"
RUN_B = "22222222-2222-4222-8222-222222222222"
NOW = "2026-10-12T12:00:00Z"


def reviewed_owner_config(*owner_ids, config_sha256="f" * 64):
    payload = {
        "config_sha256": config_sha256,
        "trusted_owner_ids": list(owner_ids),
    }
    return {
        **payload,
        "binding_sha256": fingerprint("provenance_owner_config", payload),
    }


class R13InputContractTests(unittest.TestCase):
    """One independently reported acceptance test for every R13 input row."""

    def assert_row(self, number, *, authority, failure, cap):
        row = provenance.PROVENANCE_INPUT_CONTRACT[f"IN-{number:02d}"]
        self.assertEqual(authority, row["authority"])
        self.assertEqual(failure, row["failure"])
        self.assertEqual(cap, row["cap"])
        self.assertRegex(row["reason_code"], r"^[A-Z][A-Z0-9_]+$")

    def test_in_01_repository_root(self):
        self.assert_row(1, authority="git", failure="operation", cap="one repository")

    def test_in_02_range_endpoints(self):
        self.assert_row(2, authority="frozen git refs", failure="operation", cap="10000 commits/32 MiB")

    def test_in_03_origin_main(self):
        self.assert_row(3, authority="reviewed config + provider", failure="operation", cap="one frozen OID")

    def test_in_04_raw_commit_object(self):
        self.assert_row(4, authority="git object database", failure="commit", cap="1 MiB message/32 MiB object")

    def test_in_05_commit_message(self):
        self.assert_row(5, authority="raw commit bytes", failure="commit", cap="1 MiB/65536 lines")

    def test_in_06_line_boundaries(self):
        self.assert_row(6, authority="raw commit bytes", failure="commit", cap="LF or CRLF")

    def test_in_07_writer_lines(self):
        self.assert_row(7, authority="raw commit bytes", failure="commit", cap="256 trailer-like lines")

    def test_in_08_codex_uuid(self):
        self.assert_row(8, authority="observed Codex thread", failure="commit", cap="one non-nil UUID")

    def test_in_09_script_id(self):
        self.assert_row(9, authority="reviewed generator registry", failure="commit", cap="64 ASCII bytes")

    def test_in_10_emitter_input(self):
        self.assert_row(10, authority="shared grammar", failure="operation", cap="1 MiB output")

    def test_in_11_publisher_message(self):
        self.assert_row(11, authority="raw created commit", failure="operation", cap="one canonical message")

    def test_in_12_git_path_bytes(self):
        self.assert_row(12, authority="raw git output", failure="commit", cap="32 MiB")

    def test_in_13_path_syntax(self):
        self.assert_row(13, authority="raw git output", failure="commit", cap="100000 records")

    def test_in_14_renames(self):
        self.assert_row(14, authority="--raw -z --no-renames", failure="commit", cap="delete + add")

    def test_in_15_entry_types(self):
        self.assert_row(15, authority="raw git output", failure="commit", cap="100000 records")

    def test_in_16_generated_paths(self):
        self.assert_row(16, authority="reviewed generator registry", failure="operation", cap="100000 paths")

    def test_in_17_generator_bytes(self):
        self.assert_row(17, authority="registered reproducer", failure="commit", cap="32 MiB each/512 MiB total")

    def test_in_18_merge_tree(self):
        self.assert_row(18, authority="isolated git plumbing", failure="commit", cap="32 MiB")

    def test_in_19_run_log(self):
        self.assert_row(19, authority="registered verifier", failure="commit", cap="closed evidence record")

    def test_in_20_controller_run_id(self):
        self.assert_row(20, authority="controller", failure="operation", cap="one non-nil UUID")

    def test_in_21_codex_jsonl(self):
        self.assert_row(21, authority="pinned file handle", failure="operation", cap="64 MiB/1 MiB event")

    def test_in_22_codex_result(self):
        self.assert_row(22, authority="pinned file handle", failure="operation", cap="1 MiB")

    def test_in_23_identity_record(self):
        self.assert_row(23, authority="pinned file handle", failure="operation", cap="16 KiB")

    def test_in_24_critic_verdict(self):
        self.assert_row(24, authority="observed critic artifact", failure="operation", cap="1 MiB")

    def test_in_25_critic_line(self):
        self.assert_row(25, authority="critic artifact carrier", failure="operation", cap="one final line")

    def test_in_26_override_record(self):
        self.assert_row(26, authority="observed owner comment", failure="operation", cap="4 KiB reason/255-byte owner")

    def test_in_27_override_shas(self):
        self.assert_row(27, authority="frozen range", failure="operation", cap="256 SHAs")

    def test_in_28_override_chain(self):
        self.assert_row(28, authority="digest-linked records", failure="operation", cap="32 records")

    def test_in_29_trusted_owners(self):
        self.assert_row(29, authority="reviewed host config", failure="operation", cap="positive numeric IDs")

    def test_in_30_pr_and_label(self):
        self.assert_row(30, authority="fresh provider observation", failure="operation", cap="one PR/100 labels")

    def test_in_31_provider_identity(self):
        self.assert_row(31, authority="fresh provider observation", failure="operation", cap="one coherent tuple")

    def test_in_32_pr_body(self):
        self.assert_row(32, authority="fresh provider observation", failure="operation", cap="1 MiB")

    def test_in_33_provider_comments(self):
        self.assert_row(33, authority="fully paged provider observation", failure="operation", cap="1000 comments/8 MiB wire")

    def test_in_34_host_stdout(self):
        self.assert_row(34, authority="owned subprocess", failure="operation", cap="8 MiB stdout")

    def test_in_35_worker_receipt(self):
        self.assert_row(35, authority="tested-tree receipt", failure="operation", cap="100000 paths")

    def test_in_36_publisher_paths(self):
        self.assert_row(36, authority="raw git output", failure="operation", cap="32 MiB/100000 records")

    def test_in_37_first_draft_title(self):
        self.assert_row(37, authority="shared grammar", failure="operation", cap="4 KiB subject")

    def test_in_38_first_draft_body(self):
        self.assert_row(38, authority="trusted template + escaped scalars", failure="operation", cap="1 MiB")

    def test_in_39_rewrite_message(self):
        self.assert_row(39, authority="pinned file handle", failure="operation", cap="1 MiB")

    def test_in_40_rewrite_refs_identity(self):
        self.assert_row(40, authority="frozen git refs + local identity", failure="operation", cap="4 KiB identity fields")

    def test_in_41_external_file_reads(self):
        self.assert_row(41, authority="manifest/config pins", failure="operation", cap="channel-specific before allocation")

    def test_in_42_diagnostics(self):
        self.assert_row(42, authority="stable reason codes", failure="operation", cap="256 reasons/4 KiB each")

    def test_in_43_raw_commit_headers(self):
        self.assert_row(43, authority="raw git object", failure="commit", cap="32 MiB/65536 header lines")

    def test_in_44_repository_git_config(self):
        self.assert_row(44, authority="isolated explicit git config", failure="operation", cap="closed keys")

    def test_in_45_require_run_logs(self):
        self.assert_row(45, authority="reviewed host config", failure="operation", cap="one Boolean")

    def test_in_46_generator_identity(self):
        self.assert_row(46, authority="reviewed generator registry", failure="operation", cap="closed script mapping")

    def test_in_47_publication_recovery(self):
        self.assert_row(47, authority="durable CAS record", failure="operation", cap="1 MiB record")

    def test_in_48_subprocess_stderr(self):
        self.assert_row(48, authority="owned subprocess", failure="operation", cap="8 MiB host/32 MiB git")

    def test_in_49_rewrite_remote_inputs(self):
        self.assert_row(49, authority="frozen refs + remote readback", failure="operation", cap="32 MiB response")


class R13MessageAndPathAcceptanceTests(unittest.TestCase):
    def test_awf49a_r12_056_001_emitter_parser_totality(self):
        for separator in ("\u2028", "\u2029", "\x85", "\v", "\f", "\x1c", "\x1d", "\x1e", "\r"):
            with self.subTest(separator=ascii(separator)), self.assertRaises(ValidationError):
                writer_commit_message("subject" + separator + "body", codex_run_id=RUN_A)
        emitted = writer_commit_message("subject\r\n\r\nbody", codex_run_id=RUN_A)
        self.assertNotIn("\r", emitted)
        self.assertEqual(RUN_A, parse_writer_trailer(emitted)["identity"])

    def test_commit_message_grammar_and_caps_matrix(self):
        invalid = ("\ufeffsubject", "subject\x7f", "subject\x80", "subject\u200b", " \t\n")
        for value in invalid:
            with self.subTest(value=ascii(value)), self.assertRaises(ValidationError):
                provenance.validate_commit_message(value)
        self.assertEqual("subject\nbody", provenance.validate_commit_message("subject\r\nbody"))
        with self.assertRaises(ValidationError):
            provenance.validate_commit_message("x" * (1024 * 1024 + 1))

    def test_writer_line_count_position_case_whitespace_and_lookalikes(self):
        variants = (" writer:", "writer:", "WRITER:", "Writer :", "Ｗriter:", "Writer：")
        for key in variants:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                parse_writer_trailer(f"subject\n\n{key} codex/{RUN_A}")

    def test_critic_run_line_count_position_case_whitespace_and_lookalikes(self):
        carrier = f"APPROVE\n\nCritic run: {RUN_A}"
        self.assertEqual(RUN_A, provenance.parse_critic_run_carrier(carrier)["run_id"])
        for value in (carrier + "\n" + carrier.splitlines()[-1],
                      f"Critic Run: {RUN_A}", f"Critic-run: {RUN_A}",
                      f" Critic run: {RUN_A}"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                provenance.parse_critic_run_carrier(value)

    def test_awf132_writer_emitter_rejects_dash_and_minus_separators(self):
        separators = ("\u2212", "\u2043", "\u02d7", "\u2053", "\u2796")
        for separator in separators:
            for line in (
                    f"prefix{separator}Writer: codex/{RUN_A}",
                    f"Writer{separator}: codex/{RUN_A}"):
                with self.subTest(separator=ascii(separator), line=line), \
                        self.assertRaisesRegex(
                            ValidationError, r"^EMITTER_WRITER_AMBIGUOUS:"):
                    writer_commit_message(
                        f"subject\n\n{line}", codex_run_id=RUN_A)

    def test_awf132_critic_parser_rejects_dash_and_minus_separators(self):
        separators = ("\u2212", "\u2043", "\u02d7", "\u2053", "\u2796")
        for separator in separators:
            line = f"Critic{separator}run: {RUN_A}"
            with self.subTest(separator=ascii(separator)), self.assertRaisesRegex(
                    ValidationError, r"^CRITIC_LINE_INVALID:"):
                provenance.parse_critic_run_carrier(
                    f"{line}\nCritic run: {RUN_A}")

    def test_awf132_trailer_cap_counts_colon_lookalikes(self):
        for colon in ("\ua789", "\u2236", "\ufe13", "\ufe55"):
            with self.subTest(colon=ascii(colon)), self.assertRaisesRegex(
                    ValidationError, r"^WRITER_TRAILER_LIMIT:"):
                parse_writer_trailer(
                    "subject\n\n" + "\n".join(
                        f"Key{index}{colon} value" for index in range(257)
                    )
                )

    def test_awf49a_r12_056_002_undecodable_path_is_sanitized(self):
        with self.assertRaisesRegex(ValidationError, r"PATH_UTF8.*ordinal=2.*sha256=") as caught:
            provenance.decode_git_paths(b"ok\0bad\xff\0", failure="commit")
        self.assertNotIn("bad", str(caught.exception))

    def test_safe_ref_grammar_rejects_revision_expressions(self):
        for value in ("HEAD~1", "main^", "@", "@{-1}", "main:path", "origin/main"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                provenance.validate_ref_locator(value)
        self.assertEqual("refs/heads/main", provenance.validate_ref_locator("refs/heads/main"))
        self.assertEqual("a" * 40, provenance.validate_ref_locator("a" * 40))


class TrailerParsingTests(unittest.TestCase):
    def test_tagged_and_untagged_messages(self):
        parsed = parse_writer_trailer(f"Implement change\n\nWriter: codex/{RUN_A}\n")
        self.assertEqual(("codex", RUN_A), (parsed["kind"], parsed["identity"]))
        self.assertIsNone(parse_writer_trailer("Human-authored commit\n"))
        with self.assertRaisesRegex(ValidationError, "canonical UUID"):
            parse_writer_trailer("Placeholder\n\nWriter: codex/writer-001\n")

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

    def test_non_git_line_separators_cannot_create_a_writer_trailer(self):
        separators = {
            "line separator": "\u2028",
            "paragraph separator": "\u2029",
            "next line": "\u0085",
            "vertical tab": "\v",
            "form feed": "\f",
            "file separator": "\x1c",
            "group separator": "\x1d",
            "record separator": "\x1e",
            "lone carriage return": "\r",
        }
        for name, separator in separators.items():
            message = f"Change{separator}{separator}Writer: codex/{RUN_A}"
            with self.subTest(separator=name), self.assertRaisesRegex(
                    ValidationError, "unsupported.*separator"):
                parse_writer_trailer(message)

    def test_lf_and_crlf_writer_trailers_are_accepted(self):
        for name, separator in {"LF": "\n", "CRLF": "\r\n"}.items():
            with self.subTest(separator=name):
                parsed = parse_writer_trailer(
                    f"Change{separator}{separator}Writer: codex/{RUN_A}{separator}"
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
            {"run_id": RUN_A, "head_sha": head,
             "result_sha256": "b" * 64,
             "critic_run_carrier": f"Critic run: {RUN_A}",
             "candidate_binding": {"head_sha": head},
             "critic_artifact_binding": {
                 "critic_run_id": RUN_A, "head_sha": head},
             "critic_review": {"run_id": RUN_A, "head_sha": head}},
            reviewed_head=head
        )
        self.assertEqual(
            {"critic_run_id": RUN_A, "head_sha": head,
             "trailer": f"Critic run: {RUN_A}",
             "result_sha256": "b" * 64},
            binding,
        )

    def test_verdict_without_run_id_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "critic run id"):
            validate_critic_run_binding({"head_sha": "a" * 40})
        with self.assertRaisesRegex(ValidationError, "reviewed head"):
            validate_critic_run_binding(
                {"run_id": RUN_A, "head_sha": "a" * 40}, reviewed_head="b" * 40
            )


    def test_awf49a_r3_001_candidate_binding_cannot_name_another_head(self):
        head = "b" * 40
        verdict = {
            "run_id": RUN_A,
            "head_sha": head,
            "result_sha256": "c" * 64,
            "critic_run_carrier": f"Critic run: {RUN_A}",
            "candidate_binding": {
                "repository_id": 1,
                "pr_number": 49,
                "base_sha": "a" * 40,
                "head_sha": "f" * 40,
            },
            "critic_artifact_binding": {
                "critic_run_id": RUN_A,
                "head_sha": head,
            },
            "critic_review": {"run_id": RUN_A, "head_sha": head},
        }

        with self.assertRaisesRegex(ValidationError, "Candidate binding.*reviewed head"):
            validate_critic_run_binding(verdict, reviewed_head=head)


class OwnerOverrideSliceAAcceptanceTests(unittest.TestCase):
    def test_in26_b19_b22_versioned_envelope_accepts_194_v1_and_refuses_unknown(self):
        authority = reviewed_owner_config(101)
        record = record_owner_override(
            {}, who=101, when=NOW, why="1.9.4 compatibility", pr=49,
            covered_shas=["a" * 40], reviewed_owner_config=authority,
        )
        self.assertEqual(1, record["schema_version"])
        self.assertEqual(
            record,
            verify_owner_override(
                record, reviewed_owner_config=authority, pr=49,
            ),
        )
        self.assertIn("Schema version: 1", render_owner_override(record))

        unsupported = copy.deepcopy(record)
        unsupported["schema_version"] = 3
        unsupported["override_sha256"] = fingerprint(
            "provenance_override",
            {key: value for key, value in unsupported.items()
             if key != "override_sha256"},
        )
        with self.assertRaisesRegex(
                ValidationError, r"^OVERRIDE_VERSION_UNSUPPORTED:.*3"):
            verify_owner_override(
                unsupported, reviewed_owner_config=authority, pr=49,
            )

        legacy_text_owner = {
            **record,
            "who": "owner-1",
            "override_sha256": "",
        }
        legacy_text_owner["override_sha256"] = fingerprint(
            "provenance_override",
            {key: value for key, value in legacy_text_owner.items()
             if key != "override_sha256"},
        )
        with self.assertRaisesRegex(
                ValidationError,
                r"^OVERRIDE_VERSION_AUTHORITY_INCOMPATIBLE:.*version 1"):
            verify_owner_override(
                legacy_text_owner, reviewed_owner_config=authority, pr=49,
            )

    def test_in27_coverage_has_stable_reason_and_bounded_sorted_shas(self):
        authority = reviewed_owner_config(101)
        covered = [f"{value:040x}" for value in range(257)]
        with self.assertRaisesRegex(
                ValidationError, r"^OVERRIDE_COVERAGE_INVALID:"):
            record_owner_override(
                {}, who=101, when=NOW, why="Too broad", pr=49,
                covered_shas=covered, reviewed_owner_config=authority,
            )

    def test_in28_supersession_chain_has_stable_reason_and_preserves_predecessor(self):
        authority = reviewed_owner_config(101)
        state = {}
        original = record_owner_override(
            state, who=101, when=NOW, why="Initial", pr=49,
            covered_shas=["a" * 40], reviewed_owner_config=authority,
        )
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(
                ValidationError, r"^OVERRIDE_CHAIN_INVALID:"):
            supersede_owner_override(
                state, who=101, when="2026-10-13T12:00:00Z",
                why="Wrong predecessor", pr=49,
                covered_shas=["a" * 40, "b" * 40],
                supersedes="c" * 64,
                reviewed_owner_config=authority,
            )
        self.assertEqual(before, state)
        self.assertEqual(original, state["provenance_overrides"]["49"])


class OwnerOverrideTests(unittest.TestCase):
    def test_override_is_recorded_sealed_and_renderable_for_the_pr(self):
        authority = reviewed_owner_config(101, 102)
        state = {}
        sha = "a" * 40
        record = record_owner_override(
            state,
            who=101,
            when=NOW,
            why="Fallback bot commit predates provenance enforcement",
            pr=49,
            covered_shas=[sha],
            reviewed_owner_config=authority,
        )
        self.assertEqual(record, state["provenance_overrides"]["49"])
        self.assertEqual(record, verify_owner_override(
            record, reviewed_owner_config=authority, pr=49
        ))
        rendered = render_owner_override(record)
        for value in ("101", NOW, "#49", sha, record["override_sha256"]):
            self.assertIn(value, rendered)

    def test_unauthorised_override_is_rejected_without_state_change(self):
        state = {}
        with self.assertRaisesRegex(ValidationError, "trusted owner"):
            record_owner_override(
                state,
                who=202,
                when=NOW,
                why="Attempted bypass",
                pr=49,
                covered_shas=["a" * 40],
                reviewed_owner_config=reviewed_owner_config(101),
            )
        self.assertEqual({}, state)

    def test_override_reason_cannot_inject_a_pr_body_line(self):
        with self.assertRaisesRegex(ValidationError, "bounded reason"):
            record_owner_override(
                {}, who=101, when=NOW, why="reason\nInjected: value", pr=49,
                covered_shas=["a" * 40],
                reviewed_owner_config=reviewed_owner_config(101),
            )

    def test_override_requires_non_empty_owner_identity_and_reason(self):
        cases = [
            ({"who": "", "why": "A reason"},
             "owner identity"),
            ({"who": 101, "why": ""},
             "bounded reason"),
        ]
        for values, message in cases:
            state = {}
            with self.subTest(values=values), self.assertRaisesRegex(
                    ValidationError, message):
                record_owner_override(
                    state, when=NOW, pr=49, covered_shas=["a" * 40],
                    reviewed_owner_config=reviewed_owner_config(101), **values
                )
            self.assertEqual({}, state)

    def test_superseding_override_preserves_and_binds_the_original_record(self):
        authority = reviewed_owner_config(101)
        state = {}
        original = record_owner_override(
            state, who=101, when=NOW, why="Initial exception", pr=49,
            covered_shas=["a" * 40], reviewed_owner_config=authority,
        )
        original_bytes = json.dumps(
            state["provenance_overrides"]["49"],
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

        superseding = supersede_owner_override(
            state, who=101, when="2026-10-13T11:05:00Z",
            why="Extend the authenticated exception", pr=49,
            covered_shas=["a" * 40, "b" * 40],
            supersedes=original["override_sha256"],
            reviewed_owner_config=authority,
        )

        self.assertEqual(
            original_bytes,
            json.dumps(state["provenance_overrides"]["49"],
                       sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        self.assertEqual(original, verify_owner_override(
            original, reviewed_owner_config=authority, pr=49
        ))
        self.assertEqual(original["override_sha256"], superseding["supersedes"])
        self.assertEqual(
            superseding,
            verify_owner_override(
                superseding, reviewed_owner_config=authority, pr=49,
                supersedes=original,
            ),
        )
        self.assertEqual(
            [superseding], state["provenance_override_supersessions"]["49"]
        )
        rendered = render_owner_override(superseding)
        self.assertIn(original["override_sha256"], rendered)

    def test_tampering_with_original_or_superseding_override_is_detected(self):
        authority = reviewed_owner_config(101)
        state = {}
        original = record_owner_override(
            state, who=101, when=NOW, why="Initial exception", pr=49,
            covered_shas=["a" * 40], reviewed_owner_config=authority,
        )
        superseding = supersede_owner_override(
            state, who=101, when="2026-10-13T11:05:00Z",
            why="Extended exception", pr=49,
            covered_shas=["a" * 40, "b" * 40],
            supersedes=original["override_sha256"],
            reviewed_owner_config=authority,
        )
        tampered_original = copy.deepcopy(original)
        tampered_original["why"] = "Changed"
        with self.assertRaisesRegex(ValidationError, "digest"):
            verify_owner_override(
                tampered_original, reviewed_owner_config=authority, pr=49
            )
        for field, value in (("why", "Changed"), ("supersedes", "c" * 64)):
            tampered = copy.deepcopy(superseding)
            tampered[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValidationError, "digest"):
                verify_owner_override(
                    tampered, reviewed_owner_config=authority, pr=49,
                    supersedes=original,
                )

    def test_invalid_supersede_is_refused_without_state_change(self):
        authority = reviewed_owner_config(101)
        state = {}
        original = record_owner_override(
            state, who=101, when=NOW, why="Initial exception", pr=49,
            covered_shas=["a" * 40], reviewed_owner_config=authority,
        )
        cases = [
            {"who": 202},
            {"who": ""},
            {"why": ""},
            {"pr": 50},
            {"supersedes": "c" * 64},
            {"supersedes": None},
        ]
        for changed in cases:
            with self.subTest(changed=changed):
                before = copy.deepcopy(state)
                values = {
                    "who": 101, "when": "2026-10-13T11:05:00Z",
                    "why": "Extended exception", "pr": 49,
                    "covered_shas": ["a" * 40, "b" * 40],
                    "supersedes": original["override_sha256"],
                    "reviewed_owner_config": authority,
                }
                values.update(changed)
                with self.assertRaises(ValidationError):
                    supersede_owner_override(state, **values)
                self.assertEqual(before, state)


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
        self.git_run("config", "user.name", "Fixture Writer")
        self.git_run("config", "user.email", "fixture@example.invalid")
        self.git_run("config", "commit.gpgsign", "false")
        self.write("README.md", "base\n")
        self.base = self.commit("base", f"Writer: codex/{RUN_A}")

    def tearDown(self):
        self.temporary.cleanup()

    def git_run(self, *args, allowed_codes=(0,)):
        result = subprocess.run(
            [self.git, "-C", str(self.root), *args],
            text=True,
            capture_output=True,
            env=self.environment,
            timeout=30,
        )
        if result.returncode not in allowed_codes:
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

    def commit_exact_message(self, message):
        self.git_run("add", "--all")
        with tempfile.NamedTemporaryFile(delete=False) as stream:
            message_path = Path(stream.name)
            stream.write(message.encode("utf-8"))
        try:
            self.git_run(
                "commit", "--quiet", "--cleanup=verbatim", "--file", str(message_path)
            )
        finally:
            message_path.unlink()
        return self.git_run("rev-parse", "HEAD")

    def test_awf49a_r12_056_002_undecodable_path_is_per_commit_refusal(self):
        blob = subprocess.run(
            [self.git, '-C', str(self.root), 'hash-object', '-w', '--stdin'],
            input=b'hostile\n', capture_output=True, check=True,
            env=self.environment).stdout.strip()
        index_row = b'100644 ' + blob + b'\tbad\xff\0'
        subprocess.run(
            [self.git, '-C', str(self.root), 'update-index', '-z', '--index-info'],
            input=index_row, capture_output=True, check=True, env=self.environment)
        tree = subprocess.run(
            [self.git, '-C', str(self.root), 'write-tree'], capture_output=True,
            check=True, env=self.environment).stdout.strip()
        hostile = subprocess.run(
            [self.git, '-C', str(self.root), 'commit-tree', tree.decode('ascii'),
             '-p', self.base],
            input=f"hostile path\n\nWriter: codex/{RUN_A}".encode(),
            capture_output=True, check=True, env=self.environment).stdout.decode().strip()
        later = subprocess.run(
            [self.git, '-C', str(self.root), 'commit-tree', tree.decode('ascii'),
             '-p', hostile],
            input=f"later safe commit\n\nWriter: codex/{RUN_B}".encode(),
            capture_output=True, check=True, env=self.environment).stdout.decode().strip()

        result = check_commit_range(self.root, self.base, later)

        self.assertEqual('REFUSE', result['status'])
        self.assertEqual(hostile, result['refusals'][0]['sha'])
        self.assertRegex(result['refusals'][0]['reason'], r'PATH_UTF8.*ordinal=1.*sha256=')
        self.assertNotIn('bad', result['refusals'][0]['reason'])
        self.assertEqual('PASS', result['commits'][1]['status'])

    def make_override_recovery_range(self):
        affected = []
        for index in range(2):
            self.write(f"legacy-{index}.txt", f"legacy {index}\n")
            affected.append(self.commit(
                f"legacy writer {index}",
                "Writer: codex/writer-amend-r3-p1-013",
            ))
        duplicate = f"Writer: codex/{RUN_A}\nWriter: codex/{RUN_B}"
        for index in range(2):
            self.write(f"duplicate-{index}.txt", f"duplicate {index}\n")
            affected.append(self.commit(f"duplicate writer {index}", duplicate))
        self.write("valid.txt", "valid\n")
        head = self.commit("valid writer", f"Writer: codex/{RUN_A}")
        return affected, head

    def make_superseding_override(self, affected, *, who=101,
                                  owner_ids=(101,)):
        authority = reviewed_owner_config(*owner_ids)
        state = {}
        original = record_owner_override(
            state, who=101, when=NOW, why="Initial two-commit exception",
            pr=128, covered_shas=affected[:2],
            reviewed_owner_config=authority,
        )
        latest = supersede_owner_override(
            state, who=who, when="2026-10-13T18:18:00Z",
            why="Digest-linked four-commit recovery", pr=128,
            covered_shas=affected, supersedes=original["override_sha256"],
            reviewed_owner_config=authority,
        )
        return original, latest, authority

    def make_provider_override(self, covered_shas):
        authority = reviewed_owner_config(101)
        record = record_owner_override(
            {}, who=101, when=NOW, why="Reviewed fallback", pr=49,
            covered_shas=covered_shas, reviewed_owner_config=authority,
        )
        rendered = render_owner_override(record)
        observation = {
            "version": 1,
            "repository_id": 12,
            "config_sha256": "f" * 64,
            "base": self.base,
            "base_ref": "main",
            "pr": 49,
            "labels": [],
            "trusted_owner_ids": [101],
            "comments": [{
                "kind": "issue",
                "id": 91,
                "actor_id": 101,
                "url": "https://example.invalid/pull/49#issuecomment-91",
                "body": rendered,
                "body_sha256": sha256(rendered.encode()).hexdigest(),
            }],
        }
        policy = {
            "require_run_logs": False,
            "default_ref": "refs/heads/main",
            "generated_paths": [],
            "generators": {},
        }
        return record, observation, policy, authority

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

    def test_real_commits_use_only_git_line_boundaries_for_writer_trailers(self):
        previous = self.base
        for name, separator in {"LF": "\n", "CRLF": "\r\n"}.items():
            self.write(f"{name}.txt", f"{name}\n")
            head = self.commit_exact_message(
                f"{name} message{separator}{separator}Writer: codex/{RUN_B}{separator}"
            )
            result = check_commit_range(self.root, previous, head)
            self.assertEqual("PASS", result["status"], result["refusals"])
            previous = head

        self.write("unicode-separator.txt", "refuse\n")
        head = self.commit_exact_message(
            f"Unicode message\u2028\u2028Writer: codex/{RUN_B}"
        )
        result = check_commit_range(self.root, previous, head)
        self.assertEqual("REFUSE", result["status"])
        self.assertIn("unsupported", result["refusals"][0]["reason"])

    def test_first_draft_publisher_emits_writer_trailer_from_reserved_run(self):
        self.write("src/feature.py", "value = 1\n")
        changes = [{"path": "src/feature.py", "action": "added"}]
        tree = candidate_tree(self.root, self.base, changes)
        receipt = {
            "outcome": "CHANGED",
            "changes": changes,
            "tested_tree": tree.tested_tree,
            "ignored_untracked": list(tree.ignored_untracked),
            "summary": "Implemented the feature",
        }
        pushes = []
        fixture = self

        class Adapter:
            def run(self, *args):
                if args[0] == "push":
                    pushes.append(args)
                    return ""
                return fixture.git_run(*args)

        publication = publish_codex_tested_tree(
            self.root,
            self.base,
            "main",
            receipt,
            body="Safe body\n",
            commit_message="Implement feature",
            codex_run_id=RUN_B,
            git=Adapter(),
            allowed_paths={"src/feature.py"},
        )

        message = self.git_run("show", "-s", "--format=%B", publication["head"])
        self.assertEqual(
            f"Implement feature\n\nWriter: codex/{RUN_B}",
            message,
        )
        self.assertEqual(1, len(pushes))

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

    def test_manifest_mode_change_is_not_a_reproduced_generated_entry(self):
        content = '{"version": 1}\n'
        self.write("MANIFEST.json", content)
        manifest_base = self.commit("manifest base", f"Writer: codex/{RUN_A}")
        self.git_run("update-index", "--chmod=+x", "MANIFEST.json")
        self.git_run(
            "commit", "--quiet", "--message",
            "mode only\n\nWriter: script/manifest-regen",
        )
        head = self.git_run("rev-parse", "HEAD")
        result = check_commit_range(
            self.root, manifest_base, head,
            reproduce_generated=lambda _sha, _paths: {
                "MANIFEST.json": content.encode("utf-8")
            },
        )
        self.assertEqual("REFUSE", result["status"])
        self.assertIn("mode/type", result["refusals"][0]["reason"])

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

    def test_run_log_record_is_closed_bounded_and_candidate_bound(self):
        self.write("src/bound.py", "value = 2\n")
        head = self.commit("bound", f"Writer: codex/{RUN_B}")
        policy = {'require_run_logs': False,
                  'default_ref': 'refs/heads/main',
                  'generated_paths': [], 'generators': {}}
        observation = {'version': 1, 'base': self.base, 'base_ref': 'main',
                       'pr': 49, 'labels': [], 'trusted_owner_ids': []}

        def closed(run_id, commit_sha, entries):
            return {'version': 1, 'status': 'ABSENT', 'run_id': run_id,
                    'commit_sha': commit_sha, 'entries': entries,
                    'evidence_sha256': None,
                    'reason_code': 'RUN_LOG_ABSENT'}

        accepted = check_commit_range(
            self.root, self.base, head, reviewed_policy=policy,
            provider_observation=observation,
            runtime_registry={'run_log_verifier': closed, 'generators': {}})
        self.assertEqual('PASS', accepted['status'])
        for registry in ({'run_log_verifier': lambda *_: True, 'generators': {}},
                         {'generators': {}}):
            with self.subTest(registry=registry):
                refused = check_commit_range(
                    self.root, self.base, head, reviewed_policy=policy,
                    provider_observation=observation,
                    runtime_registry=registry)
                self.assertEqual('REFUSE', refused['status'])
                self.assertRegex(refused['refusals'][0]['reason'],
                                 r'^RUN_LOG_(?:RECORD_INVALID|VERIFIER_UNREGISTERED)$')

        required = {**policy, 'require_run_logs': True}
        refused = check_commit_range(
            self.root, self.base, head, require_run_logs=False,
            reviewed_policy=required, provider_observation=observation,
            runtime_registry={'run_log_verifier': closed, 'generators': {}})
        self.assertEqual('REFUSE', refused['status'])

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
        real_run = provenance.run_bounded
        merge_environments = []

        def observe_run(*args, **kwargs):
            command = args[0]
            if "merge-tree" in command:
                merge_environments.append(dict(kwargs.get("env", {})))
            return real_run(*args, **kwargs)

        with patch("agentic.provenance.run_bounded", side_effect=observe_run):
            result = check_commit_range(
                self.root, self.base, head, origin_main="origin/main"
            )
        self.assertEqual("PASS", result["status"], result["refusals"])
        merge = next(item for item in result["commits"] if item["sha"] == head)
        self.assertEqual((True, "origin/main merge"),
                         (merge["mechanical"], merge["mechanical_reason"]))
        self.assertEqual(1, len(merge_environments))
        object_directory = Path(merge_environments[0]["GIT_OBJECT_DIRECTORY"])
        self.assertTrue(object_directory.is_absolute())
        self.assertFalse(object_directory.is_relative_to(self.root))
        self.assertIn("GIT_ALTERNATE_OBJECT_DIRECTORIES", merge_environments[0])

    def test_merge_accepts_only_reproduced_generated_conflict_resolution(self):
        self.write("MANIFEST.md", "base\n")
        merge_base = self.commit("manifest base", f"Writer: codex/{RUN_A}")
        self.git_run("checkout", "--quiet", "-b", "feature")
        self.write("MANIFEST.md", "feature\n")
        self.commit("feature manifest", f"Writer: codex/{RUN_A}")

        self.git_run("checkout", "--quiet", "main")
        self.write("MANIFEST.md", "main\n")
        main_head = self.commit("main manifest", f"Writer: codex/{RUN_B}")
        self.git_run("update-ref", "refs/remotes/origin/main", main_head)

        self.git_run("checkout", "--quiet", "feature")
        self.git_run(
            "merge", "--no-ff", "origin/main", allowed_codes=(1,)
        )
        resolution = "regenerated\n"
        self.write("MANIFEST.md", resolution)
        head = self.commit("resolve generated conflict", "Writer: script/sync-merge")

        refused = check_commit_range(
            self.root, merge_base, head, origin_main="origin/main"
        )
        self.assertEqual("REFUSE", refused["status"])
        self.assertIn("generated conflict resolution", refused["refusals"][0]["reason"])

        def reproduce(sha, paths):
            self.assertEqual((head, ["MANIFEST.md"]), (sha, paths))
            return {"MANIFEST.md": resolution.encode("utf-8")}

        passed = check_commit_range(
            self.root, merge_base, head, origin_main="origin/main",
            reproduce_generated=reproduce,
        )
        self.assertEqual("PASS", passed["status"], passed["refusals"])
        merge = next(item for item in passed["commits"] if item["sha"] == head)
        self.assertEqual(
            "origin/main merge with reproduced generated conflict resolution",
            merge["mechanical_reason"],
        )

    def test_valid_labelled_override_covers_only_named_commit(self):
        self.write("legacy.txt", "legacy\n")
        head = self.commit("legacy bot commit")
        state = {}
        override = record_owner_override(
            state,
            who=101,
            when=NOW,
            why="Known fallback bot commit",
            pr=49,
            covered_shas=[head],
            reviewed_owner_config=reviewed_owner_config(101),
        )
        result = check_commit_range(
            self.root, self.base, head,
            override=override,
            reviewed_owner_config=reviewed_owner_config(101),
            pr_number=49,
            pr_labels=["fallback: bot-written"],
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual("OVERRIDDEN", result["commits"][0]["status"])

    def test_in29_caller_and_observation_owner_lists_are_ignored_for_reviewed_config(self):
        self.write("legacy-provider.txt", "legacy provider\n")
        head = self.commit("legacy provider commit")
        override, observation, policy, authority = self.make_provider_override([head])
        observation["trusted_owner_ids"] = [202]

        result = check_commit_range(
            self.root, self.base, head, override=override,
            reviewed_policy=policy, provider_observation=observation,
            reviewed_owner_config=authority, trusted_owner_ids=[202],
        )

        self.assertEqual("PASS", result["status"], result["refusals"])
        self.assertEqual("OVERRIDDEN", result["commits"][0]["status"])
        self.assertEqual(
            91, result["provider_override_evidence"]["provider_comment_id"])

    def test_label_free_provider_override_refuses_invalid_inner_authority(self):
        self.write("legacy-provider.txt", "legacy provider\n")
        head = self.commit("legacy provider commit")
        override, observation, policy, authority = self.make_provider_override([head])

        tampered = copy.deepcopy(override)
        tampered["why"] = "Tampered reason"
        wrong_pr = copy.deepcopy(observation)
        wrong_pr["pr"] = 50
        untrusted = copy.deepcopy(authority)
        untrusted["trusted_owner_ids"] = [202]
        untrusted["binding_sha256"] = fingerprint(
            "provenance_owner_config",
            {"config_sha256": untrusted["config_sha256"],
             "trusted_owner_ids": untrusted["trusted_owner_ids"]},
        )
        wrong_comment = copy.deepcopy(observation)
        wrong_comment["comments"][0]["body_sha256"] = "0" * 64
        outside, outside_observation, _, outside_authority = self.make_provider_override(
            ["a" * 40])
        cases = (
            (tampered, observation, authority, r"digest"),
            (override, wrong_pr, authority, r"different PR"),
            (override, observation, untrusted, r"trusted owner"),
            (override, wrong_comment, authority,
             r"exactly one freshly observed trusted owner comment"),
            (outside, outside_observation, outside_authority,
             r"outside the checked PR range"),
        )
        for candidate, provider, owner_authority, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    ValidationError, reason):
                check_commit_range(
                    self.root, self.base, head, override=candidate,
                    reviewed_policy=policy, provider_observation=provider,
                    reviewed_owner_config=owner_authority,
                )

    def test_override_cannot_claim_a_commit_that_did_not_need_it(self):
        self.write("tagged.txt", "tagged\n")
        head = self.commit("tagged", f"Writer: codex/{RUN_A}")
        override = record_owner_override(
            {}, who=101, when=NOW, why="Over-broad record", pr=49,
            covered_shas=[head],
            reviewed_owner_config=reviewed_owner_config(101),
        )
        with self.assertRaisesRegex(ValidationError, "needed no override"):
            check_commit_range(
                self.root, self.base, head, override=override,
                reviewed_owner_config=reviewed_owner_config(101), pr_number=49,
                pr_labels=["fallback: bot-written"],
            )

    def test_range_honours_latest_superseding_override(self):
        authority = reviewed_owner_config(101)
        self.write("legacy-one.txt", "legacy one\n")
        first = self.commit("legacy bot commit one")
        self.write("legacy-two.txt", "legacy two\n")
        second = self.commit("legacy bot commit two")
        state = {}
        original = record_owner_override(
            state, who=101, when=NOW, why="Initial exception", pr=49,
            covered_shas=[first], reviewed_owner_config=authority,
        )
        latest = supersede_owner_override(
            state, who=101, when="2026-10-13T11:05:00Z",
            why="Cover the second affected commit", pr=49,
            covered_shas=[first, second],
            supersedes=original["override_sha256"],
            reviewed_owner_config=authority,
        )

        result = check_commit_range(
            self.root, self.base, second, override=latest,
            override_predecessors=[original],
            reviewed_owner_config=authority, pr_number=49,
            pr_labels=["fallback: bot-written"],
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual(["OVERRIDDEN", "OVERRIDDEN"],
                         [item["status"] for item in result["commits"]])

    def test_duplicate_writer_commits_refuse_without_an_override(self):
        affected, head = self.make_override_recovery_range()

        result = check_commit_range(self.root, self.base, head)

        self.assertEqual("REFUSE", result["status"])
        refusals = {item["sha"]: item["reason"] for item in result["refusals"]}
        self.assertEqual(set(affected), set(refusals))
        for sha in affected[2:]:
            self.assertIn("exactly one Writer trailer", refusals[sha])

    def test_range_accepts_exact_four_commit_digest_linked_supersession(self):
        affected, head = self.make_override_recovery_range()
        original, latest, authority = self.make_superseding_override(affected)

        result = check_commit_range(
            self.root, self.base, head, override=latest,
            override_predecessors=[original], reviewed_owner_config=authority,
            pr_number=128, pr_labels=["fallback: bot-written"],
        )

        self.assertEqual("PASS", result["status"], result["refusals"])
        overridden = {
            item["sha"] for item in result["commits"]
            if item["status"] == "OVERRIDDEN"
        }
        self.assertEqual(set(affected), overridden)
        self.assertEqual("PASS", result["commits"][-1]["status"])

    def test_range_refuses_invalid_superseding_override_chain(self):
        affected, head = self.make_override_recovery_range()
        original, latest, authority = self.make_superseding_override(affected)
        common = {
            "root": self.root,
            "base": self.base,
            "head": head,
            "reviewed_owner_config": authority,
            "pr_number": 128,
            "pr_labels": ["fallback: bot-written"],
        }

        tampered = copy.deepcopy(latest)
        tampered["why"] = "Tampered reason"

        _, unauthorised, _ = self.make_superseding_override(
            affected, who=202, owner_ids=(101, 202)
        )

        wrong_link = copy.deepcopy(latest)
        wrong_link["supersedes"] = "c" * 64
        wrong_link["override_sha256"] = fingerprint(
            "provenance_override",
            {key: value for key, value in wrong_link.items()
             if key != "override_sha256"},
        )

        cases = (
            (tampered, [original], "digest"),
            (unauthorised, [original], "trusted owner"),
            (wrong_link, [original], "does not reference the current record"),
            (latest, [], "predecessor"),
        )
        for override, predecessors, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    ValidationError, reason):
                check_commit_range(
                    override=override,
                    override_predecessors=predecessors,
                    **common,
                )


if __name__ == "__main__":
    unittest.main()
