"""Conservative Writer/Critic provenance primitives.

This module is intentionally independent of the merge gate.  It validates Git
history and produces records which the gate can consume after the AWF-49 part-b
schema and policy wiring lands.  It never updates refs or worktrees, creates
commits, posts comments, grants an override, or authenticates a host.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from . import ValidationError
from .canonical import fingerprint, timestamp
from .child_process import child_env, isolated_git_env


FALLBACK_LABEL = "fallback: bot-written"
DEFAULT_GENERATED_PATHS = frozenset({"MANIFEST.json", "MANIFEST.md"})
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SCRIPT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_WRITER_LINE = re.compile(r"^Writer:\s*(.*?)\s*$")
_TRAILER_LINE = re.compile(r"^[A-Za-z0-9-]+:\s+\S.*$")
_NON_GIT_LINE_SEPARATOR = re.compile(r"[\v\f\x1c-\x1e\x85\u2028\u2029]|\r(?!\n)")


def _require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def _run_id(value, field="run id"):
    _require(isinstance(value, str) and value == value.strip(),
             f"{field} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(f"{field} must be a canonical UUID") from exc
    _require(str(parsed) == value, f"{field} must be a canonical lowercase UUID")
    _require(parsed.int != 0, f"{field} must not be the nil UUID")
    return value


def _sha(value, field="Git SHA"):
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None,
             f"{field} must be a full lowercase Git object id")
    return value


def format_writer_trailer(kind, identity):
    """Return one canonical Writer trailer after validating its namespace."""
    _require(kind in {"codex", "script"}, "Writer kind must be codex or script")
    if kind == "codex":
        identity = _run_id(identity, "Codex run id")
    else:
        _require(isinstance(identity, str) and len(identity) <= 64
                 and _SCRIPT.fullmatch(identity) is not None,
                 "Writer script name must be a lowercase portable identifier")
    return f"Writer: {kind}/{identity}"


def _split_git_message_lines(message):
    """Split only the LF and CRLF boundaries recognized in Git messages."""
    _require(_NON_GIT_LINE_SEPARATOR.search(message) is None,
             "Commit message contains an unsupported line or paragraph separator; "
             "only LF and CRLF are allowed")
    return [line[:-1] if line.endswith("\r") else line
            for line in message.split("\n")]


def parse_writer_trailer(message):
    """Parse the sole final ``Writer`` trailer, or return ``None`` when absent.

    Writer-like lines elsewhere are rejected rather than treated as provenance;
    this prevents body text and duplicate trailers from becoming authority.
    """
    _require(isinstance(message, str) and "\x00" not in message,
             "Commit message must be text without NUL bytes")
    lines = _split_git_message_lines(message)
    writer_lines = [(index, line) for index, line in enumerate(lines)
                    if re.match(r"^writer\s*:", line, re.IGNORECASE)]
    if not writer_lines:
        return None
    _require(len(writer_lines) == 1, "Commit message must contain exactly one Writer trailer")
    index, line = writer_lines[0]
    last = len(lines) - 1
    while last >= 0 and not lines[last].strip():
        last -= 1
    block_start = last
    while block_start >= 0 and _TRAILER_LINE.fullmatch(lines[block_start]):
        block_start -= 1
    block_start += 1
    separated = block_start == 0 or not lines[block_start - 1].strip()
    _require(separated and block_start <= index <= last,
             "Writer provenance must be in the final trailer block")
    matched = _WRITER_LINE.fullmatch(line)
    _require(matched is not None, "Writer trailer key must be exactly 'Writer'")
    value = matched.group(1)
    _require(value.count("/") == 1, "Writer trailer must be codex/<run id> or script/<name>")
    kind, identity = value.split("/", 1)
    canonical = format_writer_trailer(kind, identity)
    _require(line == canonical, "Writer trailer is not in canonical form")
    return {"kind": kind, "identity": identity, "value": value, "trailer": canonical}


def validate_critic_run_binding(verdict, *, reviewed_head=None):
    """Bind a critic verdict's run UUID to exactly one reviewed head."""
    _require(isinstance(verdict, dict), "Critic verdict must be an object")
    run_id = verdict.get("run_id")
    _require(run_id is not None, "Critic verdict is missing its critic run id")
    _run_id(run_id, "Critic run id")
    head = verdict.get("head_sha")
    _require(head is not None, "Critic verdict is missing its reviewed head")
    _sha(head, "Critic reviewed head")
    if reviewed_head is not None:
        _sha(reviewed_head, "Expected reviewed head")
        _require(head == reviewed_head, "Critic verdict names a different reviewed head")
    candidate = verdict.get("candidate_binding")
    if candidate is not None:
        _require(isinstance(candidate, dict) and candidate.get("head_sha") == head,
                 "Candidate binding disagrees with the verdict reviewed head")
    artifact = verdict.get("critic_artifact_binding")
    if artifact is not None:
        _require(isinstance(artifact, dict)
                 and artifact.get("critic_run_id") == run_id
                 and artifact.get("head_sha") == head,
                 "Critic artifact binding disagrees with the verdict run or reviewed head")
    review = verdict.get("critic_review")
    if review is not None:
        _require(isinstance(review, dict) and review.get("run_id") == run_id
                 and review.get("head_sha") == head,
                 "Embedded critic review disagrees with the verdict run or reviewed head")
    return {"critic_run_id": run_id, "head_sha": head,
            "trailer": f"Critic run: {run_id}"}


