"""Change risk tiers, finding bases, dispositions, the amendment cap, closure, parity and digests."""
from __future__ import annotations
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

# Keep this test module bound to the checkout under test when a parent
# validation harness has another AWF checkout on PYTHONPATH.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, load, loads, sha256
from agentic.contracts import Contracts
from agentic.digest import PROSE_WORD_CAP, check_prose, digest_sha256, evidence_comment_event, footer, render
from agentic.gates import (critic_artifact_receipt_sha256, evaluate,
                           posting_collector_receipt_sha256,
                           review_round_receipt_sha256, review_verdict_json)
from agentic.lifecycle import RESUME, STATES, definition, transition
from agentic.review_policy import (BOUNDARY_SENTENCE, cap_disposition_plan, cap_status, check_tier_declaration,
                                   computed_tier, evidence_only, project_instructions_errors, validate_findings)
from review_admission_fixture import bind_review_admission as _legacy_bind_review_admission

NOW = "2026-09-09T12:00:00Z"
EVIDENCE = ["urn:awf:fixture:example-evidence"]


def base64_bytes(value):
    return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}


def sealed_runtime(folder):
    """A synthetic sealed runtime so CLI tests do not depend on a mutable developer manifest."""
    import hashlib
    import shutil
    runtime = Path(folder) / "runtime"
    for name in [".agentic/lib", ".agentic/schemas"]:
        shutil.copytree(ROOT / name, runtime / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (runtime / ".agentic/scripts").mkdir()
    shutil.copyfile(ROOT / ".agentic/scripts/workflow.py", runtime / ".agentic/scripts/workflow.py")
    shutil.copyfile(ROOT / ".agentic/workflow.yaml", runtime / ".agentic/workflow.yaml")
    shutil.copyfile(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml", runtime / ".agentic/PROJECT_CONFIG.yaml")
    files = {p.relative_to(runtime).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime.rglob("*") if p.is_file()}
    from agentic import VERSION
    (runtime / "MANIFEST.json").write_text(json.dumps({"format": "awf-manifest-1", "template_version": VERSION, "files": files}), encoding="utf-8")
    return runtime


def signed(**extra):
    """Placeholder owner_source; `sign()` renders the genuine grammar once the record fields exist."""
    return {"authorization_request_id": str(uuid.uuid4()),
            "owner_source": {"channel": "github_pr_comment", "comment_id": 12, "actor_id": 1001, "actor_login": "fixture-owner",
                             "raw_body": "pending", "raw_body_sha256": "a" * 64, "created_at": NOW, "updated_at": NOW}, **extra}


def sign(record, kind, head_sha, actor_id=1001):
    from agentic.authorization import render_owner_record
    from agentic.canonical import sha256
    if kind == "cap":
        from agentic.gates import cap_disposition_record_id
        record["record_id"] = cap_disposition_record_id(record)
    body = render_owner_record(record, kind, head_sha)
    record["owner_source"].update(raw_body=body, raw_body_sha256=sha256(body.encode("utf-8")), actor_id=actor_id)
    return record


def bind_review_admission(bundle):
    """Test-local upgrade of the legacy admission fixture to retained bytes."""
    admission = _legacy_bind_review_admission(bundle)
    results = []
    for reviewer in admission["completion_snapshot"]["required_reviewers"]:
        review = bundle["critic"] if reviewer == bundle["critic"]["producer_id"] else next(
            item for item in bundle["specialists"] if item["producer_id"] == reviewer)
        verdict = review["verdict"] if reviewer == bundle["critic"]["producer_id"] else (
            "APPROVE" if review["verdict"] == "PASS" else "REQUEST_CHANGES")
        result_json = canonical({"reviewer": reviewer, "verdict": verdict,
                                 "findings": review["findings"]}).decode("utf-8")
        results.append({"reviewer_id": reviewer, "state": "ACCEPTABLE",
                        "result_sha256": sha256(result_json.encode("utf-8")),
                        "terminal_at": NOW})
    snapshot = admission["completion_snapshot"]
    snapshot["results"] = results
    admission["completion_snapshot_sha256"] = fingerprint("review-completion", snapshot)
    admission["provider_preconditions"]["completion_snapshot_sha256"] = (
        admission["completion_snapshot_sha256"])
    return admission


def bind_review_round_receipt(bundle, verdict, critic, completion_snapshot=None):
    """Test-only construction of retained bytes for one critic round."""
    result_json = canonical({"reviewer": verdict["reviewer_id"],
                             "verdict": critic["verdict"],
                             "findings": critic["findings"]}).decode("utf-8")
    result_sha256 = sha256(result_json.encode("utf-8"))
    artifact_binding = {"critic_run_id": verdict["run_id"],
                        "result_sha256": result_sha256,
                        "head_sha": verdict["head_sha"], "round": verdict["round"]}
    verdict["critic_artifact_binding"] = copy.deepcopy(artifact_binding)
    verdict["critic_review"].update(
        record_id=critic["record_id"], run_id=verdict["run_id"],
        round=verdict["round"], head_sha=verdict["head_sha"],
        verdict=critic["verdict"],
        findings_sha256=fingerprint("critic-findings", critic["findings"]),
    )
    if completion_snapshot is None:
        completion_snapshot = copy.deepcopy(bundle["review_submission"]["completion_snapshot"])
        completion_snapshot["cycle_id"] = str(uuid.uuid4())
        completion_snapshot["tuple"]["head_sha"] = verdict["head_sha"]
        completion_snapshot["tuple_sha256"] = fingerprint(
            "review-tuple", completion_snapshot["tuple"])
        completion_snapshot["required_reviewers"] = [verdict["reviewer_id"]]
        completion_snapshot["reviewer_set_sha256"] = fingerprint(
            "reviewer-set", completion_snapshot["required_reviewers"])
        completion_snapshot["counts"] = {"required": 1, "completed": 1,
            "acceptable": 1, "failed": 0, "stale": 0, "outstanding": 0}
        completion_snapshot["results"] = [{"reviewer_id": verdict["reviewer_id"],
            "state": "ACCEPTABLE",
            "result_sha256": result_sha256, "terminal_at": verdict["created_at"]}]
    else:
        completion_snapshot = copy.deepcopy(completion_snapshot)
        row = next(item for item in completion_snapshot["results"]
                   if item["reviewer_id"] == verdict["reviewer_id"])
        if row["result_sha256"] != result_sha256:
            raise AssertionError("test completion digest differs from retained critic bytes")
        verdict["critic_artifact_binding"] = copy.deepcopy(artifact_binding)
    completion_snapshot_json = canonical(completion_snapshot).decode("utf-8")
    verdict["pr_body_link"] = (
        f"{bundle['candidate']['host']}/{bundle['candidate']['repository']}/pull/"
        f"{bundle['candidate']['pr_number']}#review-verdict-{verdict['round']}-{verdict['record_id']}"
    )
    verdict_json = review_verdict_json(verdict)
    verdict_sha256 = sha256(verdict_json.encode("utf-8"))
    body_bytes = (f"Synthetic PR body round {verdict['round']}.\n\n"
                  f"review-verdict:{verdict['round']}:{verdict['record_id']}:{verdict_sha256}\n")
    if verdict["round"] == max(item["round"] for item in bundle["review_verdicts"]):
        bundle["pr"]["body_sha256"] = sha256(body_bytes.encode("utf-8"))
        bundle["publication_scan"]["pr_body_sha256"] = bundle["pr"]["body_sha256"]
    previous_observation = verdict.get("posting_observation", {})
    observation = {"producer_id": previous_observation.get("producer_id", "fixture-collector"),
                   "run_id": previous_observation.get("run_id")}
    comment_id = verdict["round"]
    verdict["pr_comment_url"] = (
        f"{bundle['candidate']['host']}/{bundle['candidate']['repository']}/pull/"
        f"{bundle['candidate']['pr_number']}#issuecomment-{comment_id}"
    )
    provider_response = canonical({"comment_id": comment_id,
        "comment_url": verdict["pr_comment_url"], "comment_body": verdict_json,
        "pr_body": body_bytes})
    observation.update(source="host_observation", observed_at=verdict["created_at"],
        provider_kind="github", critic_artifact_binding=copy.deepcopy(artifact_binding),
        review_verdict_record_id=verdict["record_id"], review_verdict_sha256=verdict_sha256,
        repository_id=bundle["candidate"]["repository_id"],
        pr_number=bundle["candidate"]["pr_number"], comment_id=comment_id,
        comment_url=verdict["pr_comment_url"], comment_bytes=verdict_json,
        comment_sha256=sha256(verdict_json.encode("utf-8")),
        body_link=verdict["pr_body_link"], body_bytes=body_bytes,
        body_sha256=sha256(body_bytes.encode("utf-8")),
        provider_response_bytes=base64_bytes(provider_response),
        provider_response_sha256=sha256(provider_response),
        collector_receipt_sha256="0" * 64)
    verdict["posting_observation"] = observation
    posting_bytes = canonical(observation)
    receipt = {"critic_artifact_binding": copy.deepcopy(artifact_binding),
        "result_json": result_json, "completion_cycle_id": completion_snapshot["cycle_id"],
        "completion_tuple_sha256": completion_snapshot["tuple_sha256"],
        "completion_reviewer_set_sha256": completion_snapshot["reviewer_set_sha256"],
        "completion_snapshot_json": completion_snapshot_json,
        "completion_snapshot_sha256": fingerprint("review-completion", completion_snapshot),
        "review_verdict_json": verdict_json, "review_verdict_sha256": verdict_sha256,
        "posting_observation_json": base64_bytes(posting_bytes),
        "posting_observation_sha256": sha256(posting_bytes)}
    receipts = bundle.setdefault("review_round_receipts", [])
    receipts[:] = [item for item in receipts
                   if item["critic_artifact_binding"]["round"] != verdict["round"]]
    receipts.append(receipt)
    receipts.sort(key=lambda item: item["critic_artifact_binding"]["round"])
    uri = f"urn:awf:review-round-receipt:{verdict['round']}"
    bundle["evidence_registry"] = [entry for entry in bundle["evidence_registry"]
                                   if entry["uri"] != uri]
    bundle["evidence_registry"].append({"uri": uri,
        "sha256": review_round_receipt_sha256(receipt),
        "producer_id": verdict["producer_id"], "retained_until": "2030-01-01T00:00:00Z"})
    return receipt


def bind_production_posting_fixture(config, bundle):
    """Inject a trusted collector only into a test evaluator call."""
    collector_ids = config["merge_gate"]["production_posting_collector_ids"]
    if not collector_ids:
        raise AssertionError("the positive-path fixture needs a registered production collector")
    producer_id = collector_ids[0]
    collector_runs = [run for run in bundle["runs"] if run["role"] == "collector"]
    old_producers = {run["producer_id"] for run in collector_runs}
    for run in collector_runs:
        run["producer_id"] = producer_id
    for name in ("ci", "pr"):
        if bundle[name]["producer_id"] in old_producers:
            bundle[name]["producer_id"] = producer_id
    terminal = max(bundle["review_verdicts"], key=lambda item: item["round"])
    bind_review_round_receipt(bundle, terminal, bundle["critic"],
                              bundle["review_submission"]["completion_snapshot"])
    registration = {"provider_kind": "github", "implementation_sha256": "a" * 64,
        "release_sha256": "b" * 64,
        "repository_ids": [bundle["candidate"]["repository_id"]], "receipts": {}}
    used_collector_runs = set()
    collector_template = next(run for run in bundle["runs"] if run["role"] == "collector")
    for verdict in sorted(bundle.get("review_verdicts", []), key=lambda item: item["round"]):
        receipt = next(item for item in bundle["review_round_receipts"]
                       if item["critic_artifact_binding"]["round"] == verdict["round"])
        observation = verdict["posting_observation"]
        if observation["run_id"] in used_collector_runs:
            collector_run = copy.deepcopy(collector_template)
            collector_run.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
                                 context_id=str(uuid.uuid4()), producer_id=producer_id)
            bundle["runs"].append(collector_run)
            observation["run_id"] = collector_run["run_id"]
        used_collector_runs.add(observation["run_id"])
        observation["producer_id"] = producer_id
        basis = copy.deepcopy(observation)
        basis["collector_receipt_sha256"] = "0" * 64
        binding_sha256 = sha256(canonical(basis))
        collector_receipt = posting_collector_receipt_sha256(
            observation, binding_sha256, registration)
        observation["collector_receipt_sha256"] = collector_receipt
        registration["receipts"][observation["run_id"]] = collector_receipt
        posting_bytes = canonical(observation)
        receipt["posting_observation_json"] = base64_bytes(posting_bytes)
        receipt["posting_observation_sha256"] = sha256(posting_bytes)
        uri = f"urn:awf:review-round-receipt:{verdict['round']}"
        entry = next(item for item in bundle["evidence_registry"] if item["uri"] == uri)
        entry["sha256"] = review_round_receipt_sha256(receipt)
    for entry in bundle["evidence_registry"]:
        if entry["producer_id"] in old_producers:
            entry["producer_id"] = producer_id
    bundle["provenance_mode"] = "production_observation"
    return {producer_id: registration}


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")
        cls.config0 = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        cls.bundle0 = load(ROOT / ".agentic/examples/evidence-bundle.json")

    def setUp(self):
        self.config = copy.deepcopy(self.config0)
        self.bundle = copy.deepcopy(self.bundle0)

    def gate(self):
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        return evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)

    def prepare_test_gate(self):
        """Freeze a valid test-only collector registry before an adversarial edit."""
        bind_review_admission(self.bundle)
        return bind_production_posting_fixture(self.config, self.bundle)

    def gate_with_registry(self, registry):
        return evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)

    def verifier_run(self):
        """Register an independent verifier run (the party that authenticated the owner's comment)."""
        existing = [run for run in self.bundle["runs"] if run["role"] == "verifier"]
        if existing:
            return existing[0]
        run = copy.deepcopy(self.bundle["runs"][0])
        run.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), role="verifier", producer_id="fixture-verifier",
                   context_id=str(uuid.uuid4()), capabilities=["read_evidence", "verify_source_event"])
        self.bundle["runs"].append(run)
        return run

    def record(self, **values):
        base = self.bundle["critic"]
        run = self.verifier_run()
        if values.get("decision") in {"MERGE_WITH_NOTES", "PARK", "RESCOPE", "EXTEND_ONE_CYCLE"}:
            bind_review_admission(self.bundle)
            terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
            bind_review_round_receipt(
                self.bundle, terminal, self.bundle["critic"],
                self.bundle["review_submission"]["completion_snapshot"])
            receipt = next(item for item in self.bundle["review_round_receipts"]
                           if item["critic_artifact_binding"]["round"] == terminal["round"])
            values.setdefault("critic_artifact_binding",
                              copy.deepcopy(terminal["critic_artifact_binding"]))
            values.setdefault("review_verdict_record_id", terminal["record_id"])
            values.setdefault("review_verdict_sha256", receipt["review_verdict_sha256"])
        return {"schema_version": 3, "record_id": str(uuid.uuid4()), "created_at": NOW, "producer_id": run["producer_id"],
                "run_id": run["run_id"], "binding": copy.deepcopy(base["binding"]), "evidence": EVIDENCE, **values}

    def finding(self, fid="F1", severity="MAJOR", basis=None, status="OPEN", path="src/example.py", supersedes=None):
        return {"id": fid, "severity": severity, "summary": "Fixture finding " + fid, "evidence": EVIDENCE, "status": status,
                "resolution_evidence": [], "basis": basis, "supersedes_finding_id": supersedes, "path": path}

    def tier1_bundle(self):
        # Retarget the fixture candidate at a Tier-1-eligible path.
        for record in (self.bundle["worker"],):
            record["files_changed"] = ["tests/test_example.py"]
        self.bundle["pr"]["file_manifest"] = [{"path": "tests/test_example.py", "blob_sha": "1" * 40}]
        from agentic.canonical import fingerprint
        self.bundle["critic"]["coverage"].update(file_manifest_sha256=fingerprint("file-manifest", self.bundle["pr"]["file_manifest"]),
                                                 reviewed_paths=["tests/test_example.py"])
        self.bundle["contract"]["scope"]["expected_paths"] = ["tests/**"]
        self.bundle["contract"].update(risk_tier=1, tier_justification="Tests only.")
        self.rebind()

    def rebind(self):
        from agentic.gates import expected_binding
        from agentic.policy import policy_hash
        from agentic.review_tiers import classify
        self.bundle["contract"]["policy_hash"] = policy_hash(self.config, definition())
        paths = [item["path"] for item in self.bundle["pr"]["file_manifest"]]
        classification = classify(self.config, paths,
                                  risk_flags=[key for key, value in self.bundle["contract"].get("risk_flags", {}).items() if value])
        self.bundle["contract"]["risk_classification"] = copy.deepcopy(classification)
        self.bundle["tier_classification"] = copy.deepcopy(classification)
        binding = expected_binding(self.config, definition(), self.bundle)
        for key in ["dispatch", "worker", "critic", "ci", "pr"]:
            self.bundle[key]["binding"] = copy.deepcopy(binding)
        for run in self.bundle["runs"]:
            run["binding"] = copy.deepcopy(binding)
        for verdict in self.bundle.get("review_verdicts", []):
            verdict["binding"] = copy.deepcopy(binding)
            verdict["tier"] = classification["tier"]
        self.sync_critic_artifact()
        return binding

    def sync_critic_artifact(self):
        """Make an intentional critic-fixture edit a substantive retained artifact."""
        verdicts = self.bundle.get("review_verdicts", [])
        if not verdicts:
            return
        verdict = max(verdicts, key=lambda item: item["round"])
        critic = self.bundle["critic"]
        verdict["verdict"] = "PASS" if critic["verdict"] == "APPROVE" else "REQUEST_CHANGES"
        old_uri = f"urn:awf:critic-review:{verdict['critic_review']['record_id']}"
        verdict["critic_review"].update(
            record_id=critic["record_id"], run_id=critic["run_id"],
            round=verdict["round"], head_sha=verdict["head_sha"],
            verdict=critic["verdict"],
            findings_sha256=fingerprint("critic-findings", critic["findings"]),
        )
        uri = f"urn:awf:critic-review:{critic['record_id']}"
        verdict["evidence"] = [
            item for item in verdict["evidence"]
            if item != old_uri and item != uri
        ] + [uri]
        self.bundle["evidence_registry"] = [
            entry for entry in self.bundle["evidence_registry"]
            if entry["uri"] not in {old_uri, uri}
        ]
        self.bundle["evidence_registry"].append({
            "uri": uri,
            "sha256": critic_artifact_receipt_sha256(verdict),
            "producer_id": verdict["producer_id"],
            "retained_until": "2030-01-01T00:00:00Z",
        })


