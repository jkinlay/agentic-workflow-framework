#!/usr/bin/env python3
"""AC42 end-to-end upgrade gate harness (AWF-11).

Upgrades a recorded historical installation fixture (default 1.9.1, as AC42
specifies) to this 1.9.3 source in a fresh Git repository, then records one
PASS / FAIL / NOT_COVERED row per AC42 clause in an evidence JSON file.

The adoption-PR merge and the GitHub observations are a recorded synthetic
fixture built from the local Git objects; nothing contacts a provider, writes
outside --work, or uses credentials. Exit 0 only when every required row
passes. The real gate run is on Windows via scripts/windows/Invoke-AC42.ps1;
on other hosts the same harness is a smoke test.
"""
from __future__ import annotations
import argparse
import base64
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic import VERSION  # noqa: E402
from agentic.child_process import child_env  # noqa: E402

REQUIRED = ("fixture", "upgrade", "verify_installation", "validate_config", "self_test",
            "adoption_merge", "active_single_status_run", "handoff_snapshot",
            "project_values_preserved", "external_data_unchanged", "autocrlf_fresh_clone",
            "history_sensitive_value_blocked", "capability_matrix")
SENSITIVE = "C:\\Users\\synthetic-ac42\\raw_estate\\positions.csv"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def tree_digest(root):
    return {path.relative_to(root).as_posix(): digest(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file()}


def run(command, cwd, timeout=1800, extra=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0", **(extra or {}))
    started = time.monotonic()
    done = subprocess.run([str(part) for part in command], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=child_env(env), stdin=subprocess.DEVNULL)
    return {"command": [str(part) for part in command], "exit_code": done.returncode,
            "seconds": round(time.monotonic() - started, 1),
            "stdout_tail": done.stdout[-2000:], "stderr_tail": done.stderr[-2000:]}


class Gate:
    def __init__(self):
        self.rows = {}

    def record(self, name, ok, evidence, covered=True):
        self.rows[name] = {"result": ("PASS" if ok else "FAIL") if covered else "NOT_COVERED",
                           "evidence": evidence}
        print(f"[AC42] {name}: {self.rows[name]['result']}", file=sys.stderr, flush=True)
        return ok


def git(repo, *arguments):
    done = run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
                "-c", "user.name=AWF AC42 Fixture", "-c", "user.email=ac42@example.invalid",
                "-C", repo, *arguments], repo, timeout=300)
    if done["exit_code"] != 0:
        raise RuntimeError("git " + " ".join(arguments) + ": " + done["stderr_tail"])
    return done["stdout_tail"].strip()