def _override_payload(record):
    return {key: value for key, value in record.items() if key != "override_sha256"}


def _verify_override_shape(record):
    version = record.get("schema_version") if isinstance(record, dict) else None
    expected = {"schema_version", "override_sha256", "who", "when", "why", "pr",
                "covered_shas"}
    if version == 2:
        expected.add("supersedes")
    _require(isinstance(record, dict) and set(record) == expected,
             "Provenance override has unsupported or missing fields")
    _require(version in {1, 2}, "Unsupported provenance override version")
    _require(isinstance(record["who"], (str, int)) and not isinstance(record["who"], bool)
             and bool(str(record["who"]))
             and str(record["who"]).strip() == str(record["who"])
             and not any(ord(char) < 32 for char in str(record["who"])),
             "Provenance override needs an owner identity")
    timestamp(record["when"])
    _require(isinstance(record["why"], str) and bool(record["why"])
             and record["why"].strip() == record["why"]
             and len(record["why"]) <= 4096
             and not any(ord(char) < 32 for char in record["why"]),
             "Provenance override needs a bounded reason")
    _require(type(record["pr"]) is int and record["pr"] > 0,
             "Provenance override needs a positive PR number")
    shas = record["covered_shas"]
    _require(isinstance(shas, list) and shas and len(shas) == len(set(shas)),
             "Provenance override needs unique covered commit SHAs")
    for value in shas:
        _sha(value, "Covered commit SHA")
    if version == 2:
        _require(isinstance(record["supersedes"], str)
                 and _DIGEST.fullmatch(record["supersedes"]) is not None,
                 "Superseding provenance override needs the current override SHA-256")
    digest = fingerprint("provenance_override", _override_payload(record))
    _require(record["override_sha256"] == digest,
             "Provenance override digest is missing or stale")
    return record


def verify_owner_override(record, *, trusted_owner_ids, pr=None, supersedes=None):
    """Validate a sealed override against reviewed owner IDs and optional PR."""
    _verify_override_shape(record)
    _require(isinstance(trusted_owner_ids, (list, tuple, set, frozenset))
             and record["who"] in trusted_owner_ids,
             "Provenance override requires the owner or a trusted owner id")
    if pr is not None:
        _require(type(pr) is int and record["pr"] == pr,
                 "Provenance override is bound to a different PR")
    if supersedes is not None:
        verify_owner_override(
            supersedes, trusted_owner_ids=trusted_owner_ids, pr=record["pr"]
        )
        _require(record["schema_version"] == 2
                 and record["supersedes"] == supersedes["override_sha256"],
                 "Superseding provenance override does not reference the current record")
        _require(set(supersedes["covered_shas"]).issubset(record["covered_shas"]),
                 "Superseding provenance override must retain all covered commit SHAs")
    return record