class TierTests(Fixture):
    def test_tier_computation_and_declaration(self):
        self.assertEqual((1, []), computed_tier(self.config, ["tests/test_a.py", "docs/notes.md"]))
        tier, reasons = computed_tier(self.config, ["tests/test_a.py", ".github/workflows/ci.yml"])
        self.assertEqual(3, tier)
        self.assertIn(".github/workflows/ci.yml: matches scope.protected_paths", reasons)
        self.assertEqual((2, ["src/example.py: outside execution.risk_tiers.tier1_eligible_paths"]), computed_tier(self.config, ["src/example.py"]))
        contract = copy.deepcopy(self.bundle["contract"])
        contract.update(risk_tier=1, tier_justification="tests only")
        with self.assertRaisesRegex(ValidationError, r"\.github/workflows/ci\.yml"):
            check_tier_declaration(self.config, contract, ["tests/a.py", ".github/workflows/ci.yml"])
        contract["risk_tier"] = 3
        self.assertEqual(3, check_tier_declaration(self.config, contract, ["tests/a.py", ".github/workflows/ci.yml"]))
        contract.update(risk_tier=1)
        contract["risk_flags"]["security"] = True
        with self.assertRaisesRegex(ValidationError, "risk_flags.security"):
            check_tier_declaration(self.config, contract, ["tests/a.py"])
        contract["risk_flags"]["security"] = False
        contract["tier_justification"] = " "
        with self.assertRaises(ValidationError):
            check_tier_declaration(self.config, contract, ["tests/a.py"])

    def test_gate_refuses_tier1_on_product_source_and_records_tier(self):
        self.bundle["contract"].update(risk_tier=1, tier_justification="wrong")
        self.rebind()
        with self.assertRaisesRegex(ValidationError, "src/example.py"):
            self.gate()
        self.bundle["contract"]["risk_tier"] = 2
        self.rebind()
        gate = self.gate()
        self.assertEqual((2, "FULL", "READY_FOR_OWNER_AUTHORIZATION"), (gate["risk_tier"], gate["closure_standard"], gate["conclusion"]))

    def test_installed_cli_validate_record_names_the_path(self):
        contract = copy.deepcopy(self.bundle["contract"])
        contract.update(risk_tier=1, tier_justification="tests only")
        contract["scope"]["expected_paths"] = ["tests/**", ".github/workflows/ci.yml"]
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            path = Path(folder) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            command = [sys.executable, "-B", "-I", str(runtime / ".agentic/scripts/workflow.py"), "validate-record", "ticket-contract", str(path)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(2, result.returncode)
            self.assertIn(".github/workflows/ci.yml", result.stderr)
            contract["risk_tier"] = 3
            path.write_text(json.dumps(contract), encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(3, json.loads(result.stdout)["risk_tier"])


class BasisAndDispositionTests(Fixture):
    def test_blocker_without_basis_is_refused_not_downgraded(self):
        with self.assertRaisesRegex(ValidationError, "requires basis"):
            validate_findings([self.finding("F1", "BLOCKER")], [], ["AC1", "AC2"])
        with self.assertRaisesRegex(ValidationError, "not in the contract"):
            validate_findings([self.finding("F1", "MAJOR", {"criterion_id": "AC9"})], [], ["AC1", "AC2"])
        validate_findings([self.finding("F1", "MAJOR", {"criterion_id": "AC1"}), self.finding("F2", "MINOR")], [], ["AC1"])
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[self.finding("F1", "BLOCKER")])
        with self.assertRaisesRegex(ValidationError, "basis"):
            self.contracts.validate("critic-review", self.bundle["critic"])

    def test_lineage_requires_supersedes_on_an_established_locus(self):
        prior = [self.finding("F1", "MAJOR", {"criterion_id": "AC1"}, status="RESOLVED")]
        prior[0]["resolution_evidence"] = EVIDENCE
        new = self.finding("F7", "MAJOR", {"criterion_id": "AC1"})
        with self.assertRaisesRegex(ValidationError, "supersedes_finding_id"):
            validate_findings(prior + [new], prior, ["AC1"])
        new["supersedes_finding_id"] = "F1"
        validate_findings(prior + [new], prior, ["AC1"])
        new["supersedes_finding_id"] = "F0"
        with self.assertRaisesRegex(ValidationError, "unknown finding"):
            validate_findings(prior + [new], prior, ["AC1"])

    def test_tier1_advisory_disposition_does_not_replace_terminal_pass(self):
        self.tier1_bundle()
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[self.finding("F1", "MAJOR", {"criterion_id": "AC1"}, path="tests/test_example.py")])
        self.sync_critic_artifact()
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])
        head = self.bundle["candidate"]["head_sha"]
        disposition = sign(self.record(finding_id="F1", decision="ACCEPT_RISK", rationale="Known flaky assertion; tracked.",
                                       head_sha=head, **signed()), "finding", head)
        self.contracts.validate("finding-disposition", disposition)
        self.bundle["finding_dispositions"] = [disposition]
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])
        self.assertTrue(any("ACCEPT_RISK" in item and "F1" in item for item in gate["residual_risks"]))
        self.assertEqual(["F1"], gate["accepted_findings"])
        # HEAD_CHANGED voids the disposition: a re-signed record for another head does not apply.
        stale = sign(self.record(finding_id="F1", decision="ACCEPT_RISK", rationale="old head", head_sha="9" * 40, **signed()), "finding", "9" * 40)
        self.bundle["finding_dispositions"] = [stale]
        self.assertEqual("FAIL", self.gate()["gates"]["critic_current_tuple"]["result"])  # void for this head
        duplicate = self.record(finding_id="F1", decision="REQUIRE_FIX", rationale="dup", head_sha=head, **signed())
        duplicate["owner_source"]["comment_id"] = 13
        self.bundle["finding_dispositions"] = [disposition, sign(duplicate, "finding", head)]
        with self.assertRaisesRegex(ValidationError, "more than one disposition"):
            self.gate()

    def test_forged_or_untrusted_dispositions_are_refused(self):
        self.tier1_bundle()
        head = self.bundle["candidate"]["head_sha"]
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[self.finding("F1", "MAJOR", {"criterion_id": "AC1"}, path="tests/test_example.py")])
        self.sync_critic_artifact()
        good = sign(self.record(finding_id="F1", decision="ACCEPT_RISK", rationale="ok", head_sha=head, **signed()), "finding", head)
        forged = copy.deepcopy(good)
        forged["owner_source"].update(raw_body="lgtm", raw_body_sha256="f" * 64)
        self.bundle["finding_dispositions"] = [forged]
        with self.assertRaisesRegex(ValidationError, "digest mismatch"):
            self.gate()
        forged = copy.deepcopy(good)
        forged["owner_source"]["raw_body_sha256"] = __import__("hashlib").sha256(b"lgtm").hexdigest()
        forged["owner_source"]["raw_body"] = "lgtm"
        self.bundle["finding_dispositions"] = [forged]
        with self.assertRaisesRegex(ValidationError, "grammar"):
            self.gate()
        untrusted = sign(self.record(finding_id="F1", decision="ACCEPT_RISK", rationale="ok", head_sha=head, **signed()), "finding", head, actor_id=424242)
        self.bundle["finding_dispositions"] = [untrusted]
        with self.assertRaisesRegex(ValidationError, "untrusted numeric owner"):
            self.gate()
        # Produced by the worker's own run: refused as an unregistered/wrong-role producer.
        agent_made = copy.deepcopy(good)
        worker_run = self.bundle["runs"][1]
        agent_made.update(producer_id=worker_run["producer_id"], run_id=worker_run["run_id"])
        agent_made = sign(agent_made, "finding", head)
        self.bundle["finding_dispositions"] = [agent_made]
        with self.assertRaisesRegex(ValidationError, "registered verifier run"):
            self.gate()
        # Tampered decision after signing: text and fields disagree.
        tampered = copy.deepcopy(good)
        tampered["decision"] = "NOT_A_DEFECT"
        self.bundle["finding_dispositions"] = [tampered]
        with self.assertRaisesRegex(ValidationError, "raw owner text"):
            self.gate()
        # A verifier run under the worker's producer in a fresh context is not independent.
        verifier = self.verifier_run()
        same_producer = copy.deepcopy(verifier)
        same_producer.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()), producer_id=worker_run["producer_id"])
        self.bundle["runs"].append(same_producer)
        record = copy.deepcopy(good)
        record.update(producer_id=same_producer["producer_id"], run_id=same_producer["run_id"])
        self.bundle["finding_dispositions"] = [sign(record, "finding", head)]
        with self.assertRaisesRegex(ValidationError, "producer"):
            self.gate()
        # A child run of the critic is not independent either.
        child = copy.deepcopy(verifier)
        child.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()), producer_id="spawned-verifier",
                     parent_run_id=self.bundle["runs"][2]["run_id"])
        self.bundle["runs"].append(child)
        record = copy.deepcopy(good)
        record.update(producer_id="spawned-verifier", run_id=child["run_id"])
        self.bundle["finding_dispositions"] = [sign(record, "finding", head)]
        with self.assertRaisesRegex(ValidationError, "descends"):
            self.gate()
        self.bundle["runs"] = [run for run in self.bundle["runs"] if run["run_id"] not in {same_producer["run_id"], child["run_id"]}]
        # An edited comment is invalid.
        edited = copy.deepcopy(good)
        edited["owner_source"]["updated_at"] = "2026-09-09T12:30:00Z"
        self.bundle["finding_dispositions"] = [edited]
        with self.assertRaisesRegex(ValidationError, "edited"):
            self.gate()
        self.bundle["finding_dispositions"] = [good]
        self.assertEqual("FAIL", self.gate()["gates"]["critic_current_tuple"]["result"])

    def test_tier1_needs_configured_governance(self):
        del self.config["execution"]["risk_tiers"]
        tier, reasons = computed_tier(self.config, ["tests/test_a.py"])
        self.assertEqual(2, tier)
        self.assertTrue(any("execution.risk_tiers" in reason for reason in reasons))

    def test_boundary_findings_block_in_every_tier_regardless_of_disposition(self):
        self.tier1_bundle()
        boundary = self.finding("F2", "MAJOR", {"boundary_code": "CREDENTIAL_EXPOSURE"}, path="tests/test_example.py")
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[boundary])
        self.assertEqual("FAIL", self.gate()["gates"]["critic_current_tuple"]["result"])
        head = self.bundle["candidate"]["head_sha"]
        self.bundle["finding_dispositions"] = [sign(self.record(finding_id="F2", decision="ACCEPT_RISK", rationale="dummy token",
                                                                head_sha=head, **signed()), "finding", head)]
        with self.assertRaisesRegex(ValidationError, "mandatory boundary"):
            self.gate()
        # A boundary finding blocks at any severity.
        self.bundle["finding_dispositions"] = []
        boundary["severity"] = "MINOR"
        self.assertEqual("FAIL", self.gate()["gates"]["critic_current_tuple"]["result"])

    def test_tier2_request_changes_fails_without_disposition(self):
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[self.finding("F1", "MAJOR", {"criterion_id": "AC1"})])
        gate = self.gate()
        self.assertEqual(("FAIL", "NOT_READY"), (gate["gates"]["critic_current_tuple"]["result"], gate["conclusion"]))

    def test_project_instructions_cannot_weaken_a_boundary(self):
        self.assertEqual([], project_instructions_errors("Tier 1 changes get one critic; findings advise the owner.\n"))
        problems = project_instructions_errors("Findings on protected paths are advisory in this project.\n")
        self.assertEqual(1, len(problems))
        from agentic.configuration import inspect_config
        report = inspect_config(self.config, definition(), self.contracts,
                                project_instructions="findings on protected paths are advisory")
        self.assertEqual("REJECTED", report["status"])
        self.assertIn(BOUNDARY_SENTENCE, report["unresolved"][0]["reason"])
        self.assertEqual("ACCEPTED", inspect_config(self.config, definition(), self.contracts, project_instructions="# Notes\n")["status"])


