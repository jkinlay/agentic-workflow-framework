"""Configuration-bound repository-rules decision evidence; never provider mutation."""
from __future__ import annotations

from . import ValidationError
from .canonical import canonical, sha256, timestamp
from .configuration import inspect_config
from .providers import github


OWNER_OUTCOMES = frozenset({"PENDING", "DECLINED", "APPLIED"})


def _configuration(config, workflow, contracts):
    report = inspect_config(config, workflow, contracts)
    if report["status"] != "ACCEPTED":
        details = "; ".join(item["path"] + ": " + item["reason"]
                            for item in report["unresolved"])
        raise ValidationError("Configuration is not accepted: " + details)
    github_config = config.get("github", {})
    if "merge_method" not in github_config:
        raise ValidationError("$.github.merge_method: required value is missing")
    method = github.merge_method_name(github_config["merge_method"])
    return report, github_config, method


def _consequence(state):
    values = {
        "APPLIED": ("PUBLICATION_GOVERNANCE_OBSERVED",
                    "A configuration-compatible default-branch baseline is observed; other publication and live qualification gates still apply."),
        "MISSING": ("PUBLICATION_GOVERNANCE_MISSING",
                    "Default-branch publication lacks the configuration-compatible AWF baseline and remains operationally blocked."),
        "UNOBSERVED": ("PUBLICATION_GOVERNANCE_UNOBSERVED",
                       "AWF cannot establish the rules that govern publication; observe the bound repository/default branch before activation."),
    }
    code, message = values[state]
    return {"code": code, "message": message}


def _owner_action(repository, proposal, proposal_digest, rules_state, outcome):
    required = rules_state == "MISSING"
    state = (outcome if required else
             "NOT_AVAILABLE" if rules_state == "UNOBSERVED" else "NOT_REQUIRED")
    approval = (
        "Observe the bound repository/default branch before deciding whether ruleset application is required."
        if rules_state == "UNOBSERVED" else
        "No provider action is required because a compatible baseline is already observed."
        if rules_state == "APPLIED" else
        "Approve and apply exactly the SHA-256-bound proposed ruleset through an authenticated provider owner/admin session; "
        "then supply a fresh read-only observation."
    )
    return {
        "required": required,
        "state": state,
        "actor": "repository_owner_or_administrator",
        "approval": approval,
        "provider_request": {
            "method": "POST",
            "endpoint": "repos/" + repository + "/rulesets",
            "body_sha256": proposal_digest,
            "body": proposal,
        },
    }


def _observation_issue(observation, repository, repository_id, default_branch, now, *, post=False):
    prefix = "POST_ACTION_OBSERVATION_" if post else "RULES_OBSERVATION_"
    if not isinstance(observation, dict):
        return prefix + "INVALID"
    observed_repository = observation.get("repository")
    if not isinstance(observed_repository, str) or observed_repository.casefold() != repository.casefold():
        return prefix + "WRONG_REPOSITORY"
    response = observation.get("repository_response")
    if (not isinstance(response, dict) or type(response.get("id")) is not int
            or response["id"] != repository_id):
        return prefix + "WRONG_REPOSITORY_ID"
    if observation.get("default_branch") != default_branch:
        return prefix + "WRONG_DEFAULT_BRANCH"
    try:
        age = (timestamp(now) - timestamp(observation["observed_at"])).total_seconds()
    except (KeyError, TypeError, ValidationError):
        return prefix + "INVALID"
    if age < 0 or age > github.MAX_AGE_SECONDS:
        return prefix + "STALE_OR_FUTURE"
    return None