def record_owner_override(state, *, who, when, why, pr, covered_shas,
                          trusted_owner_ids):
    """Seal and durably add one owner-approved fallback record to AWF state."""
    _require(isinstance(state, dict), "AWF state must be an object")
    draft = {
        "schema_version": 1,
        "override_sha256": "",
        "who": who,
        "when": when,
        "why": why,
        "pr": pr,
        "covered_shas": sorted(covered_shas) if isinstance(covered_shas, list) else covered_shas,
    }
    # Validate all authority and shape before mutating caller-owned state.
    draft["override_sha256"] = fingerprint("provenance_override", _override_payload(draft))
    verify_owner_override(draft, trusted_owner_ids=trusted_owner_ids, pr=pr)
    existing = state.get("provenance_overrides", {})
    _require(isinstance(existing, dict), "AWF provenance override state is malformed")
    key = str(pr)
    _require(key not in existing, "This PR already has a provenance override record")
    updated = copy.deepcopy(existing)
    updated[key] = copy.deepcopy(draft)
    state["provenance_overrides"] = updated
    return copy.deepcopy(draft)


def supersede_owner_override(state, *, who, when, why, pr, covered_shas,
                             supersedes, trusted_owner_ids):
    """Append a sealed replacement while preserving the original record.

    The original v1 record remains at ``provenance_overrides[pr]``.  Later
    records form a digest-linked append-only list in the separate
    ``provenance_override_supersessions`` map.  The full existing chain and
    the proposed replacement are validated before caller-owned state changes.
    """
    _require(isinstance(state, dict), "AWF state must be an object")
    originals = state.get("provenance_overrides", {})
    _require(isinstance(originals, dict), "AWF provenance override state is malformed")
    key = str(pr)
    _require(key in originals, "This PR has no provenance override record to supersede")
    current = verify_owner_override(
        originals[key], trusted_owner_ids=trusted_owner_ids, pr=pr
    )
    histories = state.get("provenance_override_supersessions", {})
    _require(isinstance(histories, dict),
             "AWF provenance override supersession state is malformed")
    history = histories.get(key, [])
    _require(isinstance(history, list),
             "AWF provenance override supersession history is malformed")
    for existing in history:
        verify_owner_override(
            existing, trusted_owner_ids=trusted_owner_ids, pr=pr,
            supersedes=current,
        )
        current = existing

    draft = {
        "schema_version": 2,
        "override_sha256": "",
        "who": who,
        "when": when,
        "why": why,
        "pr": pr,
        "covered_shas": sorted(covered_shas) if isinstance(covered_shas, list)
        else covered_shas,
        "supersedes": supersedes,
    }
    draft["override_sha256"] = fingerprint(
        "provenance_override", _override_payload(draft)
    )
    verify_owner_override(
        draft, trusted_owner_ids=trusted_owner_ids, pr=pr, supersedes=current
    )
    _require(set(current["covered_shas"]).issubset(set(draft["covered_shas"])),
             "Superseding provenance override must retain all covered commit SHAs")

    updated = copy.deepcopy(histories)
    updated.setdefault(key, []).append(copy.deepcopy(draft))
    state["provenance_override_supersessions"] = updated
    return copy.deepcopy(draft)


