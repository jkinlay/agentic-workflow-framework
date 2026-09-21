"""Read-only adoption discovery and conservative filling of unresolved configuration.

Commands inspect Git/GitHub metadata only. Test commands are recorded, never run.
Discovery assertions are not repository-rule, merge, or live-execution authority.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from . import ValidationError, VERSION
from .canonical import loads, sha256
from .providers import github as github_provider
from .safeio import Tree

MAX_METADATA_BYTES = 1024 * 1024
validate_codeowner = github_provider.validate_codeowner
repository_name = github_provider.repository_name
origin_repository = github_provider.origin_repository
_executable = github_provider._discovery_executable
_read_command = github_provider._read_discovery_command


def unresolved(value):
    return value is None or value == "" or (isinstance(value, str) and
        ("CHANGE_ME" in value or "SET_BY_BOOTSTRAP" in value or not value.strip() or
         value == "00000000-0000-0000-0000-000000000000" or value == "0" * 40))


def project_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return str(parsed) if parsed.int and str(parsed) == value else None
    except (ValueError, TypeError, AttributeError):
        return None


def discover_repository(root, repository=None):
    """Compatibility entry point for the explicit GitHub discovery adapter."""
    return github_provider.discover_repository(root, repository, executable=_executable,
                                               read_command=_read_command)


def detect_test_command(root):
    """Inspect a bounded package manifest or Python test markers; execute nothing."""
    if root is None or not Path(root).is_dir():
        return None
    with Tree(root) as tree:
        if tree.inspect("package.json") is not None:
            try:
                value = loads(tree.read("package.json", maximum=MAX_METADATA_BYTES).decode("utf-8"))
                script = value.get("scripts", {}).get("test") if isinstance(value, dict) and isinstance(value.get("scripts", {}), dict) else None
                if isinstance(script, str) and script.strip():
                    return "npm test"
            except (ValidationError, UnicodeError):
                pass
        if any(tree.inspect(name) is not None for name in ("pyproject.toml", "pytest.ini")):
            return "pytest"
        try:
            with Tree(Path(root) / "tests"):
                return "pytest"
        except FileNotFoundError:
            pass
    return None


def operating_capacity_proposal(config, *, existing, requested=False, dry_run=False):
    """Offer a bounded local PR change; ordinary adoption never applies it."""
    proposed = copy.deepcopy(config)
    report = {"status": "NOT_APPLICABLE", "flag": "--propose-operating-capacity",
              "requested": requested, "staged_locally": False, "owner_review_required": True,
              "execution_authority": False, "changes": [], "values": [],
              "next_action": "New projects already use the shipped operating-capacity policy."}
    if not existing:
        if requested:
            raise ValidationError("--propose-operating-capacity requires existing project governance to review")
        return proposed, report
    execution = config.get("execution") if isinstance(config, dict) else None
    ceiling = execution.get("max_parallel_tickets") if isinstance(execution, dict) else None
    reviewers = execution.get("independent_reviewers", {}) if isinstance(execution, dict) else None
    if (type(ceiling) is not int or ceiling < 1 or not isinstance(reviewers, dict)
            or (reviewers and reviewers.get("allocation") != "one_per_stream")
            or ("count" in reviewers and (type(reviewers["count"]) is not int or reviewers["count"] < 1))):
        report.update(status="UNAVAILABLE", next_action="Resolve $.execution.max_parallel_tickets and $.execution.independent_reviewers before proposing a capacity migration.")
        if requested:
            raise ValidationError(report["next_action"])
        return proposed, report
    target = max(6, ceiling)
    count = reviewers.get("count", "derived: one per operating stream")
    report["values"] = [
        {"path": "$.execution.max_parallel_tickets", "current": ceiling, "proposed": target},
        {"path": "$.execution.independent_reviewers.count", "current": count,
         "proposed": "derived: one per operating stream"}]
    if target != ceiling:
        report["changes"].append({**report["values"][0], "action": "replace"})
    if "count" in reviewers:
        report["changes"].append({**report["values"][1], "action": "remove"})
    if requested and report["changes"]:
        proposed["execution"]["max_parallel_tickets"] = target
        if "count" in reviewers:
            del proposed["execution"]["independent_reviewers"]["count"]
        report.update(status="PLANNED_FOR_REVIEW" if dry_run else "STAGED_FOR_REVIEW", staged_locally=not dry_run,
            next_action="Include these exact local governance changes in the adoption PR. Keep adoption quiescent until owner review and merge; operating choices and all other limits are preserved.")
    elif report["changes"]:
        report.update(status="OFFERED", next_action="Offer these changes in the adoption PR. With explicit user direction, rerun bootstrap with --propose-operating-capacity on the isolated adoption branch; otherwise keep current governance.")
    else:
        report.update(status="NOT_NEEDED", next_action="The existing ceiling already permits six and reviewer count is derived; no capacity governance change is needed.")
    report["adoption_pr_section"] = "\n".join([
        "### Operating-capacity governance proposal", "",
        "| Governance path | Current | Proposed |", "| --- | --- | --- |",
        *[f"| `{row['path']}` | {row['current']} | {row['proposed']} |" for row in report["values"]],
        "", "Status: " + report["status"] + ". " + report["next_action"],
        "This local proposal neither launches agents nor enables automation or authorizes merge."])
    return proposed, report


def prepare_config(template, *, existing=None, receipt_project_id=None, project_root=None,
                   overrides=None, codeowner="@maintainer", discover=True):
    validate_codeowner(codeowner)
    overrides = overrides or {}
    config = copy.deepcopy(existing if existing is not None else template)
    sections = ("project", "github", "jira", "template", "validation", "merge_gate", "specialist_reviews")
    shape_ok = (isinstance(config, dict) and all(isinstance(config.get(key), dict) for key in sections) and
                all(isinstance(rule, dict) for rule in config["specialist_reviews"].values()) and
                isinstance(config["validation"].get("commands", []), list) and
                isinstance(config["validation"].get("required_ci_checks", []), list) and
                all(isinstance(check, dict) for check in config["validation"].get("required_ci_checks", [])))
    if not shape_ok:
        # Preserve malformed project policy for the shared validator's complete
        # residue; guessing missing policy sections could broaden authority.
        current_id = config.get("project", {}).get("id") if isinstance(config, dict) and isinstance(config.get("project"), dict) else None
        saved_id = project_uuid(current_id)
        if receipt_project_id is not None and (not project_uuid(receipt_project_id) or (saved_id and receipt_project_id != saved_id)):
            raise ValidationError("Configuration project UUID disagrees with the persisted installation receipt")
        return {"config": config, "project_id": saved_id or receipt_project_id or str(uuid.uuid4()),
                "warnings": ["Preserved incomplete or malformed existing policy; resolve the shared validator's exact paths before configuration can be accepted"],
                "observations": [], "test_commands_executed": False, "execution_authority": False}
    project, github, jira = config["project"], config["github"], config["jira"]
    warnings, observations = [], []
    def fill(section, key, value, flag):
        if value is None:
            return
        if unresolved(section.get(key)):
            section[key] = value
        elif section[key] != value and flag in overrides:
            warnings.append(f"Preserved existing {flag}; edit reviewed PROJECT_CONFIG.yaml to change an established choice")
    if existing is None:
        # Source examples never assert a target's numeric identity/default branch.
        github.update(repository_id=None, base_branch=None)
        project["id"] = None
        config["merge_gate"]["trusted_owner_ids"] = []
    elif unresolved(github.get("repository")):
        # An unbound source/example numeric ID cannot identify a target repo.
        github.update(repository_id=None, base_branch=None)
    saved_id = project_uuid(project.get("id"))
    if receipt_project_id is not None:
        if not project_uuid(receipt_project_id) or (saved_id and saved_id != receipt_project_id):
            raise ValidationError("Configuration project UUID disagrees with the persisted installation receipt")
        saved_id = receipt_project_id
    if not saved_id and not unresolved(project.get("id")):
        raise ValidationError("Existing project UUID is invalid; reconcile it explicitly before installation")
    project["id"] = saved_id or str(uuid.uuid4())
    config["template"]["expected_workflow_version"] = VERSION
    if "repository" in overrides:
        fill(github, "repository", repository_name(overrides["repository"]), "repository")
    repository = None if unresolved(github.get("repository")) else repository_name(github["repository"])
    needs_metadata = any(unresolved(github.get(key)) and key not in overrides
                         for key in ("repository", "repository_id", "base_branch"))
    metadata = discover_repository(project_root, repository) if discover and needs_metadata and project_root is not None else {}
    warnings.extend(metadata.get("warnings", []))
    observations.extend(metadata.get("observations", []))
    fill(github, "repository", metadata.get("repository"), "repository")
    if not unresolved(github.get("repository_id")) and metadata.get("repository_id") is not None and github["repository_id"] != metadata["repository_id"]:
        raise ValidationError("Preserved repository_id conflicts with observed repository metadata; reconcile repository identity before installation")
    if not unresolved(github.get("base_branch")) and metadata.get("base_branch") is not None and github["base_branch"] != metadata["base_branch"]:
        warnings.append("Preserved configured base_branch differs from observed default branch; review the target choice before adoption merge")
    for key in ("repository_id", "base_branch"):
        supplied = overrides.get(key)
        if supplied is not None and metadata.get(key) is not None and supplied != metadata[key]:
            raise ValidationError(f"Explicit {key} conflicts with observed repository metadata; reconcile before installation")
        fill(github, key, overrides.get(key, metadata.get(key)), key)
    repository = None if unresolved(github.get("repository")) else github["repository"]
    name = repository.split("/")[1] if repository else None
    for key in ("name", "short_name"):
        fill(project, key, overrides.get(key, name), key)
    for rule in config["specialist_reviews"].values():
        fill(rule, "reviewer_identity", codeowner, "codeowner")
    if existing is None or (unresolved(jira.get("site")) and unresolved(jira.get("project_key"))):
        if "jira_site" not in overrides and "jira_key" not in overrides:
            jira.update(enabled=False, site=None, project_key=None)
        else:
            jira.update(enabled=True, site=overrides.get("jira_site"), project_key=overrides.get("jira_key"))
    else:
        for key, flag in (("site", "jira_site"), ("project_key", "jira_key")):
            fill(jira, key, overrides.get(flag), flag)
    validation = config["validation"]
    if existing is None or any(unresolved(item.get("workflow_sha")) for item in validation.get("required_ci_checks", [])):
        if existing is None:
            validation["required_ci_checks"] = []
        else:
            warnings.append("Preserved unresolved existing CI check definitions; configure or explicitly remove them in PROJECT_CONFIG.yaml")
    if not validation.get("commands") or any(unresolved(command) for command in validation["commands"]):
        command = overrides["test_command"] if "test_command" in overrides else detect_test_command(project_root)
        validation["commands"] = [command] if command else []
    elif "test_command" in overrides and validation["commands"] != [overrides["test_command"]]:
        warnings.append("Preserved existing validation commands; edit reviewed PROJECT_CONFIG.yaml to change them")
    return {"config": config, "project_id": project["id"], "warnings": warnings,
            "observations": observations, "test_commands_executed": False, "execution_authority": False}


def _post_command(command, root, timeout=60):
    """Execute a verified installed CLI once, with bounded output and child time."""
    result = {"command": command, "exit_code": None, "output": None, "diagnostic": None}
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    process = None
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            deadline = time.monotonic() + timeout
            process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=err, env=env, shell=False)
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > MAX_METADATA_BYTES:
                        raise ValidationError("Post-install check exceeded its time or byte limit")
                    try:
                        process.wait(timeout=min(.05, max(.001, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                result["exit_code"] = process.returncode
                if time.monotonic() > deadline or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > MAX_METADATA_BYTES:
                    raise ValidationError("Post-install check exceeded its time or byte limit")
                out.seek(0)
                raw = out.read(MAX_METADATA_BYTES + 1)
                result["output_stream"] = "stdout"
                if not raw.strip() and process.returncode:
                    err.seek(0)
                    raw = err.read(MAX_METADATA_BYTES + 1)
                    result["output_stream"] = "stderr"
                value = loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValidationError("Post-install CLI output is not an object")
                result["output"] = value
                if process.returncode:
                    result["diagnostic"] = "Installed CLI returned a nonzero exit status"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
    except (ValidationError, OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        result["diagnostic"] = "Post-install CLI did not return bounded valid JSON; inspect the installed runtime/dependencies and rerun the recorded command"
    return result


def post_install_checks(destination, installation):
    root = Path(destination).resolve()
    script = str(root / ".agentic/scripts/workflow.py")
    commands = [[sys.executable, "-B", "-I", script, action]
                for action in ("verify-installation", "validate-config")]
    try:
        _verify_import_surface(root, installation["source_manifest_sha256"])
    except (ValidationError, OSError, ValueError, KeyError, TypeError):
        checks = [{"command": command, "exit_code": None, "output": None, "execution_status": "NOT_RUN",
                   "diagnostic": "Trusted preflight rejected installed runtime bytes/import surfaces; preserve unexpected files and reconcile before executing installed code"}
                  for command in commands]
    else:
        checks = [_post_command(command, root) for command in commands]
    verification, validation = checks
    output = verification["output"] or {}
    integrity = (verification["exit_code"] == 0 and verification["diagnostic"] is None and
                 output.get("integrity_valid") is True and
                 output.get("source_manifest_sha256") == installation["source_manifest_sha256"])
    inspection = installation["configuration"]
    config_output = validation["output"] or {}
    configured = (integrity and validation["exit_code"] == 0 and validation["diagnostic"] is None and
                  config_output.get("status") == "ACCEPTED" and inspection["status"] == "ACCEPTED" and
                  config_output.get("policy_sha256") == inspection["policy_sha256"])
    operating = config_output.get("operating") or {}
    initialized_operating = installation.get("operating") or {}
    configured = (configured and operating.get("status") == "ACCEPTED" and
                  initialized_operating.get("status") == "ACCEPTED" and
                  isinstance(operating.get("hash"), str) and
                  operating["hash"] == initialized_operating.get("hash"))
    actual_residue = config_output.get("unresolved")
    observed_configuration = (config_output.get("status") in {"ACCEPTED", "REJECTED"} and
                              isinstance(actual_residue, list) and all(isinstance(item, dict) and
                                  isinstance(item.get("path"), str) and isinstance(item.get("reason"), str)
                                  for item in actual_residue))
    configured = configured and observed_configuration and not actual_residue
    status = "CONFIGURED" if configured else "INSTALLED_UNCONFIGURED" if integrity else "INSTALLATION_VERIFICATION_FAILED"
    if observed_configuration:
        configuration = {**config_output, "observation_source": "installed validate-config subprocess",
                         "pre_install_inspection_status": inspection["status"]}
    else:
        configuration = {"status": "UNOBSERVED", "policy_sha256": None, "unresolved": [],
                         "ci_gate": "NOT_CONFIGURED", "warnings": [],
                         "observation_source": "installed validate-config output unavailable or incomplete"}
    if configured:
        next_action = "Show workflow.py operating show and offer keep defaults, review Epics and recommend, or custom; include the table in the adoption PR, then verify its accepted default checkout after merge"
    elif not integrity:
        next_action = "Resolve the failed installation verification and rerun both recorded checks; do not claim installation or activation"
    elif observed_configuration and config_output["status"] == "REJECTED" and actual_residue:
        next_action = "Resolve the actual installed configuration paths: " + "; ".join(
            item["path"] + " (" + str(item.get("flag", "edit PROJECT_CONFIG.yaml")) + ")" for item in actual_residue) + "; rerun both recorded checks"
    elif observed_configuration and config_output["status"] == "ACCEPTED":
        next_action = "Installed configuration differs from the inspected installation plan; replan against the current configuration and rerun both recorded checks"
    else:
        next_action = "Correct the installed runtime/dependencies or unreadable configuration using the recorded command diagnostics, then rerun both checks"
    from .host_preflight import preflight, render_markdown
    host = preflight(destination)
    if host["next_action"]:
        next_action += "; host preflight WARN: " + host["next_action"]
    return {"status": status, "transaction_status": installation["status"], "installed": integrity,
            "post_install_checks": checks, "active": False, "configuration": configuration,
            "pre_install_configuration": inspection, "host_preflight": host,
            "adoption_pr_host_preflight_section": render_markdown(host), "next_action": next_action}


def _verify_import_surface(root, expected_digest):
    """Use trusted release code to reject target import shadows before execution.

    Isolated Python excludes the target script/current directory and PYTHONPATH;
    the workflow intentionally inserts .agentic/lib, whose complete inventory is
    therefore pinned here. Unexpected caches/packages/extensions are not deleted.
    """
    from .installer import INSTALLED, verify_installed
    if verify_installed(root) != expected_digest:
        raise ValidationError("Installed source identity differs from bootstrap")
    with Tree(root) as tree:
        receipt = loads(tree.read(INSTALLED).decode("utf-8"))
        source_raw = receipt.get("source_manifest_json")
        if not isinstance(source_raw, str) or sha256(source_raw.encode("utf-8")) != expected_digest:
            raise ValidationError("Installed import inventory requires the pinned source manifest")
        manifest = loads(source_raw)
        for prefix in (".agentic/lib/", ".agentic/scripts/"):
            expected = {path.removeprefix(prefix): digest for path, digest in manifest["files"].items() if path.startswith(prefix)}
            with Tree(Path(root) / prefix) as runtime:
                if set(runtime.file_list()) != set(expected):
                    raise ValidationError("Unexpected installed runtime import files")
                for path, digest in expected.items():
                    if sha256(runtime.read(path)) != digest:
                        raise ValidationError("Installed runtime import file differs from source")
