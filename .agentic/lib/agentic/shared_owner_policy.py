"""Accepted shared-account policy binding for explicitly enrolled consumers.

Observations alone never authorize execution. Each consumer must authenticate
its owner source and retain the returned policy evidence with its own binding.
"""
from __future__ import annotations

import copy
from urllib.parse import urlsplit

from . import ValidationError
from .canonical import load_yaml, sha256
from . import protected_settings as _settings
from .git_isolation import independent_policy_read

SETTING = "shared_owner_automation_actor_id"
SETTING_PATH = "$.merge_gate." + SETTING
OWNER_EVIDENCE_FIELDS = frozenset({"shared_owner_automation_account",
                                   "owner_policy_sha256", "accepted_owner_policy"})


def _git(root, *arguments, **options):
    with independent_policy_read(root):
        return _settings._git(root, *arguments, **options)


def _default_ref(root):
    with independent_policy_read(root):
        return _settings._default_ref(root)


def _blob(root, commit, path):
    with independent_policy_read(root):
        return _settings._blob(root, commit, path)


def _origin_url(root):
    with independent_policy_read(root):
        return _settings._origin_url(root)


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


def _legacy_owner_overlap(config):
    """Recognize the well-formed default-off overlap affected by compatibility."""
    github, gate = ((config.get("github"), config.get("merge_gate"))
                    if isinstance(config, dict) else (None, None))
    if not isinstance(github, dict) or not isinstance(gate, dict) or gate.get(SETTING) is not None:
        return False
    expected, owners = github.get("expected_actor_id"), gate.get("trusted_owner_ids")
    return (type(expected) is int and expected > 0 and isinstance(owners, list)
            and all(type(owner) is int and owner > 0 for owner in owners)
            and len(owners) == len(set(owners))
            and expected in owners)


def _distinct_legacy_signer(config, actor_id):
    """Identify a signer other than the automation account; grant no authority.

    Each consumer keeps its own original trusted-owner check for that signer.
    """
    return (_legacy_owner_overlap(config) and type(actor_id) is int
            and actor_id != config["github"]["expected_actor_id"])


def _detach_legacy_record(config, record):
    """Bind the compatible decision to bytes no later policy read can replace.

    Every other mode keeps the caller's object and its existing behaviour.
    """
    return copy.deepcopy(record) if _legacy_owner_overlap(config) else record


def _require_detached_unchanged(record, detached):
    """A record replaced while it was checked keeps the separation refusal."""
    if record is not detached and record != detached:
        raise ValidationError("The configured automation actor must not be a trusted owner")


def accepted_owner_evidence(root, config, actor_id, *, synthetic=False,
                            repository=None, project_key=None, legacy_distinct_signer=False,
                            legacy_unsigned=False):
    """Bind one enrolled consumer without relaxing the global owner validator.

    The project root is supplied by the trusted host, never by an owner record.
    No observation object or ref can be supplied in place of immutable reads.
    A null/absent opt-out retains legacy authority without new Git
    prerequisites and cannot acquire shared-account evidence. An observable
    accepted shared account still detects a hidden proposal. An enabled opt-out
    requires production policy, including for synthetic reports.
    Source authentication and replay checks remain the consumer's.
    """
    # Intake's documented process-local assignment map is not reviewed policy.
    # Strip only that context; every reviewed configuration byte still binds.
    config = {key: value for key, value in config.items() if key != "_awf_runtime"}
    if not all(isinstance(config.get(key, {}), dict) for key in ("github", "merge_gate")):
        _refuse_hidden_opt_out(config, root)
    shared = configured_shared_owner_actor_id(config)
    if shared is None:
        if (config.get("github", {}).get("expected_actor_id") in config.get("merge_gate", {}).get("trusted_owner_ids", [])
                and not (legacy_distinct_signer and _distinct_legacy_signer(config, actor_id))
                and not (legacy_unsigned and actor_id is None and _legacy_owner_overlap(config))):
            from .authorization import trusted_owner_ids
            trusted_owner_ids(config)
        # An unset opt-out adds no Git prerequisite to legacy owner authority.
        # This optional downgrade probe can refuse a hidden accepted exception,
        # but can never enable one or make an unavailable Git read mandatory.
        if root is None:
            return {}
        try:
            ref, _ = _default_ref(root)
            raw = None
            if ref is not None:
                commit = _git(root, "rev-parse", "--verify", ref + "^{commit}").decode("ascii").strip()
                raw = _policy_blob(root, commit, ".agentic/PROJECT_CONFIG.yaml")
        except ValidationError:
            # Only this spelling failed; an accepted opt-out still refuses.
            _refuse_hidden_opt_out(config, root)
            return {}
        # An absent file, relative root or nested repository may not be the
        # project's own accepted policy; its legacy result stands unless hidden.
        from pathlib import Path
        if raw is None or not Path(root).is_absolute() or any(_outer_policies(root)):
            _refuse_hidden_opt_out(config, root)
        if ref is None:
            return {}
        accepted = load_yaml(raw) if raw is not None else {}
        if (isinstance(accepted, dict) and
                configured_shared_owner_actor_id(accepted) is None and
                (accepted.get("github", {}).get("expected_actor_id") ==
                 config.get("github", {}).get("expected_actor_id") or
                 accepted.get("github", {}).get("expected_actor_id") not in
                 config.get("merge_gate", {}).get("trusted_owner_ids", []))):
            return {}
    if root is None:
        raise ValidationError("Owner records require an accepted default-branch policy observation")
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


