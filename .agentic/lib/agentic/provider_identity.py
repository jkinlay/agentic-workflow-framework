"""Immutable GitHub and Jira provider identity admission.

The helpers are transport independent. Trusted host adapters supply bounded
observations; this module never changes accounts, chooses the first connection,
looks up an issue, or performs a provider mutation.
"""
from __future__ import annotations

import copy
from urllib.parse import urlsplit

from . import ValidationError
from .canonical import fingerprint, now_text, timestamp


GITHUB_FIELDS = frozenset({
    "actor_id", "actor_login", "auth_profile", "repository_id", "repository",
    "readable", "protocol", "observed_at",
})
JIRA_CONNECTION_FIELDS = frozenset({
    "connection_id", "cloud_id", "site", "account_id", "account_login",
    "projects", "observed_at",
})
JIRA_PROJECT_FIELDS = frozenset({"project_id", "project_key", "browse"})


class ProviderIdentityError(ValidationError):
    """Provider identity refusal with a stable machine-readable code."""

    def __init__(self, code, message, *, expected=None, observed=None):
        super().__init__(message)
        self.code = code
        self.expected = expected
        self.observed = observed


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProviderIdentityError("INVALID_OBSERVATION", f"{label} must be non-empty text")
    return value


def _site(value, label):
    value = _text(value, label)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ProviderIdentityError("INVALID_OBSERVATION", f"{label} must be an HTTPS origin")
    return value.rstrip("/")


def _github_expected(config):
    github = config.get("github", {}) if isinstance(config, dict) else {}
    repository_id = github.get("repository_id")
    actor_id = github.get("expected_actor_id")
    profile = github.get("auth_profile")
    if type(repository_id) is not int or repository_id < 1:
        raise ProviderIdentityError("IDENTITY_UNBOUND", "GitHub numeric repository identity is unbound")
    if actor_id is None and profile is None:
        raise ProviderIdentityError(
            "IDENTITY_UNBOUND", "GitHub expected actor ID or authentication profile is unbound")
    if actor_id is not None and (type(actor_id) is not int or actor_id < 1):
        raise ProviderIdentityError("IDENTITY_UNBOUND", "GitHub expected actor ID is invalid")
    if profile is not None:
        _text(profile, "github.auth_profile")
    login = github.get("expected_actor_login")
    if login is not None:
        _text(login, "github.expected_actor_login")
    return {"repository_id": repository_id, "repository": _text(
        github.get("repository"), "github.repository"), "actor_id": actor_id,
        "actor_login": login, "auth_profile": profile}


def github_identity_preflight(config, observation):
    """Require the intended actor/profile and numeric repository before change."""
    expected = _github_expected(config)
    if not isinstance(observation, dict) or set(observation) != GITHUB_FIELDS:
        raise ProviderIdentityError("INVALID_OBSERVATION", "GitHub identity observation has an invalid shape",
                                    expected=expected, observed=None)
    actor_id = observation["actor_id"]
    repository_id = observation["repository_id"]
    if type(actor_id) is not int or actor_id < 1 or type(repository_id) is not int or repository_id < 1:
        raise ProviderIdentityError("INVALID_OBSERVATION", "GitHub immutable IDs must be positive integers",
                                    expected=expected, observed=observation)
    for field in ("actor_login", "repository", "protocol", "observed_at"):
        _text(observation[field], "GitHub observation " + field)
    if observation["auth_profile"] is not None:
        _text(observation["auth_profile"], "GitHub observation auth_profile")
    timestamp(observation["observed_at"])
    if observation["protocol"] not in {"https", "ssh"} or type(observation["readable"]) is not bool:
        raise ProviderIdentityError("INVALID_OBSERVATION", "GitHub protocol/readability observation is invalid",
                                    expected=expected, observed=observation)
    mismatch = []
    if expected["actor_id"] is not None and actor_id != expected["actor_id"]:
        mismatch.append("actor_id")
    if expected["actor_login"] is not None and observation["actor_login"].casefold() != expected["actor_login"].casefold():
        mismatch.append("actor_login")
    if expected["auth_profile"] is not None and observation["auth_profile"] != expected["auth_profile"]:
        mismatch.append("auth_profile")
    if repository_id != expected["repository_id"]:
        mismatch.append("repository_id")
    if observation["repository"].casefold() != expected["repository"].casefold():
        mismatch.append("repository")
    if mismatch:
        raise ProviderIdentityError("IDENTITY_MISMATCH", "GitHub identity mismatch: " + ", ".join(mismatch),
                                    expected=expected, observed=observation)
    if not observation["readable"]:
        raise ProviderIdentityError("ACCESS_DENIED", "Configured numeric GitHub repository is not readable",
                                    expected=expected, observed=observation)
    return {"status": "PASS", "provider": "github", "expected": expected,
            "observed": copy.deepcopy(observation), "changes_attempted": False,
            "tokens_reported": False, "execution_authority": False}