def render_owner_override(record):
    """Render the exact auditable section the controller can place on the PR."""
    _verify_override_shape(record)
    commits = "\n".join(f"  - `{value}`" for value in record["covered_shas"])
    supersession = (
        f"- Supersedes override SHA-256: `{record['supersedes']}`\n"
        if record["schema_version"] == 2 else ""
    )
    return (
        "<!-- awf-provenance-override:start -->\n"
        "## AWF provenance override\n\n"
        f"- PR: #{record['pr']}\n"
        f"- Who: {record['who']}\n"
        f"- When: {record['when']}\n"
        f"- Why: {record['why']}\n"
        f"- Override SHA-256: `{record['override_sha256']}`\n"
        f"{supersession}"
        "- Covered commits:\n"
        f"{commits}\n"
        "<!-- awf-provenance-override:end -->\n"
    )


def _verify_override_chain(record, predecessors, *, trusted_owner_ids, pr):
    """Verify an override and every digest-linked predecessor, oldest first."""
    _require(isinstance(predecessors, (list, tuple)),
             "Provenance override predecessors must be an ordered list")
    _verify_override_shape(record)
    if record["schema_version"] == 1:
        _require(not predecessors,
                 "An original provenance override cannot have predecessors")
        return verify_owner_override(
            record, trusted_owner_ids=trusted_owner_ids, pr=pr
        )
    _require(predecessors,
             "A superseding provenance override requires its predecessor chain")
    current = None
    for predecessor in predecessors:
        if current is None:
            verified = verify_owner_override(
                predecessor, trusted_owner_ids=trusted_owner_ids, pr=pr
            )
            _require(verified["schema_version"] == 1,
                     "Provenance override predecessor chain must start with the original record")
        else:
            verified = verify_owner_override(
                predecessor, trusted_owner_ids=trusted_owner_ids, pr=pr,
                supersedes=current,
            )
        current = verified
    return verify_owner_override(
        record, trusted_owner_ids=trusted_owner_ids, pr=pr,
        supersedes=current,
    )


