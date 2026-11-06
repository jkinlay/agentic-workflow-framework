#!/usr/bin/env python3
"""AC42 end-to-end upgrade gate harness (AWF-11).

Upgrades a recorded historical installation fixture (default 1.9.1, as AC42
specifies) to this 1.9.6 source in a fresh Git repository, then records one
PASS / FAIL / NOT_COVERED row per AC42 clause in an evidence JSON file.

The adoption-PR merge and the GitHub observations are a recorded synthetic
fixture built from the local Git objects; nothing contacts a provider, writes
outside --work, or uses credentials. Exit 0 only when every required row
passes. The real gate run is on Windows via tools/windows/Invoke-AC42.ps1;
on other hosts the same harness is a smoke test.

On Windows and POSIX, the synthetic project stores ``core.autocrlf=true`` only
in that throwaway repository.  The fresh-clone regression applies the setting
with ``git -c`` to the clone command, so neither a caller repository nor global
Git configuration is changed.
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
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic import VERSION  # noqa: E402
from agentic.child_process import child_env, isolated_git_env  # noqa: E402

REQUIRED = ("fixture", "upgrade", "verify_installation", "validate_config", "self_test",
            "adoption_merge", "active_single_status_run", "handoff_snapshot",
            "project_values_preserved", "external_data_unchanged", "autocrlf_fresh_clone",
            "history_sensitive_value_blocked", "capability_matrix",
            "read_only_external_resource_admission")
SENSITIVE_PLACEHOLDER = "<external-estate>/positions.csv"


STATUS_FIXTURE_SITE = r'''"""AC42-only provider fixture for the installed status subprocess."""
import copy
import json
import os
from pathlib import Path
import sys

from agentic.providers import github_status as status

fixture = json.loads(Path(os.environ["AWF_AC42_PROVIDER_FIXTURE"]).read_text(encoding="utf-8"))
request_log = Path(os.environ["AWF_AC42_PROVIDER_REQUEST_LOG"])
original_host_executable = status.host_executable


def record(value):
    with request_log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=True) + "\n")


def count(value):
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True).encode("utf-8"))


def host_executable(name, root):
    if name == "ac42-recorded-provider":
        return sys.executable
    return original_host_executable(name, root)


def read(endpoint, deadline, gh, pr_file_metadata=False):
    record(endpoint)
    if endpoint not in fixture["api"]:
        raise KeyError("unrecorded provider endpoint: " + endpoint)
    value = copy.deepcopy(fixture["api"][endpoint])
    return value, count(value)


def read_graphql(query, variables, deadline, gh):
    record("graphql")
    value = copy.deepcopy(fixture["graphql"])
    return value, count(value)


status.host_executable = host_executable
status._gh_get = read
status._gh_get_pr_files = read
status._gh_graphql = read_graphql
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def tree_digest(root):
    return {path.relative_to(root).as_posix(): digest(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file()}


def run(command, cwd, timeout=1800, extra=None, full_output=False):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0", **(extra or {}))
    started = time.monotonic()
    done = subprocess.run([str(part) for part in command], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=child_env(env), stdin=subprocess.DEVNULL)
    result = {"command": [str(part) for part in command], "exit_code": done.returncode,
              "seconds": round(time.monotonic() - started, 1),
              "stdout_tail": done.stdout[-2000:], "stderr_tail": done.stderr[-2000:]}
    if full_output:
        result["stdout"] = done.stdout
        result["stderr"] = done.stderr
    return result


def gate_outcome(rows, *, execution_eligible, has_error=False):
    """Return a fail-closed AC42 outcome and name every required failure."""
    failures = []
    for name in REQUIRED:
        result = rows.get(name, {}).get("result", "MISSING")
        if result != "PASS":
            failures.append({"check": name, "result": result})
    result = "PASS" if not failures and not has_error else "FAIL"
    return {"result": result,
            "gate_eligible": bool(execution_eligible and result == "PASS"),
            "required_check_failures": failures}


OWNER_BLOB_MAX_BYTES = 8 * 1024 * 1024


def owner_blob(project, commit, name, max_bytes=OWNER_BLOB_MAX_BYTES):
    """Committed bytes of one owner file, or None when the path is absent.

    Git runs isolated (no ambient config, replace refs disabled). Any other
    failure or an oversized blob raises, so the check fails closed.
    """
    def call(*args):
        return subprocess.run(["git", "--no-replace-objects", "-c", "core.useReplaceRefs=false",
                               "-C", str(project), *args], capture_output=True, timeout=60,
                              env=child_env(isolated_git_env(dict(os.environ))))
    listed = call("ls-tree", "-z", "--full-tree", commit, "--", name)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-tree {commit}:{name} failed")
    entries = [item for item in listed.stdout.split(b"\0") if item]
    if not entries:
        return None
    meta, _, path = entries[0].partition(b"\t")
    mode, kind, oid = meta.split()
    if len(entries) != 1 or kind != b"blob" or path.decode("utf-8") != name:
        raise RuntimeError(f"{name} at {commit} is not a single blob")
    size = call("cat-file", "-s", oid.decode())
    if size.returncode != 0 or int(size.stdout) > max_bytes:
        raise RuntimeError(f"{name} at {commit} is unreadable or exceeds {max_bytes} bytes")
    done = call("cat-file", "blob", oid.decode())
    if done.returncode != 0 or len(done.stdout) != int(size.stdout):
        raise RuntimeError(f"{name} at {commit} could not be read completely")
    return done.stdout


def _mapping_entries(node, yaml):
    """Return unique scalar-key entries from one composed YAML mapping."""
    if not isinstance(node, yaml.MappingNode):
        return None
    entries = {}
    for key, value in node.value:
        if not isinstance(key, yaml.ScalarNode) or key.value in entries:
            return None
        entries[key.value] = (key, value)
    return entries


def _line_start(text, index):
    return max(text.rfind("\n", 0, index), text.rfind("\r", 0, index)) + 1


def _insert_mapping_entry(text, node, flow_entry, block_lines, newline, yaml):
    """Insert into one structurally identified mapping without re-emitting it."""
    entries = _mapping_entries(node, yaml)
    if entries is None:
        return None
    if node.flow_style:
        openings = [token.start_mark.index for token in yaml.scan(text)
                    if isinstance(token, yaml.tokens.FlowMappingStartToken)
                    and node.start_mark.index <= token.start_mark.index < node.end_mark.index]
        if not openings:
            return None
        opening = min(openings)
        separator = ", " if entries else ""
        return text[:opening + 1] + flow_entry + separator + text[opening + 1:]

    insertion = node.end_mark.index
    if not (0 <= insertion <= len(text)):
        return None
    if entries:
        indent = next(iter(entries.values()))[0].start_mark.column
    else:
        indent = node.start_mark.column
    insertion_line = _line_start(text, insertion)
    if insertion < len(text) and not text[insertion_line:insertion].strip(" \t"):
        insertion = insertion_line
    prefix = "" if insertion == 0 or text[insertion - 1] in "\r\n" else newline
    rendered = prefix + "".join(" " * indent + line + newline for line in block_lines)
    return text[:insertion] + rendered + text[insertion:]


def _compose_mapping(text):
    import yaml
    try:
        root = yaml.compose(text)
        value = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError):
        return None
    entries = _mapping_entries(root, yaml)
    if entries is None or not isinstance(value, dict):
        return None
    return yaml, root, entries, value


def expected_project_config(raw):
    """Derive the exact bytes after the documented additive config edits."""
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None

    composed = _compose_mapping(text)
    if composed is None:
        return None
    yaml, _, root_entries, value = composed
    template = root_entries.get("template")
    template_entries = _mapping_entries(template[1], yaml) if template else None
    version_entry = template_entries.get("expected_workflow_version") if template_entries else None
    version_node = version_entry[1] if version_entry else None
    if (not isinstance(version_node, yaml.ScalarNode)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version_node.value)):
        return None
    start, end = version_node.start_mark.index, version_node.end_mark.index
    scalar_source = text[start:end]
    matches = list(re.finditer(re.escape(version_node.value), scalar_source))
    if len(matches) != 1:
        return None
    match = matches[0]
    text = (text[:start + match.start()] + VERSION
            + text[start + match.end():])
    expected_value = copy.deepcopy(value)
    expected_value["template"]["expected_workflow_version"] = VERSION
    recomposed = _compose_mapping(text)
    if recomposed is None or recomposed[3] != expected_value:
        return None

    newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
    yaml, _, root_entries, value = recomposed
    jira_entry = root_entries.get("jira")
    jira_entries = _mapping_entries(jira_entry[1], yaml) if jira_entry else None
    if jira_entries is None or not isinstance(value.get("jira"), dict):
        return None
    identity_fields = ("cloud_id", "provider_project_id", "controller_actor_id")
    missing = [name for name in identity_fields if name not in jira_entries]
    if missing:
        site_entry = jira_entries.get("site")
        if site_entry is None:
            return None
        insertion = _line_start(text, site_entry[0].start_mark.index)
        leading = text[insertion:site_entry[0].start_mark.index]
        if leading.strip(" \t"):
            return None
        block = "".join(leading + '"' + name + '": null,' + newline
                        for name in missing)
        text = text[:insertion] + block + text[insertion:]
        for name in missing:
            expected_value["jira"][name] = None
        recomposed = _compose_mapping(text)
        if recomposed is None or recomposed[3] != expected_value:
            return None

    # The review-tier migration serializes strict JSON; YAML retains all bytes
    # except the one structurally scoped insertion.
    json_value = None
    if text.lstrip().startswith(("{", "[")):
        try:
            json_value = json.loads(text)
        except (TypeError, ValueError):
            pass
    tier_default = {"roles": ["critic", "specialist"],
                    "findings": "blocking", "max_rounds": 3}
    if json_value is not None:
        if not isinstance(json_value, dict):
            return None
        execution = json_value.setdefault("execution", {})
        if not isinstance(execution, dict):
            return None
        tiers = execution.setdefault("risk_tiers", {})
        if not isinstance(tiers, dict):
            return None
        tiers.setdefault("tier3_review", copy.deepcopy(tier_default))
        return (json.dumps(json_value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    recomposed = _compose_mapping(text)
    if recomposed is None:
        return None
    yaml, root, root_entries, value = recomposed
    expected_value = copy.deepcopy(value)
    execution_entry = root_entries.get("execution")
    tier_flow = ("tier3_review: {roles: [critic, specialist], findings: blocking, "
                 "max_rounds: 3}")
    if execution_entry is None:
        text = _insert_mapping_entry(
            text, root,
            "execution: {risk_tiers: {" + tier_flow + "}}",
            ["execution:", "  risk_tiers:", "    tier3_review:",
             "      roles: [critic, specialist]", "      findings: blocking",
             "      max_rounds: 3"], newline, yaml)
        expected_value["execution"] = {"risk_tiers": {"tier3_review": tier_default}}
    else:
        execution_entries = _mapping_entries(execution_entry[1], yaml)
        if execution_entries is None or not isinstance(value.get("execution"), dict):
            return None
        risk_entry = execution_entries.get("risk_tiers")
        if risk_entry is None:
            text = _insert_mapping_entry(
                text, execution_entry[1],
                "risk_tiers: {" + tier_flow + "}",
                ["risk_tiers:", "  tier3_review:",
                 "    roles: [critic, specialist]", "    findings: blocking",
                 "    max_rounds: 3"], newline, yaml)
            expected_value["execution"]["risk_tiers"] = {"tier3_review": tier_default}
        else:
            risk_entries = _mapping_entries(risk_entry[1], yaml)
            if risk_entries is None or not isinstance(value["execution"].get("risk_tiers"), dict):
                return None
            if "tier3_review" not in risk_entries:
                text = _insert_mapping_entry(
                    text, risk_entry[1], tier_flow,
                    ["tier3_review:", "  roles: [critic, specialist]",
                     "  findings: blocking", "  max_rounds: 3"], newline, yaml)
                expected_value["execution"]["risk_tiers"]["tier3_review"] = tier_default
    if text is None:
        return None
    recomposed = _compose_mapping(text)
    if recomposed is None or recomposed[3] != expected_value:
        return None
    return text.encode("utf-8")


def expected_gitignore(existing):
    """Derive the exact N1 .gitignore bytes without using installer code."""
    try:
        required = (ROOT / ".agentic/templates/operating.gitignore").read_bytes()
    except OSError:
        return None
    if not required.endswith(b"\n") or b"\x00" in required:
        return None
    if existing is None:
        return required

    header = b"# AWF operating recovery files are local; changes/ is versioned audit evidence."
    terminal = b".awf-*.tmp"
    spans = []
    start = None
    for match in re.finditer(rb"[^\n]*\n|[^\n]+$", existing):
        raw = match.group()
        line = raw[:-1].removesuffix(b"\r") if raw.endswith(b"\n") else raw
        if line == header:
            if start is not None:
                return None
            start = match.start()
        elif line.startswith(header) or (line.startswith(b"# AWF ") and header.startswith(line)):
            return None
        elif line == terminal and start is not None:
            spans.append((start, match.end()))
            start = None
    if start is not None:
        return None
    if not spans:
        separator = b"\n" if existing and not existing.endswith(b"\n") else b""
        return existing + separator + required

    outside = []
    previous = 0
    for index, (span_start, span_end) in enumerate(spans):
        outside.append(existing[previous:span_start])
        if index == 0:
            outside.append(required)
        previous = span_end
    outside.append(existing[previous:])
    return b"".join(outside)


def project_values_preserved(owner_before, owner_after, config, installed):
    """Return the exact owner-file preservation result and diagnostic evidence."""
    changed = {}
    for name, raw in owner_before.items():
        after = owner_after.get(name)
        if after != raw:
            changed[name] = list(difflib.unified_diff(
                (raw or b"").decode("utf-8", "replace").splitlines(),
                (after or b"").decode("utf-8", "replace").splitlines(),
                lineterm="", n=0))[2:40]
    # AWF-managed records and the byte-exact N1 .gitignore transformation.
    allowed = {config, installed, ".agentic/workflow-version.yaml", ".gitignore"}
    expected_ignore = expected_gitignore(owner_before.get(".gitignore"))
    ignore_ok = expected_ignore is not None and owner_after.get(".gitignore") == expected_ignore
    expected_config = expected_project_config(owner_before.get(config, b""))
    config_ok = expected_config is not None and owner_after.get(config) == expected_config
    ok = set(changed) <= allowed and config_ok and ignore_ok
    return ok, {"changed": changed, "project_config_exact": config_ok,
                "gitignore_prefix_preserved": ignore_ok, "gitignore_exact": ignore_ok}


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


def clone_with_autocrlf(repository, destination, cwd, *, extra=None):
    """Clone with Windows conversion command-scoped to the disposable clone."""
    return run(["git", "-c", "core.autocrlf=true", "clone", "-q",
                repository, destination], cwd, extra=extra)


def provider_fixture(project, head, upgrade_commit, receipt, installed_files, full_name, repository_id, branch):
    """Recorded GitHub answers for a merged adoption PR #7 whose merge commit is head."""
    from agentic.installer import CONFIG, INSTALLED, PROVENANCE
    from agentic.providers import github_status as status
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
                            "head": {"ref": "awf/upgrade-1.9.4", "sha": "d" * 40, "repo": dict(repository)}},
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
        pullRequest={"id": "PR_ac42", "number": 7, "baseRefName": branch, "headRefName": "awf/upgrade-1.9.4",
                     "headRefOid": "d" * 40, "headRepository": graph_repo, "state": "MERGED", "merged": True,
                     "mergedAt": "2026-10-07T12:00:00Z", "mergeCommit": {"oid": head, "repository": graph_repo}})}}
    return {"api": api, "graphql": graphql}