class CapTests(Fixture):
    def open_findings(self):
        return [self.finding("F7", "MAJOR", {"criterion_id": "AC2"}), self.finding("F10", "MAJOR", {"criterion_id": "AC1"}, status="DISPUTED")]

    def disposition(self, decision, **extra):
        value = self.record(decision=decision, open_finding_ids=["F10", "F7"], notes="", cycles=3, cap_extensions=0,
                            **{"successor_ticket": None, **extra}, **signed())
        sign(value, "cap", self.bundle["candidate"]["head_sha"])
        self.contracts.validate("review-cap-disposition", value)
        return value

    def test_cap_state_and_lifecycle_transitions(self):
        self.assertIn("REVIEW_CAP_REACHED", STATES)
        self.assertIn("REVIEW_CAP_REACHED", RESUME)
        self.assertTrue(cap_status(self.config, 3, 0)["cap_reached"])
        self.assertFalse(cap_status(self.config, 2, 0)["cap_reached"])
        with self.assertRaisesRegex(ValidationError, "amendment_cycles_available"):
            transition("CHANGES_REQUESTED", "AMENDMENT_ACCEPTED", {k: True for k in ["dispatch_permitted", "lease_current", "budget_reserved", "ownership_current"]})
        self.assertEqual("REVIEW_CAP_REACHED", transition("CHANGES_REQUESTED", "CAP_REACHED", {"amendment_cycles_exhausted": True, "open_findings_presented": True}))
        with self.assertRaises(ValidationError):
            transition("REVIEW_CAP_REACHED", "AMENDMENT_ACCEPTED", {})
        plans = {"MERGE_WITH_NOTES": "FINAL_REVIEW", "PARK": "BLOCKED", "RESCOPE": "SUPERSEDED", "EXTEND_ONE_CYCLE": "CHANGES_REQUESTED"}
        for decision, target in plans.items():
            with self.subTest(decision=decision):
                extra = {"successor_ticket": "EX-99"} if decision == "RESCOPE" else {}
                plan = cap_disposition_plan(self.config, self.disposition(decision, **extra), 3, 0, self.open_findings())
                self.assertEqual(target, transition("REVIEW_CAP_REACHED", plan["event"], plan["facts"]))
        plan = cap_disposition_plan(self.config, self.disposition("MERGE_WITH_NOTES"), 3, 0, self.open_findings())
        self.assertEqual(2, len(plan["residual_risks"]))
        plan = cap_disposition_plan(self.config, self.disposition("PARK"), 3, 0, self.open_findings())
        self.assertEqual({"reason_code": "REVIEW_CAP_PARKED", "resume_state": "CHANGES_REQUESTED"}, plan["blocker"])

    def test_ac2_run_and_review_round_caps_are_independent(self):
        self.config["execution"]["max_agent_runs_per_ticket"] = 16
        self.config["execution"]["model_routing"]["budgets"]["max_runs_per_ticket"] = 16
        self.assertTrue(cap_status(self.config, 3, 0)["cap_reached"])
        self.config["execution"]["max_agent_runs_per_ticket"] = 1
        self.config["execution"]["model_routing"]["budgets"]["max_runs_per_ticket"] = 1
        self.assertFalse(cap_status(self.config, 2, 0)["cap_reached"])
        self.assertTrue(cap_status(self.config, 3, 0)["cap_reached"])

    def test_third_extension_refused_by_governance_path(self):
        self.assertEqual(1, cap_disposition_plan(self.config, self.disposition("EXTEND_ONE_CYCLE"), 3, 0, self.open_findings())["cap_extensions"])
        with self.assertRaisesRegex(ValidationError, r"\$\.execution\.max_cap_extensions"):
            cap_disposition_plan(self.config, self.disposition("EXTEND_ONE_CYCLE"), 5, 2, self.open_findings())
        with self.assertRaisesRegex(ValidationError, "before the amendment cap"):
            cap_disposition_plan(self.config, self.disposition("EXTEND_ONE_CYCLE"), 1, 0, self.open_findings())
        with self.assertRaisesRegex(ValidationError, "exactly"):
            cap_disposition_plan(self.config, self.disposition("PARK"), 3, 0, self.open_findings()[:1])
        with self.assertRaisesRegex(ValidationError, "boundary"):
            findings = self.open_findings()
            findings[0]["basis"] = {"boundary_code": "PROTECTED_PATH"}
            cap_disposition_plan(self.config, self.disposition("MERGE_WITH_NOTES"), 3, 0, findings)

    def test_schema_caps_extensions_and_signature_is_required(self):
        self.config["execution"]["max_cap_extensions"] = 4
        from agentic.policy import validate_config
        with self.assertRaises(ValidationError):
            validate_config(self.config, definition(), self.contracts)
        value = self.disposition("PARK")
        del value["owner_source"]
        with self.assertRaises(ValidationError):
            self.contracts.validate("review-cap-disposition", value)

    def test_merge_with_notes_reaches_a_ready_gate_and_carries_notes(self):
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[self.finding("F7", "MAJOR", {"criterion_id": "AC2"})])
        self.sync_critic_artifact()
        self.assertEqual("NOT_READY", self.gate()["conclusion"])
        cap = self.record(decision="MERGE_WITH_NOTES", open_finding_ids=["F7"], notes="owner accepts", cycles=3, cap_extensions=0,
                          successor_ticket=None, **signed())
        self.bundle["cap_disposition"] = sign(cap, "cap", self.bundle["candidate"]["head_sha"])
        gate = self.gate()
        self.assertEqual(("PASS", "READY_FOR_OWNER_AUTHORIZATION", ["F7"]), (gate["gates"]["critic_current_tuple"]["result"], gate["conclusion"], gate["accepted_findings"]))
        self.assertTrue(any("Merged with notes" in item and "F7" in item for item in gate["residual_risks"]))
        unauthenticated = copy.deepcopy(self.bundle["cap_disposition"])
        unauthenticated["critic_artifact_binding"]["result_sha256"] = "f" * 64
        self.bundle["cap_disposition"] = unauthenticated
        with self.assertRaisesRegex(ValidationError, "does not authenticate"):
            self.gate()
        self.bundle["cap_disposition"] = cap
        from agentic.authorization import make_request
        from agentic.interaction import gate_handoff
        request = make_request(gate, self.contracts, NOW)
        report = gate_handoff(gate, request, self.config, self.contracts, NOW)
        self.assertIn("F7", report["summary"])
        early = copy.deepcopy(self.bundle["cap_disposition"])
        early["cycles"] = 1
        self.bundle["cap_disposition"] = sign(early, "cap", self.bundle["candidate"]["head_sha"])
        with self.assertRaisesRegex(ValidationError, "precedes the cap"):
            self.gate()
        reused = sign(self.record(finding_id="F7", decision="REQUIRE_FIX", rationale="same comment", head_sha=self.bundle["candidate"]["head_sha"], **signed()),
                      "finding", self.bundle["candidate"]["head_sha"])
        self.bundle["cap_disposition"] = sign(cap, "cap", self.bundle["candidate"]["head_sha"])
        self.bundle["finding_dispositions"] = [reused]  # same comment_id 12 signs both records
        with self.assertRaisesRegex(ValidationError, "already signs"):
            self.gate()
        self.bundle["finding_dispositions"] = []
        wrong = copy.deepcopy(self.bundle["cap_disposition"])
        wrong["open_finding_ids"] = ["ZZZ"]
        self.bundle["cap_disposition"] = sign(wrong, "cap", self.bundle["candidate"]["head_sha"])
        with self.assertRaisesRegex(ValidationError, "open serious findings"):
            self.gate()
        boundary = self.finding("F8", "MAJOR", {"boundary_code": "SCOPE_ESCAPE"})
        self.bundle["critic"]["findings"].append(boundary)
        with_boundary = self.record(decision="MERGE_WITH_NOTES",
            open_finding_ids=["F7", "F8"], notes="owner accepts", cycles=3,
            cap_extensions=0, successor_ticket=None, **signed())
        self.bundle["cap_disposition"] = sign(with_boundary, "cap", self.bundle["candidate"]["head_sha"])
        with self.assertRaisesRegex(ValidationError, "boundary"):
            self.gate()

    def test_evidence_only_amendments_do_not_consume_a_cycle(self):
        contract = self.bundle["contract"]
        self.assertTrue(evidence_only(contract, ["evidence/run.json", "docs/notes.md"]))
        self.assertFalse(evidence_only(contract, ["evidence/run.json", "src/example.py"]))
        self.assertFalse(evidence_only(contract, []))

    def test_gate_rejects_unbound_verdict_posting_urls(self):
        registry = self.prepare_test_gate()
        verdict = self.bundle["review_verdicts"][0]
        verdict["pr_comment_url"] = "https://example.invalid/unrelated/comment"
        verdict["pr_body_link"] = "https://example.invalid/unrelated/body"
        self.assertEqual("FAIL", self.gate_with_registry(registry)["gates"]["verdict_posting"]["result"])

    def test_gate_rejects_fabricated_candidate_prefixed_posting(self):
        registry = self.prepare_test_gate()
        verdict = self.bundle["review_verdicts"][0]
        verdict["pr_comment_url"] = "https://github.com/fixture/example/pull/7#fabricated-comment"
        verdict["posting_observation"]["comment_url"] = verdict["pr_comment_url"]
        verdict["posting_observation"]["comment_sha256"] = "f" * 64
        self.assertEqual("FAIL", self.gate_with_registry(registry)["gates"]["verdict_posting"]["result"])

    def test_awf16_r1_003_gate_rejects_posting_observation_without_registered_host_producer(self):
        """AWF16-R1-003: posting evidence must be independently registered and hashed."""
        registry = self.prepare_test_gate()
        verdict = self.bundle["review_verdicts"][0]
        worker = next(run for run in self.bundle["runs"] if run["role"] == "worker")
        verdict["posting_observation"]["producer_id"] = worker["producer_id"]
        self.assertEqual("FAIL", self.gate_with_registry(registry)["gates"]["verdict_posting"]["result"])

    def test_awf16_r1_009_real_gate_and_schema_regressions(self):
        """AWF16-R1-009: exercise the real gate with canonical boundary and posting records."""
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])
        boundary = self.finding("AWF16-R1-009-boundary", "MINOR", {"boundary_code": "SCOPE_ESCAPE"})
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[boundary])
        self.assertEqual("FAIL", self.gate()["gates"]["critic_current_tuple"]["result"])

    def test_awf16_r2c_002_each_consumed_round_has_registered_critic_artifact(self):
        """AWF16-R2C-002: a terminal run cannot reuse the round-one critic artifact."""
        registry = self.prepare_test_gate()
        verdict = self.bundle["review_verdicts"][0]
        verdict["critic_review"]["record_id"] = next(
            run["record_id"] for run in self.bundle["runs"]
            if run["run_id"] == verdict["run_id"]
        )
        self.assertEqual("NOT_READY", self.gate_with_registry(registry)["conclusion"])

    def test_awf16_r2c_004_legacy_security_is_rejected_without_mutation(self):
        """AWF16-R2C-004: missing durable security evidence fails closed."""
        self.bundle["contract"]["risk_flags"]["security"] = True
        from agentic.review_tiers import classify
        observed = classify(self.config, ["src/example.py"], risk_flags=["security"])
        self.bundle["contract"]["risk_classification"] = copy.deepcopy(observed)
        self.bundle["tier_classification"] = copy.deepcopy(observed)
        self.rebind()
        self.bundle["contract"]["risk_classification"]["risk_flags"] = []
        self.bundle["tier_classification"]["risk_flags"] = []
        from agentic.gates import expected_binding
        binding = expected_binding(self.config, definition(), self.bundle)
        for key in ("dispatch", "worker", "critic", "ci", "pr"):
            self.bundle[key]["binding"] = copy.deepcopy(binding)
        for run in self.bundle["runs"]:
            run["binding"] = copy.deepcopy(binding)
        for verdict in self.bundle["review_verdicts"]:
            verdict["binding"] = copy.deepcopy(binding)
        bind_production_posting_fixture(self.config, self.bundle)
        bind_review_admission(self.bundle)
        before = copy.deepcopy(self.bundle)
        with self.assertRaisesRegex(ValidationError, "risk_classification"):
            evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual(before, self.bundle)

    def test_awf16_195_r3_003_final_gate_rejects_configured_cap_above_policy(self):
        """AWF16-195-R3-003: the mandatory three-round ceiling remains authoritative."""
        from agentic.review_tiers import round_cap
        with self.assertRaisesRegex(ValidationError, "mandatory"):
            round_cap(2, {"execution": {"risk_tiers": {"tier2_review": {"max_rounds": 4}}}})

    def test_awf16_195_r3_004_historical_tier_is_not_terminal_tier_requirement(self):
        """AWF16-195-R3-004: retained pre-escalation receipts are historical, not terminal."""
        verdict = self.bundle["review_verdicts"][0]
        verdict["round"] = 1
        verdict["tier"] = 2
        self.assertEqual(2, verdict["tier"])
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])

    def test_gate_rejects_terminal_critic_sharing_worker_context(self):
        verdict = self.bundle["review_verdicts"][0]
        critic_run = next(run for run in self.bundle["runs"] if run["run_id"] == verdict["run_id"])
        worker_run = next(run for run in self.bundle["runs"] if run["run_id"] == self.bundle["worker"]["run_id"])
        critic_run["context_id"] = worker_run["context_id"]
        with self.assertRaisesRegex(ValidationError, "not independent"):
            self.gate()

    def test_configured_tier_three_cap_is_used_by_round_validation(self):
        from agentic.review_tiers import validate_round
        with self.assertRaisesRegex(ValidationError, "cap is 1"):
            validate_round(3, 2, config={"execution": {"risk_tiers": {"tier3_review": {"max_rounds": 1}}}})

    def test_awf16_r3_001_gate_preserves_old_head_round_and_requires_distinct_critic_runs(self):
        """AWF16-R3-001: retain an old-head verdict while the terminal round binds the new head."""
        first = self.bundle["review_verdicts"][0]
        first_artifact = copy.deepcopy(self.bundle["critic"])
        first["head_sha"] = "a" * 40
        first["created_at"] = "2020-01-01T00:00:00Z"
        first["candidate_binding"]["head_sha"] = first["head_sha"]
        first["critic_review"]["head_sha"] = first["head_sha"]
        first_run = next(run for run in self.bundle["runs"] if run["run_id"] == first["run_id"])
        first_run["created_at"] = "2020-01-01T00:00:00Z"
        second_run = copy.deepcopy(next(run for run in self.bundle["runs"]
                                        if run["run_id"] == first["run_id"]))
        second_run.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
                          producer_id="fixture-critic-2", context_id=str(uuid.uuid4()), created_at=NOW)
        self.bundle["runs"].append(second_run)
        self.bundle["critic"].update(run_id=second_run["run_id"], producer_id=second_run["producer_id"], created_at=NOW)
        second = copy.deepcopy(first)
        second.update(record_id=str(uuid.uuid4()), run_id=second_run["run_id"],
                      producer_id=second_run["producer_id"], round=2, created_at=NOW,
                      head_sha=self.bundle["candidate"]["head_sha"])
        second["reviewer_id"] = second_run["producer_id"]
        second["pr_comment_url"] = "https://github.com/fixture/example/pull/7#issuecomment-2"
        second["candidate_binding"]["head_sha"] = second["head_sha"]
        second["posting_observation"] = copy.deepcopy(first["posting_observation"])
        second["critic_review"] = copy.deepcopy(first["critic_review"])
        second["critic_review"].update(record_id=second_run["record_id"], run_id=second_run["run_id"], round=2, head_sha=self.bundle["candidate"]["head_sha"])
        self.bundle["review_verdicts"].append(second)
        self.bundle["review_round_receipts"] = []
        bind_review_round_receipt(self.bundle, first, first_artifact)
        bind_review_round_receipt(self.bundle, second, self.bundle["critic"])
        registry = self.prepare_test_gate()
        self.assertEqual("PASS", self.gate_with_registry(registry)["gates"]["verdict_posting"]["result"])
        self.bundle["review_verdicts"][1]["run_id"] = first["run_id"]
        self.bundle["review_verdicts"][1]["producer_id"] = first["producer_id"]
        self.bundle["review_verdicts"][1]["reviewer_id"] = first["reviewer_id"]
        self.bundle["review_verdicts"][1]["critic_review"]["run_id"] = first["run_id"]
        self.bundle["review_verdicts"][1]["critic_review"]["record_id"] = first["critic_review"]["record_id"]
        with self.assertRaisesRegex(ValidationError, "distinct independent critic"):
            self.gate_with_registry(registry)