def provider_fixture(project, head, upgrade_commit, receipt, installed_files, full_name, repository_id, branch):
    """Recorded GitHub answers for a merged adoption PR #7 whose merge commit is head."""
    from agentic.installer import CONFIG, INSTALLED, PROVENANCE
    from agentic.providers import github_status as status
    from agentic.installer import json_bytes
    repository = {"id": repository_id, "node_id": "R_ac42", "full_name": full_name}
    base = "repos/" + full_name
    entry = lambda name, raw: {"path": name, "type": "blob", "mode": "100644", "sha": status.blob_sha(raw)}
    accepted = set(receipt["immutable_files"]) | {CONFIG, INSTALLED, PROVENANCE, ".github/CODEOWNERS"}
    groups = sorted({name.split("/", 1)[0] for name in accepted if "/" in name})
    group_shas = {name: format(index + 11, "x")[-1] * 40 for index, name in enumerate(groups)}
    root_entries = [entry(name, installed_files[name]) for name in sorted(accepted) if "/" not in name]
    root_entries += [{"path": name, "type": "tree", "mode": "040000", "sha": group_shas[name]} for name in groups]
    api = {
        base: dict(repository, default_branch=branch),
        base + "/branches/" + branch: {"name": branch, "commit": {"sha": head}},
        base + "/pulls/7": {"number": 7, "node_id": "PR_ac42", "state": "closed", "merged": True,
                            "merged_at": "2026-10-07T12:00:00+00:00", "merge_commit_sha": None,
                            "base": {"ref": branch, "repo": dict(repository)},
                            "head": {"ref": "awf/upgrade-1.9.3", "sha": "d" * 40, "repo": dict(repository)}},
        base + "/pulls/7/files?per_page=50&page=1": [
            {"filename": INSTALLED, "status": "modified", "sha": status.blob_sha(installed_files[INSTALLED])}],
        base + f"/commits/{upgrade_commit}/pulls?per_page=100": [
            {"number": 7, "merged_at": "2026-10-07T12:00:00Z", "base": {"ref": branch, "repo": {"id": repository_id}}}],
        base + f"/commits/{head}/pulls?per_page=100": [
            {"number": 7, "merged_at": "2026-10-07T12:00:00Z", "base": {"ref": branch, "repo": {"id": repository_id}}}],
        base + f"/contents/{INSTALLED}?ref={head}": {
            "type": "file", "path": INSTALLED, "sha": status.blob_sha(installed_files[INSTALLED]),
            "encoding": "base64", "content": base64.b64encode(installed_files[INSTALLED]).decode()},
        base + f"/git/commits/{head}": {"sha": head, "tree": {"sha": "a" * 40}},
        base + "/git/trees/" + "a" * 40: {"truncated": False, "tree": root_entries},
    }
    for group in groups:
        prefix = group + "/"
        api[base + "/git/trees/" + group_shas[group] + "?recursive=1"] = {
            "truncated": False, "tree": [entry(name.removeprefix(prefix), installed_files[name])
                                         for name in sorted(accepted) if name.startswith(prefix)]}
    graph_repo = {"id": "R_ac42", "databaseId": repository_id, "nameWithOwner": full_name}
    graphql = {"data": {"repository": dict(graph_repo,
        defaultBranchRef={"name": branch, "target": {"oid": head}},
        pullRequest={"id": "PR_ac42", "number": 7, "baseRefName": branch, "headRefName": "awf/upgrade-1.9.3",
                     "headRefOid": "d" * 40, "headRepository": graph_repo, "state": "MERGED", "merged": True,
                     "mergedAt": "2026-10-07T12:00:00Z", "mergeCommit": {"oid": head, "repository": graph_repo}})}}
    requests = []

    def read(endpoint, deadline, gh, pr_file_metadata=False):
        requests.append(endpoint)
        if endpoint not in api:
            raise KeyError("unrecorded provider endpoint: " + endpoint)
        value = api[endpoint]
        return copy.deepcopy(value), len(json_bytes(value))

    def read_graphql(query, variables, deadline, gh):
        requests.append("graphql")
        return copy.deepcopy(graphql), len(json_bytes(graphql))
    return read, read_graphql, requests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-version", default="1.9.1", choices=("1.8.3", "1.8.9", "1.9.1", "1.9.2"))
    parser.add_argument("--work", type=Path, help="Empty scratch directory outside this repository (default: new temp dir)")
    parser.add_argument("--evidence", type=Path, required=True, help="Evidence JSON to create; never overwritten")
    parser.add_argument("--runtime-wheelhouse", type=Path,
                        help="Upgrade through scripts/bootstrap_project.py with this verified offline wheelhouse "
                             "(the real Windows gate); omitted = in-process installer (smoke run)")
    parser.add_argument("--skip-self-test", action="store_true", help="Smoke runs only; the gate requires the self-test")
    args = parser.parse_args(argv)
    if args.evidence.exists():
        parser.error("--evidence already exists")
    work = (args.work or Path(tempfile.mkdtemp(prefix="awf-ac42-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()) or work.is_relative_to(ROOT.resolve()):
        parser.error("--work must be an empty directory outside the AWF source tree")
    from upgrade_fixtures import materialize, verify_materialized
    from agentic.canonical import load
    from agentic.installer import CONFIG, INSTALLED, install
    from agentic.handoff import build_snapshot
    from agentic.providers import github_status as status
    gate, started = Gate(), time.monotonic()
    pin = digest((ROOT / "MANIFEST.json").read_bytes())
    report = {"format": "awf-ac42-evidence-1", "awf_version": VERSION, "from_version": args.from_version,
              "source_manifest_sha256": pin, "host": {"system": platform.system(), "release": platform.release(),
              "python": sys.version.split()[0], "git": run(["git", "--version"], work)["stdout_tail"].strip()},
              "mode": "bootstrap_cli" if args.runtime_wheelhouse else "in_process_installer",
              "gate_eligible": platform.system() == "Windows" and bool(args.runtime_wheelhouse) and not args.skip_self_test,
              "provider_observations": "recorded synthetic fixture (no network)", "execution_authority": False}
    project, external = work / "project", work / "raw_estate"
    try:
        evidence = materialize(args.from_version, project)
        verify_materialized(args.from_version, project)
        external.mkdir()
        (external / "positions.csv").write_bytes(b"synthetic,read-only\r\n1,2\r\n")
        external_before = tree_digest(external)
        # Documented adoption step: the owner merges the scoped installed-path
        # attributes into the project's root .gitattributes (the installer never does).
        attributes = (ROOT / ".agentic/templates/installed.gitattributes").read_bytes()
        if not (project / ".gitattributes").exists():
            (project / ".gitattributes").write_bytes(attributes)
        github = load(project / CONFIG)["github"]
        full_name, repository_id, branch = github["repository"], github["repository_id"], github["base_branch"]
        git(project, "init", "-q", "-b", branch)
        git(project, "remote", "add", "origin", f"https://github.com/{full_name}.git")
        git(project, "config", "core.autocrlf", "true")
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", f"AWF {args.from_version} installation with operating history")
        base_commit = git(project, "rev-parse", "HEAD")
        owner_before = {name: (project / name).read_bytes() for name in evidence["owner_files"]}
        gate.record("fixture", True, {"files": len(tree_digest(project)), "base_commit": base_commit,
                    "owner_files": sorted(owner_before), "core.autocrlf": git(project, "config", "core.autocrlf"),
                    "codeowners_project_owned": ".github/CODEOWNERS" in owner_before})

        git(project, "checkout", "-q", "-b", "awf/upgrade-1.9.3")
        if args.runtime_wheelhouse:
            step = run([sys.executable, "-B", ROOT / "scripts/bootstrap_project.py", "--mode", "upgrade",
                        "--dest", project, "--expected-manifest-sha256", pin,
                        "--runtime-wheelhouse", args.runtime_wheelhouse.resolve()], ROOT)
            ok = step["exit_code"] == 0
        else:
            result = install(ROOT, project, pin, mode="upgrade", configure=True, discover=False)
            step, ok = {"status": result.get("status"), "installed": result.get("installed")}, True
        gate.record("upgrade", ok, step)
        python = project / ".agentic/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python = python if python.is_file() else Path(sys.executable)
        workflow = project / ".agentic/scripts/workflow.py"
        for name, command in (("verify_installation", "verify-installation"), ("validate_config", "validate-config")):
            step = run([python, "-B", workflow, "--root", project, command], project)
            gate.record(name, step["exit_code"] == 0, step)
        if args.skip_self_test:
            gate.record("self_test", False, "skipped (--skip-self-test)", covered=False)
        else:
            step = run([python, "-B", project / ".agentic/scripts/self_test.py", "--checks-only"], project, timeout=5400)
            gate.record("self_test", step["exit_code"] == 0, step)

        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Upgrade AWF to " + VERSION)
        upgrade_commit = git(project, "rev-parse", "HEAD")
        git(project, "checkout", "-q", branch)
        git(project, "merge", "-q", "--no-ff", "-m", f"Merge pull request #7 from {full_name.split('/')[0]}/awf/upgrade-1.9.3",
            "awf/upgrade-1.9.3")
        head = git(project, "rev-parse", "HEAD")
        gate.record("adoption_merge", True, {"merge_commit": head, "pr": 7, "fixture": "local merge, recorded provider answers"})

        receipt = load(project / INSTALLED)
        installed_files = {p.relative_to(project).as_posix(): p.read_bytes()
                           for p in project.rglob("*") if p.is_file() and ".git" not in p.parts}
        read, read_graphql, requests = provider_fixture(project, head, upgrade_commit, receipt, installed_files,
                                                        full_name, repository_id, branch)
        release = work / "release-source"
        shutil.copytree(ROOT, release, ignore=shutil.ignore_patterns(".git", ".tmp-tests", "__pycache__", "*.pyc"))
        gitexe = shutil.which("git")
        with patch.object(status, "host_executable", side_effect=lambda name, root: gitexe if name == "git" else sys.executable), \
                patch.object(status, "_gh_get", side_effect=read), \
                patch.object(status, "_gh_get_pr_files", side_effect=read), \
                patch.object(status, "_gh_graphql", side_effect=read_graphql):
            observed = status.project_status(project, adoption_pr=7, release_source=release,
                                             expected_manifest_sha256=pin)
        blockers = [c for c in observed["checks"] if c["state"] not in ("PASS", "N_A", "SKIP")]
        gate.record("active_single_status_run", observed["project_state"] == "ACTIVE",
                    {"project_state": observed["project_state"], "line": observed.get("line"),
                     "blockers": blockers, "provider_requests": requests})
        matrix = observed.get("capabilities")
        gate.record("capability_matrix", isinstance(matrix, dict) and bool(matrix), matrix)
        snapshot = build_snapshot(project, status=observed)
        gate.record("handoff_snapshot", snapshot["awf"]["project_state"]["value"] == observed["project_state"]
                    and snapshot["repository"]["head"]["value"] == head, snapshot)

        changed = {}
        for name, raw in owner_before.items():
            after = (project / name).read_bytes() if (project / name).is_file() else None
            if after != raw:
                changed[name] = list(difflib.unified_diff(raw.decode("utf-8", "replace").splitlines(),
                    (after or b"").decode("utf-8", "replace").splitlines(), lineterm="", n=0))[2:40]
        # AWF-managed records and the documented append-only .gitignore block.
        allowed = {CONFIG, INSTALLED, ".agentic/workflow-version.yaml", ".gitignore"}
        ignore_after = (project / ".gitignore").read_bytes() if (project / ".gitignore").is_file() else b""
        ignore_ok = ".gitignore" not in owner_before or ignore_after.startswith(owner_before[".gitignore"])
        config_lines = [line for line in changed.get(CONFIG, []) if line[:1] in "+-"]
        config_ok = all("expected_workflow_version" in line or line.strip("+- ").startswith(
            ('"cloud_id"', '"provider_project_id"', '"controller_actor_id"')) for line in config_lines)
        gate.record("project_values_preserved", set(changed) <= allowed and config_ok and ignore_ok,
                    {"changed": changed, "gitignore_prefix_preserved": ignore_ok})
        gate.record("external_data_unchanged", tree_digest(external) == external_before, external_before)

        clone = work / "fresh-clone"
        run(["git", "-c", "core.autocrlf=true", "clone", "-q", project, clone], work)
        step = run([python, "-B", clone / ".agentic/scripts/workflow.py", "--root", clone, "verify-installation"], clone)
        gate.record("autocrlf_fresh_clone", step["exit_code"] == 0, step)

        git(project, "checkout", "-q", "-b", "awf/ac43-history")
        (project / "notes-ac43.md").write_text("estate: " + SENSITIVE + "\n", encoding="utf-8")
        git(project, "add", "notes-ac43.md")
        git(project, "commit", "-q", "-m", "Add estate note")
        (project / "notes-ac43.md").unlink()
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Remove estate note")
        step = run([python, "-B", workflow, "--root", project, "publication-scan", "--base", branch,
                    "--head", "awf/ac43-history", "--json"], project)
        gate.record("history_sensitive_value_blocked", step["exit_code"] != 0 and "notes-ac43.md" in step["stdout_tail"], step)
        gate.record("read_only_external_resource_admission", False,
                    "AC48-AC51 per-stream admission of a declared read-only resource is not exercised by this harness",
                    covered=False)
    except Exception as exc:  # recorded, never hidden
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["rows"] = gate.rows
    report["seconds"] = round(time.monotonic() - started, 1)
    report["result"] = "PASS" if all(gate.rows.get(n, {}).get("result") == "PASS" for n in REQUIRED) and "error" not in report else "FAIL"
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with args.evidence.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True, default=str)
        stream.write("\n")
    print(json.dumps({"result": report["result"], "gate_eligible": report["gate_eligible"],
                      "evidence": str(args.evidence), "evidence_sha256": digest(args.evidence.read_bytes()),
                      "work": str(work)}))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
