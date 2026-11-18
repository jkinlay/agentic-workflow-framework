"""Accepted shared-account policy binding for explicitly enrolled consumers.

Observations alone never authorize execution. Each consumer must authenticate
its owner source and retain the returned policy evidence with its own binding.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from . import ValidationError
from .canonical import load_yaml, sha256
from .protected_settings import _blob, _default_ref, _git, _origin_url

SETTING = "shared_owner_automation_actor_id"
SETTING_PATH = "$.merge_gate." + SETTING
OWNER_EVIDENCE_FIELDS = frozenset({"shared_owner_automation_account",
                                   "owner_policy_sha256", "accepted_owner_policy"})


def consumer_policy_root(root=None, store=None):
    """Use host context only; never select a root from an owner source."""
    from pathlib import Path
    roots = tuple(getattr(store, "worktree_roots", ()))
    if root is not None:
        if roots and Path(root).resolve() not in {Path(item).resolve() for item in roots}:
            raise ValidationError("Owner policy root differs from protected controller worktrees")
        return root
    return roots[0] if len(roots) == 1 else None


def validate_retained_owner_evidence(record, actor_id):
    """Check the closed annotation shape; this does not authenticate policy."""
    fields = OWNER_EVIDENCE_FIELDS.intersection(record)
    if not fields:
        return {}
    if fields != OWNER_EVIDENCE_FIELDS:
        raise ValidationError("Shared-owner retained evidence is incomplete")
    evidence = {key: record[key] for key in OWNER_EVIDENCE_FIELDS}
    policy = evidence["accepted_owner_policy"]
    policy_fields = {"status", SETTING, "policy_sha256", "config_sha256", "workflow_sha256",
                     "default_branch_ref", "default_branch_commit", "warnings", "execution_authority"}
    import re
    if (evidence["shared_owner_automation_account"] is not True or not isinstance(policy, dict)
            or set(policy) != policy_fields or policy["status"] != "OBSERVED"
            or type(actor_id) is not int or type(policy[SETTING]) is not int or policy[SETTING] != actor_id
            or policy["execution_authority"] is not False
            or evidence["owner_policy_sha256"] != policy["policy_sha256"]
            or not all(isinstance(policy[key], str) and re.fullmatch(r"[0-9a-f]{64}", policy[key])
                       for key in ("policy_sha256", "config_sha256", "workflow_sha256"))
            or not isinstance(policy["default_branch_ref"], str)
            or not policy["default_branch_ref"].startswith("refs/remotes/origin/")
            or not isinstance(policy["default_branch_commit"], str)
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", policy["default_branch_commit"]) is None
            or policy["warnings"] != shared_owner_warnings({"merge_gate": {SETTING: actor_id}})):
        raise ValidationError("Shared-owner retained evidence is malformed or differs from its policy digest")
    return evidence


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
             "message": f"Configured shared owner/automation account {actor}: records identify an account, not a person. This report grants no owner authority; each consumer requires accepted default-branch policy."}]


def _has_git_metadata(root):
    """A missing fixture repository is distinct from an unreadable Git read."""
    from pathlib import Path
    try:
        (Path(root) / ".git").lstat()
    except FileNotFoundError:
        return False
    return True


def accepted_owner_evidence(root, config, actor_id, *, synthetic=False,
                            repository=None, project_key=None):
    """Bind one enrolled consumer without relaxing the global owner validator.

    The project root is supplied by the trusted host, never by an owner record.
    No observation object or ref can be supplied in place of immutable reads.
    Observe production policy even when the proposal hides the setting. Only
    explicitly synthetic, unset-setting reports may lack a policy observation;
    they never acquire shared-account evidence. Source authentication and
    replay checks remain the consumer's.
    """
    # Intake's documented process-local assignment map is not reviewed policy.
    # Strip only that context; every reviewed configuration byte still binds.
    config = {key: value for key, value in config.items() if key != "_awf_runtime"}
    shared = configured_shared_owner_actor_id(config)
    if shared is None:
        if config.get("github", {}).get("expected_actor_id") in config.get("merge_gate", {}).get("trusted_owner_ids", []):
            from .authorization import trusted_owner_ids
            trusted_owner_ids(config)
    if root is None:
        if shared is None and synthetic:
            return {}
        raise ValidationError("Owner records require an accepted default-branch policy observation")
    if shared is None and synthetic:
        if not _has_git_metadata(root) or _default_ref(root)[0] is None:
            return {}
    from pathlib import Path
    from .contracts import Contracts
    from .lifecycle import definition
    from .policy import policy_hash

    workflow = definition()
    contracts = Contracts(Path(__file__).resolve().parents[2] / "schemas")
    observation = observe_accepted_owner_policy(
        root, config, workflow, contracts, policy_sha256=policy_hash(config, workflow))
    if repository is not None and repository != config["github"]["repository"]:
        raise ValidationError("Stream inventory differs from the accepted owner policy repository")
    if (project_key is not None and config.get("jira", {}).get("enabled", True)
            and project_key != config["jira"]["project_key"]):
        raise ValidationError("Stream inventory differs from the accepted owner policy project")
    if shared is None:
        return {}
    if type(actor_id) is not int or actor_id != shared:
        return {}
    return {"shared_owner_automation_account": True,
            "owner_policy_sha256": observation["policy_sha256"],
            "accepted_owner_policy": observation}


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
    if raw_config is None or raw_workflow is None:
        raise ValidationError("Shared-owner accepted default-branch policy is missing")
    try:
        accepted = load_yaml(raw_config)
        accepted_workflow = load_yaml(raw_workflow)
    except (ValidationError, ValueError, TypeError, UnicodeError):
        raise ValidationError("Shared-owner accepted default-branch policy is malformed") from None
    report = inspect_config(accepted, accepted_workflow, contracts)
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
