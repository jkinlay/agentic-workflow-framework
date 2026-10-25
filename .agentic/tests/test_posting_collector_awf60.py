"""AWF-60 production posting-collector and final-gate regressions."""
from __future__ import annotations

import base64
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic import posting_collector
from agentic.canonical import canonical, fingerprint, load, sha256
from agentic.cli import main as workflow_main
from agentic.gates import evaluate, review_round_receipt_sha256, review_verdict_json
from agentic.intake_assignment import assign_intake
from agentic.lifecycle import definition
from agentic.child_process import HOST_PROVIDER_RUNNER_POLICY
from agentic.providers import github_review_host
from agentic.providers.github_review_host import HostDriver
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
        self.posting_calls = []
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
            # Independent provider-rendered fixture, never derived from raw body.
            "body_html": "\n".join(
                f'<a id="review-verdict-{v["round"]}-{v["record_id"]}"></a>'
                for v in bundle["review_verdicts"]
            ),
            "base": {"repo": {"id": candidate["repository_id"]}},
        }

    def api(self, suffix):
        return copy.deepcopy(self.responses[suffix])

    def posting_pull(self, number):
        self.posting_calls.append(number)
        return copy.deepcopy(self.responses[f"pulls/{number}"])


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
        self._rebuild_host()

    def _rebuild_host(self):
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

    def _tier_three_owner_review(self):
        path = "src/release_gate.py"
        self.bundle["worker"]["files_changed"] = [path]
        self.bundle["pr"]["file_manifest"] = [{"path": path, "blob_sha": "1" * 40}]
        self.bundle["critic"]["coverage"].update(
            file_manifest_sha256=fingerprint(
                "file-manifest", self.bundle["pr"]["file_manifest"]
            ),
            reviewed_paths=[path],
        )
        self.bundle["contract"]["scope"]["expected_paths"] = [path]
        self.bundle["contract"].update(
            risk_tier=3, tier_justification="Release gate escalation."
        )
        self.rebind()
        self._rebuild_host()
        verifier = self.verifier_run()
        terminal = self.bundle["review_verdicts"][-1]
        owner = copy.deepcopy(terminal)
        approval_id = 9001
        approval_url = (
            f"{self.bundle['candidate']['host']}/"
            f"{self.bundle['candidate']['repository']}/pull/"
            f"{self.bundle['candidate']['pr_number']}#pullrequestreview-{approval_id}"
        )
        owner.update(
            record_id="10000000-0000-4000-8000-000000000001",
            producer_id=verifier["producer_id"],
            run_id=verifier["run_id"],
            reviewer_id="fixture-owner",
            owner_review=True,
            owner_id=1001,
            pr_comment_url=approval_url,
        )
        self.bundle["owner_review"] = owner
        candidate = self.bundle["candidate"]
        pr_suffix = f"pulls/{candidate['pr_number']}"
        self.host.responses[pr_suffix]["head"] = {"sha": candidate["head_sha"]}
        self.host.responses[pr_suffix]["base"]["sha"] = candidate["target_base_sha"]
        self.host.responses[f"{pr_suffix}/reviews/{approval_id}"] = {
            "id": approval_id,
            "html_url": approval_url,
            "state": "APPROVED",
            "commit_id": candidate["head_sha"],
            "user": {"id": 1001},
        }
        return owner

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

    def _collect_terminal(self):
        verdict = self.bundle["review_verdicts"][-1]
        return collect_posting_observation(
            self.host, candidate=self.bundle["candidate"], verdict=verdict,
            review_round_receipt=self.bundle["review_round_receipts"][-1],
            collector_run_id=verdict["posting_observation"]["run_id"],
            observed_at=verdict["posting_observation"]["observed_at"],
            release_sha256=RELEASE_SHA256,
        )

    def test_awf67_a1_to_a12_a29_rendered_negatives_preserve_valid_raw(self):
        suffix, raw, anchor, fragment = self._terminal_fragment_fixture()
        cases = {
            "A1 absent": "<p>Provider omitted the target.</p>",
            "A4 empty": "",
            "A5 round": anchor.replace("review-verdict-1-", "review-verdict-99-"),
            "A6 record": anchor.replace(fragment, "review-verdict-1-other-record"),
            "A7 text": fragment,
            "A7 escaped": anchor.replace("<", "&lt;").replace(">", "&gt;"),
            "A7 entities": f'&lt;a id=&quot;{fragment}&quot;&gt;&lt;/a&gt;',
            "A8 comment": f"<!-- {anchor} -->",
            "A10 href": f'<a href="#{fragment}">verdict</a>',
            "A11 div id": f'<div id="{fragment}"></div>',
            "A11 span name": f'<span name="{fragment}"></span>',
            "A11 unrelated": f'<a title="{fragment}"></a>',
            "A11 extra attribute": f'<a id="{fragment}" title="verdict"></a>',
            "A11 self closing": f'<a id="{fragment}"/>',
            "A11 uppercase": f'<A ID="{fragment}"></A>',
            "A11 encoded id": anchor.replace("review-verdict", "review&#45;verdict"),
            "A12 unclosed": f'<a id="{fragment}">',
            "A12 mismatched": f'<a id="{fragment}"></div>',
            "A12 unclosed parent": f"<div>{anchor}",
            "A12 stray closer": f"</div>{anchor}",
            "A12 duplicate target": anchor + anchor,
            "A12 duplicate attributes": f'<a id="{fragment}" id="{fragment}"></a>',
            "A12 both attributes": f'<a id="{fragment}" name="{fragment}"></a>',
            "A12 malformed after valid": anchor + "<broken",
            "A12 unclosed comment after valid": anchor + "<!--",
            "A12 nested anchor": f'<a href="elsewhere">{anchor}</a>',
        }
        for tag in ("code", "pre", "script", "style", "textarea", "template",
                    "title", "xmp", "iframe", "noscript", "svg", "math"):
            cases[f"A9 {tag}"] = f"<{tag}>{anchor}</{tag}>"
        for attrs in ('hidden', 'style="display:none"', 'aria-hidden="true"'):
            cases[f"A9 {attrs}"] = f"<div {attrs}>{anchor}</div>"
        for prefix in ("user-content-", "another-prefix-"):
            for attribute in ("id", "name"):
                cases[f"A29 {prefix} {attribute}"] = (
                    f'<a {attribute}="{prefix}{fragment}"></a>'
                )
        for label, rendered in cases.items():
            with self.subTest(case=label):
                self.host.responses[suffix]["body_html"] = rendered
                self.assertEqual(raw, self.host.responses[suffix]["body"])
                with self.assertRaisesRegex(PostingCollectorMismatch, "POSTING_RENDERED"):
                    self._collect_terminal()

    def test_awf67_a2_a3_missing_or_non_text_rendered_fails_closed(self):
        suffix, _raw, _anchor, _fragment = self._terminal_fragment_fixture()
        pull = self.host.responses[suffix]
        for value in (None, [], {}, 1, True, b"bytes", "\ud800"):
            with self.subTest(value=repr(value)):
                pull["body_html"] = value
                with self.assertRaises(ValidationError):
                    self._collect_terminal()
        del pull["body_html"]
        with self.assertRaises(ValidationError):
            self._collect_terminal()

    def test_awf67_a14_a15_and_a22_literal_mapping_and_dedicated_read(self):
        suffix, raw, anchor, fragment = self._terminal_fragment_fixture()
        for attribute in ("id", "name"):
            with self.subTest(attribute=attribute):
                target = f'<a {attribute}="{fragment}"></a>'
                self.host.responses[suffix]["body"] = raw.replace(anchor, target)
                self.host.responses[suffix]["body_html"] = (
                    f'<p dir="auto">Independent rendered summary.</p>\n{target}'
                )
                self.host.posting_calls.clear()
                registrations, registry = self._install_runtime_observations()
                self.assertTrue(registrations)
                self.assertIn(COLLECTOR_ID, registry)
                self.assertEqual([self.bundle["candidate"]["pr_number"]] * 2,
                                 self.host.posting_calls)

    def test_awf67_a23_rendered_bytes_are_retained_and_digest_bound(self):
        suffix, _raw, _anchor, _fragment = self._terminal_fragment_fixture()
        first = self._collect_terminal()["observation"]
        provider = json.loads(base64.b64decode(first["provider_response_bytes"]["data"]))
        self.assertEqual(self.host.responses[suffix]["body_html"], provider["pr_body_html"])
        self.assertEqual(first["body_bytes"], provider["pr_body"])
        self.host.responses[suffix]["body_html"] += "\n<p>Rendered change.</p>"
        second = self._collect_terminal()["observation"]
        self.assertEqual(first["body_sha256"], second["body_sha256"])
        self.assertNotEqual(first["provider_response_sha256"],
                            second["provider_response_sha256"])
        self.assertNotEqual(first["collector_receipt_sha256"],
                            second["collector_receipt_sha256"])

    def test_awf67_a24_a27_retained_tampering_and_raw_only_envelope_refused(self):
        self._install_runtime_observations()
        for omit in (False, True):
            with self.subTest(raw_only=omit):
                changed = copy.deepcopy(self.bundle)
                observation = changed["review_verdicts"][-1]["posting_observation"]
                provider = json.loads(base64.b64decode(
                    observation["provider_response_bytes"]["data"]))
                if omit:
                    provider.pop("pr_body_html", None)
                else:
                    provider["pr_body_html"] = "<p>Retained tampering.</p>"
                wire = canonical(provider)
                observation["provider_response_bytes"]["data"] = base64.b64encode(wire).decode("ascii")
                observation["provider_response_sha256"] = sha256(wire)
                with self.assertRaisesRegex(PostingCollectorMismatch, "POSTING_PROVIDER_RESPONSE_MISMATCH"):
                    registry_from_review_host(self.host, changed, release_sha256=RELEASE_SHA256)

    def test_awf67_a25_live_rendered_target_disappears(self):
        self._install_runtime_observations()
        suffix, raw, _anchor, _fragment = self._terminal_fragment_fixture()
        self.host.responses[suffix]["body_html"] = "<p>Target disappeared.</p>"
        with self.assertRaisesRegex(PostingCollectorMismatch, "POSTING_RENDERED"):
            registry_from_review_host(self.host, self.bundle, release_sha256=RELEASE_SHA256)
        self.assertEqual(raw, self.host.responses[suffix]["body"])

    def test_awf67_a26_every_retained_round_needs_its_rendered_target(self):
        # A second complete collector claim, with its own record/run/receipt.
        second = copy.deepcopy(self.bundle["review_verdicts"][0])
        second.update(round=2, record_id=str(uuid.uuid4()))
        second["critic_artifact_binding"]["round"] = 2
        prefix = f'{self.bundle["candidate"]["host"]}/{self.bundle["candidate"]["repository"]}/pull/{self.bundle["candidate"]["pr_number"]}'
        second["pr_comment_url"] = prefix + "#issuecomment-2"
        second["pr_body_link"] = prefix + f'#review-verdict-2-{second["record_id"]}'
        run = copy.deepcopy(next(r for r in self.bundle["runs"]
                                 if r["run_id"] == second["posting_observation"]["run_id"]))
        run.update(run_id=str(uuid.uuid4()), record_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()))
        second["posting_observation"].update(run_id=run["run_id"], comment_id=2,
            comment_url=second["pr_comment_url"], comment_bytes=review_verdict_json(second))
        receipt = copy.deepcopy(self.bundle["review_round_receipts"][0])
        receipt.update(critic_artifact_binding=copy.deepcopy(second["critic_artifact_binding"]),
            review_verdict_json=review_verdict_json(second))
        receipt["review_verdict_sha256"] = sha256(receipt["review_verdict_json"].encode("utf-8"))
        self.bundle["runs"].append(run)
        self.bundle["review_verdicts"].append(second)
        self.bundle["review_round_receipts"].append(receipt)
        self.bundle["evidence_registry"].append({
            "uri": "urn:awf:review-round-receipt:2", "sha256": "0" * 64})
        self._rebuild_host()
        suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        pull = self.host.responses[suffix]
        # Independently assemble all raw round markers and literal source targets.
        pull["body"] = "\n".join(
            f'<a id="review-verdict-{v["round"]}-{v["record_id"]}"></a>\n'
            f'review-verdict:{v["round"]}:{v["record_id"]}:{r["review_verdict_sha256"]}'
            for v, r in zip(self.bundle["review_verdicts"], self.bundle["review_round_receipts"])
        )
        self._install_runtime_observations()
        earlier, terminal = self.bundle["review_verdicts"]
        earlier_target = f'<a id="review-verdict-{earlier["round"]}-{earlier["record_id"]}"></a>'
        terminal_target = f'<a id="review-verdict-{terminal["round"]}-{terminal["record_id"]}"></a>'
        pull["body_html"] = pull["body_html"].replace(earlier_target, "")
        self.assertIn(terminal_target, pull["body_html"])
        with self.assertRaisesRegex(PostingCollectorMismatch, "POSTING_RENDERED"):
            registry_from_review_host(self.host, self.bundle, release_sha256=RELEASE_SHA256)

    def _production_transport(self, directory):
        candidate = self.bundle["candidate"]
        config_path = Path(directory) / "host.json"
        config_path.write_bytes(b"{}\n")
        executable = Path(sys.executable).resolve()
        driver = HostDriver.__new__(HostDriver)
        driver.c = {
            "repository": candidate["repository"], "repository_id": candidate["repository_id"],
            "pr": candidate["pr_number"], "_config_path": str(config_path),
            "config_hash": sha256(config_path.read_bytes()), "command_timeout_seconds": 120,
            "executables": {"gh": {"path": str(executable), "sha256": sha256(executable.read_bytes())}},
        }
        return driver

    def test_awf67_a16_to_a22_a29_fixed_production_read_and_failures(self):
        suffix, _raw, _anchor, _fragment = self._terminal_fragment_fixture()
        pull = copy.deepcopy(self.host.responses[suffix])
        pull["html_url"] = f'https://github.com/{self.bundle["candidate"]["repository"]}/{suffix.replace("pulls/", "pull/")}'
        with tempfile.TemporaryDirectory() as directory:
            driver = self._production_transport(directory)
            wire = json.dumps(pull).encode("utf-8")
            completed = subprocess.CompletedProcess([], 0, wire, b"")
            budget = HOST_PROVIDER_RUNNER_POLICY.make_budget("posting-test")
            with mock.patch.object(github_review_host, "run_bounded", return_value=completed) as runner:
                self.assertEqual(pull, driver.posting_pull(driver.c["pr"], operation_budget=budget))
            runner.assert_called_once()
            self.assertEqual([
                driver.c["executables"]["gh"]["path"], "api", "--hostname", "github.com",
                "--method", "GET", f'repos/{driver.c["repository"]}/{suffix}',
                "--header", "Accept: application/vnd.github.full+json",
            ], runner.call_args.args[0])
            self.assertIs(HOST_PROVIDER_RUNNER_POLICY, runner.call_args.kwargs["policy"])
            self.assertIs(budget, runner.call_args.kwargs["operation_budget"])
            self.assertIsNone(runner.call_args.kwargs["stdin"])
            failures = (
                OSError("provider unavailable"),
                ValidationError("SUBPROCESS_TIMEOUT: child timed out"),
                ValidationError("SUBPROCESS_STDOUT_LIMIT: output exceeded"),
                subprocess.CompletedProcess([], 1, b"", b"access denied"),
                subprocess.CompletedProcess([], 0, b"not JSON", b""),
                subprocess.CompletedProcess([], 0, b"\xff", b""),
            )
            for result in failures:
                with self.subTest(failure=repr(result)):
                    options = ({"side_effect": result} if isinstance(result, Exception)
                               else {"return_value": result})
                    with mock.patch.object(github_review_host, "run_bounded", **options) as runner:
                        with self.assertRaises((ValidationError, OSError)):
                            driver.posting_pull(driver.c["pr"])
                    runner.assert_called_once()

    def test_awf67_a2_a3_a21_provider_rejects_shape_types_and_identity(self):
        suffix, _raw, _anchor, _fragment = self._terminal_fragment_fixture()
        valid = copy.deepcopy(self.host.responses[suffix])
        valid["html_url"] = f'https://github.com/{self.bundle["candidate"]["repository"]}/pull/{valid["number"]}'
        cases = [None, [], {}, dict(valid, number=valid["number"] + 1),
                 dict(valid, number=True), dict(valid, html_url="https://github.com/other/project/pull/7"),
                 dict(valid, base={"repo": {"id": valid["base"]["repo"]["id"] + 1}}),
                 dict(valid, base=None), dict(valid, base={"repo": []})]
        for field in ("body", "body_html"):
            missing = copy.deepcopy(valid)
            del missing[field]
            cases.append(missing)
            cases.extend(dict(valid, **{field: value}) for value in (None, [], {}, 3, True, "\ud800"))
        with tempfile.TemporaryDirectory() as directory:
            driver = self._production_transport(directory)
            for value in cases:
                with self.subTest(value=repr(value)):
                    driver.run = mock.Mock(return_value=json.dumps(value))
                    with self.assertRaises(ValidationError):
                        driver.posting_pull(driver.c["pr"])
                    driver.run.assert_called_once()
            driver.run = mock.Mock()
            for number in (0, -1, True, "7", driver.c["pr"] + 1):
                with self.subTest(requested_pr=number):
                    with self.assertRaises(ValidationError):
                        driver.posting_pull(number)
            driver.run.assert_not_called()

    def test_awf67_a20_a30_recorded_driver_caps_and_generic_api_unchanged(self):
        suffix, _raw, _anchor, _fragment = self._terminal_fragment_fixture()
        value = copy.deepcopy(self.host.responses[suffix])
        value["html_url"] = f'https://github.com/{self.bundle["candidate"]["repository"]}/pull/{value["number"]}'
        wire = json.dumps(value)
        calls = []

        def recorded_run(name, arguments):
            calls.append((name, arguments))
            return wire

        with tempfile.TemporaryDirectory() as directory:
            driver = self._production_transport(directory)
            driver.run = recorded_run
            budget = HOST_PROVIDER_RUNNER_POLICY.make_budget("posting-replay")
            self.assertEqual(value, driver.posting_pull(driver.c["pr"], operation_budget=budget))
            self.assertEqual(1, budget.operations)
            self.assertEqual(len(wire.encode("utf-8")), budget.consumed_bytes)
            self.assertEqual(value, driver.api(suffix, operation_budget=budget))
            self.assertEqual(2, budget.operations)
            self.assertEqual(2 * len(wire.encode("utf-8")), budget.consumed_bytes)
            self.assertEqual(["api", "--hostname", "github.com", "--method", "GET",
                              f'repos/{driver.c["repository"]}/{suffix}'], calls[-1][1])
            budget.consume(budget.remaining_bytes)
            with self.assertRaisesRegex(ValidationError, "OPERATION_BUDGET"):
                driver.posting_pull(driver.c["pr"], operation_budget=budget)
            driver.run = lambda name, arguments: "x" * (HOST_PROVIDER_RUNNER_POLICY.stdout_limit + 1)
            with self.assertRaisesRegex(ValidationError, "HOST_STDOUT_LIMIT"):
                driver.posting_pull(driver.c["pr"])

    def test_awf67_a31_actual_provider_collector_cli_and_gate(self):
        self.config["github"]["host"] = "https://github.com"
        self.bundle["candidate"]["host"] = "https://github.com"
        self.rebind()
        bind_review_admission(self.bundle)
        bind_production_posting_fixture(self.config, self.bundle)
        self._rebuild_host()
        self._install_runtime_observations()
        suffix, _raw, _anchor, _fragment = self._terminal_fragment_fixture()
        provenance_runtime = self.config.get("_awf_runtime")

        def evaluate_with_provenance(config, *args, **kwargs):
            config.get("_awf_runtime").update(commit_provenance=provenance_runtime["commit_provenance"])
            kwargs["repository"] = provenance_runtime["repository"]
            return evaluate(config, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            runtime = ROOT
            driver = self._production_transport(directory)
            bundle_path = Path(directory) / "bundle.json"
            config_path = Path(directory) / "project.json"
            state_path = Path(directory) / "assignment.json"
            config_path.write_text(json.dumps(self.config), encoding="utf-8")
            assignment_config = copy.deepcopy(self.config)
            assignment_config["jira"]["enabled"] = False
            state = {}
            assign_intake(assignment_config,
                {"key": self.bundle["contract"]["ticket"], "summary": "Moderate source change",
                 "description": "", "write_paths": ["src/example.py"], "risk_flags": []},
                state, None, now=NOW, controller_actor_id=assignment_config["jira"]["controller_actor_id"])
            state_path.write_text(json.dumps(state), encoding="utf-8")
            for failure in (None, "target", "unavailable", "timeout", "denied", "json", "utf8", "limit"):
                with self.subTest(failure=failure):
                    bundle_path.write_text(json.dumps(self.bundle), encoding="utf-8")

                    def transport(command, **kwargs):
                        endpoint = command[command.index("--method") + 2]
                        requested = endpoint.removeprefix(f'repos/{driver.c["repository"]}/')
                        response = copy.deepcopy(self.host.responses[requested])
                        if requested == suffix:
                            if failure == "unavailable":
                                raise OSError("offline provider")
                            if failure == "timeout":
                                raise ValidationError("SUBPROCESS_TIMEOUT: timed out")
                            if failure == "limit":
                                raise ValidationError("SUBPROCESS_STDOUT_LIMIT: cap")
                            if failure == "denied":
                                return subprocess.CompletedProcess(command, 1, b"", b"denied")
                            if failure in {"json", "utf8"}:
                                return subprocess.CompletedProcess(command, 0,
                                    b"bad JSON" if failure == "json" else b"\xff", b"")
                            if failure == "target":
                                response["body_html"] = "<p>Missing rendered target.</p>"
                        return subprocess.CompletedProcess(command, 0,
                            json.dumps(response).encode("utf-8"), b"")

                    stdout = io.StringIO()
                    with mock.patch.object(posting_collector, "load_config", return_value=driver.c), \
                            mock.patch.object(posting_collector, "HostDriver", return_value=driver), \
                            mock.patch.object(github_review_host, "run_bounded", side_effect=transport), \
                            mock.patch("agentic.cli.verify_installed", return_value=RELEASE_SHA256), \
                            mock.patch("agentic.cli.evaluate", side_effect=evaluate_with_provenance), \
                            redirect_stdout(stdout):
                        exit_code = workflow_main([
                            "--root", str(runtime), "evaluate", str(bundle_path),
                            "--config", str(config_path), "--assignment-state", str(state_path),
                            "--posting-collector-host-config", str(Path(directory) / "host.json"),
                            "--now", NOW,
                        ])
                    self.assertEqual(0, exit_code)
                    gate = json.loads(stdout.getvalue())
                    self.assertEqual("READY_FOR_OWNER_AUTHORIZATION" if failure is None else "NOT_READY",
                                     gate["conclusion"])
                    self.assertEqual("PASS" if failure is None else "FAIL", gate["gates"]["verdict_posting"]["result"])

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

    def test_ac5_collector_observes_formal_owner_approval_for_exact_candidate(self):
        owner = self._tier_three_owner_review()
        verdict = self.bundle["review_verdicts"][-1]
        receipt = self.bundle["review_round_receipts"][-1]
        collected = collect_posting_observation(
            self.host,
            candidate=self.bundle["candidate"],
            verdict=verdict,
            review_round_receipt=receipt,
            collector_run_id=verdict["posting_observation"]["run_id"],
            observed_at=verdict["posting_observation"]["observed_at"],
            release_sha256=RELEASE_SHA256,
            owner_review=owner,
        )
        provider = json.loads(base64.b64decode(
            collected["observation"]["provider_response_bytes"]["data"]
        ).decode("utf-8"))
        self.assertEqual("APPROVED", provider["owner_approval"]["state"])
        self.assertEqual(1001, provider["owner_approval"]["user"]["id"])
        self.assertEqual(
            self.bundle["candidate"]["head_sha"],
            provider["owner_approval"]["commit_id"],
        )
        self.assertEqual(
            owner["candidate_binding"], provider["owner_approval"]["candidate"]
        )
        verdict["posting_observation"] = collected["observation"]
        owner["posting_observation"] = collected["observation"]
        registry = registry_from_review_host(
            self.host, self.bundle, release_sha256=RELEASE_SHA256
        )
        self.assertIn(
            collected["observation"]["run_id"], registry[COLLECTOR_ID]["receipts"]
        )

    def test_ac5_collector_refuses_wrong_owner_or_candidate_approval(self):
        owner = self._tier_three_owner_review()
        verdict = self.bundle["review_verdicts"][-1]
        receipt = self.bundle["review_round_receipts"][-1]
        suffix = f"pulls/{self.bundle['candidate']['pr_number']}/reviews/9001"
        cases = {
            "owner": ("user", {"id": 2002}),
            "candidate": ("commit_id", "f" * 40),
        }
        original = copy.deepcopy(self.host.responses[suffix])
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                self.host.responses[suffix] = copy.deepcopy(original)
                self.host.responses[suffix][field] = value
                with self.assertRaisesRegex(
                    PostingCollectorMismatch, "OWNER_APPROVAL_MISMATCH"
                ):
                    collect_posting_observation(
                        self.host,
                        candidate=self.bundle["candidate"],
                        verdict=verdict,
                        review_round_receipt=receipt,
                        collector_run_id=verdict["posting_observation"]["run_id"],
                        observed_at=verdict["posting_observation"]["observed_at"],
                        release_sha256=RELEASE_SHA256,
                        owner_review=owner,
                    )
        self.host.responses[suffix] = original

    def test_ac5_registry_refuses_self_asserted_owner_without_provider_approval(self):
        self._tier_three_owner_review()
        with self.assertRaisesRegex(
            PostingCollectorMismatch, "OWNER_APPROVAL_MISMATCH"
        ):
            registry_from_review_host(
                self.host, self.bundle, release_sha256=RELEASE_SHA256
            )

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
                        generate_examples.main([])

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
        provenance_runtime = self.config.get("_awf_runtime")

        def evaluate_with_provenance(config, *args, **kwargs):
            config.get("_awf_runtime").update(
                commit_provenance=provenance_runtime["commit_provenance"])
            kwargs["repository"] = provenance_runtime["repository"]
            return evaluate(config, *args, **kwargs)

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
            assignment_state_path = Path(folder) / "controller-state.json"
            assignment_config = copy.deepcopy(self.config)
            assignment_config["jira"]["enabled"] = False
            assignment_state = {}
            assign_intake(
                assignment_config,
                {"key": self.bundle["contract"]["ticket"], "summary": "Moderate source change",
                 "description": "", "write_paths": ["src/example.py"], "risk_flags": []},
                assignment_state, None, now=NOW,
                controller_actor_id=assignment_config["jira"]["controller_actor_id"],
            )
            assignment_state_path.write_text(json.dumps(assignment_state), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config",
                return_value=registry,
            ) as collector, mock.patch(
                "agentic.cli.evaluate", side_effect=evaluate_with_provenance,
            ), redirect_stdout(stdout):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--assignment-state", str(assignment_state_path),
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
            assignment_state_path = Path(folder) / "controller-state.json"
            assignment_config = copy.deepcopy(self.config)
            assignment_config["jira"]["enabled"] = False
            assignment_state = {}
            assign_intake(
                assignment_config,
                {"key": self.bundle["contract"]["ticket"], "summary": "Moderate source change",
                 "description": "", "write_paths": ["src/example.py"], "risk_flags": []},
                assignment_state, None, now=NOW,
                controller_actor_id=assignment_config["jira"]["controller_actor_id"],
            )
            assignment_state_path.write_text(json.dumps(assignment_state), encoding="utf-8")
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
                    "--assignment-state", str(assignment_state_path),
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
            assignment_state_path = Path(folder) / "controller-state.json"
            assignment_config = copy.deepcopy(self.config)
            assignment_config["jira"]["enabled"] = False
            assignment_state = {}
            assign_intake(
                assignment_config,
                {"key": self.bundle["contract"]["ticket"], "summary": "Moderate source change",
                 "description": "", "write_paths": ["src/example.py"], "risk_flags": []},
                assignment_state, None, now=NOW,
                controller_actor_id=assignment_config["jira"]["controller_actor_id"],
            )
            assignment_state_path.write_text(json.dumps(assignment_state), encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config"
            ) as collector, redirect_stderr(stderr):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--assignment-state", str(assignment_state_path),
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
