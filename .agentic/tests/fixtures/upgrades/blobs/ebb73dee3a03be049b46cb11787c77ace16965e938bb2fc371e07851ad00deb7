"""Structured activation checks and capability admission.

Status is observation, not authority.  The helpers in this module are reusable by
host preflight and dispatch code without creating state or enabling adapters.
"""
from __future__ import annotations

from copy import deepcopy

from . import ValidationError
from .canonical import now_text


CHECK_STATES = {"PASS", "INVALID", "MISMATCH", "UNAVAILABLE", "UNOBSERVED", "NOT_APPLICABLE"}
BLOCKING_STATES = CHECK_STATES - {"PASS", "NOT_APPLICABLE"}
CAPABILITY_STATES = {"AVAILABLE", "UNAVAILABLE", "UNOBSERVED", "NOT_APPLICABLE"}
CAPABILITY_NAMES = (
    "local_work", "external_data_read", "branch_publication", "pr_creation",
    "independent_review", "jira_read", "jira_write", "merge_execution",
)


def check(code, stage, state, evidence, remedy, *, observed_at=None):
    """Build one stable activation check row."""
    if state not in CHECK_STATES:
        raise ValidationError("Unsupported activation check state")
    if not all(isinstance(value, str) and value for value in (code, stage, evidence, remedy)):
        raise ValidationError("Activation checks require code, stage, evidence and remedy")
    return {"code": code, "stage": stage, "state": state, "evidence": evidence,
            "remedy": remedy, "observed_at": observed_at or now_text()}


def access_diagnostic(exc, path):
    """Return a bounded filesystem diagnostic without leaking an absolute path."""
    code = getattr(exc, "winerror", None)
    if code is None:
        code = getattr(exc, "errno", None)
    return {"classification": "ACCESS_UNAVAILABLE", "path": path,
            "os_error_code": code, "error_type": type(exc).__name__}


def blockers(checks):
    return [deepcopy(item) for item in checks if item.get("state") in BLOCKING_STATES]


def activation_summary(report):
    """List every blocker before the one safe next command."""
    rows = blockers(report.get("checks", []))
    command = report.get("next_command") or "python -B .agentic/scripts/workflow.py status --require-active"
    return {"blockers": rows, "next_command": command}


def _capability(state, evidence, observed_at, completed_by=None):
    if state not in CAPABILITY_STATES:
        raise ValidationError("Unsupported capability state")
    row = {"state": state, "evidence": evidence, "observed_at": observed_at}
    if completed_by is not None:
        row["completed_by"] = completed_by
    return row


def build_capability_matrix(project_state, checks, config=None, observations=None, *, observed_at=None):
    """Build L5 capability rows from checks plus explicitly supplied observations.

    Publication/PR readiness belongs to L3, external resources to K14/L6, and
    provider/Jira identities to K12/K13.  Until those observers supply a row, the
    matrix says UNOBSERVED rather than inferring readiness from ACTIVE.
    """
    at = observed_at or now_text()
    local_ok = project_state in {"CONFIGURED", "ACTIVE"} and not any(
        item.get("stage") in {"installation", "configuration", "operating"}
        and item.get("state") in BLOCKING_STATES for item in checks)
    jira_enabled = bool((config or {}).get("jira", {}).get("enabled", True))
    matrix = {
        "local_work": _capability("AVAILABLE" if local_ok else "UNAVAILABLE",
                                  "Installed configuration and operating snapshot are accepted" if local_ok
                                  else "Local installation/configuration checks are not all available", at),
        "external_data_read": _capability("UNOBSERVED", "No external-resource observation is available", at, "PR implementing K14/L6"),
        "branch_publication": _capability("UNOBSERVED", "Publication readiness has not been observed", at, "PR implementing L3"),
        "pr_creation": _capability("UNOBSERVED", "Draft-PR creation permission has not been observed", at, "PR implementing L3"),
        "independent_review": _capability("UNOBSERVED", "Independent reviewer availability has not been observed", at, "PR implementing L3"),
        "jira_read": _capability("UNOBSERVED" if jira_enabled else "NOT_APPLICABLE",
                                 "Jira identity/read access has not been observed" if jira_enabled else "Jira is disabled", at,
                                 "PR implementing K13" if jira_enabled else None),
        "jira_write": _capability("UNOBSERVED" if jira_enabled else "NOT_APPLICABLE",
                                  "Jira identity/write access has not been observed" if jira_enabled else "Jira is disabled", at,
                                  "PR implementing K13" if jira_enabled else None),
        "merge_execution": _capability("UNOBSERVED", "Trusted merge executor availability has not been observed", at, "PR implementing L3"),
    }
    for name, supplied in (observations or {}).items():
        if name not in CAPABILITY_NAMES or not isinstance(supplied, dict):
            raise ValidationError("Unknown or malformed capability observation")
        state, evidence = supplied.get("state"), supplied.get("evidence")
        when = supplied.get("observed_at", at)
        if state not in CAPABILITY_STATES or not isinstance(evidence, str) or not evidence or not isinstance(when, str):
            raise ValidationError("Capability observations require state, evidence and observed_at")
        matrix[name] = _capability(state, evidence, when, supplied.get("completed_by"))
    return matrix


def require_capabilities(matrix, required):
    """Refuse dispatch unless every named capability is explicitly AVAILABLE."""
    unavailable = []
    for name in required:
        if name not in CAPABILITY_NAMES:
            raise ValidationError("Unknown required capability: " + str(name))
        row = matrix.get(name)
        if not isinstance(row, dict) or row.get("state") != "AVAILABLE":
            unavailable.append({"capability": name, "state": (row or {}).get("state", "UNOBSERVED"),
                                "evidence": (row or {}).get("evidence", "No capability observation")})
    if unavailable:
        details = "; ".join(item["capability"] + "=" + item["state"] for item in unavailable)
        raise ValidationError("CAPABILITY_UNAVAILABLE: refuse dispatch: " + details)
    return {"status": "ADMITTED", "required_capabilities": list(required), "execution_authority": False}
