"""AWF-60 production posting-collector and final-gate regressions."""
from __future__ import annotations

import base64
import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic import posting_collector
from agentic.canonical import canonical, load, sha256
from agentic.cli import main as workflow_main
from agentic.gates import evaluate, review_round_receipt_sha256
from agentic.lifecycle import definition
from agentic.posting_collector import (
    COLLECTOR_ID,
    PostingCollectorMismatch,
    collect_posting_observation,
    registry_from_review_host,
)
from test_review_policy import (
    Fixture,
    NOW,
    bind_production_posting_fixture,
    bind_review_admission,
    sealed_runtime,
)
from source_only import skip_unless_source_repo


RELEASE_SHA256 = "d" * 64


class TestOnlyReviewHost:
    """No-network provider double; deliberately confined to the test tree."""

    __test__ = False

    def __init__(self, bundle):
        self.responses = {}
        candidate = bundle["candidate"]
        for verdict in bundle["review_verdicts"]:
            observation = verdict["posting_observation"]
            self.responses[f"issues/comments/{observation['comment_id']}"] = {
                "id": observation["comment_id"],
                "html_url": observation["comment_url"],
                "body": observation["comment_bytes"],
            }
        terminal = max(bundle["review_verdicts"], key=lambda item: item["round"])
        self.responses[f"pulls/{candidate['pr_number']}"] = {
            "number": candidate["pr_number"],
            "html_url": (
                f"{candidate['host']}/{candidate['repository']}/pull/"
                f"{candidate['pr_number']}"
            ),
            "body": terminal["posting_observation"]["body_bytes"],
            "base": {"repo": {"id": candidate["repository_id"]}},
        }

    def api(self, suffix):
        return copy.deepcopy(self.responses[suffix])


