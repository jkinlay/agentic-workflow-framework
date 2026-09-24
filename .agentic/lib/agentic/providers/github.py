"""GitHub adapter for read-only repository discovery and rules observations.

The local record is a host assertion, not authenticated GitHub/human evidence.
An externally pinned imported record must come from the trusted coordinator.
The prerequisite result does not enable any adapter or replace its qualification.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote, urlsplit

from .. import ValidationError
from ..canonical import canonical, fresh, loads, now_text, sha256, validate_value
from ..child_process import child_env
from ..safeio import Tree

FORMAT = "awf-repository-rules-observation-1"
HOST = "github.com"
MAX_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * MAX_BYTES
MAX_PAGES = 5
MAX_RULESETS = 20
MAX_SECONDS = 60
MAX_AGE_SECONDS = 900
METADATA_DISCOVERY_SECONDS = 15
FIELDS = {"format", "source", "host", "observed_at", "repository", "default_branch",
          "repository_response", "branch_pages", "rulesets", "complete"}
PR_DEFAULT = {"required_approving_review_count": 0, "dismiss_stale_reviews_on_push": True,
              "require_code_owner_review": False, "require_last_push_approval": False,
              "required_review_thread_resolution": True, "allowed_merge_methods": ["squash", "rebase"]}
CHECK_DEFAULT = {"strict_required_status_checks_policy": True, "do_not_enforce_on_create": False,
                 "required_status_checks": []}
TEMPLATE = {"name": "AWF main", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "bypass_actors": [], "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": PR_DEFAULT},
                {"type": "required_status_checks", "parameters": CHECK_DEFAULT}]}


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def validate_ruleset_template(value):
    validate_value(value)
    require(canonical(value) == canonical(TEMPLATE), "Ruleset template must match the shipped default-branch baseline")
    return value


def validate_codeowner(value):
    require(isinstance(value, str) and re.fullmatch(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:/[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?)?", value)
            and "--" not in value, "Use one @handle or @organization/team token without whitespace or control characters")
    return value


def repository_name(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}", value)
            and value.split("/")[1] not in (".", ".."), "Map an explicit OWNER/REPO repository")
    return value


def branch_name(value):
    require(isinstance(value, str) and 0 < len(value) <= 255 and not value.startswith(("/", "."))
            and not value.endswith(("/", ".", ".lock")) and ".." not in value and "@{" not in value
            and "//" not in value and not any(ord(char) < 33 or ord(char) == 127 or char in "~^:?*[\\" for char in value),
            "Expected a concrete valid default-branch name")
    return value


def origin_repository(value):
    """Accept GitHub HTTPS or SSH origins without credential-bearing URLs."""
    value = value.strip()
    if value.startswith("git@github.com:"):
        name = value.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(value)
        if parsed.hostname != HOST or parsed.query or parsed.fragment or parsed.password or parsed.port:
            raise ValidationError("Origin is not a supported GitHub repository identity")
        if not ((parsed.scheme == "https" and parsed.username is None) or
                (parsed.scheme == "ssh" and parsed.username == "git")):
            raise ValidationError("Origin is not a supported GitHub repository identity")
        name = parsed.path.removeprefix("/")
    return repository_name(name.removesuffix(".git"))


def _discovery_executable(name, root):
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found).resolve()
    if path.is_relative_to(Path(root).resolve()) or path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        return None
    return str(path)


def _read_discovery_command(command, root, deadline):
    """Read bounded metadata without a shell, hooks, prompt or raw errors."""
    if time.monotonic() >= deadline:
        raise ValidationError("Metadata discovery deadline exhausted")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0", GH_PROMPT_DISABLED="1", GH_PAGER="cat")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=err, env=child_env(env), shell=False)
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > MAX_BYTES:
                    raise ValidationError("Metadata discovery exceeded its time or byte limit")
                try:
                    process.wait(timeout=min(.05, max(.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode or time.monotonic() > deadline or os.fstat(err.fileno()).st_size > MAX_BYTES:
                raise ValidationError("Metadata discovery did not complete successfully")
            out.seek(0)
            raw = out.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValidationError("Metadata discovery exceeded its byte limit")
            return raw.decode("utf-8", errors="strict").strip()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def discover_repository(root, repository=None, *, executable=None, read_command=None):
    """Read Git/GitHub metadata through this disabled-by-default adapter."""
    result = {"repository": repository, "repository_id": None, "base_branch": None, "observations": [], "warnings": []}
    root = Path(root)
    if not root.is_dir():
        return result
    deadline = time.monotonic() + METADATA_DISCOVERY_SECONDS
    executable = executable or _discovery_executable
    read_command = read_command or _read_discovery_command
    git = executable("git", root)
    if git:
        def read_git(*args):
            return read_command([git, "-c", "core.hooksPath=" + os.devnull, "--no-pager", *args], root, deadline)
        try:
            top = read_git("rev-parse", "--show-toplevel")
            if Path(top).resolve() != root.resolve():
                raise ValidationError("Target is not the Git repository root; ancestor metadata is not target identity")
            origin = origin_repository(read_git("remote", "get-url", "origin"))
            if repository is None:
                result["repository"] = origin
                result["observations"].append("github.repository: git origin")
            elif origin.casefold() != repository.casefold():
                result["warnings"].append("Explicit/preserved repository differs from origin; origin branch metadata was not used")
            if result["repository"].casefold() == origin.casefold():
                branch = read_git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
                if branch.startswith("refs/remotes/origin/"):
                    result["base_branch"] = branch.removeprefix("refs/remotes/origin/")
                    result["observations"].append("github.base_branch: local origin/HEAD (may be stale)")
        except (ValidationError, OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            result["warnings"].append("Git origin/default metadata unavailable; provide unresolved identity flags")
    gh = executable("gh", root)
    if gh and result["repository"]:
        try:
            value = loads(read_command([gh, "api", "--hostname", HOST, "--method", "GET",
                "repos/" + repository_name(result["repository"])], root, deadline))
            if not isinstance(value, dict) or not isinstance(value.get("full_name"), str) or value["full_name"].casefold() != result["repository"].casefold():
                raise ValidationError("Repository metadata identity mismatch")
            if type(value.get("id")) is not int or value["id"] <= 0 or not isinstance(value.get("default_branch"), str) or not value["default_branch"].strip():
                raise ValidationError("Repository metadata is incomplete")
            result.update(repository_id=value["id"], base_branch=value["default_branch"])
            result["observations"].append("github.repository_id/base_branch: read-only GitHub repository metadata")
        except (ValidationError, OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            result["warnings"].append("GitHub metadata unavailable or inconsistent; no numeric repository identity was inferred")
    elif result["repository"]:
        result["warnings"].append("GitHub CLI unavailable; provide --repository-id if unresolved")
    return result


def synthetic_observation(repository, default_branch, *, rules=None, rulesets=None, observed_at=None, repository_id=1):
    """Explicit offline fixture; never acquires live-enablement authority."""
    repository_name(repository)
    branch_name(default_branch)
    return {"format": FORMAT, "source": "synthetic_fixture", "host": HOST,
            "observed_at": observed_at or now_text(), "repository": repository, "default_branch": default_branch,
            "repository_response": {"id": repository_id, "full_name": repository, "default_branch": default_branch},
            "branch_pages": [{"page": 1, "rules": copy.deepcopy(rules or [])}],
            "rulesets": copy.deepcopy(rulesets or []), "complete": True}


def _rules(value):
    require(isinstance(value, list) and len(value) <= 500 and all(isinstance(rule, dict) and isinstance(rule.get("type"), str) for rule in value),
            "Invalid rule inventory")
    return value


def _baseline(rules):
    """One effective no-bypass ruleset must supply the complete baseline."""
    by_type = {}
    for rule in _rules(rules):
        require(rule["type"] not in by_type, "Duplicate rule type in one ruleset")
        by_type[rule["type"]] = rule
    if not {"deletion", "non_fast_forward", "pull_request", "required_status_checks"} <= set(by_type):
        return False, []
    pr = by_type["pull_request"].get("parameters")
    checks = by_type["required_status_checks"].get("parameters")
    require(isinstance(pr, dict) and isinstance(checks, dict), "Missing rule parameters")
    required_pr = set(PR_DEFAULT)
    require(required_pr <= set(pr) and set(CHECK_DEFAULT) <= set(checks), "Incomplete rule parameters")
    require(type(pr["required_approving_review_count"]) is int and 0 <= pr["required_approving_review_count"] <= 10
            and all(type(pr[name]) is bool for name in required_pr - {"required_approving_review_count", "allowed_merge_methods"}), "Invalid pull-request parameters")
    methods = pr["allowed_merge_methods"]
    require(isinstance(methods, list) and methods and all(isinstance(item, str) for item in methods)
            and len(methods) == len(set(methods)), "Invalid merge-method inventory")
    require(type(checks["strict_required_status_checks_policy"]) is bool and type(checks["do_not_enforce_on_create"]) is bool,
            "Invalid status-check policy")
    required_checks = checks["required_status_checks"]
    require(isinstance(required_checks, list) and len(required_checks) <= 100, "Invalid required checks")
    for check in required_checks:
        require(isinstance(check, dict) and isinstance(check.get("context"), str) and 0 < len(check["context"]) <= 255
                and (check.get("integration_id") is None or type(check["integration_id"]) is int and check["integration_id"] > 0), "Invalid check/App binding")
    adequate = (pr["dismiss_stale_reviews_on_push"] and pr["required_review_thread_resolution"]
                and set(methods) <= {"squash", "rebase"} and checks["strict_required_status_checks_policy"]
                and not checks["do_not_enforce_on_create"])
    return adequate, required_checks


def _validate_observation(value, repository, default_branch, now):
    validate_value(value)
    require(len(canonical(value)) <= MAX_TOTAL_BYTES, "Observation exceeds the aggregate byte limit")
    require(isinstance(value, dict) and set(value) == FIELDS and value["format"] == FORMAT
            and value["source"] in ("github_api", "synthetic_fixture") and value["host"] == HOST and value["complete"] is True,
            "Unsupported or incomplete observation envelope")
    repository_name(repository)
    repository_name(value["repository"])
    branch_name(value["default_branch"])
    require(value["repository"].casefold() == repository.casefold(), "Observation belongs to another repository")
    if default_branch is not None:
        require(value["default_branch"] == branch_name(default_branch), "Observed default branch differs from expected branch")
    fresh(value["observed_at"], now, MAX_AGE_SECONDS, skew_seconds=0)
    meta = value["repository_response"]
    require(isinstance(meta, dict) and type(meta.get("id")) is int and meta["id"] > 0
            and isinstance(meta.get("full_name"), str) and meta["full_name"].casefold() == repository.casefold()
            and meta.get("default_branch") == value["default_branch"], "Repository/default-branch response binding differs")
    pages = value["branch_pages"]
    require(isinstance(pages, list) and 1 <= len(pages) <= MAX_PAGES, "Missing branch-rule pagination")
    effective = []
    for index, page in enumerate(pages, 1):
        require(isinstance(page, dict) and set(page) == {"page", "rules"} and type(page["page"]) is int and page["page"] == index,
                "Invalid page sequence")
        rules = _rules(page["rules"])
        require(len(rules) == 100 if index < len(pages) else len(rules) < 100, "Branch-rule pagination is incomplete")
        effective.extend(rules)
    groups = {}
    for rule in effective:
        key = rule.get("ruleset_id")
        require(type(key) is int and key > 0 and rule.get("ruleset_source_type") in ("Repository", "Organization")
                and isinstance(rule.get("ruleset_source"), str), "Unsupported effective ruleset identity")
        groups.setdefault(key, []).append(rule)
    details = value["rulesets"]
    require(isinstance(details, list) and len(details) <= MAX_RULESETS and all(isinstance(item, dict) for item in details), "Invalid ruleset detail inventory")
    mapping = {item.get("id"): item for item in details}
    require(len(mapping) == len(details) and all(type(key) is int and key > 0 for key in mapping)
            and set(mapping) == set(groups), "Effective ruleset details are incomplete or duplicated")
    return groups, mapping


def assess_repository_rules(observation=None, *, repository=None, default_branch=None, review_app_id=None, now=None):
    report = {"repository_rules": "UNOBSERVED", "repository": repository, "default_branch": default_branch,
              "expected_review_app_id": review_app_id,
              "adoption_allowed": True, "rules_enablement_ready": False, "execution_authority": False,
              "additional_live_qualification_required": True, "enablement_preflight": "BLOCKED",
              "observation_source": None, "observation_sha256": None, "baseline_ruleset_ids": [],
              "decision_codes": ["prepare_adoption_pr", "report_unobserved_ruleset"], "warnings": [],
              "ruleset_template": ".agentic/templates/awf-main-ruleset.json",
              "next_step": "Proceed with the adoption PR; report unobserved rules and offer the default-branch template. Observe rules before live enablement."}
    if observation is None:
        report["warnings"].append("No bound repository-rules observation was supplied")
        return report
    try:
        require(review_app_id is None or type(review_app_id) is int and review_app_id > 0, "Review App ID must be a positive integer")
        groups, details = _validate_observation(observation, repository, default_branch, now or now_text())
        report.update(default_branch=observation["default_branch"], repository_id=observation["repository_response"]["id"],
                      observation_source=observation["source"], observation_sha256=sha256(canonical(observation)),
                      observed_at=observation["observed_at"])
        incomplete = False
        app_bound = False
        for identity, rules in groups.items():
            detail = details[identity]
            source_type, source = rules[0]["ruleset_source_type"], rules[0]["ruleset_source"]
            require(all(rule["ruleset_source_type"] == source_type and rule["ruleset_source"] == source for rule in rules), "Effective ruleset source differs")
            require(detail.get("source_type") == source_type and detail.get("source") == source
                    and (source_type != "Repository" or source.casefold() == repository.casefold())
                    and (source_type != "Organization" or source.casefold() == repository.split("/")[0].casefold()), "Ruleset source does not bind the target")
            require(detail.get("target") == "branch" and detail.get("enforcement") == "active", "Ruleset is not an active branch ruleset")
            effective_rules = [{key: value for key, value in rule.items() if key in ("type", "parameters")} for rule in rules]
            detail_rules = _rules(detail.get("rules"))
            require(sorted(map(canonical, effective_rules)) == sorted(map(canonical, detail_rules)), "Effective branch rules and ruleset details disagree")
            adequate, checks = _baseline(effective_rules)
            if not adequate:
                continue
            if "bypass_actors" not in detail:
                incomplete = True
                continue
            require(isinstance(detail["bypass_actors"], list), "Invalid bypass inventory")
            if detail["bypass_actors"]:
                continue
            report["baseline_ruleset_ids"].append(identity)
            app_bound |= review_app_id is not None and any(check["context"] == "awf/review" and check.get("integration_id") == review_app_id for check in checks)
        if not report["baseline_ruleset_ids"] and incomplete:
            report["warnings"].append("Applicable baseline bypass actors were not visible; empty bypass cannot be assumed")
            return report
        applied = bool(report["baseline_ruleset_ids"])
        report["repository_rules"] = "APPLIED" if applied else "MISSING"
        report["decision_codes"] = ["prepare_adoption_pr"] + ([] if applied else ["report_missing_ruleset"])
        report["rules_enablement_ready"] = applied and app_bound and observation["source"] == "github_api"
        if report["rules_enablement_ready"]:
            report["enablement_preflight"] = "RULES_READY_QUALIFICATION_STILL_REQUIRED"
            report["next_step"] = "Proceed with adoption; rules prerequisite observed. Existing adapter/loop/secrets qualification and authorization still apply before any enablement."
        else:
            report["warnings"].append("Live rules readiness needs a fresh API observation of the baseline plus awf/review bound to the expected GitHub App; an empty check list has no effective up-to-date check")
            report["next_step"] = "Proceed with the adoption PR and report rule gaps. Apply/configure the offered ruleset separately, then observe it before live enablement."
        report["limitations"] = ["The record and its source/time are trusted-host assertions, not authenticated by this helper.",
            "This checks the rules prerequisite only; it neither enables nor mechanically changes the existing adapter/reference gate.",
            "Additional project CI and existing live qualification remain required; remote rejection/mergeability was not probed."]
    except (ValidationError, OSError, KeyError, TypeError, ValueError):
        report["warnings"].append("Rules observation is malformed, stale, partial, or does not bind this repository/default branch")
    return report


def load_observation_report(path=None, expected_sha256=None, **binding):
    if path is None:
        return assess_repository_rules(**binding)
    try:
        require(isinstance(expected_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", expected_sha256), "Observation needs an external pin")
        path = Path(path).absolute()
        with Tree(path.parent) as tree:
            raw = tree.read(path.name, maximum=MAX_TOTAL_BYTES)
        require(len(raw) <= MAX_TOTAL_BYTES and sha256(raw) == expected_sha256, "Observation raw bytes differ from supplied pin")
        result = assess_repository_rules(loads(raw.decode("utf-8")), **binding)
        result["observation_file_sha256"] = sha256(raw)
        return result
    except (ValidationError, OSError, ValueError, UnicodeError):
        result = assess_repository_rules(**binding)
        result["warnings"].append("Supplied observation could not be read and verified; installation/adoption remains allowed")
        return result


def _gh_get(endpoint, deadline, *, gh="gh"):
    """Bounded GET-only child; raw stderr/provider error content is never reported."""
    remaining = deadline - time.monotonic()
    require(remaining > 0, "Rules observation deadline exhausted")
    command = [str(gh), "api", "--hostname", HOST, "--method", "GET", "-H", "Accept: application/vnd.github+json",
               "-H", "X-GitHub-Api-Version: 2026-03-10", endpoint]
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_PAGER="cat")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   env=child_env(env))
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, "Rules observation deadline exhausted")
                require(os.fstat(out.fileno()).st_size <= MAX_BYTES and os.fstat(err.fileno()).st_size <= MAX_BYTES, "Rules response exceeds byte limit")
                try:
                    process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            require(time.monotonic() <= deadline and process.returncode == 0, "Rules GET did not complete successfully")
            require(os.fstat(err.fileno()).st_size <= MAX_BYTES, "Rules stderr exceeds byte limit")
            out.seek(0)
            raw = out.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "Rules response exceeds byte limit")
            return loads(raw.decode("utf-8")), len(raw)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def observe_repository_rules(repository, *, default_branch=None, gh="gh", get=None):
    """Discover default branch with GET; no branch override creates live proof."""
    reader = get or (lambda endpoint, deadline: _gh_get(endpoint, deadline, gh=gh))
    try:
        repository_name(repository)
        deadline = time.monotonic() + MAX_SECONDS
        total = 0
        def fetch(endpoint):
            nonlocal total
            value, count = reader(endpoint, deadline)
            total += count
            require(total <= MAX_TOTAL_BYTES and time.monotonic() <= deadline, "Rules collection limit exceeded")
            return value
        meta = fetch(f"repos/{repository}")
        require(isinstance(meta, dict) and isinstance(meta.get("full_name"), str) and meta["full_name"].casefold() == repository.casefold(), "Repository metadata differs")
        branch = branch_name(meta.get("default_branch"))
        require(default_branch is None or branch == default_branch, "Observed default branch differs")
        pages, identities = [], set()
        for page in range(1, MAX_PAGES + 1):
            rules = _rules(fetch(f"repos/{repository}/rules/branches/{quote(branch, safe='')}?per_page=100&page={page}"))
            require(len(rules) <= 100, "Invalid page size")
            pages.append({"page": page, "rules": rules})
            for rule in rules:
                require(type(rule.get("ruleset_id")) is int and rule["ruleset_id"] > 0, "Missing effective ruleset identity")
                identities.add(rule["ruleset_id"])
            require(len(identities) <= MAX_RULESETS, "Too many applicable rulesets")
            if len(rules) < 100:
                break
        require(len(pages[-1]["rules"]) < 100, "Rule pagination exceeds bounded inventory")
        details = [fetch(f"repos/{repository}/rulesets/{identity}?includes_parents=true") for identity in sorted(identities)]
        current = fetch(f"repos/{repository}")
        require(isinstance(current, dict) and current.get("id") == meta.get("id")
                and isinstance(current.get("full_name"), str) and current["full_name"].casefold() == meta["full_name"].casefold()
                and current.get("default_branch") == branch, "Repository/default branch changed during rule collection")
        observation = {"format": FORMAT, "source": "github_api", "host": HOST, "observed_at": now_text(),
            "repository": repository, "default_branch": branch, "repository_response": meta,
            "branch_pages": pages, "rulesets": details, "complete": True}
        _validate_observation(observation, repository, default_branch, now_text())
        return observation
    except (ValidationError, OSError, KeyError, TypeError, ValueError, UnicodeError, subprocess.SubprocessError):
        return None  # No access, malformed response, unsupported scope or partial GET is UNOBSERVED.