class _Git:
    def __init__(self, root):
        path = Path(root).resolve()
        _require(path.is_dir(), "Provenance repository root must be an existing directory")
        self.root = path

    def run(self, *args, binary=False, allow_failure=False, environment=None):
        try:
            completed = subprocess.run(
                ["git", "-c", "safe.directory=" + str(self.root),
                 "-C", str(self.root), *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=not binary,
                encoding=None if binary else "utf-8",
                errors=None if binary else "strict",
                timeout=60,
                shell=False,
                check=False,
                env=child_env(isolated_git_env(extra=environment)),
            )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise ValidationError(
                f"Git provenance inspection failed ({args[0]}): {type(exc).__name__}"
            ) from exc
        if completed.returncode and not allow_failure:
            error = (completed.stderr if binary else completed.stderr.strip())
            if isinstance(error, bytes):
                error = error.decode("utf-8", "replace").strip()
            raise ValidationError(f"Git provenance inspection failed ({' '.join(args)}): {error}")
        return completed

    def text(self, *args):
        return self.run(*args).stdout.strip()

    def resolve(self, value, field):
        _require(isinstance(value, str) and value and not value.startswith("-"),
                 f"{field} must be a Git commit or ref")
        resolved = self.text("rev-parse", "--verify", f"{value}^{{commit}}")
        return _sha(resolved, field)

    def blob(self, commit, path):
        entry = self.entry(commit, path)
        return None if entry is None else entry["content"]

    def entry(self, treeish, path, *, environment=None):
        result = self.run(
            "ls-tree", "-z", treeish, "--", path,
            binary=True, allow_failure=True, environment=environment,
        )
        if result.returncode or not result.stdout:
            return None
        rows = [row for row in result.stdout.split(b"\0") if row]
        if len(rows) != 1 or b"\t" not in rows[0]:
            return None
        metadata, raw_path = rows[0].split(b"\t", 1)
        try:
            mode, object_type, object_id = metadata.decode("ascii").split()
            decoded_path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        if decoded_path != path:
            return None
        content = self.run(
            "cat-file", object_type, object_id,
            binary=True, allow_failure=True, environment=environment,
        )
        if content.returncode:
            return None
        return {
            "mode": mode,
            "type": object_type,
            "object_id": object_id,
            "content": content.stdout,
        }

    def object_directory(self):
        value = self.text("rev-parse", "--git-path", "objects")
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        _require(path.is_dir(), "Git provenance object database is unavailable")
        return path


def _commit_details(git, commit):
    row = git.text("rev-list", "--parents", "-n", "1", commit).split()
    parents = row[1:]
    if parents:
        changed = git.run("diff", "--no-renames", "--name-only", "-z",
                          parents[0], commit).stdout
    else:
        changed = git.run("diff-tree", "--root", "--no-commit-id", "--name-only",
                          "-r", "-z", commit).stdout
    return {
        "sha": commit,
        "parents": parents,
        "message": git.run("show", "-s", "--format=%B", commit).stdout,
        "changed_files": sorted(path for path in changed.split("\x00") if path),
    }


def _decode_merge_tree_output(output):
    fields = output.split(b"\0")
    try:
        tree = fields[0].decode("ascii")
        paths = [field.decode("utf-8") for field in fields[1:] if field]
    except UnicodeDecodeError:
        return None, None
    if _SHA.fullmatch(tree) is None:
        return None, None
    return tree, paths


def _expected_generated_entry(value):
    if isinstance(value, bytes):
        return {"mode": "100644", "type": "blob", "content": value}
    if not isinstance(value, dict) or set(value) != {"mode", "type", "content"}:
        return None
    if (value["mode"] not in {"100644", "100755"}
            or value["type"] != "blob" or not isinstance(value["content"], bytes)):
        return None
    return value


def _verify_generated_entries(git, details, paths, reproduce_generated):
    if not callable(reproduce_generated):
        return False, "generated outputs were not reproduced byte-for-byte with mode/type"
    try:
        reproduced = reproduce_generated(details["sha"], list(paths))
    except Exception as exc:
        return False, f"generator reproduction failed: {type(exc).__name__}: {exc}"
    if not isinstance(reproduced, dict) or set(reproduced) != set(paths):
        return False, "generator reproduction did not return every changed path exactly once"
    for path in paths:
        expected = _expected_generated_entry(reproduced[path])
        if expected is None:
            return False, (
                f"generated output {path} reproduction needs bytes or an exact "
                "mode/type/content entry"
            )
        actual = git.entry(details["sha"], path)
        if actual is None:
            return False, f"generated output {path} is not a committed tree entry"
        if (expected["mode"], expected["type"]) != (actual["mode"], actual["type"]):
            return False, (
                f"generated output {path} mode/type did not reproduce "
                f"({actual['mode']} {actual['type']} committed)"
            )
        if expected["content"] != actual["content"]:
            return False, f"generated output {path} did not reproduce byte-for-byte"
    return True, "byte-for-byte generated outputs with matching mode/type"


def _origin_main_merge(git, details, origin_main, generated_paths,
                       reproduce_generated):
    parents = details["parents"]
    if len(parents) != 2:
        return False, "commit is not a two-parent merge"
    origin = git.resolve(origin_main, "origin/main ref")
    lineage = git.run("merge-base", "--is-ancestor", parents[1], origin,
                      allow_failure=True)
    if lineage.returncode:
        return False, "second parent is not in origin/main history"
    source_objects = git.object_directory()
    actual = git.text("rev-parse", f"{details['sha']}^{{tree}}")
    with tempfile.TemporaryDirectory(prefix="awf-provenance-objects-") as directory:
        object_directory = Path(directory).resolve() / "objects"
        object_directory.mkdir()
        environment = {
            "GIT_OBJECT_DIRECTORY": str(object_directory),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(source_objects),
        }
        reproduced = git.run(
            "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z",
            parents[0], parents[1], binary=True, allow_failure=True,
            environment=environment,
        )
        tree, conflict_paths = _decode_merge_tree_output(reproduced.stdout)
        if tree is None:
            return False, "origin/main merge reproduction failed in a disposable object database"
        if reproduced.returncode == 0:
            if conflict_paths:
                return False, "origin/main merge reproduction returned unexpected conflict paths"
            if tree != actual:
                return False, "origin/main merge tree differs from Git's automatic merge result"
            return True, "origin/main merge"
        if reproduced.returncode != 1 or not conflict_paths:
            return False, "origin/main merge reproduction failed in a disposable object database"
        if not set(conflict_paths).issubset(generated_paths):
            return False, (
                "origin/main merge has non-generated conflicts: "
                + ", ".join(conflict_paths)
            )
        differences = git.run(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", tree, actual,
            environment=environment,
        ).stdout
        changed_from_automatic = {path for path in differences.split("\0") if path}
        if not changed_from_automatic.issubset(set(conflict_paths)):
            return False, (
                "origin/main merge conflict resolution changed non-conflicted paths: "
                + ", ".join(sorted(changed_from_automatic - set(conflict_paths)))
            )
        matched, reason = _verify_generated_entries(
            git, details, sorted(set(conflict_paths)), reproduce_generated
        )
        if not matched:
            return False, "generated conflict resolution " + reason
        return True, "origin/main merge with reproduced generated conflict resolution"


def _generated_commit(git, details, generated_paths, reproduce_generated):
    paths = details["changed_files"]
    if not paths or not set(paths).issubset(generated_paths):
        return False, ("changed paths are not limited to declared generated outputs: "
                       + (", ".join(paths) if paths else "<none>"))
    return _verify_generated_entries(git, details, paths, reproduce_generated)


def classify_mechanical_commit(git, details, *, origin_main="origin/main",
                               generated_paths=DEFAULT_GENERATED_PATHS,
                               reproduce_generated=None):
    """Classify only reproducible mainline merges or exact generator output."""
    if len(details["parents"]) > 1:
        return _origin_main_merge(
            git, details, origin_main, set(generated_paths), reproduce_generated
        )
    return _generated_commit(git, details, set(generated_paths), reproduce_generated)


def _run_log_status(verifier, run_id, sha, paths, required):
    if verifier is None:
        return (not required,
                "required run-log verification is not configured" if required
                else "optional run log unobserved")
    try:
        result = verifier(run_id, sha, list(paths))
    except Exception as exc:
        return False, f"run-log verification failed: {type(exc).__name__}: {exc}"
    if result is None:
        return (not required,
                "required run log is absent" if required else "optional run log absent")
    if result is True:
        return True, "run log matches commit and changed files"
    if result is False:
        return False, "run log does not match the commit and changed files"
    if isinstance(result, dict) and result.get("status") in {"MATCH", "ABSENT", "MISMATCH"}:
        status = result["status"]
        reason = result.get("reason") or {
            "MATCH": "run log matches commit and changed files",
            "ABSENT": "run log is absent",
            "MISMATCH": "run log does not match the commit and changed files",
        }[status]
        if not isinstance(reason, str) or not reason.strip():
            return False, "run-log verifier returned an invalid reason"
        return status == "MATCH" or status == "ABSENT" and not required, reason
    return False, "run-log verifier returned an unsupported result"


def check_commit_range(root, base, head, *, origin_main="origin/main",
                       generated_paths=(), reproduce_generated=None,
                       run_log_verifier=None, require_run_logs=False,
                       override=None, trusted_owner_ids=(), pr_number=None,
                       pr_labels=(), override_predecessors=()):
    """Check every ``base..head`` commit and report a refusal reason per commit.

    Optional run logs fail only when configured as required; an observed mismatch
    always fails.  A sealed owner override applies only to its named SHAs and only
    on a PR carrying the exact fallback label.  A superseding override must be
    accompanied by its ordered, oldest-first predecessor chain so its digest
    link cannot be accepted in isolation.
    """
    _require(type(require_run_logs) is bool, "require_run_logs must be Boolean")
    git = _Git(root)
    base_sha = git.resolve(base, "Range base")
    head_sha = git.resolve(head, "Range head")
    ancestry = git.run("merge-base", "--is-ancestor", base_sha, head_sha,
                       allow_failure=True)
    _require(ancestry.returncode == 0, "Range base is not an ancestor of range head")
    commits_text = git.text("rev-list", "--reverse", "--topo-order",
                            f"{base_sha}..{head_sha}")
    commits = commits_text.splitlines() if commits_text else []
    override_record = None
    covered = set()
    if override is not None:
        _require(isinstance(pr_labels, (list, tuple, set, frozenset))
                 and FALLBACK_LABEL in pr_labels,
                 f"Provenance override requires the exact '{FALLBACK_LABEL}' fallback label")
        _require(type(pr_number) is int and pr_number > 0,
                 "Provenance override checking requires the bound PR number")
        override_record = _verify_override_chain(
            override, override_predecessors,
            trusted_owner_ids=trusted_owner_ids, pr=pr_number,
        )
        covered = set(override_record["covered_shas"])
        _require(covered.issubset(set(commits)),
                 "Provenance override names commits outside the checked PR range")
    else:
        _require(not override_predecessors,
                 "Provenance override predecessors require an override record")

    allowed_generated = set(DEFAULT_GENERATED_PATHS)
    _require(isinstance(generated_paths, (list, tuple, set, frozenset))
             and all(isinstance(path, str) and path and "\\" not in path
                     and not path.startswith(("/", "./")) and ":" not in path
                     and all(part not in {"", ".", ".."} for part in path.split("/"))
                     for path in generated_paths),
             "generated_paths must contain portable repository paths")
    allowed_generated.update(generated_paths)
    results = []
    refusals = []
    used_overrides = set()
    for commit in commits:
        details = _commit_details(git, commit)
        item = {
            "sha": commit,
            "writer": None,
            "changed_files": details["changed_files"],
            "mechanical": False,
            "mechanical_reason": None,
            "run_log": None,
            "status": "PASS",
            "reasons": [],
        }
        try:
            trailer = parse_writer_trailer(details["message"])
        except ValidationError as exc:
            trailer = None
            item["reasons"].append(str(exc))
        if trailer is None and not item["reasons"]:
            item["reasons"].append("missing Writer trailer")
        if trailer is not None:
            item["writer"] = trailer["value"]
            if trailer["kind"] == "script":
                try:
                    mechanical, reason = classify_mechanical_commit(
                        git, details, origin_main=origin_main,
                        generated_paths=allowed_generated,
                        reproduce_generated=reproduce_generated,
                    )
                except (ValidationError, OSError, subprocess.SubprocessError) as exc:
                    mechanical, reason = False, str(exc)
                item["mechanical"] = mechanical
                item["mechanical_reason"] = reason
                if not mechanical:
                    item["reasons"].append(
                        "script-tagged commit is not reproducibly mechanical: " + reason
                    )
            else:
                matched, reason = _run_log_status(
                    run_log_verifier, trailer["identity"], commit,
                    details["changed_files"], require_run_logs,
                )
                item["run_log"] = reason
                if not matched:
                    item["reasons"].append(reason)
        if item["reasons"]:
            if commit in covered:
                item["status"] = "OVERRIDDEN"
                item["override_sha256"] = override_record["override_sha256"]
                used_overrides.add(commit)
            else:
                item["status"] = "REFUSE"
                refusals.append({"sha": commit, "reason": "; ".join(item["reasons"])})
        results.append(item)
    _require(used_overrides == covered,
             "Provenance override names commits that needed no override")
    return {
        "status": "PASS" if not refusals else "REFUSE",
        "base": base_sha,
        "head": head_sha,
        "commits": results,
        "refusals": refusals,
        "override_sha256": (override_record or {}).get("override_sha256"),
        "run_logs_required": require_run_logs,
    }