def _known_root(root):
    """Fix a relative root where its host supplies it; keep an absolute spelling."""
    from pathlib import Path
    path = Path(root)
    return path if path.is_absolute() else path.resolve()


def _root_spellings(root):
    """Spell one directory every way the isolated Git reader may need it.

    That reader uses its root as both working directory and -C argument, so a
    relative root is applied twice; Git also refuses some extended spellings.
    """
    from pathlib import Path
    known = _known_root(root)
    resolved = known.resolve()
    plain = resolved.as_posix()
    if plain.startswith("//?/UNC/"):
        plain = "//" + plain[len("//?/UNC/"):]
    elif plain.startswith(("//?/", "//./")):
        plain = plain[len("//?/"):]
    return tuple(dict.fromkeys((known, resolved, Path(plain))))


def _outer_policies(root):
    """Accepted bytes an enclosing repository holds for this exact project path.

    A project in a subdirectory cannot protect its own directory from a nested
    ``.git`` or bare-repository files, which would replace the discovered policy.
    """
    import os
    resolved = _root_spellings(root)[-1]
    for outer in resolved.parents:
        if not os.path.lexists(outer / ".git"):
            continue
        try:
            ref, _ = _default_ref(outer)
            if ref is None:
                continue
            commit = _git(outer, "rev-parse", "--verify", ref + "^{commit}").decode("ascii").strip()
            raw = _policy_blob(outer, commit, resolved.relative_to(outer).as_posix()
                               + "/.agentic/PROJECT_CONFIG.yaml")
        except ValidationError:
            continue  # Git cannot observe a repository here.
        if raw is not None:
            yield raw


def _policy_setting(raw):
    try:
        accepted = load_yaml(raw)
    except ValidationError:
        return None  # Malformed accepted bytes are no usable policy.
    gate = accepted.get("merge_gate") if isinstance(accepted, dict) else None
    return gate.get(SETTING) if isinstance(gate, dict) else None


def _accepted_opt_out(root):
    """Read only the accepted setting; an unobservable policy stays unknown.

    Only the closed Git reader's own refusal means unobservable.
    """
    for spelling in _root_spellings(root):
        try:
            ref, _ = _default_ref(spelling)
            if ref is None:
                continue
            commit = _git(spelling, "rev-parse", "--verify", ref + "^{commit}").decode("ascii").strip()
            raw = _policy_blob(spelling, commit, ".agentic/PROJECT_CONFIG.yaml")
        except ValidationError:
            continue
        if raw is not None:
            value = _policy_setting(raw)
            if value is not None:
                return value
            break
    return next((value for value in map(_policy_setting, _outer_policies(root)) if value is not None), None)


def _refuse_hidden_opt_out(config, *roots):
    """Let the accepted policy, never the proposal, decide that nothing is hidden.

    Any proposal without a valid opt-out is checked, whatever its identity,
    owners or section shapes. An accepted unset setting, or a root without an
    observable default branch, changes nothing. No result grants authority.
    """
    try:
        if configured_shared_owner_actor_id(config) is not None:
            return  # A valid proposal meets each caller's own accepted-policy rules.
    except (ValidationError, AttributeError, TypeError):
        pass
    known = [root for root in roots if root is not None]
    with independent_policy_read(*known):
        for root in known:
            try:
                hidden = _accepted_opt_out(root) is not None
            except Exception as exc:
                # An unexpected failure is not an observation that nothing is set.
                raise ValidationError("Shared-owner accepted policy could not be checked") from exc
            if hidden:
                raise ValidationError("Shared-owner accepted policy digest changed")


def _refuse_hidden_owner_policy(config, *roots):
    """Detect a hidden accepted opt-out where a caller knows more than its root.

    The default-off overlap keeps its complete accepted-policy comparison; every
    other proposal is refused only when the accepted opt-out is set.
    """
    if _legacy_owner_overlap(config):
        for root in roots:
            accepted_owner_evidence(root, config, None, legacy_unsigned=True)
    else:
        _refuse_hidden_opt_out(config, *roots)


def _legacy_owner_root(config, root):
    """Supply a known root to a rootless caller only in the default-off overlap."""
    return root if _legacy_owner_overlap(config) else None


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

    root = _known_root(root)
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
    if any(outer != raw_config for outer in _outer_policies(root)):
        raise ValidationError("Shared-owner accepted policy differs from its enclosing repository")
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