class ClosureAndParityTests(Fixture):
    def test_closure_standard_is_required_from_both_parties(self):
        self.bundle["contract"]["closure_standard"] = {"kind": "DECLARED_LIMITATIONS", "evidence_required": ["manifest"],
            "accepted_limitations": [{"id": "L1", "text": "Dynamic dispatch sites are declared, not resolved.", "attribution": "owner 2026-09-18"}]}
        self.rebind()
        self.assertEqual("PASS", self.gate()["gates"]["acceptance_criteria"]["result"])
        self.bundle["worker"]["closure"]["result"] = "NOT_MET"
        self.assertEqual("FAIL", self.gate()["gates"]["acceptance_criteria"]["result"])
        self.bundle["worker"]["closure"]["result"] = "MET"
        self.bundle["critic"]["closure"]["result"] = "NOT_MET"
        with self.assertRaisesRegex(ValidationError, "closure"):
            self.gate()  # an APPROVE verdict cannot carry NOT_MET closure
        self.bundle["critic"]["closure"]["result"] = "MET"
        self.bundle["contract"]["closure_standard"]["accepted_limitations"] = []
        with self.assertRaises(ValidationError):
            self.contracts.validate("ticket-contract", self.bundle["contract"])

    def test_closure_change_after_review_is_a_contract_change(self):
        original = self.bundle["contract"]["closure_standard"]["kind"]
        self.bundle["contract"]["closure_standard"]["kind"] = "DECLARED_LIMITATIONS"
        self.bundle["contract"]["closure_standard"]["accepted_limitations"] = [{"id": "L1", "text": "x", "attribution": "owner"}]
        with self.assertRaisesRegex(ValidationError, "contract_hash|binding"):
            self.gate()  # records still bind the old contract hash
        self.assertEqual("BLOCKED", transition("FINAL_REVIEW", "REQUIREMENTS_CHANGED",
                                              {"external_event_recorded": True, "blocker_recorded": True, "prior_evidence_invalidated": True}))
        self.bundle["contract"]["closure_standard"]["kind"] = original

    def test_unevaluable_files_and_undeclared_skips_fail_acceptance(self):
        command = self.bundle["worker"]["validation"][0]
        command["unevaluable_files"] = ["tests/test_broken.py"]
        self.assertEqual("FAIL", self.gate()["gates"]["acceptance_criteria"]["result"])
        command["unevaluable_files"] = []
        command.update(tests_discovered=5, tests_executed=3, declared_skips=[])
        self.assertEqual("FAIL", self.gate()["gates"]["acceptance_criteria"]["result"])
        command["declared_skips"] = [{"id": "test_symlink", "reason_code": "PLATFORM_PRIVILEGE"}, {"id": "test_licensed_tool", "reason_code": "LICENCE_ABSENT"}]
        self.bundle["ci"]["checks"][0]["tests_executed"] = 3
        gate = self.gate()
        self.assertEqual(("PASS", "PASS"), (gate["gates"]["acceptance_criteria"]["result"], gate["gates"]["local_ci_parity"]["result"]))
        command["declared_skips"][0]["reason_code"] = "expected skip"
        with self.assertRaises(ValidationError):
            self.contracts.validate("worker-result", self.bundle["worker"])

    def test_worker_commit_route_is_optional_and_enum_bounded(self):
        worker = copy.deepcopy(self.bundle["worker"])
        worker.pop("commit_route", None)
        self.contracts.validate("worker-result", worker)
        worker["commit_route"] = "PUBLISHER"
        self.contracts.validate("worker-result", worker)
        for required in ("tested_tree", "changes"):
            incomplete = copy.deepcopy(worker)
            incomplete.pop(required)
            with self.subTest(required=required), self.assertRaises(ValidationError):
                self.contracts.validate("worker-result", incomplete)
        worker["commit_route"] = "UNTRUSTED"
        with self.assertRaises(ValidationError):
            self.contracts.validate("worker-result", worker)

    def test_local_ci_parity_requires_agreement_or_declared_distinction(self):
        self.bundle["worker"]["validation"][0].update(tests_discovered=401, tests_executed=401)
        self.bundle["ci"]["checks"][0]["tests_executed"] = 404
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["local_ci_parity"]["result"])
        self.assertTrue(any("401" in item and "404" in item for item in gate["residual_risks"]))
        self.bundle["contract"]["validation"]["platform_distinction"] = {"reason": "py.exe launcher path differs on Windows", "regression_test_id": "test_launcher_posix"}
        self.rebind()
        self.assertEqual("PASS", self.gate()["gates"]["local_ci_parity"]["result"])

    def test_history_verifying_check_needs_full_checkout(self):
        self.config["validation"]["required_ci_checks"][0]["verifies_history"] = True
        self.rebind()
        self.assertEqual("PASS", self.gate()["gates"]["provenance"]["result"])
        self.bundle["ci"]["checks"][0]["checkout_depth"] = "shallow"
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["provenance"]["result"])
        self.assertTrue(any("full-history" in item for item in gate["residual_risks"]))

    def test_overlapping_exclusive_resource_runs_fail_provenance(self):
        worker, critic = self.bundle["runs"][1], self.bundle["runs"][2]
        worker["resources_held"] = [{"name": "licensed_compute_kernel", "slots": 1, "from": "2026-09-09T11:00:00Z", "until": "2026-09-09T11:40:00Z"}]
        critic["resources_held"] = [{"name": "licensed_compute_kernel", "slots": 1, "from": "2026-09-09T11:30:00Z", "until": "2026-09-09T11:50:00Z"}]
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["provenance"]["result"])
        critic["resources_held"][0].update({"from": "2026-09-09T11:40:00Z", "until": "2026-09-09T11:50:00Z"})
        self.assertEqual("PASS", self.gate()["gates"]["provenance"]["result"])