def activation_rules_decision(config, workflow, contracts, *, before_observation,
                              post_observation=None, owner_outcome="PENDING",
                              review_app_id=None, now):
    """Return AC46/AC58 decision evidence without making a provider request."""
    config_report, github_config, merge_method = _configuration(config, workflow, contracts)
    if owner_outcome not in OWNER_OUTCOMES:
        raise ValidationError("owner_outcome must be PENDING, DECLINED, or APPLIED")
    repository = github.repository_name(github_config["repository"])
    default_branch = github.branch_name(github_config["base_branch"])
    timestamp(now)
    proposal = github.ruleset_for_config(config, configuration_status=config_report["status"])
    proposal_digest = sha256(canonical(proposal))
    observed = github.assess_repository_rules(
        before_observation, repository=repository, default_branch=default_branch,
        review_app_id=review_app_id, merge_method=merge_method,
        expected_repository_id=github_config["repository_id"], now=now)
    state = observed["repository_rules"]
    consequence_state = ("UNOBSERVED" if state == "APPLIED"
                         and observed.get("observation_source") != "github_api" else state)
    blockers = []
    post = {"required": state == "MISSING", "state": "UNOBSERVED",
            "observation_sha256": None, "observed_at": None,
            "configuration_compatible": None}
    final_state = state

    if state == "UNOBSERVED":
        blockers.append("RULES_UNOBSERVED")
        blockers.append(_observation_issue(
            before_observation, repository, github_config["repository_id"], default_branch,
            now) or "RULES_OBSERVATION_INVALID")
    elif state == "APPLIED" and observed.get("observation_source") != "github_api":
        blockers.append("RULES_OBSERVATION_NOT_LIVE")
    elif state == "MISSING":
        blockers.append("RULES_MISSING")
        if owner_outcome == "PENDING":
            blockers.append("OWNER_APPROVAL_PENDING")
        elif owner_outcome == "DECLINED":
            blockers.append("OWNER_DECLINED")
        elif post_observation is None:
            blockers.append("POST_ACTION_OBSERVATION_MISSING")
        else:
            assessed_post = github.assess_repository_rules(
                post_observation, repository=repository, default_branch=default_branch,
                review_app_id=review_app_id, merge_method=merge_method,
                expected_repository_id=github_config["repository_id"], now=now)
            post.update({
                "state": assessed_post["repository_rules"],
                "observation_sha256": assessed_post.get("observation_sha256"),
                "observed_at": assessed_post.get("observed_at"),
                "configuration_compatible": assessed_post.get("configuration_compatible"),
            })
            issue = _observation_issue(
                post_observation, repository, github_config["repository_id"],
                default_branch, now, post=True)
            if issue is not None:
                blockers.append(issue)
            elif assessed_post.get("observation_source") != "github_api":
                blockers.append("POST_ACTION_OBSERVATION_NOT_LIVE")
            else:
                try:
                    fresh_after_action = timestamp(post_observation["observed_at"]) > timestamp(before_observation["observed_at"])
                except (KeyError, TypeError, ValidationError):
                    fresh_after_action = False
                if not fresh_after_action:
                    blockers.append("POST_ACTION_OBSERVATION_NOT_FRESH")
                elif assessed_post["repository_rules"] != "APPLIED" or assessed_post.get("configuration_compatible") is not True:
                    blockers.append("RULES_MISSING_AFTER_ACTION")
                else:
                    blockers.clear()
                    final_state = "APPLIED"
    else:
        post = {"required": False, "state": "NOT_APPLICABLE",
                "observation_sha256": None, "observed_at": None,
                "configuration_compatible": True}

    blocked = bool(blockers)
    return {
        "format": "awf-rules-activation-decision-1",
        "status": "BLOCKED" if blocked else "RULES_OBSERVED",
        "configuration": {
            "status": "ACCEPTED",
            "policy_sha256": config_report["policy_sha256"],
            "merge_method": merge_method,
        },
        "binding": {
            "repository": repository,
            "repository_id": github_config["repository_id"],
            "default_branch": default_branch,
        },
        "observed_rules_state": state,
        "observed_rules": observed,
        "publication_consequence": _consequence(consequence_state),
        "proposed_ruleset": proposal,
        "proposed_ruleset_sha256": proposal_digest,
        "owner_approval_action": _owner_action(
            repository, proposal, proposal_digest, state, owner_outcome),
        "post_action_observation": post,
        "final_rules_state": final_state,
        "operational_blocker": blocked,
        "blocker_codes": blockers,
        "provider_mutation_performed": False,
        "execution_authority": False,
    }
