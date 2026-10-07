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
import tempfile

from . import ValidationError
from .child_process import child_env, isolated_git_env

SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_BOUND_FILES = 10000


def git_environment():
    """No inherited GIT_* redirection, no replace refs, no lazy promisor fetches, no hooks."""
    return isolated_git_env()


def git_command(repository, *args):
    from .providers.github_status import host_executable
    executable = host_executable("git", Path(repository))
    return [executable, "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.quotePath=false", "-c", "protocol.file.allow=never", "-C", str(repository), *args]


def run_git(repository, *args, extra_env=None):
    environment = isolated_git_env(extra=extra_env) if extra_env else git_environment()
    try:
        return subprocess.run(git_command(repository, *args), capture_output=True, timeout=120,
                              env=child_env(environment), stdin=subprocess.DEVNULL)
    except subprocess.SubprocessError as exc:
        raise ValidationError(f"git {args[0]} did not complete: {type(exc).__name__}") from exc


def git(repository, *args, binary=False):
    # Read-only plumbing only: cat-file, rev-parse, ls-tree and merge-base run no hooks.
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


def commit_parents(repository, sha):
    parents = []
    for line in git(repository, "cat-file", "commit", sha, binary=True).split(b"\n"):
        if not line:
            break
        if line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii"))
    return parents


def is_ancestor(repository, ancestor, descendant):
    result = run_git(repository, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode not in (0, 1):
        raise ValidationError(f"git merge-base failed with exit {result.returncode}")
    return result.returncode == 0


def integration_tree(repository, onto, head):
    """Tree of head integrated onto ``onto``; new objects go to a throwaway store."""
    if is_ancestor(repository, onto, head):
        return git(repository, "rev-parse", head + "^{tree}")
    objects = git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects")
    with tempfile.TemporaryDirectory(prefix="awf-closeout-objects-") as scratch:
        result = run_git(repository, "merge-tree", "--write-tree", "--no-messages", onto, head,
                         extra_env={"GIT_OBJECT_DIRECTORY": scratch, "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects})
    if result.returncode != 0:
        raise ValidationError(f"reviewed_head_sha does not merge cleanly onto {onto}; the merged tree is not the reviewed code")
    return result.stdout.decode("utf-8").split("\n", 1)[0].strip()


def validate_relationship(record, repository):
    """The merge commit must carry exactly the reviewed head integrated onto the recorded history.

    Fast-forward: the merge commit is the reviewed head. Merge commit: two
    parents, the second is the reviewed head, the first descends from base, and
    the tree equals the reviewed head merged onto the first parent. Squash and
    rebase: a linear first-parent chain from base (one commit for a squash, at
    most one per reviewed commit for a rebase) whose tip tree equals the
    reviewed head merged onto base. Anything else fails closed.
    """
    head, base, merge = record["reviewed_head_sha"], record["base_sha"], record["merge_commit_sha"]
    if not is_ancestor(repository, base, merge):
        raise ValidationError(f"base_sha {base} is not an ancestor of merge_commit_sha {merge}")
    if merge == head:
        return "fast_forward"
    unrelated = ValidationError(f"merge_commit_sha {merge} does not merge reviewed_head_sha {head} onto base_sha {base}")
    tree = git(repository, "rev-parse", merge + "^{tree}")
    mismatch = ValidationError(f"merge_commit_sha {merge} tree is not reviewed_head_sha {head} integrated onto base_sha {base}")
    parents = commit_parents(repository, merge)
    if len(parents) == 2:
        if parents[1] != head or not is_ancestor(repository, base, parents[0]):
            raise unrelated
        if tree != integration_tree(repository, parents[0], head):
            raise mismatch
        return "merge_commit"
    reviewed = int(git(repository, "rev-list", "--count", "--no-merges", f"{base}..{head}"))
    commit, length = merge, 0
    while len(parents) == 1 and length < max(reviewed, 1):
        commit, length = parents[0], length + 1
        if commit == base:
            break
        parents = commit_parents(repository, commit)
    if commit != base:
        raise unrelated
    if tree != integration_tree(repository, base, head):
        raise mismatch
    return "squash" if length == 1 else "rebase"


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
    report["merge_relationship"] = validate_relationship(record, repository)
    report["checked"].append("sha_relationship")
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