def installed_active_status(python, workflow, project, release, pin, fixture, work):
    """Run ACTIVE through the installed workflow CLI with recorded provider answers."""
    fixture_root = work / "active-status-fixture"
    fixture_root.mkdir()
    fixture_path = fixture_root / "provider.json"
    request_log = fixture_root / "requests.jsonl"
    (fixture_root / "sitecustomize.py").write_text(STATUS_FIXTURE_SITE, encoding="utf-8", newline="\n")
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=True), encoding="utf-8", newline="\n")
    environment = {
        "AWF_AC42_PROVIDER_FIXTURE": str(fixture_path),
        "AWF_AC42_PROVIDER_REQUEST_LOG": str(request_log),
        "PYTHONPATH": os.pathsep.join((str(fixture_root), str(project / ".agentic/lib"))),
    }
    step = run([python, "-B", workflow, "--root", project, "status", "--json", "--require-active",
                "--adoption-pr", "7", "--gh", "ac42-recorded-provider",
                "--release-source", release, "--expected-manifest-sha256", pin],
               project, extra=environment, full_output=True)
    stdout = step.pop("stdout")
    step.pop("stderr")
    if step["exit_code"] != 0:
        raise RuntimeError("installed workflow.py status failed: " + step["stderr_tail"])
    try:
        observed = json.loads(stdout)
    except ValueError as exc:
        raise RuntimeError("installed workflow.py status returned invalid JSON") from exc
    requests = ([json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]
                if request_log.is_file() else [])
    return observed, step, requests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-version", default="1.9.1", choices=("1.8.3", "1.8.9", "1.9.1", "1.9.2", "1.9.3"))
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
    gate, started = Gate(), time.monotonic()
    pin = digest((ROOT / "MANIFEST.json").read_bytes())
    execution_eligible = platform.system() == "Windows" and bool(args.runtime_wheelhouse) and not args.skip_self_test
    report = {"format": "awf-ac42-evidence-1", "awf_version": VERSION, "from_version": args.from_version,
              "source_manifest_sha256": pin, "host": {"system": platform.system(), "release": platform.release(),
              "python": sys.version.split()[0], "git": run(["git", "--version"], work)["stdout_tail"].strip()},
              "mode": "bootstrap_cli" if args.runtime_wheelhouse else "in_process_installer",
              "execution_gate_eligible": execution_eligible, "gate_eligible": False,
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
        # This repository was just created under --work and is wholly
        # disposable.  Be explicit about the local scope: never select a
        # global/system write target or mutate the AWF source repository.
        git(project, "config", "--local", "core.autocrlf", "true")
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", f"AWF {args.from_version} installation with operating history")
        base_commit = git(project, "rev-parse", "HEAD")
        owner_before = {name: (project / name).read_bytes() for name in evidence["owner_files"]}
        gate.record("fixture", True, {"files": len(tree_digest(project)), "base_commit": base_commit,
                    "owner_files": sorted(owner_before), "core.autocrlf": git(project, "config", "core.autocrlf"),
                    "codeowners_project_owned": ".github/CODEOWNERS" in owner_before})

        git(project, "checkout", "-q", "-b", "awf/upgrade-1.9.4")
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
        # Self-test runs on the committed upgrade, as owner CI does: heavy-validation
        # tests require a clean checkout whose reviewed bytes match HEAD.
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Upgrade AWF to " + VERSION)
        upgrade_commit = git(project, "rev-parse", "HEAD")
        if args.skip_self_test:
            gate.record("self_test", False, "skipped (--skip-self-test)", covered=False)
        else:
            sidecar = Path(str(args.evidence) + ".self-test.json")
            step = run([python, "-B", project / ".agentic/scripts/self_test.py", "--checks-only",
                        "--report", sidecar], project, timeout=5400)
            try:
                st_report = json.loads(sidecar.read_text(encoding="utf-8"))
                tests = st_report.get("tests", {})
                step["full_report"] = {"path": sidecar.name, "sha256": digest(sidecar.read_bytes())}
                step["summary"] = {"status": st_report.get("status"), "error": st_report.get("error"),
                                   "run": tests.get("run"), "failures": tests.get("failures"),
                                   "errors": tests.get("errors"),
                                   "failed_tests": [line for line in str(st_report.get("test_log", "")).splitlines()
                                                    if line.startswith(("FAIL:", "ERROR:"))][:60]}
            except (OSError, ValueError) as exc:
                step["summary"] = {"status": "UNREADABLE", "error": type(exc).__name__}
            gate.record("self_test", step["exit_code"] == 0, step)

        git(project, "checkout", "-q", branch)
        git(project, "merge", "-q", "--no-ff", "-m", f"Merge pull request #7 from {full_name.split('/')[0]}/awf/upgrade-1.9.4",
            "awf/upgrade-1.9.4")
        head = git(project, "rev-parse", "HEAD")
        gate.record("adoption_merge", True, {"merge_commit": head, "pr": 7, "fixture": "local merge, recorded provider answers"})

        receipt = load(project / INSTALLED)
        installed_files = {p.relative_to(project).as_posix(): p.read_bytes()
                           for p in project.rglob("*") if p.is_file() and ".git" not in p.parts}
        fixture = provider_fixture(project, head, upgrade_commit, receipt, installed_files,
                                   full_name, repository_id, branch)
        release = work / "release-source"
        shutil.copytree(ROOT, release, ignore=shutil.ignore_patterns(".git", ".tmp-tests", "__pycache__", "*.pyc"))
        observed, status_step, requests = installed_active_status(
            python, workflow, project, release, pin, fixture, work)
        blockers = [c for c in observed["checks"] if c["state"] not in ("PASS", "N_A", "SKIP")]
        gate.record("active_single_status_run", observed["project_state"] == "ACTIVE",
                    {"project_state": observed["project_state"], "line": observed.get("line"),
                     "blockers": blockers, "provider_requests": requests,
                     "command": status_step["command"], "exit_code": status_step["exit_code"]})
        matrix = observed.get("capabilities")
        gate.record("capability_matrix", isinstance(matrix, dict) and bool(matrix), matrix)
        snapshot = build_snapshot(project, status=observed)
        gate.record("handoff_snapshot", snapshot["awf"]["project_state"]["value"] == observed["project_state"]
                    and snapshot["repository"]["head"]["value"] == head, snapshot)

        # Compare committed blobs: core.autocrlf=true rewrites working-tree line
        # endings on checkout/merge, which is not an upgrader byte change.
        def blob(commit, name):
            return owner_blob(project, commit, name)
        owner_before = {name: blob(base_commit, name) for name in owner_before}
        if any(raw is None for raw in owner_before.values()):
            raise RuntimeError("owner file absent from the base commit")
        owner_after = {name: blob("HEAD", name) for name in owner_before}
        values_ok, values_evidence = project_values_preserved(
            owner_before, owner_after, CONFIG, INSTALLED)
        gate.record("project_values_preserved", values_ok, values_evidence)
        gate.record("external_data_unchanged", tree_digest(external) == external_before, external_before)

        clone = work / "fresh-clone"
        clone_with_autocrlf(project, clone, work)
        step = run([python, "-B", clone / ".agentic/scripts/workflow.py", "--root", clone, "verify-installation"], clone)
        gate.record("autocrlf_fresh_clone", step["exit_code"] == 0, step)

        git(project, "checkout", "-q", "-b", "awf/ac43-history")
        (project / "notes-ac43.md").write_text(
            "estate: " + SENSITIVE_PLACEHOLDER + "\n", encoding="utf-8")
        git(project, "add", "notes-ac43.md")
        git(project, "commit", "-q", "-m", "Add estate note")
        (project / "notes-ac43.md").unlink()
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "Remove estate note")
        mapping = work / "publication-mapping.json"
        mapping.write_text(json.dumps({"version": 1, "aliases": {},
            "deny_literals": [SENSITIVE_PLACEHOLDER], "deny_regexes": [],
            "internal_hostnames": [], "builtin_allow": []}), encoding="utf-8")
        step = run([python, "-B", workflow, "--root", project, "publication-scan", "--base", branch,
                    "--head", "awf/ac43-history", "--mapping", mapping, "--json"], project)
        gate.record("history_sensitive_value_blocked", step["exit_code"] != 0 and "notes-ac43.md" in step["stdout_tail"], step)
        gate.record("read_only_external_resource_admission", False,
                    "AC48-AC51 per-stream admission of a declared read-only resource is not exercised by this harness",
                    covered=False)
    except Exception as exc:  # recorded, never hidden
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["rows"] = gate.rows
    report["seconds"] = round(time.monotonic() - started, 1)
    report.update(gate_outcome(gate.rows, execution_eligible=execution_eligible,
                               has_error="error" in report))
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