class DigestTests(Fixture):
    def state(self):
        return {"ticket": "EX-1", "awf_state": "MERGED", "pr_number": 7, "head_sha": "b" * 40, "base_sha": "c" * 40, "tree_sha": "e" * 40, "risk_tier": 2}

    def test_digest_shape_is_fixed_and_footer_generated(self):
        bind_review_admission(self.bundle)
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        body = render(self.state(), audience="jira", gate=gate, findings=[self.finding("F1", "MAJOR", {"criterion_id": "AC1"})],
                      validation=self.bundle["worker"]["validation"], ci=self.bundle["ci"]["checks"], reviewer={"engine": "codex", "run_id": "r-1"})
        expected = ROOT / ".agentic/examples/digest-jira.md"
        self.assertEqual(expected.read_text(encoding="utf-8"), body)
        self.assertTrue(body.rstrip().endswith(footer()))
        self.assertEqual(1, body.count("Authority not claimed"))
        again = render(self.state(), audience="jira", gate=gate, findings=[self.finding("F1", "MAJOR", {"criterion_id": "AC1"})],
                       validation=self.bundle["worker"]["validation"], ci=self.bundle["ci"]["checks"], reviewer={"engine": "codex", "run_id": "r-1"})
        self.assertEqual(digest_sha256(body), digest_sha256(again))

    def test_evidence_comment_event_needs_digest_hash_and_is_not_a_transition(self):
        base = load(ROOT / ".agentic/templates/controller-event.json")["record"]
        base.update(event_id=str(uuid.uuid4()), project_id=self.config["project"]["id"], sequence=1, timestamp=NOW, actor="controller",
                    correlation_id=str(uuid.uuid4()), payload_hash="a" * 64, previous_hash="0" * 64, event_hash="b" * 64, evidence=EVIDENCE,
                    ticket="EX-1", previous_state=None, state=None, candidate=None, external_event_id=None, causation_id=None, digest_sha256=None)
        body = render(self.state(), audience="pr")
        event = evidence_comment_event(body, base)
        self.contracts.validate("controller-event", event)
        self.assertEqual(digest_sha256(body), event["digest_sha256"])
        event["digest_sha256"] = None
        with self.assertRaises(ValidationError):
            self.contracts.validate("controller-event", event)
        transition_event = {**base, "event_type": "lifecycle_transition", "state": "IN_PROGRESS", "ticket": "EX-1"}
        self.contracts.validate("controller-event", transition_event)
        with self.assertRaises(ValidationError):
            self.contracts.validate("controller-event", {**base, "event_type": "FREE_TEXT"})

    def test_prose_cap(self):
        check_prose("Closeout posted; see digest.")
        with self.assertRaisesRegex(ValidationError, str(PROSE_WORD_CAP)):
            check_prose("\u200b".join(["word"] * (PROSE_WORD_CAP + 1)))
        with self.assertRaisesRegex(ValidationError, "subset"):
            render({**self.state(), "authorities": ["everything"]})
        with self.assertRaisesRegex(ValidationError, str(PROSE_WORD_CAP)):
            check_prose("word " * (PROSE_WORD_CAP + 1))
        with self.assertRaisesRegex(ValidationError, "footer"):
            check_prose("No merge, no release, no activation was performed by this comment.")


