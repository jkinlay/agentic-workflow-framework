"""Closeout evidence bound to recorded Git objects, never to the working tree.

Identity fields come from the JSON record only. A Markdown rendering is derived
from the validated record and is never an input. Every object is resolved
through Git plumbing; an absent object fails closed and is named.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import subprocess

from . import ValidationError

SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_BOUND_FILES = 10000


def git_environment():
    """No inherited GIT_* redirection, no replace refs, no lazy promisor fetches, no hooks."""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_") and key.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    return env


def git_command(repository, *args):
    from .providers.github_status import host_executable
    executable = host_executable("git", Path(repository))
    return [executable, "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.quotePath=false", "-c", "protocol.file.allow=never", "-C", str(repository), *args]


def run_git(repository, *args):
    try:
        return subprocess.run(git_command(repository, *args), capture_output=True, timeout=120, env=git_environment(), stdin=subprocess.DEVNULL)
    except subprocess.SubprocessError as exc:
        raise ValidationError(f"git {args[0]} did not complete: {type(exc).__name__}") from exc


def git(repository, *args, binary=False):
    # Read-only plumbing only: cat-file, rev-parse and ls-tree run no hooks.
    result = run_git(repository, *args)
    if result.returncode != 0:
        detail = re.sub(r"[^\x20-\x7e]", "?", result.stderr.decode("utf-8", "replace"))[:200]
        raise ValidationError(f"git {args[0]} failed with exit {result.returncode}: {detail}")
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def object_exists(repository, sha, kind):
    if not isinstance(sha, str) or not SHA.match(sha):
        raise ValidationError(f"{kind} is not a 40-hex Git object ID")
    probe = run_git(repository, "cat-file", "-t", sha)
    if probe.returncode != 0:
        raise ValidationError(f"{kind} {sha} is absent from the object store (shallow clone or unfetched history)")
    return probe.stdout.decode("utf-8").strip()


def validate_closeout(record, repository, contracts=None):
    """Resolve commit -> tree -> path/blob -> content digest; fail closed on any gap."""
    if contracts is not None:
        contracts.validate("closeout-record", record)
    repository = Path(repository)
    report = {"record_id": record["record_id"], "pr_number": record["pr_number"], "checked": [], "working_tree_read": False}
    for field in ("reviewed_head_sha", "base_sha", "merge_commit_sha"):
        if object_exists(repository, record[field], field) != "commit":
            raise ValidationError(f"{field} {record[field]} is not a commit")
        report["checked"].append(field)
    tree = git(repository, "rev-parse", record["merge_commit_sha"] + "^{tree}")
    if tree != record["merge_tree_sha"]:
        raise ValidationError(f"merge_tree_sha {record['merge_tree_sha']} differs from the merge commit's tree {tree}")
    if object_exists(repository, record["merge_tree_sha"], "merge_tree_sha") != "tree":
        raise ValidationError("merge_tree_sha is not a tree")
    report["checked"].append("merge_tree_sha")
    if len(record["bound_files"]) > MAX_BOUND_FILES:
        raise ValidationError("closeout record binds more files than the validator accepts")
    listed = {}
    for entry in git(repository, "ls-tree", "-r", "-z", record["merge_tree_sha"], binary=True).split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        listed[path.decode("utf-8", "surrogateescape")] = meta.decode("ascii").split()[2]
    for item in record["bound_files"]:
        path = item["path"]
        if object_exists(repository, item["blob_sha"], f"blob for {path}") != "blob":
            raise ValidationError(f"{item['blob_sha']} is not a blob")
        if path not in listed:
            raise ValidationError(f"bound file {path!r} is absent from tree {record['merge_tree_sha']}")
        if listed[path] != item["blob_sha"]:
            raise ValidationError(f"bound file {path!r} resolves to blob {listed[path]}, record names {item['blob_sha']}")
        content = git(repository, "cat-file", "blob", item["blob_sha"], binary=True)
        digest = hashlib.sha256(content).hexdigest()
        if digest != item["sha256"]:
            raise ValidationError(f"bound file {path!r} blob content SHA-256 {digest} differs from record {item['sha256']}")
        report["checked"].append(path)
    report["status"] = "VALID"
    report["execution_authority"] = False
    return report


def render_markdown(record):
    """Derived rendering for humans; never parsed back."""
    lines = [f"# Closeout PR #{record['pr_number']}", "",
             f"Reviewed head: `{record['reviewed_head_sha']}`", f"Base: `{record['base_sha']}`",
             f"Merge commit: `{record['merge_commit_sha']}` (tree `{record['merge_tree_sha']}`)", "",
             "| Path | Blob | SHA-256 |", "| --- | --- | --- |"]
    lines += [f"| `{item['path']}` | `{item['blob_sha']}` | `{item['sha256']}` |" for item in record["bound_files"]]
    lines += ["", f"Gate record: `{record['gate_record_id']}`; reviews: " + ", ".join(f"`{r}`" for r in record["review_record_ids"])
              + (f"; Jira transition: `{record['jira_transition_record_id']}`" if record["jira_transition_record_id"] else "; Jira transition: none"),
              "", "Derived from the validated closeout-record; this rendering is not an input to validation."]
    return "\n".join(lines) + "\n"
