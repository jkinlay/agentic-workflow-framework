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
MAX_REBASE_COMMITS = 1000


def git_environment():
    """No inherited GIT_* redirection, no replace refs, no lazy promisor fetches, no hooks."""
    return isolated_git_env()


def git_command(repository, *args):
    from .providers.github_status import host_executable
    executable = host_executable("git", Path(repository))
    return [executable, "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.quotePath=false", "-c", "protocol.file.allow=never", "-C", str(repository), *args]


def run_git(repository, *args):
    try:
        return subprocess.run(git_command(repository, *args), capture_output=True, timeout=120,
                              env=child_env(git_environment()), stdin=subprocess.DEVNULL)
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


def read_commit(repository, sha):
    """Parents, tree and the author/message identity of a commit (committer excluded)."""
    raw = git(repository, "cat-file", "commit", sha, binary=True)
    header, _, message = raw.partition(b"\n\n")
    parents, tree, author = [], None, None
    for line in header.split(b"\n"):
        if line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii"))
        elif line.startswith(b"tree "):
            tree = line[5:].decode("ascii")
        elif line.startswith(b"author "):
            author = line
    return parents, tree, (author, message)


def is_ancestor(repository, ancestor, descendant):
    result = run_git(repository, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode not in (0, 1):
        raise ValidationError(f"git merge-base failed with exit {result.returncode}")
    return result.returncode == 0


class MergeProbe:
    """Computes merge trees in a throwaway bare repository borrowing the objects read-only.

    The probe has no repository-local configuration, attributes or hooks (global
    and system configuration are already disabled), so no custom merge driver or
    filter can run, and new objects never enter the validated repository.
    """

    def __init__(self, repository):
        self.repository = repository
        self.scratch = None

    def __enter__(self):
        objects = git(self.repository, "rev-parse", "--path-format=absolute", "--git-path", "objects")
        self.scratch = tempfile.TemporaryDirectory(prefix="awf-closeout-probe-")
        root = Path(self.scratch.name)
        git(root, "init", "--bare", "--quiet", "--template=", "probe.git")
        self.probe = root / "probe.git"
        (self.probe / "objects/info").mkdir(parents=True, exist_ok=True)
        (self.probe / "objects/info/alternates").write_text(objects + "\n", encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.scratch.cleanup()

    def merge_tree(self, onto, head, merge_base=None):
        extra = [f"--merge-base={merge_base}"] if merge_base else []
        result = run_git(self.probe, "merge-tree", "--write-tree", "--no-messages", *extra, onto, head)
        if result.returncode != 0:
            raise ValidationError(f"reviewed commit {head} does not apply cleanly onto {onto}; the merged tree is not the reviewed code")
        return result.stdout.decode("utf-8").split("\n", 1)[0].strip()


def integration_tree(repository, onto, head):
    """Tree of head integrated onto ``onto``."""
    if is_ancestor(repository, onto, head):
        return git(repository, "rev-parse", head + "^{tree}")
    with MergeProbe(repository) as probe:
        return probe.merge_tree(onto, head)


def validate_relationship(record, repository):
    """The merge commit must carry exactly the reviewed head integrated onto the recorded base.

    Fast-forward: the merge commit is the reviewed head. Merge commit: parents
    are exactly (base, reviewed head) and the tree is their merge. Squash: one
    commit on base whose tree is the reviewed head merged onto base. Rebase: a
    linear chain on base replaying each reviewed commit in order with the same
    author and message and the cherry-picked tree. Anything else fails closed.
    """
    head, base, merge = record["reviewed_head_sha"], record["base_sha"], record["merge_commit_sha"]
    if not is_ancestor(repository, base, merge):
        raise ValidationError(f"base_sha {base} is not an ancestor of merge_commit_sha {merge}")
    if merge == head:
        return "fast_forward"
    unrelated = ValidationError(f"merge_commit_sha {merge} does not merge reviewed_head_sha {head} onto base_sha {base}")
    mismatch = ValidationError(f"merge_commit_sha {merge} tree is not reviewed_head_sha {head} integrated onto base_sha {base}")
    parents, tree, _ = read_commit(repository, merge)
    if parents == [base, head]:
        if tree != integration_tree(repository, base, head):
            raise mismatch
        return "merge_commit"
    if parents == [base]:
        if tree != integration_tree(repository, base, head):
            raise mismatch
        return "squash"
    if len(parents) != 1:
        raise unrelated
    reviewed = git(repository, "rev-list", "--topo-order", f"--max-count={MAX_REBASE_COMMITS + 1}",
                   f"{base}..{head}").split()[::-1]
    if not 1 < len(reviewed) <= MAX_REBASE_COMMITS:
        raise unrelated
    chain, commit = [], merge
    while commit != base and len(chain) < len(reviewed):
        commit_parents, commit_tree, identity = read_commit(repository, commit)
        if len(commit_parents) != 1:
            raise unrelated
        chain.append((commit, commit_tree, identity))
        commit = commit_parents[0]
    if commit != base or len(chain) != len(reviewed):
        raise unrelated
    with MergeProbe(repository) as probe:
        onto = base
        for original, (rebased, rebased_tree, identity) in zip(reviewed, reversed(chain)):
            original_parents, _, original_identity = read_commit(repository, original)
            if len(original_parents) != 1 or identity != original_identity:
                raise unrelated
            # Cherry-pick semantics: the reviewed commit's own change replayed onto the previous step.
            if rebased_tree != probe.merge_tree(onto, original, merge_base=original_parents[0]):
                raise mismatch
            onto = rebased
    return "rebase"


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
