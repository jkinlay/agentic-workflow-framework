"""Shared-account policy diagnostics; observations never grant owner authority.

This foundation changes no owner-authority consumer. Consumers must bind the
default-branch observation and annotate retained evidence before using it.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from . import ValidationError
from .canonical import load_yaml, sha256
from .protected_settings import _blob, _default_ref, _git, _origin_url

SETTING = "shared_owner_automation_actor_id"
SETTING_PATH = "$.merge_gate." + SETTING


def configured_shared_owner_actor_id(config):
    """Validate a proposal's exact account relationship, without authorizing it."""
    gate = config.get("merge_gate", {})
    actor = gate.get(SETTING)
    if actor is None:
        return None
    if type(actor) is not int or not 1 <= actor <= 9007199254740991:
        raise ValidationError(SETTING_PATH + ": expected a positive integer or null, never a boolean")
    expected = config.get("github", {}).get("expected_actor_id")
    if type(expected) is not int or actor != expected:
        raise ValidationError(SETTING_PATH + ": must equal github.expected_actor_id; profile-only configuration cannot opt out")
    owners = gate.get("trusted_owner_ids")
    if (not isinstance(owners, list) or any(type(item) is not int or item < 1 for item in owners)
            or len(set(owners)) != len(owners) or actor not in owners):
        raise ValidationError(SETTING_PATH + ": account must appear in unique positive numeric merge_gate.trusted_owner_ids")
    return actor


def shared_owner_warnings(config):
    """Report a proposed account even if other fields prevent acceptance."""
    gate = config.get("merge_gate") if isinstance(config, dict) else None
    actor = gate.get(SETTING) if isinstance(gate, dict) else None
    if type(actor) is not int or not 1 <= actor <= 9007199254740991:
        return []
    return [{"path": SETTING_PATH, "code": "SHARED_OWNER_AUTOMATION_ACCOUNT",
             "message": f"Configured shared owner/automation account {actor}: records identify an account, not a person. This report grants no owner authority; owner-authority behavior remains unchanged pending consumer support."}]


def _origin_matches(url, github):
    """Compare local origin identity without resolving or contacting a provider."""
    expected = urlsplit(github["host"])
    if url.startswith("git@") and ":" in url:
        host, path = url[4:].split(":", 1)
        return (expected.port is None and host.casefold() == expected.hostname.casefold()
                and path.removesuffix(".git").casefold() == github["repository"].casefold())
    parsed = urlsplit(url)
    return (parsed.scheme == expected.scheme and parsed.netloc.casefold() == expected.netloc.casefold()
            and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password
            and parsed.path.removeprefix("/").removesuffix(".git").casefold() == github["repository"].casefold())


def _policy_blob(root, commit, path):
    entry = _git(root, "ls-tree", commit, "--", path)
    if entry and not entry.startswith(b"100644 blob "):
        raise ValidationError("Shared-owner accepted policy must be a regular file")
    return _blob(root, commit, path)


def observe_accepted_owner_policy(root, config, workflow, contracts, *, policy_sha256):
    """Observe pinned default-branch bytes, refusing supplied policy substitution.

    The host refreshes origin/HEAD separately. Git metadata must remain outside
    worker write authority. No caller-selected ref, fetch or working-file read
    participates. This foundation is observational, not an authority permit.
    """
    from .configuration import inspect_config
    from .policy import policy_hash

    ref, _ = _default_ref(root)
    if ref is None:
        raise ValidationError("Shared-owner default branch is unobserved")
    raw_commit = _git(root, "rev-parse", "--verify", ref + "^{commit}")
    commit = raw_commit.decode("ascii").strip()
    if len(commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in commit):
        raise ValidationError("Shared-owner default-branch commit is invalid")
    raw_config = _policy_blob(root, commit, ".agentic/PROJECT_CONFIG.yaml")
    raw_workflow = _policy_blob(root, commit, ".agentic/workflow.yaml")
    raw_instructions = _policy_blob(root, commit, "PROJECT_INSTRUCTIONS.md")
    if raw_config is None or raw_workflow is None:
        raise ValidationError("Shared-owner accepted default-branch policy is missing")
    try:
        accepted = load_yaml(raw_config)
        accepted_workflow = load_yaml(raw_workflow)
        instructions = raw_instructions.decode("utf-8") if raw_instructions is not None else None
    except (ValidationError, ValueError, TypeError, UnicodeError):
        raise ValidationError("Shared-owner accepted default-branch policy is malformed") from None
    report = inspect_config(accepted, accepted_workflow, contracts,
                            project_instructions=instructions)
    if report["status"] != "ACCEPTED":
        raise ValidationError("Shared-owner accepted default-branch policy is invalid")
    digest = report["policy_sha256"]
    if digest != policy_sha256:
        raise ValidationError("Shared-owner accepted policy digest changed")
    if policy_hash(config, workflow) != digest:
        raise ValidationError("Supplied configuration differs from accepted default-branch policy")
    github = accepted["github"]
    if ref != "refs/remotes/origin/" + github["base_branch"]:
        raise ValidationError("Shared-owner accepted default-branch identity differs")
    origin = _origin_url(root)
    try:
        matches = origin is not None and _origin_matches(origin, github)
    except (ValueError, TypeError):
        matches = False
    if not matches:
        raise ValidationError("Shared-owner origin repository differs from accepted policy")
    if (_default_ref(root)[0] != ref or
            _git(root, "rev-parse", "--verify", ref + "^{commit}").decode("ascii").strip() != commit
            or _origin_url(root) != origin):
        raise ValidationError("Shared-owner default-branch policy changed during read")
    return {"status": "OBSERVED", SETTING: configured_shared_owner_actor_id(accepted),
            "policy_sha256": digest, "config_sha256": sha256(raw_config),
            "workflow_sha256": sha256(raw_workflow), "default_branch_ref": ref,
            "default_branch_commit": commit, "warnings": shared_owner_warnings(accepted),
            "execution_authority": False}