class PostingCollectorTests(Fixture):
    def setUp(self):
        super().setUp()
        self.config["github"].update(
            host="https://example.invalid", repository="example/project"
        )
        self.bundle["candidate"].update(
            host="https://example.invalid", repository="example/project"
        )
        for verdict in self.bundle["review_verdicts"]:
            verdict["pr_comment_url"] = (
                f"https://example.invalid/example/project/pull/"
                f"{self.bundle['candidate']['pr_number']}#issuecomment-{verdict['round']}"
            )
        self.rebind()
        bind_review_admission(self.bundle)
        bind_production_posting_fixture(self.config, self.bundle)
        self.host = TestOnlyReviewHost(self.bundle)
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        fragment = f"review-verdict-{terminal['round']}-{terminal['record_id']}"
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        self.host.responses[pr_suffix]["body"] += f'\n<a id="{fragment}"></a>\n'
        body_sha256 = sha256(self.host.responses[pr_suffix]["body"].encode("utf-8"))
        self.bundle["pr"]["body_sha256"] = body_sha256
        self.bundle["publication_scan"]["pr_body_sha256"] = body_sha256

    def _install_runtime_observations(self):
        registrations = []
        for verdict in sorted(self.bundle["review_verdicts"], key=lambda item: item["round"]):
            receipt = next(
                item for item in self.bundle["review_round_receipts"]
                if item["critic_artifact_binding"]["round"] == verdict["round"]
            )
            collected = collect_posting_observation(
                self.host,
                candidate=self.bundle["candidate"],
                verdict=verdict,
                review_round_receipt=receipt,
                collector_run_id=verdict["posting_observation"]["run_id"],
                observed_at=verdict["posting_observation"]["observed_at"],
                release_sha256=RELEASE_SHA256,
            )
            verdict["posting_observation"] = collected["observation"]
            registrations.append(collected["registration"])
            posting_bytes = canonical(collected["observation"])
            receipt["posting_observation_json"] = {
                "encoding": "base64",
                "data": base64.b64encode(posting_bytes).decode("ascii"),
            }
            receipt["posting_observation_sha256"] = sha256(posting_bytes)
            uri = f"urn:awf:review-round-receipt:{verdict['round']}"
            entry = next(
                item for item in self.bundle["evidence_registry"] if item["uri"] == uri
            )
            entry["sha256"] = review_round_receipt_sha256(receipt)
        registry = registry_from_review_host(
            self.host, self.bundle, release_sha256=RELEASE_SHA256
        )
        return registrations, registry

    def _terminal_fragment_fixture(self):
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        fragment = f"review-verdict-{terminal['round']}-{terminal['record_id']}"
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        body = self.host.responses[pr_suffix]["body"]
        anchor = f'<a id="{fragment}"></a>'
        self.assertIn(anchor, body)
        return pr_suffix, body, anchor, fragment

    def _assert_fragment_markup_rejected(self, markup):
        pr_suffix, body, anchor, _fragment = self._terminal_fragment_fixture()
        self.host.responses[pr_suffix]["body"] = body.replace(anchor, markup)
        try:
            with self.assertRaisesRegex(
                PostingCollectorMismatch, "POSTING_ANCHOR_MISMATCH"
            ):
                self._install_runtime_observations()
        finally:
            self.host.responses[pr_suffix]["body"] = body

    def test_ac1_live_observation_and_runtime_registry_reach_owner_authorization(self):
        registrations, registry = self._install_runtime_observations()
        gate = evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry=registry,
        )
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertEqual("PASS", gate["gates"]["verdict_posting"]["result"])
        self.assertTrue(all(item["collector_id"] == COLLECTOR_ID for item in registrations))

    def test_ac2_comment_mismatch_has_specific_reason_and_gate_stays_not_ready(self):
        self._install_runtime_observations()
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        suffix = f"issues/comments/{terminal['posting_observation']['comment_id']}"
        self.host.responses[suffix]["body"] += "\nchanged"
        with self.assertRaisesRegex(PostingCollectorMismatch, "POSTING_COMMENT_MISMATCH"):
            registry_from_review_host(
                self.host, self.bundle, release_sha256=RELEASE_SHA256
            )
        gate = evaluate(
            self.config, definition(), self.bundle, self.contracts, NOW,
            posting_collector_registry={},
        )
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_ac2_body_and_anchor_mismatches_are_specific(self):
        self._install_runtime_observations()
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        original = self.host.responses[pr_suffix]["body"]
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        fragment = f"review-verdict-{terminal['round']}-{terminal['record_id']}"
        cases = {
            "POSTING_BODY_MISMATCH": original + "\nchanged",
            "POSTING_ANCHOR_MISMATCH": original.replace(
                f'<a id="{fragment}"></a>\n', ""
            ),
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                self.host.responses[pr_suffix]["body"] = body
                with self.assertRaisesRegex(PostingCollectorMismatch, reason):
                    registry_from_review_host(
                        self.host, self.bundle, release_sha256=RELEASE_SHA256
                    )
        self.host.responses[pr_suffix]["body"] = original

    def test_ac2_digest_marker_without_target_fragment_is_not_observed(self):
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        fragment = f"review-verdict-{terminal['round']}-{terminal['record_id']}"
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        self.host.responses[pr_suffix]["body"] = self.host.responses[pr_suffix][
            "body"
        ].replace(f'<a id="{fragment}"></a>\n', "")

        with self.assertRaisesRegex(
            PostingCollectorMismatch, "POSTING_ANCHOR_MISMATCH"
        ):
            self._install_runtime_observations()

        gate = evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry={},
        )
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])

    def test_ac2_code_examples_do_not_define_target_fragment(self):
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        fragment = f"review-verdict-{terminal['round']}-{terminal['record_id']}"
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        original = self.host.responses[pr_suffix]["body"]
        anchor = f'<a id="{fragment}"></a>'
        cases = {
            "inline code": f"`{anchor}`",
            "indented code": f"    {anchor}",
        }

        for label, replacement in cases.items():
            with self.subTest(label=label):
                self.host.responses[pr_suffix]["body"] = original.replace(
                    anchor, replacement
                )
                with self.assertRaisesRegex(
                    PostingCollectorMismatch, "POSTING_ANCHOR_MISMATCH"
                ):
                    self._install_runtime_observations()

        self.host.responses[pr_suffix]["body"] = original

    def test_a1_backtick_and_tilde_fences_do_not_define_target_fragment(self):
        _pr_suffix, _body, anchor, _fragment = self._terminal_fragment_fixture()
        cases = {
            "top-level backticks": f"```html\n{anchor}\n```",
            "top-level tildes": f"~~~html\n{anchor}\n~~~",
            "list-item backticks": f"- ```html\n  {anchor}\n  ```",
            "list-item tildes": f"- ~~~html\n  {anchor}\n  ~~~",
            "blockquote backticks": f"> ```html\n> {anchor}\n> ```",
            "blockquote tildes": f"> ~~~html\n> {anchor}\n> ~~~",
        }

        for label, markup in cases.items():
            with self.subTest(label=label):
                self._assert_fragment_markup_rejected(markup)

    def test_a2_closed_html_comment_does_not_define_target_fragment(self):
        _pr_suffix, _body, anchor, _fragment = self._terminal_fragment_fixture()
        self._assert_fragment_markup_rejected(f"<!--\n{anchor}\n-->")

    def test_a3_unclosed_html_comment_hides_everything_after_it(self):
        _pr_suffix, _body, anchor, _fragment = self._terminal_fragment_fixture()
        self._assert_fragment_markup_rejected(f"<!-- unclosed\n{anchor}")

    def test_a4_ambiguous_markdown_and_html_fail_closed(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "list item": f"- {anchor}",
            "blockquote": f"> {anchor}",
            "raw HTML block": f"<div>\n{anchor}\n</div>",
            "quoted greater-than": (
                f'<a id="{fragment}" title="ambiguous > delimiter"></a>'
            ),
            "unclosed fence after anchor": f"{anchor}\n```html\nunclosed",
            "short fence closer after anchor": f"{anchor}\n````html\ncode\n```",
            "mismatched fence after anchor": f"{anchor}\n```html\ncode\n~~~",
        }

        for label, markup in cases.items():
            with self.subTest(label=label):
                self._assert_fragment_markup_rejected(markup)

    def test_r6_bare_script_start_hides_target_fragment(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        self.assertFalse(
            posting_collector._defines_fragment(
                f"<script\n{anchor}\n</script>", fragment
            )
        )

    def test_r6_type_1_script_start_delimiters_hide_target_fragment(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        starts = ("<script", "<script ", "<SCRIPT\t", "<script>")

        for start in starts:
            with self.subTest(start=start):
                self.assertFalse(
                    posting_collector._defines_fragment(
                        f"{start}\n{anchor}\n</script>", fragment
                    )
                )

    def test_r6_all_commonmark_html_block_starts_hide_target_fragment(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "type 1 literal tag": ("<style", "</style>"),
            "type 2 comment": ("<!-- Foo", "   baz -->"),
            "type 3 processing instruction": ("<?php", "?>"),
            "type 4 declaration": ("<!DOCTYPE html", ">"),
            "type 5 CDATA": ("<![CDATA[", "]]>"),
            "type 6 block tag": ("<table>", ""),
            "type 6 closing block tag": ("</table>", ""),
            "type 7 complete tag": ("<Warning>", ""),
            "type 7 complete closing tag": ("</ins>", ""),
        }

        for label, (start, end) in cases.items():
            for indent in range(4):
                with self.subTest(label=label, indent=indent):
                    body = f"{' ' * indent}{start}\n{anchor}\n{end}"
                    self.assertFalse(
                        posting_collector._defines_fragment(body, fragment)
                    )

    def test_r6_unterminated_html_blocks_hide_target_fragment_to_eof(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        starts = {
            "type 1": "<pre",
            "type 2": "<!-- Foo",
            "type 3": "<?php",
            "type 4": "<!DOCTYPE html",
            "type 5": "<![CDATA[",
            "type 6": "<table>",
            "type 7": "<Warning>",
        }

        for label, start in starts.items():
            with self.subTest(label=label):
                self.assertFalse(
                    posting_collector._defines_fragment(
                        f"{start}\n{anchor}", fragment
                    )
                )

    def test_r6_target_fragment_after_ended_html_block_is_visible(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "type 1": "<script\nignored\n</script>",
            "type 2": "<!-- Foo\nignored\n-->",
            "type 3": "<?php\nignored\n?>",
            "type 4": "<!DOCTYPE html\nignored\n>",
            "type 5": "<![CDATA[\nignored\n]]>",
            "type 6": "<table>\nignored\n",
            "type 7": "<Warning>\nignored\n",
        }

        for label, block in cases.items():
            with self.subTest(label=label):
                self.assertTrue(
                    posting_collector._defines_fragment(
                        f"{block}\n{anchor}", fragment
                    )
                )

    def test_r7_type_1_blocks_ignore_other_literal_closing_tags(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "script": "pre",
            "pre": "style",
            "style": "textarea",
            "textarea": "script",
        }

        for opener, mismatched_closer in cases.items():
            for matching_closer in (f"</{opener}>", None):
                with self.subTest(
                    opener=opener,
                    mismatched_closer=mismatched_closer,
                    matching_closer=matching_closer,
                ):
                    lines = [
                        f"<{opener}",
                        f"</{mismatched_closer}>",
                        anchor,
                    ]
                    if matching_closer is not None:
                        lines.extend(("", matching_closer))
                    self.assertFalse(
                        posting_collector._defines_fragment(
                            "\n".join(lines), fragment
                        )
                    )

    def test_r7_types_2_to_5_ignore_mismatched_end_markers(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "type 2": ("<!-- Foo", "?>", "-->"),
            "type 3": ("<?php", "-->", "?>"),
            "type 4": ("<!DOCTYPE html", "]]", ">"),
            "type 5": ("<![CDATA[", "?>", "]]>")
        }

        for label, (start, mismatched_end, matching_end) in cases.items():
            with self.subTest(label=label):
                self.assertFalse(
                    posting_collector._defines_fragment(
                        f"{start}\n{mismatched_end}\n{anchor}\n{matching_end}",
                        fragment,
                    )
                )

    def test_r7_types_6_and_7_end_only_at_blank_lines(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "type 6": "<table>\nignored\n</table>",
            "type 7": "<Warning>\nignored\n</Warning>",
        }

        for label, block in cases.items():
            with self.subTest(label=label):
                self.assertFalse(
                    posting_collector._defines_fragment(
                        f"{block}\n{anchor}\n", fragment
                    )
                )

    def test_r7_each_html_block_type_ends_at_its_own_condition(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "type 1 script": "<script\nignored\n</SCRIPT>",
            "type 1 pre": "<pre\nignored\n</PRE>",
            "type 1 style": "<style\nignored\n</STYLE>",
            "type 1 textarea": "<textarea\nignored\n</TEXTAREA>",
            "type 2": "<!-- Foo\nignored\n-->",
            "type 3": "<?php\nignored\n?>",
            "type 4": "<!DOCTYPE html\nignored\n>",
            "type 5": "<![CDATA[\nignored\n]]>",
            "type 6": "<table>\nignored\n",
            "type 7": "<Warning>\nignored\n",
        }

        for label, block in cases.items():
            with self.subTest(label=label):
                self.assertTrue(
                    posting_collector._defines_fragment(
                        f"{block}\n{anchor}", fragment
                    )
                )

    def test_r7_production_collector_and_gate_refuse_type_1_bypass(self):
        pr_suffix, body, anchor, _fragment = self._terminal_fragment_fixture()
        self.host.responses[pr_suffix]["body"] = body.replace(
            anchor,
            f"<script\n</pre>\n{anchor}\n\n</script>",
        )

        with self.assertRaisesRegex(
            PostingCollectorMismatch, "POSTING_ANCHOR_MISMATCH"
        ):
            self._install_runtime_observations()

        gate = evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry={},
        )
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])

    def test_r6_html_block_starts_in_recognised_containers_fail_closed(self):
        _pr_suffix, _body, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "blockquote": f"> <script\n> {anchor}\n> </script>",
            "list": f"- <script\n  {anchor}\n  </script>",
        }

        for label, body in cases.items():
            with self.subTest(label=label):
                self.assertFalse(
                    posting_collector._defines_fragment(body, fragment)
                )

    def test_r5_matching_atx_heading_does_not_define_target_fragment(self):
        _pr_suffix, _body, _anchor, fragment = self._terminal_fragment_fixture()
        self._assert_fragment_markup_rejected(f"# {fragment}")

    def test_b1_backslash_escaped_anchor_does_not_define_target_fragment(self):
        _pr_suffix, _body, anchor, _fragment = self._terminal_fragment_fixture()
        self._assert_fragment_markup_rejected("\\" + anchor)

    def test_b2_non_rendering_element_contents_do_not_define_target_fragment(self):
        _pr_suffix, _body, anchor, _fragment = self._terminal_fragment_fixture()
        for element in (
            "script", "style", "textarea", "template", "title", "xmp", "pre", "code"
        ):
            with self.subTest(element=element):
                self._assert_fragment_markup_rejected(
                    f"<{element}>{anchor}</{element}>"
                )

    def test_b2_non_rendering_tag_id_does_not_define_target_fragment(self):
        _pr_suffix, _body, _anchor, fragment = self._terminal_fragment_fixture()
        self._assert_fragment_markup_rejected(
            f'<script id="{fragment}"></script>'
        )

    def test_r5_id_only_defines_fragment_on_anchor_element(self):
        _pr_suffix, _body, _anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            element: f'<{element} id="{fragment}"></{element}>'
            for element in (
                "form", "div", "span", "details", "p", "h2", "img", "section"
            )
        }

        for element, markup in cases.items():
            with self.subTest(element=element):
                self._assert_fragment_markup_rejected(markup)

    def test_r5_anchor_attribute_allowlist_is_exact(self):
        _pr_suffix, _body, _anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "fragment in href": f'<a href="{fragment}"></a>',
            "extra attribute": f'<a id="{fragment}" title="verdict"></a>',
            "both allowed attributes": (
                f'<a id="{fragment}" name="{fragment}"></a>'
            ),
            "single quotes": f"<a id='{fragment}'></a>",
            "bare value": f"<a id={fragment}></a>",
            "uppercase": f'<A ID="{fragment}"></A>',
            "self closing": f'<a id="{fragment}"/>',
        }

        for label, markup in cases.items():
            with self.subTest(label=label):
                self._assert_fragment_markup_rejected(markup)

    def test_r3_002_name_only_defines_fragment_on_anchor_element(self):
        _pr_suffix, _body, _anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "div": f'<div name="{fragment}"></div>',
            "span": f'<span name="{fragment}"></span>',
            "p": f'<p name="{fragment}"></p>',
            "img": f'<img name="{fragment}"/>',
        }

        for element, markup in cases.items():
            with self.subTest(element=element):
                self._assert_fragment_markup_rejected(markup)

    def test_r3_002_anchor_name_still_defines_target_fragment(self):
        pr_suffix, body, anchor, fragment = self._terminal_fragment_fixture()
        self.host.responses[pr_suffix]["body"] = body.replace(
            anchor, f'<a name="{fragment}"></a>'
        )
        registrations, registry = self._install_runtime_observations()
        self.assertTrue(registrations)
        self.assertIn(COLLECTOR_ID, registry)

    def test_r5_anchor_id_defines_target_fragment(self):
        pr_suffix, body, anchor, fragment = self._terminal_fragment_fixture()
        self.host.responses[pr_suffix]["body"] = body.replace(
            anchor, f'<a id="{fragment}"></a>'
        )
        registrations, registry = self._install_runtime_observations()
        self.assertTrue(registrations)
        self.assertIn(COLLECTOR_ID, registry)

    def test_r5_docstring_pins_accepted_anchor_literal(self):
        expected_example = '<a id="review-verdict-<round>-<record_id>"></a>'
        examples = [
            line.strip()
            for line in posting_collector.__doc__.splitlines()
            if line.strip().startswith('<a id="review-verdict-')
        ]
        self.assertEqual([expected_example], examples)

        round_number = 7
        record_id = "record-123"
        fragment = f"review-verdict-{round_number}-{record_id}"
        anchor = examples[0].replace("<round>", str(round_number)).replace(
            "<record_id>", record_id
        )
        self.assertEqual(f'<a id="{fragment}"></a>', anchor)
        self.assertTrue(
            posting_collector._defines_fragment(f"summary\n{anchor}\n", fragment)
        )

    def test_b3_entity_encoded_anchor_does_not_define_target_fragment(self):
        _pr_suffix, _body, _anchor, fragment = self._terminal_fragment_fixture()
        self._assert_fragment_markup_rejected(
            f'&lt;a id=&quot;{fragment}&quot;&gt;&lt;/a&gt;'
        )

    def test_malformed_nested_pull_response_is_structured_mismatch(self):
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        original = self.host.responses[pr_suffix]
        malformed_shapes = (
            {**original, "base": None},
            {**original, "base": []},
            {**original, "base": {"repo": None}},
            {**original, "base": {"repo": []}},
        )

        for pull in malformed_shapes:
            with self.subTest(base=pull["base"]):
                self.host.responses[pr_suffix] = pull
                with self.assertRaisesRegex(
                    PostingCollectorMismatch, "POSTING_BODY_MISMATCH"
                ):
                    self._install_runtime_observations()

        self.host.responses[pr_suffix] = original

    def test_ac2_digest_round_and_collector_run_mismatches_are_specific(self):
        self._install_runtime_observations()
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        cases = (
            ("review_verdict_sha256", "e" * 64, "POSTING_VERDICT_DIGEST_MISMATCH"),
            (
                "critic_artifact_binding",
                {**terminal["critic_artifact_binding"], "round": 99},
                "POSTING_ROUND_BINDING_MISMATCH",
            ),
            ("run_id", "00000000-0000-0000-0000-000000000099", "POSTING_COLLECTOR_RUN_MISMATCH"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.bundle)
                changed_terminal = max(
                    changed["review_verdicts"], key=lambda item: item["round"]
                )
                changed_terminal["posting_observation"][field] = value
                with self.assertRaisesRegex(PostingCollectorMismatch, reason):
                    registry_from_review_host(
                        self.host, changed, release_sha256=RELEASE_SHA256
                    )

    def test_ac3_config_cannot_register_without_runtime_observation(self):
        self._install_runtime_observations()
        self.config["merge_gate"]["production_posting_collector_ids"] = [COLLECTOR_ID]
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])

    def test_ac4_test_double_is_not_exposed_by_production_module(self):
        import agentic.posting_collector as production

        self.assertFalse(hasattr(production, "TestOnlyReviewHost"))
        self.assertNotIn("TestOnlyReviewHost", Path(production.__file__).read_text(encoding="utf-8"))

    @skip_unless_source_repo(
        "AC5 byte parity needs the source-only contract and example generators",
        "scripts/generate_contracts.py",
        "scripts/generate_examples.py",
    )
    def test_ac5_generated_registration_schema_and_template_have_byte_parity(self):
        scripts_path = str(ROOT / "scripts")
        inserted_scripts_path = scripts_path not in sys.path
        if inserted_scripts_path:
            sys.path.insert(0, scripts_path)
        try:
            import generate_contracts
            import generate_examples

            with tempfile.TemporaryDirectory() as folder:
                generated_root = Path(folder)
                with mock.patch.object(generate_contracts, "ROOT", generated_root):
                    with redirect_stdout(io.StringIO()):
                        generate_contracts.main()
                with mock.patch.object(generate_examples, "ROOT", generated_root):
                    with redirect_stdout(io.StringIO()):
                        generate_examples.main()

                checked_schema = (
                    ROOT / ".agentic/schemas/posting-collector-registration.schema.json"
                )
                generated_schema = (
                    generated_root
                    / ".agentic/schemas/posting-collector-registration.schema.json"
                )
                self.assertEqual(checked_schema.read_bytes(), generated_schema.read_bytes())

                checked_template = (
                    ROOT / ".agentic/templates/posting-collector-registration.yaml"
                )
                generated_template = (
                    generated_root
                    / ".agentic/templates/posting-collector-registration.yaml"
                )
                self.assertEqual(
                    checked_template.read_bytes(), generated_template.read_bytes()
                )
        finally:
            if inserted_scripts_path:
                sys.path.remove(scripts_path)

    def test_ac5_shipped_registration_template_validates_and_refuses_empty_receipts(self):
        checked_template = (
            ROOT / ".agentic/templates/posting-collector-registration.yaml"
        )
        template = load(checked_template)
        self.assertEqual(
            "posting-collector-registration", template["template_for"]
        )
        filled_record = copy.deepcopy(template["record"])
        filled_record["receipts"] = {
            "00000000-0000-0000-0000-000000000001": "f" * 64,
        }
        self.contracts.validate("posting-collector-registration", filled_record)

        refused_record = copy.deepcopy(filled_record)
        refused_record["receipts"] = {}
        with self.assertRaises(ValidationError) as refused:
            self.contracts.validate(
                "posting-collector-registration", refused_record
            )
        self.assertIn("receipts", str(refused.exception))
        self.assertIn("non-empty", str(refused.exception))

    def test_cli_supplies_only_runtime_collector_registry_to_gate(self):
        _registrations, registry = self._install_runtime_observations()
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            bundle_path = Path(folder) / "bundle.json"
            bundle_path.write_text(
                json.dumps(self.bundle, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            config_path = Path(folder) / "project-config.json"
            config_path.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            host_config_path = Path(folder) / "review-host.json"
            host_config_path.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config",
                return_value=registry,
            ) as collector, redirect_stdout(stdout):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--posting-collector-host-config", str(host_config_path),
                    "--now", NOW,
                ])
        self.assertEqual(0, exit_code)
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", json.loads(stdout.getvalue())["conclusion"])
        collector.assert_called_once()

    def test_cli_reports_collector_mismatch_and_keeps_gate_not_ready(self):
        self._install_runtime_observations()
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            bundle_path = Path(folder) / "bundle.json"
            bundle_path.write_text(
                json.dumps(self.bundle, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            config_path = Path(folder) / "project-config.json"
            config_path.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            host_config_path = Path(folder) / "review-host.json"
            host_config_path.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config",
                side_effect=PostingCollectorMismatch(
                    "POSTING_BODY_MISMATCH: live PR body differs"
                ),
            ), redirect_stdout(stdout):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--posting-collector-host-config", str(host_config_path),
                    "--now", NOW,
                ])
        gate = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertIn("POSTING_BODY_MISMATCH", gate["residual_risks"][-1])

    def test_cli_rejects_malformed_bundle_before_collector_runs(self):
        malformed = copy.deepcopy(self.bundle)
        malformed["review_verdicts"] = [None]
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            bundle_path = Path(folder) / "bundle.json"
            bundle_path.write_text(
                json.dumps(malformed, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            config_path = Path(folder) / "project-config.json"
            config_path.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            host_config_path = Path(folder) / "review-host.json"
            host_config_path.write_text("{}\n", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config"
            ) as collector, redirect_stderr(stderr):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--posting-collector-host-config", str(host_config_path),
                    "--now", NOW,
                ])

        response = json.loads(stderr.getvalue())
        self.assertEqual(2, exit_code)
        self.assertEqual("REJECTED", response["status"])
        self.assertFalse(response["execution_authority"])
        self.assertIn("review_verdicts", response["reason"])
        collector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
