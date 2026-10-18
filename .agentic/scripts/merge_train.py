#!/usr/bin/env python3
"""Gh-backed merge-train publisher adapter.

Register this adapter with the host and publish through
``agentic.merge_train.publish_batch``. That entry point requires an authenticated
candidate, registered observer and publisher, freeze rechecks and durable intent.
Direct mutation commands are unavailable.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from agentic import ValidationError, VERSION
from agentic import merge_train
from agentic.child_process import child_env, isolated_git_env, GIT_RUNNER_POLICY
from agentic.installer import verify_release
from agentic.safeio import Tree, relative_parts, run_bounded


def _run_gh(gh, arguments):
    try:
        result = subprocess.run(
            [gh, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=120, check=False, env=child_env())
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("gh publication outcome is unknown") from exc
    if result.returncode:
        raise ValidationError(
            f"gh command failed with exit {result.returncode}")
    return result.stdout.strip()


def _batch_branch(branch):
    if (not isinstance(branch, str)
            or not re.fullmatch(r"batch/[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            or any(part in {"", ".", ".."} or part.endswith((".", ".lock"))
                   for part in branch.split("/"))
            or ".." in branch):
        raise ValidationError("publisher may push only a batch ref")
    return branch


def _api(gh, hostname, endpoint, *fields):
    output = _run_gh(gh, ["api", endpoint, "--hostname", hostname, *fields])
    try:
        return json.loads(output)
    except ValueError as exc:
        raise ValidationError("gh returned invalid JSON") from exc


def _pull_request(value):
    try:
        result = {"number": value["number"], "branch": value["head"]["ref"],
                  "base_branch": value["base"]["ref"],
                  "head_sha": value["head"]["sha"], "title": value["title"],
                  "draft": value["draft"]}
    except (KeyError, TypeError) as exc:
        raise ValidationError("gh returned an incomplete PR identity") from exc
    merge_train._positive(result["number"], "PR number")
    merge_train._sha(result["head_sha"], "PR head")
    for field in ("branch", "base_branch", "title"):
        merge_train._identifier(result[field], "PR " + field)
    if type(result["draft"]) is not bool:
        raise ValidationError("gh returned an invalid PR draft state")
    return result


def _git(git, root, arguments, *, stdin=None, mutation=False):
    """Bounded, noninteractive Git; never retain child diagnostics or credentials."""
    try:
        done = run_bounded(
            [git, "--no-replace-objects", "-c", "core.hooksPath=" + os.devnull,
             "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
             "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
             "-C", str(root), *arguments],
            stdin=stdin, cwd=str(root), env=isolated_git_env(), policy=GIT_RUNNER_POLICY)
    except (ValidationError, OSError, subprocess.SubprocessError):
        if mutation:
            raise TimeoutError("batch transfer outcome is unknown") from None
        raise ValidationError("batch Git operation unavailable or exceeded its bound") from None
    if done.returncode:
        if mutation:
            # Even an error response can follow a successful remote mutation.
            raise TimeoutError("batch transfer outcome is unknown") from None
        raise ValidationError("batch Git operation failed")
    return done.stdout


def _local_binding(local_repository, git):
    source, executable = Path(local_repository), Path(git)
    if (not source.is_absolute() or not source.is_dir()
            or not executable.is_absolute() or not executable.is_file()
            or executable.suffix.lower() in {".bat", ".cmd", ".ps1"}):
        raise ValidationError("publisher needs explicit local repository and native Git bindings")
    source, executable = source.resolve(), str(executable.resolve())
    database = _git(executable, source, ["rev-parse", "--absolute-git-dir"]).decode().strip()
    if not Path(database).is_absolute() or not Path(database).is_dir():
        raise ValidationError("publisher local repository is unavailable")
    return Path(database).resolve(), executable


def _materialize(git, database, head, destination):
    # Read raw blobs rather than archive/checkout: committed export-ignore,
    # export-subst, filters and line-ending attributes cannot alter gate bytes.
    entries = _git(git, database, ["ls-tree", "-r", "-z", "--full-tree", head])
    members = []
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, sha = metadata.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValidationError("batch release contains unsupported file types")
        name = name.decode("utf-8")
        relative_parts(name)
        if any(part.casefold() == ".git" for part in name.split("/")):
            raise ValidationError("batch release contains Git metadata")
        members.append((name, sha))
    raw = _git(git, database, ["cat-file", "--batch"],
               stdin=b"".join(sha + b"\n" for _, sha in members))
    offset = 0
    with Tree(destination) as tree:
        for name, expected in members:
            end = raw.find(b"\n", offset)
            sha, kind, size = raw[offset:end].split()
            size = int(size)
            offset = end + 1
            if sha != expected or kind != b"blob" or size < 0 or offset + size >= len(raw):
                raise ValidationError("batch object materialization is incomplete")
            tree.write(name, raw[offset:offset + size])
            offset += size + 1
        if offset != len(raw):
            raise ValidationError("batch object materialization has trailing bytes")
        verify_release(tree)


def _upgrade_gate(destination):
    # The driver enforces discovery and execution counts, independently of the
    # command's exit status. Artifacts and bytecode stay outside the source repo.
    # This is the release-specific upgrade acceptance test (e.g. upgrade_194),
    # not the source-only upgrade infrastructure/installed-runtime suites.
    driver = (
        "import sys, unittest; from pathlib import Path; "
        "root=Path.cwd(); sys.path.insert(0,str(root/'.agentic/lib')); "
        "suite=unittest.TestLoader().discover(str(root/'.agentic/tests'),"
        f"pattern={'test_upgrade_' + VERSION.replace('.', '') + '.py'!r}); "
        "result=unittest.TextTestRunner().run(suite); "
        "sys.exit(0 if result.wasSuccessful() and "
        "result.testsRun > 0 and not result.skipped else 1)"
    )
    try:
        done = run_bounded([sys.executable, "-B", "-I", "-c", driver],
                           cwd=str(destination), env=isolated_git_env(), timeout=1800)
    except (ValidationError, OSError, subprocess.SubprocessError):
        raise ValidationError("batch upgrade gate unavailable or exceeded its bound") from None
    if done.returncode:
        raise ValidationError("batch upgrade gate failed or executed zero tests")


def _transfer(git, database, destination, branch, head, gh):
    ref = "refs/heads/" + branch
    existing = _git(git, database, ["-c", "credential.helper=", "-c",
        f"credential.{destination}.helper=!{shlex.quote(str(gh))} auth git-credential",
        "ls-remote", "--refs", destination, ref])
    if existing.strip():
        raise ValidationError("batch destination already exists")
    output = _git(git, database, ["-c", "credential.helper=", "-c",
        f"credential.{destination}.helper=!{shlex.quote(str(gh))} auth git-credential",
        "-c", "credential.interactive=false", "-c", "push.followTags=false",
        "push", "--porcelain", "--no-verify", "--recurse-submodules=no",
        "--force-with-lease=" + ref + ":", destination, head + ":" + ref], mutation=True)
    if not any(line.split(b"\t")[:2] == [b"*", (head + ":" + ref).encode()]
               for line in output.splitlines()):
        # An identical concurrent creation can be reported as up-to-date. It
        # must not be accepted as this operation's create-only publication.
        raise TimeoutError("batch transfer outcome is unknown")


def push_batch_ref(gh, repository, branch, head_sha, *, hostname="github.com",
                   local_repository, git):
    _batch_branch(branch)
    merge_train._sha(head_sha, "batch head")
    if (not re.fullmatch(r"[A-Za-z0-9.-]+", hostname)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)):
        raise ValidationError("publisher destination is invalid")
    source, git = _local_binding(local_repository, git)
    with tempfile.TemporaryDirectory(prefix="awf-batch-transport-") as temporary:
        root = Path(temporary)
        database, release = root / "objects.git", root / "release"
        # Minimal bare object store: no config file, credentials, hooks or remote.
        (database / "objects").mkdir(parents=True)
        (database / "refs").mkdir()
        (database / "HEAD").write_text("ref: refs/heads/unused\n", encoding="ascii")
        release.mkdir()
        _git(git, database, ["fetch", "--no-tags", "--no-write-fetch-head",
                             str(source), head_sha])
        if _git(git, database, ["cat-file", "-t", head_sha]).strip() != b"commit":
            raise ValidationError("batch head must name a local commit")
        _materialize(git, database, head_sha, release)
        _upgrade_gate(release)
        _transfer(git, database, f"https://{hostname}/{repository}.git", branch, head_sha, gh)
    observed = observe_batch_ref(gh, repository, branch, hostname=hostname)
    if observed != {"branch": branch, "head_sha": head_sha}:
        raise ValidationError("batch remote observation differs from the exact candidate")
    return observed


def _batch_ref(value):
    try:
        ref, sha = value["ref"], value["object"]["sha"]
        if not ref.startswith("refs/heads/"):
            raise ValueError("unexpected ref")
        branch = _batch_branch(ref[len("refs/heads/"):])
        merge_train._sha(sha, "observed batch head")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("gh returned an invalid batch ref") from exc
    return {"branch": branch, "head_sha": sha}


def observe_batch_ref(gh, repository, branch, *, hostname="github.com"):
    _batch_branch(branch)
    values = _api(gh, hostname,
                  f"repos/{repository}/git/matching-refs/heads/{quote(branch, safe='/')}")
    if not isinstance(values, list):
        raise ValidationError("gh returned an invalid ref observation")
    exact = [value for value in values
             if isinstance(value, dict) and value.get("ref") == "refs/heads/" + branch]
    if len(exact) > 1:
        raise ValidationError("gh returned ambiguous batch refs")
    return _batch_ref(exact[0]) if exact else None


def observe_pull_request(gh, repository, branch, *, hostname="github.com"):
    _batch_branch(branch)
    query = urlencode({"state": "open", "head": repository.split("/")[0] + ":" + branch})
    values = _api(gh, hostname, f"repos/{repository}/pulls?{query}")
    if not isinstance(values, list) or len(values) > 1:
        raise ValidationError("gh returned an ambiguous PR observation")
    return _pull_request(values[0]) if values else None


def create_draft_pr(gh, repository, branch, base_branch, title, *, hostname="github.com"):
    _batch_branch(branch)
    if base_branch != "main":
        raise ValidationError("batch publication target branch must be main")
    merge_train._identifier(title, "draft PR title")
    return _pull_request(_api(
        gh, hostname, f"repos/{repository}/pulls", "-f", f"head={branch}",
        "-f", f"base={base_branch}", "-f", f"title={title}", "-F", "draft=true"))


def mark_draft_pr_ready(gh, repository, pr_number, *, hostname="github.com"):
    merge_train._positive(pr_number, "PR number")
    endpoint = f"repos/{repository}/pulls/{pr_number}"
    before = _pull_request(_api(gh, hostname, endpoint))
    _batch_branch(before["branch"])
    if before["number"] != pr_number or before["base_branch"] != "main":
        raise ValidationError("PR identity differs from the batch publication target")
    _run_gh(gh, ["pr", "ready", str(pr_number),
                 "--repo", f"{hostname}/{repository}"])
    return _pull_request(_api(gh, hostname, endpoint))


def register_publisher(*, gh, provider_identity, implementation_sha256, release_sha256,
                       local_repository, git):
    """Build a closed publisher registration after observing its repository.

    The host supplies its accepted implementation/release pins. Registration
    grants only the three publication capabilities and two read callbacks.
    """
    identity = merge_train._provider_identity(provider_identity)
    host = urlsplit(identity["host"])
    if (host.scheme != "https" or not host.hostname or host.netloc != host.hostname
            or host.path or host.query or host.fragment
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", identity["repository"])):
        raise ValidationError("publisher provider identity has an invalid host or repository")
    repository = identity["repository"]
    hostname = host.hostname
    local_repository, git = _local_binding(local_repository, git)
    registration = {
        "producer_kind": "merge_train_publisher", "adapter_kind": "gh",
        "implementation_sha256": implementation_sha256,
        "release_sha256": release_sha256,
        "capabilities": list(merge_train.PUBLISHER_CAPABILITIES),
        "provider_identity": deepcopy(identity),
        "receipt_namespace": merge_train.PUBLISHER_NAMESPACE,
        "push_batch_ref": lambda branch, head: push_batch_ref(
            gh, repository, branch, head, hostname=hostname,
            local_repository=local_repository, git=git),
        "create_draft_pr": lambda branch, base, title: create_draft_pr(
            gh, repository, branch, base, title, hostname=hostname),
        "mark_draft_pr_ready": lambda number: mark_draft_pr_ready(
            gh, repository, number, hostname=hostname),
        "observe_batch_ref": lambda branch: observe_batch_ref(
            gh, repository, branch, hostname=hostname),
        "observe_pull_request": lambda branch: observe_pull_request(
            gh, repository, branch, hostname=hostname),
    }
    merge_train._validate_publisher(registration, identity)
    observed = _api(gh, hostname, f"repos/{repository}")
    if (not isinstance(observed, dict)
            or type(observed.get("id")) is not int
            or observed["id"] != identity["repository_id"]
            or observed.get("full_name") != repository):
        raise ValidationError("gh repository differs from the publisher provider identity")
    return registration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    parser.error("publication requires the host's registered publish_batch path")


if __name__ == "__main__":
    raise SystemExit(main())