def observe_github_identity(config, *, read_actor, read_repository, observe_protocol):
    """Collect a bounded read-only observation through a trusted host adapter."""
    expected = _github_expected(config)
    if not all(callable(item) for item in (read_actor, read_repository, observe_protocol)):
        raise ProviderIdentityError("IDENTITY_UNOBSERVED", "GitHub identity observer is unavailable")
    actor = read_actor()
    repository = read_repository(expected["repository_id"])
    protocol = observe_protocol()
    if not isinstance(actor, dict) or set(actor) != {"id", "login", "auth_profile", "observed_at"}:
        raise ProviderIdentityError("INVALID_OBSERVATION", "GitHub actor observation has an invalid shape")
    if not isinstance(repository, dict) or set(repository) != {"id", "name_with_owner", "readable"}:
        raise ProviderIdentityError("INVALID_OBSERVATION", "GitHub repository observation has an invalid shape")
    return github_identity_preflight(config, {
        "actor_id": actor["id"], "actor_login": actor["login"],
        "auth_profile": actor["auth_profile"], "repository_id": repository["id"],
        "repository": repository["name_with_owner"], "readable": repository["readable"],
        "protocol": protocol, "observed_at": actor["observed_at"],
    })


def _jira_expected(config):
    jira = config.get("jira", {}) if isinstance(config, dict) else {}
    if jira.get("enabled", True) is False:
        raise ProviderIdentityError("NOT_APPLICABLE", "Jira is disabled")
    fields = {"cloud_id": jira.get("cloud_id"), "site": jira.get("site"),
              "project_id": jira.get("provider_project_id"),
              "project_key": jira.get("project_key"),
              "account_id": jira.get("controller_actor_id")}
    if any(fields[name] is None for name in ("cloud_id", "project_id", "account_id")):
        raise ProviderIdentityError("IDENTITY_UNBOUND", "Jira immutable cloud, project, or account identity is unbound",
                                    expected=fields)
    for name, value in fields.items():
        if name == "site":
            fields[name] = _site(value, "jira.site")
        else:
            _text(value, "jira." + name)
    return fields


def expected_jira_identity(config):
    """Return a copy of the complete configured Jira identity or refuse unbound policy."""
    return copy.deepcopy(_jira_expected(config))


def validate_jira_connections(connections):
    if not isinstance(connections, list):
        raise ProviderIdentityError("INVALID_OBSERVATION", "Jira connections must be an array")
    validated, identities = [], set()
    for index, item in enumerate(connections):
        if not isinstance(item, dict) or set(item) != JIRA_CONNECTION_FIELDS:
            raise ProviderIdentityError("INVALID_OBSERVATION", f"Jira connection {index} has an invalid shape")
        value = copy.deepcopy(item)
        for field in ("connection_id", "cloud_id", "account_id", "account_login", "observed_at"):
            _text(value[field], f"Jira connection {index} {field}")
        value["site"] = _site(value["site"], f"Jira connection {index} site")
        timestamp(value["observed_at"])
        identity = (value["connection_id"], value["cloud_id"], value["account_id"])
        if identity in identities:
            raise ProviderIdentityError("INVALID_OBSERVATION", "Jira connection observation is duplicated")
        identities.add(identity)
        if not isinstance(value["projects"], list):
            raise ProviderIdentityError("INVALID_OBSERVATION", f"Jira connection {index} projects must be an array")
        project_ids = set()
        for project in value["projects"]:
            if not isinstance(project, dict) or set(project) != JIRA_PROJECT_FIELDS:
                raise ProviderIdentityError("INVALID_OBSERVATION", "Jira project observation has an invalid shape")
            _text(project["project_id"], "Jira project ID")
            _text(project["project_key"], "Jira project key")
            if type(project["browse"]) is not bool or project["project_id"] in project_ids:
                raise ProviderIdentityError("INVALID_OBSERVATION", "Jira project observation is invalid or duplicated")
            project_ids.add(project["project_id"])
        validated.append(value)
    return validated


