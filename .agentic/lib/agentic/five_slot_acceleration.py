"""Fail-closed evidence contract for the AWF five-slot acceleration topology.

The module does not launch agents, run tests, write Jira, publish a PR, or grant
execution authority.  It validates host-collected evidence before a controller
may begin the single expensive full-suite cycle.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from . import ValidationError
from .canonical import canonical, fingerprint, loads, sha256


FORMAT = "awf-five-slot-acceleration-plan-1"
INVENTORY_FORMAT = "awf-adversarial-regression-inventory-1"
GATE_NAME = "pre-controller-adversarial-regression-gate"
WORKER_STREAMS = ("A", "B", "C")
DEFAULT_SLOT_COUNT = 5
ADVERSARIAL_CATEGORIES = (
    "race",
    "alias",
    "replacement-object",
    "provider-identity",
    "receipt-replay",
)
ROLE_STATES = {"RUNNING", "PAUSED", "BLOCKED"}
ACTIVE_LIFECYCLE = {
    "ACTIVE": "In Progress",
    "PAUSED": "In Progress",
    "BLOCKED": "In Progress",
    "PR_READY": "In Review",
}
INACTIVE_LIFECYCLE = {"OPEN": "Open", "ON_HOLD": "On Hold"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SHA = re.compile(r"[0-9a-f]{40,64}")
SHA256 = re.compile(r"[0-9a-f]{64}")
TEXT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
RELATIVE_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
CANDIDATE_FIELDS = (
    "repository_id", "pr_number", "base_sha", "head_sha", "tree_sha",
    "manifest_sha256", "provider_pr_body_sha256",
)


def _require(condition: bool, message: str):
    if not condition:
        raise ValidationError(message)


def _mapping(value: Any, label: str) -> dict:
    _require(isinstance(value, dict), f"{label} must be an object")
    return value


def _exact(value: Any, fields: set[str], label: str) -> dict:
    result = _mapping(value, label)
    _require(set(result) == fields, f"{label} has missing or unknown fields")
    return result


def _text(value: Any, label: str, *, pattern=TEXT_ID) -> str:
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None,
             f"{label} is not canonical text")
    return value


def _description(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value.strip() == value
             and 0 < len(value) <= 8192
             and not any(ord(character) < 32 for character in value),
             f"{label} must be nonempty single-line text")
    return value


def _relative_path(value: Any, label: str) -> str:
    _require(isinstance(value, str) and len(value) <= 512
             and RELATIVE_PATH.fullmatch(value) is not None
             and ".." not in value.split("/"),
             f"{label} must be a safe repository-relative path")
    return value


def _digest(value: Any, label: str) -> str:
    return _text(value, label, pattern=SHA256)


def _git_oid(value: Any, label: str) -> str:
    return _text(value, label, pattern=SHA)


def _positive(value: Any, label: str, maximum=1_000_000) -> int:
    _require(type(value) is int and 1 <= value <= maximum,
             f"{label} must be an integer from 1 to {maximum}")
    return value


def _role_execution(value: Any, label: str) -> dict:
    execution = _exact(value, {
        "status", "reason", "resume_trigger", "evidence_sha256",
    }, label)
    status = execution["status"]
    _require(status in ROLE_STATES, f"{label}.status is not a supported role state")
    if status == "RUNNING":
        _require(execution["reason"] is None and execution["resume_trigger"] is None
                 and execution["evidence_sha256"] is None,
                 f"{label} RUNNING state cannot carry pause or blocker evidence")
    else:
        _description(execution["reason"], f"{label}.reason")
        _description(execution["resume_trigger"], f"{label}.resume_trigger")
        _digest(execution["evidence_sha256"], f"{label}.evidence_sha256")
    return deepcopy(execution)


def canonicalize_pr_body(value: str | bytes) -> bytes:
    """Return strict UTF-8, LF-only bytes with exactly one trailing LF."""
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValidationError("PR body must be strict UTF-8") from exc
    else:
        _require(isinstance(value, str), "PR body must be text or bytes")
        text = value
        try:
            text.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValidationError("PR body contains an invalid Unicode scalar") from exc
    _require(not text.startswith("\ufeff"), "PR body must not contain a UTF-8 BOM")
    _require("\x00" not in text, "PR body must not contain NUL")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return (text.rstrip("\n") + "\n").encode("utf-8")


def provider_body_receipt(local_body: str | bytes, provider_readback: bytes) -> dict:
    """Canonicalize locally and require byte-exact provider readback."""
    canonical_body = canonicalize_pr_body(local_body)
    _require(isinstance(provider_readback, bytes), "Provider PR-body readback must be raw bytes")
    try:
        provider_readback.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("Provider PR-body readback is not strict UTF-8") from exc
    _require(provider_readback == canonical_body,
             "Provider PR-body readback differs byte-for-byte from the canonical body")
    _require(b"\r" not in provider_readback and provider_readback.endswith(b"\n")
             and not provider_readback.endswith(b"\n\n"),
             "Provider PR-body readback is not LF-only with one trailing LF")
    return {
        "canonicalization": "utf8-lf-one-trailing-lf",
        "provider_readback": "BYTE_EXACT",
        "provider_pr_body_sha256": sha256(provider_readback),
        "bytes": len(provider_readback),
    }


def validate_candidate(value: Any, body_sha256: str, label="candidate") -> dict:
    candidate = _exact(value, set(CANDIDATE_FIELDS), label)
    _require(type(candidate["repository_id"]) is int and candidate["repository_id"] > 0,
             f"{label}.repository_id must be a positive numeric provider ID")
    _positive(candidate["pr_number"], f"{label}.pr_number")
    for field in ("base_sha", "head_sha", "tree_sha"):
        _git_oid(candidate[field], f"{label}.{field}")
    _digest(candidate["manifest_sha256"], f"{label}.manifest_sha256")
    _digest(candidate["provider_pr_body_sha256"], f"{label}.provider_pr_body_sha256")
    _require(candidate["base_sha"] != candidate["head_sha"],
             f"{label} base and head must differ")
    _require(candidate["provider_pr_body_sha256"] == body_sha256,
             f"{label} does not bind the byte-exact provider PR body")
    return deepcopy(candidate)


def _same_candidate(value: Any, candidate: dict, label: str):
    observed = validate_candidate(value, candidate["provider_pr_body_sha256"], label)
    _require(observed == candidate, f"{label} differs from the frozen exact tuple")


def validate_topology(value: Any) -> tuple[dict, list[dict]]:
    topology = _exact(value, {
        "default_slots", "observed_slots", "controller", "adversarial_handler",
        "workers", "integration_steward", "degraded_mode",
    }, "topology")
    _require(topology["default_slots"] == DEFAULT_SLOT_COUNT,
             "Default topology must declare exactly five slots")
    observed = _positive(topology["observed_slots"], "topology.observed_slots", 64)
    controller = _exact(topology["controller"], {"role", "agent_id", "execution"},
                        "topology.controller")
    _require(controller["role"] == "sole-controller", "Topology must contain one sole controller")
    _text(controller["agent_id"], "topology.controller.agent_id")
    controller_execution = _role_execution(controller["execution"],
                                            "topology.controller.execution")
    adversarial = _exact(topology["adversarial_handler"],
                         {"role", "agent_id", "dedicated", "gate_name", "execution"},
                         "topology.adversarial_handler")
    _require(adversarial["role"] == "adversarial-case-handler"
             and adversarial["dedicated"] is True and adversarial["gate_name"] == GATE_NAME,
             "A dedicated named pre-controller adversarial handler is mandatory")
    _text(adversarial["agent_id"], "topology.adversarial_handler.agent_id")
    adversarial_execution = _role_execution(
        adversarial["execution"], "topology.adversarial_handler.execution")
    workers = topology["workers"]
    _require(isinstance(workers, list) and len(workers) == 3,
             "Topology must contain exactly three worker streams")
    ids = {controller["agent_id"], adversarial["agent_id"]}
    _require(len(ids) == 2, "Controller and adversarial handler must be distinct")
    deliverable_ids = set()
    normalized = []
    for expected_stream, worker in zip(WORKER_STREAMS, workers):
        worker = _exact(worker, {"stream", "agent_id", "execution", "active", "inactive"},
                        f"worker {expected_stream}")
        _require(worker["stream"] == expected_stream,
                 "Worker streams must be ordered exactly A, B, C")
        agent_id = _text(worker["agent_id"], f"worker {expected_stream}.agent_id")
        _require(agent_id not in ids, "Every topology role must have a distinct agent identity")
        ids.add(agent_id)
        execution = _role_execution(worker["execution"],
                                    f"worker {expected_stream}.execution")
        active = _deliverable(worker["active"], f"worker {expected_stream}.active",
                              active=True)
        expected_work_state = {
            "RUNNING": "ACTIVE", "PAUSED": {"PAUSED", "PR_READY"}, "BLOCKED": "BLOCKED",
        }[execution["status"]]
        if isinstance(expected_work_state, set):
            _require(active["work_state"] in expected_work_state,
                     "Paused workers must have PAUSED or PR_READY lifecycle state")
        else:
            _require(active["work_state"] == expected_work_state,
                     "Worker execution and active Jira lifecycle state are inconsistent")
        inactive = worker["inactive"]
        _require(isinstance(inactive, list), f"worker {expected_stream}.inactive must be a list")
        previous_order = active["dependency_order"]
        for index, item in enumerate(inactive):
            item = _deliverable(item, f"worker {expected_stream}.inactive[{index}]",
                                active=False)
            _require(item["dependency_order"] > previous_order,
                     "Inactive deliverables must follow dependency order")
            previous_order = item["dependency_order"]
        for item in [active, *inactive]:
            _require(item["deliverable_id"] not in deliverable_ids,
                     "A deliverable cannot appear in more than one stream")
            deliverable_ids.add(item["deliverable_id"])
        normalized.append({**worker, "execution": execution,
                           "active": active, "inactive": inactive})
    steward = _exact(topology["integration_steward"], {"kind", "consumes_agent_slot"},
                     "topology.integration_steward")
    _require(steward == {"kind": "deterministic-non-model-infrastructure",
                         "consumes_agent_slot": False},
             "Integration/PR stewardship must be deterministic non-model infrastructure")
    degraded = topology["degraded_mode"]
    role_states = {
        controller["agent_id"]: controller_execution["status"],
        adversarial["agent_id"]: adversarial_execution["status"],
        **{worker["agent_id"]: worker["execution"]["status"] for worker in normalized},
    }
    running_roles = sorted(role_id for role_id, status in role_states.items()
                           if status == "RUNNING")
    paused_roles = sorted(role_id for role_id, status in role_states.items()
                          if status == "PAUSED")
    _require(len(running_roles) <= observed,
             "Running roles exceed the host's observed slot capacity")
    if observed < DEFAULT_SLOT_COUNT:
        _require(degraded is not None,
                 "Fewer than five slots requires explicit degraded-mode authorization")
        degraded = _exact(degraded, {
            "authorized", "authorized_by", "authorization_evidence_sha256", "reason",
            "paused_roles",
        }, "topology.degraded_mode")
        _require(degraded["authorized"] is True,
                 "Fewer than five slots requires explicit degraded-mode authorization")
        _text(degraded["authorized_by"], "topology.degraded_mode.authorized_by")
        _digest(degraded["authorization_evidence_sha256"],
                "topology.degraded_mode.authorization_evidence_sha256")
        _text(degraded["reason"], "topology.degraded_mode.reason")
        _require(degraded["paused_roles"] == paused_roles and paused_roles,
                 "Degraded-mode evidence must name every explicitly paused role")
        if observed == 1:
            running_workers = [worker for worker in normalized
                               if worker["execution"]["status"] == "RUNNING"]
            _require(len(running_workers) <= 1,
                     "One observed slot cannot claim concurrent worker streams")
    else:
        _require(degraded is None, "Degraded mode must be null when five slots are available")
    normalized_topology = deepcopy(topology)
    normalized_topology["controller"]["execution"] = controller_execution
    normalized_topology["adversarial_handler"]["execution"] = adversarial_execution
    normalized_topology["running_roles"] = running_roles
    normalized_topology["paused_roles"] = paused_roles
    return normalized_topology, normalized


def _deliverable(value: Any, label: str, *, active: bool) -> dict:
    item = _exact(value, {
        "deliverable_id", "dependency_order", "work_state", "jira_status",
    }, label)
    _text(item["deliverable_id"], f"{label}.deliverable_id")
    _positive(item["dependency_order"], f"{label}.dependency_order")
    lifecycle = ACTIVE_LIFECYCLE if active else INACTIVE_LIFECYCLE
    _require(item["work_state"] in lifecycle,
             f"{label}.work_state is invalid for {'active' if active else 'inactive'} work")
    _require(item["jira_status"] == lifecycle[item["work_state"]],
             f"{label} Jira status is inconsistent with {item['work_state']}")
    if active:
        _require(item["jira_status"] != "Open",
                 "Started active work cannot return to Jira Open")
    return item


def _inventory_test_artifact(row: dict, repository_root: Path) -> tuple[str, bytes]:
    root = repository_root.resolve()
    path = (root / row["evidence_path"]).resolve()
    _require(path != root and root in path.parents and path.is_file(),
             f"Inventory evidence path does not exist: {row['evidence_path']}")
    _require(path.suffix == ".py", "Inventory evidence must be a Python test artifact")
    raw = path.read_bytes()
    try:
        tree = ast.parse(raw, filename=row["evidence_path"])
    except (SyntaxError, ValueError) as exc:
        raise ValidationError("Inventory evidence is not a parseable Python test artifact") from exc
    parts = row["test_id"].replace("::", ".").split(".")
    parts = [part for part in parts if part and not part.endswith(".py")]
    found = False
    if len(parts) == 1:
        found = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == parts[0] for node in tree.body)
    elif len(parts) == 2:
        found = any(isinstance(node, ast.ClassDef) and node.name == parts[0]
                    and any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and child.name == parts[1] for child in node.body)
                    for node in tree.body)
    _require(found,
             f"Inventory test ID does not exist in evidence path: {row['test_id']}")
    return sha256(raw), raw


def validate_inventory(raw: bytes, expected_sha256: str,
                       *, repository_root: Path = REPOSITORY_ROOT) -> tuple[dict, str]:
    _digest(expected_sha256, "expected inventory SHA-256")
    _require(sha256(raw) == expected_sha256,
             "Permanent adversarial inventory bytes differ from the expected digest")
    try:
        value = loads(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ValidationError("Permanent adversarial inventory is not UTF-8") from exc
    inventory = _exact(value, {"format", "inventory_id", "regressions"}, "regression inventory")
    _require(inventory["format"] == INVENTORY_FORMAT,
             "Unsupported adversarial regression inventory format")
    _text(inventory["inventory_id"], "regression inventory.inventory_id")
    rows = inventory["regressions"]
    _require(isinstance(rows, list) and rows,
             "Permanent adversarial inventory must contain named regressions")
    ids = set()
    findings = set()
    categories = []
    normalized_rows = []
    for index, row in enumerate(rows):
        row = _exact(row, {
            "regression_id", "category", "name", "test_id", "evidence_path",
            "permanent", "source_finding_id",
        }, f"regression[{index}]")
        regression_id = _text(row["regression_id"], f"regression[{index}].regression_id")
        _require(regression_id not in ids, "Duplicate permanent regression ID")
        ids.add(regression_id)
        category = _text(row["category"], f"regression[{index}].category")
        _require(category in ADVERSARIAL_CATEGORIES,
                 "Every permanent regression must use a mandatory adversarial category")
        categories.append(category)
        _description(row["name"], f"regression[{index}].name")
        _text(row["test_id"], f"regression[{index}].test_id")
        _relative_path(row["evidence_path"], f"regression[{index}].evidence_path")
        _require(row["permanent"] is True, "Every inventory member must be permanent")
        finding = row["source_finding_id"]
        if finding is not None:
            _text(finding, f"regression[{index}].source_finding_id")
            _require(finding not in findings,
                     "An accepted critic finding must map to exactly one permanent regression")
            findings.add(finding)
        artifact_sha256, _ = _inventory_test_artifact(row, repository_root)
        normalized_rows.append({**row, "artifact_sha256": artifact_sha256})
    missing = [category for category in ADVERSARIAL_CATEGORIES
               if category not in categories]
    _require(not missing,
             "Permanent adversarial inventory omits mandatory categories: " + ", ".join(missing))
    normalized_inventory = deepcopy(inventory)
    normalized_inventory["regressions"] = normalized_rows
    return normalized_inventory, expected_sha256


def build_regression_receipt(candidate: dict, row: dict, result_sha256: str,
                             *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    """Build a candidate and artifact-bound receipt from a host result digest."""
    _digest(result_sha256, "regression result SHA-256")
    artifact_sha256, _ = _inventory_test_artifact(row, repository_root)
    core = {
        "regression_id": row["regression_id"],
        "category": row["category"],
        "test_id": row["test_id"],
        "evidence_path": row["evidence_path"],
        "artifact_sha256": artifact_sha256,
        "candidate": deepcopy(candidate),
        "status": "PASS",
        "result_sha256": result_sha256,
    }
    return {**core, "receipt_sha256": fingerprint("adversarial-regression-execution", core)}


def deterministic_shards(test_ids: list[str], resource_capacity: int) -> list[dict]:
    _positive(resource_capacity, "shard resource capacity", 256)
    _require(isinstance(test_ids, list) and test_ids,
             "Heavy validation requires a nonempty test inventory")
    normalized = []
    for index, test_id in enumerate(test_ids):
        normalized.append(_text(test_id, f"test_ids[{index}]"))
    _require(len(normalized) == len(set(normalized)), "Duplicate heavy-validation test ID")
    normalized.sort()
    count = min(3, resource_capacity, len(normalized))
    buckets = [[] for _ in range(count)]
    for index, test_id in enumerate(normalized):
        buckets[index % count].append(test_id)
    return [{"shard_id": f"shard-{index + 1:02d}", "test_ids": tests}
            for index, tests in enumerate(buckets)]


def aggregate_shard_receipts(candidate: dict, assignments: list[dict], receipts: list[dict]) -> str:
    _require(isinstance(receipts, list) and len(receipts) == len(assignments),
             "Every deterministic shard requires exactly one receipt")
    normalized = []
    for index, (assignment, receipt) in enumerate(zip(assignments, receipts)):
        receipt = _exact(receipt, {"shard_id", "test_ids", "status", "result_sha256"},
                         f"shard receipt[{index}]")
        _require(receipt["shard_id"] == assignment["shard_id"]
                 and receipt["test_ids"] == assignment["test_ids"],
                 "Shard receipt differs from the deterministic assignment")
        _require(receipt["status"] == "PASS", "Every shard must pass before aggregation")
        _digest(receipt["result_sha256"], f"shard receipt[{index}].result_sha256")
        normalized.append(receipt)
    return fingerprint("five-slot-shard-aggregate", {
        "candidate": candidate, "assignments": assignments, "receipts": normalized,
    })


def validate_plan(plan_raw: bytes, expected_plan_sha256: str, *, local_pr_body: bytes,
                  provider_readback: bytes, inventory_raw: bytes,
                  expected_inventory_sha256: str) -> dict:
    """Validate a complete candidate-bound acceleration evidence bundle."""
    _digest(expected_plan_sha256, "expected plan SHA-256")
    _require(sha256(plan_raw) == expected_plan_sha256,
             "Acceleration plan bytes differ from the expected digest")
    try:
        plan = loads(plan_raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ValidationError("Acceleration plan is not UTF-8") from exc
    plan = _exact(plan, {
        "format", "candidate", "topology", "freeze", "accepted_critic_findings",
        "adversarial_gate", "sharded_validation", "full_suite", "controller_verification",
        "review_completion", "publication_scan", "deny_scan", "final_independent_review",
        "owner_ready",
    }, "acceleration plan")
    _require(plan["format"] == FORMAT, "Unsupported five-slot acceleration plan format")
    body = provider_body_receipt(local_pr_body, provider_readback)
    candidate = validate_candidate(plan["candidate"], body["provider_pr_body_sha256"])
    topology, workers = validate_topology(plan["topology"])
    inventory, inventory_digest = validate_inventory(inventory_raw, expected_inventory_sha256)

    freeze = _exact(plan["freeze"], {"sequence", "status", "candidate"}, "freeze")
    _require(freeze["sequence"] == 1 and freeze["status"] == "FROZEN",
             "Candidate code/tree/manifest/provider body must freeze first")
    _same_candidate(freeze["candidate"], candidate, "freeze.candidate")

    accepted = plan["accepted_critic_findings"]
    _require(isinstance(accepted, list), "accepted_critic_findings must be a list")
    accepted_ids = []
    for index, finding in enumerate(accepted):
        finding = _exact(finding, {"finding_id", "acceptance_evidence_sha256"},
                         f"accepted_critic_findings[{index}]")
        accepted_ids.append(_text(finding["finding_id"], f"accepted finding[{index}].finding_id"))
        _digest(finding["acceptance_evidence_sha256"],
                f"accepted finding[{index}].acceptance_evidence_sha256")
    _require(len(accepted_ids) == len(set(accepted_ids)), "Duplicate accepted critic finding")
    inventory_findings = {row["source_finding_id"] for row in inventory["regressions"]
                          if row["source_finding_id"] is not None}
    _require(set(accepted_ids) <= inventory_findings,
             "Every accepted critic finding must be a permanent named regression with evidence")

    adversarial = _exact(plan["adversarial_gate"], {
        "sequence", "name", "status", "candidate", "inventory_sha256",
        "regression_receipts",
    }, "adversarial_gate")
    _require(adversarial["sequence"] == 2 and adversarial["name"] == GATE_NAME
             and adversarial["status"] == "PASS",
             "The named adversarial regression gate must pass immediately after freeze")
    _same_candidate(adversarial["candidate"], candidate, "adversarial_gate.candidate")
    _require(adversarial["inventory_sha256"] == inventory_digest,
             "Adversarial gate did not run the frozen permanent inventory")
    receipts = adversarial["regression_receipts"]
    _require(isinstance(receipts, list), "regression_receipts must be a list")
    expected_regressions = [row["regression_id"] for row in inventory["regressions"]]
    observed_regressions = []
    for index, (receipt, row) in enumerate(zip(receipts, inventory["regressions"])):
        receipt = _exact(receipt, {
            "regression_id", "category", "test_id", "evidence_path",
            "artifact_sha256", "candidate", "status", "result_sha256", "receipt_sha256",
        },
                         f"regression receipt[{index}]")
        observed_regressions.append(receipt["regression_id"])
        _require(receipt["regression_id"] == row["regression_id"]
                 and receipt["category"] == row["category"]
                 and receipt["test_id"] == row["test_id"]
                 and receipt["evidence_path"] == row["evidence_path"],
                 "Adversarial receipt differs from its ordered permanent inventory member")
        _require(receipt["artifact_sha256"] == row["artifact_sha256"],
                 "Adversarial receipt is not bound to the frozen test artifact")
        _same_candidate(receipt["candidate"], candidate,
                        f"regression receipt[{index}].candidate")
        _require(receipt["status"] == "PASS", "Every permanent adversarial regression must pass")
        _digest(receipt["result_sha256"], f"regression receipt[{index}].result_sha256")
        supplied = _digest(receipt["receipt_sha256"],
                           f"regression receipt[{index}].receipt_sha256")
        core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        _require(supplied == fingerprint("adversarial-regression-execution", core),
                 "Adversarial execution receipt is fabricated or mismatched")
    _require(observed_regressions == expected_regressions,
             "Adversarial gate receipts must exactly cover the ordered permanent inventory")

    shards = _exact(plan["sharded_validation"], {
        "sequence", "resource_capacity", "test_ids", "assignments", "receipts",
        "aggregate_sha256",
    }, "sharded_validation")
    _require(shards["sequence"] == 3, "Sharded validation must follow the adversarial gate")
    assignments = deterministic_shards(shards["test_ids"], shards["resource_capacity"])
    _require(shards["assignments"] == assignments,
             "Heavy-test shard assignments are not deterministic and bounded")
    aggregate = aggregate_shard_receipts(candidate, assignments, shards["receipts"])
    _require(shards["aggregate_sha256"] == aggregate,
             "Heavy-test aggregate receipt does not exactly match all shards")

    full_suite = _exact(plan["full_suite"], {"sequence", "runs"}, "full_suite")
    _require(full_suite["sequence"] == 4 and isinstance(full_suite["runs"], list)
             and len(full_suite["runs"]) == 1,
             "Exactly one full suite may run, after freeze, adversarial gate, and shards")
    run = _exact(full_suite["runs"][0], {"status", "candidate", "receipt_sha256"},
                 "full_suite.runs[0]")
    _require(run["status"] == "PASS", "The single full-suite run must pass")
    _same_candidate(run["candidate"], candidate, "full_suite.runs[0].candidate")
    _digest(run["receipt_sha256"], "full_suite.runs[0].receipt_sha256")

    controller = _exact(plan["controller_verification"],
                        {"sequence", "status", "exact_tuple", "receipt_sha256"},
                        "controller_verification")
    _require(controller["sequence"] == 5 and controller["status"] == "PASS",
             "Controller verification must follow the single full suite")
    _same_candidate(controller["exact_tuple"], candidate,
                    "controller_verification.exact_tuple")
    _digest(controller["receipt_sha256"], "controller_verification.receipt_sha256")

    completion = _exact(plan["review_completion"],
                        {"sequence", "status", "frozen", "candidate", "receipt_sha256"},
                        "review_completion")
    _require(completion["sequence"] == 6 and completion["status"] == "PASS"
             and completion["frozen"] is True,
             "Independent-review completion must be frozen after controller verification")
    _same_candidate(completion["candidate"], candidate, "review_completion.candidate")
    _digest(completion["receipt_sha256"], "review_completion.receipt_sha256")

    _pass_gate(plan["publication_scan"], candidate, "publication_scan", 7)
    _pass_gate(plan["deny_scan"], candidate, "deny_scan", 8)
    final = _exact(plan["final_independent_review"], {
        "sequence", "status", "independent", "reviewer_id", "candidate", "receipt_sha256",
    }, "final_independent_review")
    _require(final["sequence"] == 9 and final["status"] == "APPROVE"
             and final["independent"] is True,
             "Final independent review remains mandatory after both scans")
    _same_candidate(final["candidate"], candidate, "final_independent_review.candidate")
    _digest(final["receipt_sha256"], "final_independent_review.receipt_sha256")
    reviewer_id = _text(final["reviewer_id"], "final_independent_review.reviewer_id")
    role_ids = {topology["controller"]["agent_id"],
                topology["adversarial_handler"]["agent_id"],
                *(worker["agent_id"] for worker in workers)}
    _require(reviewer_id not in role_ids,
             "Final reviewer must be independent from all five implementation roles")
    _require(plan["owner_ready"] == "NO", "Visibility-only acceleration evidence must keep OWNER_READY=NO")

    mode = "DEFAULT_FIVE_SLOT" if topology["observed_slots"] >= DEFAULT_SLOT_COUNT else "AUTHORIZED_DEGRADED"
    return {
        "format": "awf-five-slot-acceleration-receipt-1",
        "status": "PASS",
        "mode": mode,
        "gate_name": GATE_NAME,
        "candidate": candidate,
        "provider_body": body,
        "inventory_sha256": inventory_digest,
        "shard_aggregate_sha256": aggregate,
        "plan_sha256": expected_plan_sha256,
        "blocked_streams": [worker["stream"] for worker in workers
                            if worker["execution"]["status"] == "BLOCKED"],
        "paused_streams": [worker["stream"] for worker in workers
                           if worker["execution"]["status"] == "PAUSED"],
        "running_roles": topology["running_roles"],
        "other_streams_continue": any(worker["execution"]["status"] == "RUNNING"
                                      for worker in workers),
        "owner_ready": "NO",
        "execution_authority": False,
        "receipt_sha256": fingerprint("five-slot-acceleration-receipt", {
            "candidate": candidate, "plan_sha256": expected_plan_sha256,
            "inventory_sha256": inventory_digest, "shard_aggregate_sha256": aggregate,
        }),
    }


def _pass_gate(value: Any, candidate: dict, label: str, sequence: int):
    gate = _exact(value, {"sequence", "status", "candidate", "receipt_sha256"}, label)
    _require(gate["sequence"] == sequence and gate["status"] == "PASS",
             f"{label} must remain mandatory and pass in sequence")
    _same_candidate(gate["candidate"], candidate, f"{label}.candidate")
    _digest(gate["receipt_sha256"], f"{label}.receipt_sha256")


def encode_plan(value: dict) -> bytes:
    """Helper for trusted callers and tests; JSON remains newline terminated."""
    return canonical(value) + b"\n"