if __name__ == "__main__":
    unittest.main()


class ReviewPolicyPilotTests(unittest.TestCase):
    """The prepared review-policy suite grades strictly; a wrong decision fails its case."""
    def test_suite_prepares_and_grades_strictly(self):
        sys.path.insert(0, str(ROOT / ".agentic/scripts"))
        import benchmark_native as benchmark
        packet, rubric_sha = benchmark.prepare(ROOT, suite="review-policy")
        self.assertEqual([f"NATIVE-{n}" for n in range(46, 52)], [case["case_id"] for case in packet["cases"]])
        rubric_raw = benchmark.read(ROOT / ".agentic/benchmarks/native/review-policy-rubric.json")
        rubric = benchmark.parse(rubric_raw)
        targets = {row["case_id"]: row for row in rubric["cases"]}
        packet_raw = benchmark.encoded(packet)
        results = []
        for case in packet["cases"]:
            target = targets[case["case_id"]]
            results.append({"case_id": case["case_id"], "case_sha256": case["case_sha256"], "prompt_sha256": case["prompt_sha256"],
                            "execution": {"requested_model": "fixture", "actual_model": None, "requested_effort": "high", "actual_effort": None, "host_identity": None},
                            "decision_codes": list(target["required_codes"]), "evidence_refs": list(target["required_evidence_refs"]),
                            "rationale": "synthetic answer copied from the public rubric; not a model observation", "observed_host_actions": []})
        observation = {"schema_version": 2, "benchmark_id": packet["benchmark_id"], "packet_sha256": benchmark.sha(packet_raw), "results": results}
        report = benchmark.grade(packet_raw, benchmark.encoded(observation), rubric_raw, benchmark.sha(packet_raw), rubric_sha)
        self.assertEqual(("PASS", 6), (report["status"], report["passed_cases"]))
        results[0]["decision_codes"] = ["escalate_within_policy", "block_readiness"]
        observation["results"] = results
        report = benchmark.grade(packet_raw, benchmark.encoded(observation), rubric_raw, benchmark.sha(packet_raw), rubric_sha)
        self.assertEqual(("FAIL", 5), (report["status"], report["passed_cases"]))
        self.assertIn("escalate_within_policy", report["cases"][0]["unexpected_decisions"])

    def test_dispatch_must_copy_contract_resources(self):
        fixture = Fixture("setUp")
        fixture.setUpClass()
        fixture.setUp()
        fixture.bundle["dispatch"]["required_resources"] = ["licensed_analysis_tool"]
        with self.assertRaisesRegex(ValidationError, "required_resources"):
            fixture.gate()