def discover_jira_binding_candidates(config, connections):
    """Return all site/key matches; never choose the first accessible connection."""
    jira = config.get("jira", {})
    site = _site(jira.get("site"), "jira.site")
    key = _text(jira.get("project_key"), "jira.project_key")
    candidates = []
    for connection in validate_jira_connections(connections):
        for project in connection["projects"]:
            if connection["site"].casefold() == site.casefold() and project["project_key"] == key:
                candidates.append({"connection_id": connection["connection_id"],
                    "cloud_id": connection["cloud_id"], "site": connection["site"],
                    "account_id": connection["account_id"], "account_login": connection["account_login"],
                    "project_id": project["project_id"], "project_key": project["project_key"],
                    "browse": project["browse"], "observed_at": connection["observed_at"]})
    return sorted(candidates, key=lambda item: (item["cloud_id"], item["account_id"], item["project_id"]))


def jira_identity_preflight(config, connections):
    """Resolve the configured cloud/account/project exactly and prove browse access."""
    expected = _jira_expected(config)
    observed_connections = validate_jira_connections(connections)
    matches = [item for item in observed_connections if item["cloud_id"] == expected["cloud_id"]]
    if len(matches) != 1:
        raise ProviderIdentityError("IDENTITY_MISMATCH", "Configured Jira cloud does not resolve uniquely",
                                    expected=expected, observed=matches)
    connection = matches[0]
    projects = [item for item in connection["projects"] if item["project_id"] == expected["project_id"]]
    observed = {"cloud_id": connection["cloud_id"], "site": connection["site"],
                "account_id": connection["account_id"], "account_login": connection["account_login"],
                "project_id": projects[0]["project_id"] if len(projects) == 1 else None,
                "project_key": projects[0]["project_key"] if len(projects) == 1 else None,
                "browse": projects[0]["browse"] if len(projects) == 1 else False,
                "connection_id": connection["connection_id"], "observed_at": connection["observed_at"]}
    mismatch = []
    for key in ("cloud_id", "site", "account_id", "project_id", "project_key"):
        left, right = observed.get(key), expected[key]
        if key == "site":
            equal = isinstance(left, str) and left.casefold() == right.casefold()
        else:
            equal = left == right
        if not equal:
            mismatch.append(key)
    if mismatch:
        raise ProviderIdentityError("IDENTITY_MISMATCH", "Jira identity mismatch: " + ", ".join(mismatch),
                                    expected=expected, observed=observed)
    if not observed["browse"]:
        raise ProviderIdentityError("ACCESS_DENIED", "Configured Jira project lacks browse permission",
                                    expected=expected, observed=observed)
    return {"status": "PASS", "provider": "jira", "expected": expected,
            "observed": observed, "issue_lookups": 0, "writes_attempted": 0,
            "execution_authority": False}


def observe_jira_identity(config, *, list_connections):
    """Resolve the configured immutable Jira identity through a read-only adapter."""
    if not callable(list_connections):
        raise ProviderIdentityError("IDENTITY_UNOBSERVED", "Jira connection observer is unavailable")
    return jira_identity_preflight(config, list_connections())


