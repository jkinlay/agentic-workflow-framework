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
import uuid

from . import ValidationError
from .canonical import fingerprint, timestamp


FALLBACK_LABEL = "fallback: bot-written"
DEFAULT_GENERATED_PATHS = frozenset({"MANIFEST.json", "MANIFEST.md"})
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SCRIPT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_WRITER_LINE = re.compile(r"^Writer:\s*(.*?)\s*$")
_TRAILER_LINE = re.compile(r"^[A-Za-z0-9-]+:\s+\S.*$")


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


def parse_writer_trailer(message):
    """Parse the sole final ``Writer`` trailer, or return ``None`` when absent.

    Writer-like lines elsewhere are rejected rather than treated as provenance;
    this prevents body text and duplicate trailers from becoming authority.
    """
    _require(isinstance(message, str) and "\x00" not in message,
             "Commit message must be text without NUL bytes")
    lines = message.splitlines()
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
    expected = {"schema_version", "override_sha256", "who", "when", "why", "pr",
                "covered_shas"}
    _require(isinstance(record, dict) and set(record) == expected,
             "Provenance override has unsupported or missing fields")
    _require(record["schema_version"] == 1, "Unsupported provenance override version")
    _require(isinstance(record["who"], (str, int)) and not isinstance(record["who"], bool)
             and str(record["who"]).strip() == str(record["who"])
             and not any(ord(char) < 32 for char in str(record["who"])),
             "Provenance override needs an owner identity")
    timestamp(record["when"])
    _require(isinstance(record["why"], str) and record["why"].strip() == record["why"]
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
    digest = fingerprint("provenance_override", _override_payload(record))
    _require(record["override_sha256"] == digest,
             "Provenance override digest is missing or stale")
    return record


def verify_owner_override(record, *, trusted_owner_ids, pr=None):
    """Validate a sealed override against reviewed owner IDs and optional PR."""
    _verify_override_shape(record)
    _require(isinstance(trusted_owner_ids, (list, tuple, set, frozenset))
             and record["who"] in trusted_owner_ids,
             "Provenance override requires the owner or a trusted owner id")
    if pr is not None:
        _require(type(pr) is int and record["pr"] == pr,
                 "Provenance override is bound to a different PR")
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


def render_owner_override(record):
    """Render the exact auditable section the controller can place on the PR."""
    _verify_override_shape(record)
    commits = "\n".join(f"  - `{value}`" for value in record["covered_shas"])
    return (
        "<!-- awf-provenance-override:start -->\n"
        "## AWF provenance override\n\n"
        f"- PR: #{record['pr']}\n"
        f"- Who: {record['who']}\n"
        f"- When: {record['when']}\n"
        f"- Why: {record['why']}\n"
        f"- Override SHA-256: `{record['override_sha256']}`\n"
        "- Covered commits:\n"
        f"{commits}\n"
        "<!-- awf-provenance-override:end -->\n"
    )


class _Git:
    def __init__(self, root):
        path = Path(root).resolve()
        _require(path.is_dir(), "Provenance repository root must be an existing directory")
        self.root = path

    def run(self, *args, binary=False, allow_failure=False):
        completed = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "strict",
            timeout=60,
        )
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
        result = self.run("show", f"{commit}:{path}", binary=True, allow_failure=True)
        if result.returncode:
            return None
        return result.stdout


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


def _origin_main_merge(git, details, origin_main):
    parents = details["parents"]
    if len(parents) != 2:
        return False, "commit is not a two-parent merge"
    origin = git.resolve(origin_main, "origin/main ref")
    lineage = git.run("merge-base", "--is-ancestor", parents[1], origin,
                      allow_failure=True)
    if lineage.returncode:
        return False, "second parent is not in origin/main history"
    reproduced = git.run("merge-tree", "--write-tree", parents[0], parents[1],
                         allow_failure=True)
    if reproduced.returncode:
        return False, "origin/main merge could not be reproduced without conflict resolution"
    output = reproduced.stdout.splitlines()
    if not output or _SHA.fullmatch(output[0].strip()) is None:
        return False, "origin/main merge reproduction returned no tree"
    actual = git.text("rev-parse", f"{details['sha']}^{{tree}}")
    if output[0].strip() != actual:
        return False, "origin/main merge tree differs from Git's automatic merge result"
    return True, "origin/main merge"


def _generated_commit(git, details, generated_paths, reproduce_generated):
    paths = details["changed_files"]
    if not paths or not set(paths).issubset(generated_paths):
        return False, ("changed paths are not limited to declared generated outputs: "
                       + (", ".join(paths) if paths else "<none>"))
    if not callable(reproduce_generated):
        return False, "generated outputs were not reproduced byte-for-byte"
    try:
        reproduced = reproduce_generated(details["sha"], list(paths))
    except Exception as exc:
        return False, f"generator reproduction failed: {type(exc).__name__}: {exc}"
    if not isinstance(reproduced, dict) or set(reproduced) != set(paths):
        return False, "generator reproduction did not return every changed path exactly once"
    for path in paths:
        expected = reproduced[path]
        actual = git.blob(details["sha"], path)
        if not isinstance(expected, bytes) or actual is None or expected != actual:
            return False, f"generated output {path} did not reproduce byte-for-byte"
    return True, "byte-for-byte generated outputs"


def classify_mechanical_commit(git, details, *, origin_main="origin/main",
                               generated_paths=DEFAULT_GENERATED_PATHS,
                               reproduce_generated=None):
    """Classify only reproducible mainline merges or exact generator output."""
    if len(details["parents"]) > 1:
        return _origin_main_merge(git, details, origin_main)
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
                       pr_labels=()):
    """Check every ``base..head`` commit and report a refusal reason per commit.

    Optional run logs fail only when configured as required; an observed mismatch
    always fails.  A sealed owner override applies only to its named SHAs and only
    on a PR carrying the exact fallback label.
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
        override_record = verify_owner_override(
            override, trusted_owner_ids=trusted_owner_ids, pr=pr_number
        )
        covered = set(override_record["covered_shas"])
        _require(covered.issubset(set(commits)),
                 "Provenance override names commits outside the checked PR range")

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