def require_jira_write_identity(config, observation):
    """Check an already resolved identity immediately before issue lookup/write."""
    expected = _jira_expected(config)
    if not isinstance(observation, dict):
        raise ProviderIdentityError("INVALID_OBSERVATION", "Jira write identity observation is missing",
                                    expected=expected)
    observed = {"cloud_id": observation.get("cloud_id"), "site": observation.get("site"),
                "project_id": observation.get("project_id"), "project_key": observation.get("project_key"),
                "account_id": observation.get("account_id", observation.get("controller_actor_id"))}
    mismatch = [key for key in expected if (
        not isinstance(observed[key], str) or
        (observed[key].casefold() != expected[key].casefold() if key == "site" else observed[key] != expected[key]))]
    if mismatch:
        raise ProviderIdentityError("IDENTITY_MISMATCH", "Jira write identity mismatch: " + ", ".join(mismatch),
                                    expected=expected, observed=observed)
    return {"cloud_id": expected["cloud_id"], "site": expected["site"],
            "project_id": expected["project_id"], "project_key": expected["project_key"],
            "controller_actor_id": expected["account_id"]}


def jira_binding_plan(config, connections, confirmation=None, *, now=None):
    """Discover bindings, or produce a governance patch after exact confirmation."""
    candidates = discover_jira_binding_candidates(config, connections)
    candidate_sha256 = fingerprint("jira-binding-candidates", candidates)
    result = {"status": "CANDIDATES", "candidates": candidates,
              "candidate_sha256": candidate_sha256, "binding_patch": None,
              "execution_authority": False}
    if confirmation is None:
        return result
    required = {"format", "decision", "candidate_sha256", "cloud_id", "site", "project_id",
                "project_key", "account_id", "owner_id", "confirmed_at", "authentication_evidence"}
    if not isinstance(confirmation, dict) or set(confirmation) != required:
        raise ProviderIdentityError("INVALID_CONFIRMATION", "Jira binding confirmation has an invalid shape")
    if confirmation["format"] != "awf-jira-binding-confirmation-1" or confirmation["decision"] != "BIND":
        raise ProviderIdentityError("INVALID_CONFIRMATION", "Jira binding confirmation decision is invalid")
    confirmed_at = timestamp(confirmation["confirmed_at"])
    reference = timestamp(now or now_text())
    observations = [timestamp(item["observed_at"]) for item in candidates]
    if (not observations or confirmed_at < max(observations) or confirmed_at > reference
            or (reference - confirmed_at).total_seconds() > 900):
        raise ProviderIdentityError("INVALID_CONFIRMATION",
                                    "Jira binding confirmation is stale or predates its candidates")
    if confirmation["candidate_sha256"] != candidate_sha256:
        raise ProviderIdentityError("IDENTITY_MISMATCH", "Jira binding candidates changed after confirmation")
    if confirmation["owner_id"] not in config.get("merge_gate", {}).get("trusted_owner_ids", []):
        raise ProviderIdentityError("INVALID_CONFIRMATION", "Jira binding confirmation owner is not trusted")
    if (not isinstance(confirmation["authentication_evidence"], list)
            or not confirmation["authentication_evidence"]
            or not all(isinstance(item, str) and item.startswith("urn:") for item in confirmation["authentication_evidence"])):
        raise ProviderIdentityError("INVALID_CONFIRMATION", "Jira binding confirmation needs host authentication evidence")
    chosen = [item for item in candidates if all(item[key] == confirmation[key] for key in
        ("cloud_id", "site", "project_id", "project_key", "account_id"))]
    if len(chosen) != 1 or not chosen[0]["browse"]:
        raise ProviderIdentityError("IDENTITY_MISMATCH", "Confirmed Jira binding is absent, ambiguous, or unreadable")
    result.update(status="CONFIRMED", binding_patch={"cloud_id": chosen[0]["cloud_id"],
        "site": chosen[0]["site"], "provider_project_id": chosen[0]["project_id"],
        "project_key": chosen[0]["project_key"], "controller_actor_id": chosen[0]["account_id"]},
        confirmation=copy.deepcopy(confirmation))
    return result
