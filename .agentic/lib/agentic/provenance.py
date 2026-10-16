"""Conservative Writer/Critic provenance primitives.

This module is intentionally independent of the merge gate.  It validates Git
history and produces records which the gate can consume after the AWF-49 part-b
schema and policy wiring lands.  It never updates refs or worktrees, creates
commits, posts comments, grants an override, or authenticates a host.
"""
from __future__ import annotations

import copy
from hashlib import sha256
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
import uuid

from . import ValidationError
from .canonical import fingerprint, timestamp
from .child_process import child_env, isolated_git_env
from .safeio import run_bounded


FALLBACK_LABEL = "fallback: bot-written"
DEFAULT_GENERATED_PATHS = frozenset({"MANIFEST.json", "MANIFEST.md"})
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SCRIPT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_WRITER_LINE = re.compile(r"^Writer:\s*(.*?)\s*$")
_TRAILER_LINE = re.compile(r"^[A-Za-z0-9-]+:\s+\S.*$")
_NON_GIT_LINE_SEPARATOR = re.compile(r"[\v\f\x1c-\x1e\x85\u2028\u2029]|\r(?!\n)")
_SAFE_REF = re.compile(
    r"^refs/(?:heads|remotes)/[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$"
)
_MESSAGE_LIMIT = 1024 * 1024
_MESSAGE_LINE_LIMIT = 65536
_TRAILER_LIKE_LIMIT = 256
_GIT_OUTPUT_LIMIT = 32 * 1024 * 1024
_PATH_RECORD_LIMIT = 100000
_COMMIT_LIMIT = 10000
_GENERATED_BLOB_LIMIT = 32 * 1024 * 1024
_GENERATED_TOTAL_LIMIT = 512 * 1024 * 1024
_REASON_LIMIT = 4096
_REPOSITORY_FILE_LIMIT = 1024 * 1024
_WINDOWS_RESERVED_STEMS = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
    *(f"com{number}" for number in "¹²³"),
    *(f"lpt{number}" for number in "¹²³"),
})

# Unicode confusables whose names and NFKC forms do not identify their visual
# colon role.  NFKC and Unicode character names cover the general case below;
# this fixed fallback keeps the remaining reviewed confusables auditable.
_COLON_CONFUSABLES = frozenset({
    0x0589, 0x05C3, 0x1361, 0x16EC, 0xA4FD,
})

# Fixed Unicode 15 format-control inventory.  This deliberately does not use
# ``unicodedata.category``: grammar decisions must not drift when the runtime's
# Unicode database changes.  U+FEFF has its own more specific refusal code.
_FORMAT_CONTROLS = frozenset({
    0x00AD, *range(0x0600, 0x0606), 0x061C, 0x06DD, 0x070F,
    0x0890, 0x0891, 0x08E2, 0x180E, *range(0x200B, 0x2010),
    *range(0x202A, 0x202F), *range(0x2060, 0x2065),
    *range(0x2066, 0x2070), *range(0xFFF9, 0xFFFC), 0x110BD, 0x110CD,
    *range(0x13430, 0x13440), *range(0x1BCA0, 0x1BCA4),
    *range(0x1D173, 0x1D17B), 0xE0001, *range(0xE0020, 0xE0080),
})


def _contract(authority, failure, cap, code):
    return {"authority": authority, "failure": failure, "cap": cap,
            "reason_code": code}


# Auditable, version-stable inventory consumed by the per-row R13 tests and
# documented verbatim in doc 41.  It is data, not an authority source.
PROVENANCE_INPUT_CONTRACT = {
    "IN-01": _contract("git", "operation", "one repository", "REPOSITORY_INVALID"),
    "IN-02": _contract("frozen git refs", "operation", "10000 commits/32 MiB", "RANGE_INVALID"),
    "IN-03": _contract("reviewed config + provider", "operation", "one frozen OID", "DEFAULT_REF_UNBOUND"),
    "IN-04": _contract("git object database", "commit", "1 MiB message/32 MiB object", "COMMIT_OBJECT_INVALID"),
    "IN-05": _contract("raw commit bytes", "commit", "1 MiB/65536 lines", "MESSAGE_INVALID"),
    "IN-06": _contract("raw commit bytes", "commit", "LF or CRLF", "MESSAGE_SEPARATOR"),
    "IN-07": _contract("raw commit bytes", "commit", "256 trailer-like lines", "WRITER_AMBIGUOUS"),
    "IN-08": _contract("observed Codex thread", "commit", "one non-nil UUID", "RUN_ID_INVALID"),
    "IN-09": _contract("reviewed generator registry", "commit", "64 ASCII bytes", "SCRIPT_ID_INVALID"),
    "IN-10": _contract("shared grammar", "operation", "1 MiB output", "EMITTER_INVALID"),
    "IN-11": _contract("raw created commit", "operation", "one canonical message", "PUBLISHED_MESSAGE_MISMATCH"),
    "IN-12": _contract("raw git output", "commit", "32 MiB", "PATH_UTF8"),
    "IN-13": _contract("raw git output", "commit", "100000 records", "PATH_INVALID"),
    "IN-14": _contract("--raw -z --no-renames", "commit", "delete + add", "RENAME_COLLAPSED"),
    "IN-15": _contract("raw git output", "commit", "100000 records", "ENTRY_INVALID"),
    "IN-16": _contract("reviewed generator registry", "operation", "100000 paths", "GENERATED_PATH_INVALID"),
    "IN-17": _contract("registered reproducer", "commit", "32 MiB each/512 MiB total", "GENERATOR_INVALID"),
    "IN-18": _contract("isolated git plumbing", "commit", "32 MiB", "MERGE_TREE_INVALID"),
    "IN-19": _contract("registered verifier", "commit", "closed evidence record", "RUN_LOG_INVALID"),
    "IN-20": _contract("controller", "operation", "one non-nil UUID", "CONTROLLER_RUN_INVALID"),
    "IN-21": _contract("pinned file handle", "operation", "64 MiB/1 MiB event", "CODEX_LOG_INVALID"),
    "IN-22": _contract("pinned file handle", "operation", "1 MiB", "CODEX_RESULT_INVALID"),
    "IN-23": _contract("pinned file handle", "operation", "16 KiB", "IDENTITY_RECORD_INVALID"),
    "IN-24": _contract("observed critic artifact", "operation", "1 MiB", "CRITIC_BINDING_INVALID"),
    "IN-25": _contract("critic artifact carrier", "operation", "one final line", "CRITIC_LINE_INVALID"),
    "IN-26": _contract("observed owner comment", "operation", "4 KiB reason/255-byte owner", "OVERRIDE_FIELD_INVALID"),
    "IN-27": _contract("frozen range", "operation", "256 SHAs", "OVERRIDE_COVERAGE_INVALID"),
    "IN-28": _contract("digest-linked records", "operation", "32 records", "OVERRIDE_CHAIN_INVALID"),
    "IN-29": _contract("reviewed host config", "operation", "positive numeric IDs", "OWNER_TRUST_UNBOUND"),
    "IN-30": _contract("fresh provider observation", "operation", "one PR/100 labels", "PR_LABEL_UNBOUND"),
    "IN-31": _contract("fresh provider observation", "operation", "one coherent tuple", "PROVIDER_IDENTITY_INVALID"),
    "IN-32": _contract("fresh provider observation", "operation", "1 MiB", "PR_BODY_INVALID"),
    "IN-33": _contract("fully paged provider observation", "operation", "1000 comments/8 MiB wire", "PROVIDER_PAGINATION_INVALID"),
    "IN-34": _contract("owned subprocess", "operation", "8 MiB stdout", "HOST_STDOUT_LIMIT"),
    "IN-35": _contract("tested-tree receipt", "operation", "100000 paths", "WORKER_RECEIPT_INVALID"),
    "IN-36": _contract("raw git output", "operation", "32 MiB/100000 records", "PUBLISHER_PATH_INVALID"),
    "IN-37": _contract("shared grammar", "operation", "4 KiB subject", "TITLE_INVALID"),
    "IN-38": _contract("trusted template + escaped scalars", "operation", "1 MiB", "BODY_INVALID"),
    "IN-39": _contract("pinned file handle", "operation", "1 MiB", "REWRITE_MESSAGE_INVALID"),
    "IN-40": _contract("frozen git refs + local identity", "operation", "4 KiB identity fields", "REWRITE_IDENTITY_INVALID"),
    "IN-41": _contract("manifest/config pins", "operation", "channel-specific before allocation", "EXTERNAL_READ_LIMIT"),
    "IN-42": _contract("stable reason codes", "operation", "256 reasons/4 KiB each", "DIAGNOSTIC_INVALID"),
    "IN-43": _contract("raw git object", "commit", "32 MiB/65536 header lines", "COMMIT_HEADER_INVALID"),
    "IN-44": _contract("isolated explicit git config", "operation", "closed keys", "GIT_CONFIG_UNSAFE"),
    "IN-45": _contract("reviewed host config", "operation", "one Boolean", "RUN_LOG_POLICY_UNBOUND"),
    "IN-46": _contract("reviewed generator registry", "operation", "closed script mapping", "GENERATOR_UNREGISTERED"),
    "IN-47": _contract("durable CAS record", "operation", "1 MiB record", "RECOVERY_RECORD_INVALID"),
    "IN-48": _contract("owned subprocess", "operation", "8 MiB host/32 MiB git", "STDERR_LIMIT"),
    "IN-49": _contract("frozen refs + remote readback", "operation", "32 MiB response", "REMOTE_READBACK_INVALID"),
}


def _require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def _run_id(value, field="run id"):
    _require(isinstance(value, str) and value == value.strip(),
             f"RUN_ID_INVALID: {field} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(f"RUN_ID_INVALID: {field} must be a canonical UUID") from exc
    _require(str(parsed) == value,
             f"RUN_ID_INVALID: {field} must be a canonical lowercase UUID")
    _require(parsed.int != 0, f"RUN_ID_INVALID: {field} must not be the nil UUID")
    return value


def _sha(value, field="Git SHA"):
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None,
             f"OID_INVALID: {field} must be a full lowercase Git object id")
    return value


def format_writer_trailer(kind, identity):
    """Return one canonical Writer trailer after validating its namespace."""
    _require(kind in {"codex", "script"},
             "WRITER_KIND_INVALID: Writer kind must be codex or script")
    if kind == "codex":
        identity = _run_id(identity, "Codex run id")
    else:
        _require(isinstance(identity, str) and len(identity) <= 64
                 and _SCRIPT.fullmatch(identity) is not None,
                 "SCRIPT_ID_INVALID: Writer script name must be a lowercase portable identifier")
    return f"Writer: {kind}/{identity}"


def _safe_reason(code, *, ordinal=None, rejected=None):
    parts = [code]
    if ordinal is not None:
        parts.append(f"ordinal={ordinal}")
    if rejected is not None:
        parts.append("sha256=" + sha256(rejected).hexdigest())
    return " ".join(parts)[:_REASON_LIMIT]


def validate_commit_message(message):
    """Validate and LF-normalize the shared byte-exact message grammar."""
    _require(isinstance(message, str), "MESSAGE_TYPE: commit message must be text")
    try:
        encoded = message.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValidationError("MESSAGE_UTF8: commit message is not strict UTF-8") from exc
    _require(len(encoded) <= _MESSAGE_LIMIT,
             "MESSAGE_SIZE: commit message exceeds 1 MiB")
    _require("\ufeff" not in message, "MESSAGE_BOM: U+FEFF is forbidden")
    _require("\x00" not in message, "MESSAGE_NUL: NUL is forbidden")
    _require(not re.search(r"[\v\f\x1c-\x1e\x85\u2028\u2029]|\r(?!\n)", message),
             "MESSAGE_SEPARATOR: unsupported line separator; only LF and CRLF are accepted")
    # C0 permits HT/LF/CR only; CR is checked as part of CRLF below.  C1,
    # DEL and the fixed Unicode format-control set are always forbidden.
    for char in message:
        code = ord(char)
        if ((code < 32 and char not in "\t\n\r") or 0x7F <= code <= 0x9F
                or code in _FORMAT_CONTROLS):
            raise ValidationError(
                "MESSAGE_CONTROL: unsupported control or Unicode format character"
            )
    normalized = message.replace("\r\n", "\n")
    lines = normalized.split("\n")
    _require(len(lines) <= _MESSAGE_LINE_LIMIT,
             "MESSAGE_LINES: commit message exceeds 65536 lines")
    _require(bool(lines) and bool(lines[0].strip(" \t")),
             "MESSAGE_SUBJECT: commit subject must contain non-whitespace text")
    return normalized


def _ascii_fold_key(value):
    folded = []
    for char in value:
        code = ord(char)
        if code > 127 and unicodedata.category(char) in {"Zs", "Pd"}:
            # Reserved provenance keys treat every Unicode space/dash as a
            # visual separator, without changing the original message bytes.
            char = " "
        elif 0xFF01 <= code <= 0xFF5E:
            char = chr(code - 0xFEE0)
        folded.append(char)
    return "".join(folded)


def _colon_like(char):
    """Return whether one character can visually carry a reserved-key colon."""
    if char == ":" or unicodedata.normalize("NFKC", char) == ":":
        return True
    if ord(char) <= 127:
        return False
    name = unicodedata.name(char, "")
    return ("COLON" in name or "RATIO" in name
            or ord(char) in _COLON_CONFUSABLES)


def _fold_reserved_key(value):
    """Fold spacing, dashes, fullwidth ASCII, and colon confusables."""
    ascii_folded = _ascii_fold_key(value)
    return "".join(":" if _colon_like(original) else folded
                   for original, folded in zip(value, ascii_folded))


def _writer_like(line):
    candidate = _fold_reserved_key(line)
    # Search the entire physical line, including indented occurrences.  This
    # intentionally treats prose/code examples as ambiguous rather than proof.
    return re.search(r"(?i)(?:^|[ \t])writer[ \t]*:", candidate) is not None


def _writer_key_lookalike(line):
    """Detect deterministic non-ASCII Writer-key and colon confusables.

    A six-character key in which each position is either the expected ASCII
    Writer letter or a non-ASCII substitution is reserved, as is a canonical
    key followed by a colon lookalike.  This catches the ambiguity without
    depending on a runtime Unicode confusables database.
    """
    candidate = _fold_reserved_key(line)
    for matched in re.finditer(r"(?:^|[ \t])([^ \t:]+)[ \t]*(:)", candidate):
        key = matched.group(1)
        if len(key) != len("writer"):
            continue
        if key.casefold() == "writer" and line[matched.start(2)] != ":":
            return True
        substitutions = False
        for actual, expected in zip(key, "writer"):
            if ord(actual) > 127:
                substitutions = True
            elif actual.lower() != expected:
                break
        else:
            if substitutions:
                return True
    return False


def _confusable_ascii_token(actual, expected):
    """Return whether a token reserves ``expected`` via non-ASCII substitution."""
    if len(actual) != len(expected):
        return False
    substituted = False
    for character, canonical in zip(actual, expected):
        if ord(character) > 127:
            substituted = True
        elif character.lower() != canonical:
            return False
    return substituted


def _critic_key_lookalike(line):
    """Detect non-ASCII substitutions or colons in ``Critic run`` keys."""
    candidate = _fold_reserved_key(line)
    expression = r"(?:^|[ \t])([^ \t:-]+)[ \t-]+([^ \t:]+)[ \t]*(:)"
    for matched in re.finditer(expression, candidate):
        first, second, _ = matched.groups()
        first_matches = (first.casefold() == "critic" or
                         _confusable_ascii_token(first, "critic"))
        second_matches = (second.casefold() in {"run", "runs"} or
                          _confusable_ascii_token(second, "run") or
                          _confusable_ascii_token(second, "runs"))
        separator_is_lookalike = line[matched.start(3)] != ":"
        if first_matches and second_matches and (separator_is_lookalike or any(
                ord(character) > 127 for character in first + second)):
            return True
    return False


def validate_ref_locator(value, field="Git revision"):
    """Accept only full lowercase OIDs or unambiguous fully-qualified refs."""
    _require(isinstance(value, str) and value == value.strip(),
             f"REF_INVALID: {field} must be canonical")
    if _SHA.fullmatch(value):
        return value
    _require(_SAFE_REF.fullmatch(value) is not None
             and ".." not in value and "//" not in value
             and not value.endswith((".", ".lock")),
             f"REF_INVALID: {field} must be a full OID or refs/heads|remotes name")
    return value


def _portable_path_key(value):
    """Return the host-independent collision key for an accepted Git path."""
    return unicodedata.normalize("NFC", value).casefold()


def _validate_object_path(value):
    _require(isinstance(value, str) and value == unicodedata.normalize("NFC", value),
             "PATH_NFC: path must be NFC text")
    raw = value.encode("utf-8", "strict")
    _require(raw and len(raw) <= 4096, "PATH_SIZE: path is empty or too long")
    _require("\\" not in value and not value.startswith("/") and ":" not in value
             and not any(character in '<>"|?*' for character in value),
             "PATH_SYNTAX: path must be repository-relative POSIX text")
    parts = value.split("/")
    _require(all(part not in {"", ".", ".."} and len(part.encode("utf-8")) <= 255
                 for part in parts), "PATH_COMPONENT: unsafe path component")
    _require(not any(ord(char) < 32 or ord(char) == 127 for char in value),
             "PATH_CONTROL: path contains a control character")
    _require(all(not part.endswith((".", " ")) for part in parts),
             "PATH_TRAILING: path components may not end in dot or space")
    _require(all(part.split(".", 1)[0].casefold() not in _WINDOWS_RESERVED_STEMS
                 for part in parts),
             "PATH_RESERVED: Windows device names are forbidden on every host")
    _require(all(part.casefold() != ".git" for part in parts),
             "PATH_DOT_GIT: .git aliases are forbidden")
    return value


def decode_git_paths(raw, *, failure="operation", require_terminal_nul=True):
    """Decode bounded NUL records without ever echoing hostile path bytes."""
    _require(isinstance(raw, bytes), "PATH_STREAM_TYPE: Git path output must be bytes")
    _require(len(raw) <= _GIT_OUTPUT_LIMIT, "PATH_STREAM_SIZE: Git path output exceeds 32 MiB")
    if not raw:
        return []
    _require(not require_terminal_nul or raw.endswith(b"\0"),
             "PATH_FRAMING: Git path output lacks terminal NUL")
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    _require(len(records) <= _PATH_RECORD_LIMIT,
             "PATH_COUNT: Git path output exceeds 100000 records")
    result = []
    seen = set()
    for ordinal, record in enumerate(records, 1):
        try:
            value = record.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError(_safe_reason("PATH_UTF8", ordinal=ordinal,
                                               rejected=record)) from exc
        try:
            _validate_object_path(value)
        except ValidationError as exc:
            raise ValidationError(_safe_reason("PATH_INVALID", ordinal=ordinal,
                                               rejected=record)) from exc
        key = _portable_path_key(value)
        _require(key not in seen,
                 _safe_reason("PATH_DUPLICATE", ordinal=ordinal, rejected=record))
        seen.add(key)
        result.append(value)
    return result


def parse_critic_run_carrier(carrier):
    """Parse the sole exact final nonblank Critic run line."""
    normalized = validate_commit_message(carrier)
    lines = normalized.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    _require(not any(_critic_key_lookalike(line) for line in lines),
             "CRITIC_LINE_INVALID: non-ASCII Critic-run key lookalike is ambiguous")
    like = [index for index, line in enumerate(lines)
            if re.search(r"(?i)critic[ \t-]*runs?[ \t]*:",
                         _fold_reserved_key(line))]
    _require(len(like) == 1 and like[0] == len(lines) - 1,
             "CRITIC_LINE_INVALID: exactly one final Critic run line is required")
    matched = re.fullmatch(r"Critic run: ([0-9a-f-]+)", lines[-1])
    _require(matched is not None, "CRITIC_LINE_INVALID: noncanonical Critic run line")
    return {"run_id": _run_id(matched.group(1), "Critic run id"),
            "line": lines[-1]}


def _split_git_message_lines(message):
    """Split only the LF and CRLF boundaries recognized in Git messages."""
    return validate_commit_message(message).split("\n")


def parse_writer_trailer(message):
    """Parse the sole final ``Writer`` trailer, or return ``None`` when absent.

    Writer-like lines elsewhere are rejected rather than treated as provenance;
    this prevents body text and duplicate trailers from becoming authority.
    """
    lines = _split_git_message_lines(message)
    _require(not any(_writer_key_lookalike(line) for line in lines),
             "WRITER_LOOKALIKE: non-ASCII Writer-key lookalike is ambiguous")
    _require(sum(1 for line in lines if ":" in line or "\uff1a" in line)
             <= _TRAILER_LIKE_LIMIT,
             "WRITER_TRAILER_LIMIT: Commit message exceeds 256 trailer-like lines")
    writer_lines = [(index, line) for index, line in enumerate(lines)
                    if _writer_like(line)]
    if not writer_lines:
        return None
    _require(len(writer_lines) == 1,
             "WRITER_COUNT: Commit message must contain exactly one Writer trailer")
    index, line = writer_lines[0]
    last = len(lines) - 1
    while last >= 0 and lines[last] == "":
        last -= 1
    block_start = last
    while block_start >= 0 and _TRAILER_LINE.fullmatch(lines[block_start]):
        block_start -= 1
    block_start += 1
    separated = block_start == 0 or lines[block_start - 1] == ""
    _require(separated and block_start <= index <= last,
             "WRITER_POSITION: Writer provenance must be in the final trailer block")
    matched = _WRITER_LINE.fullmatch(line)
    _require(matched is not None,
             "WRITER_KEY: Writer trailer key must be exactly 'Writer'")
    value = matched.group(1)
    _require(value.count("/") == 1,
             "WRITER_VALUE: Writer trailer must be codex/<run id> or script/<name>")
    kind, identity = value.split("/", 1)
    canonical = format_writer_trailer(kind, identity)
    _require(line == canonical, "WRITER_CANONICAL: Writer trailer is not in canonical form")
    return {"kind": kind, "identity": identity, "value": value, "trailer": canonical}


def validate_critic_run_binding(verdict, *, reviewed_head=None):
    """Bind a compact persistence handoff or full verdict to one result/head."""
    _require(isinstance(verdict, dict),
             "CRITIC_BINDING_INVALID: Critic verdict must be an object")
    run_id = verdict.get("run_id")
    _require(run_id is not None,
             "CRITIC_BINDING_INVALID: Critic verdict is missing its critic run id")
    _run_id(run_id, "Critic run id")
    head = verdict.get("head_sha")
    _require(head is not None,
             "CRITIC_BINDING_INVALID: Critic verdict is missing its reviewed head")
    _sha(head, "Critic reviewed head")
    if reviewed_head is not None:
        _sha(reviewed_head, "Expected reviewed head")
        _require(head == reviewed_head,
                 "CRITIC_BINDING_INVALID: Critic verdict names a different reviewed head")
    digest = verdict.get("result_sha256")
    _require(isinstance(digest, str) and _DIGEST.fullmatch(digest),
             "CRITIC_BINDING_INVALID: Critic result digest is missing or invalid")
    nested_names = {"candidate_binding", "critic_artifact_binding", "critic_review"}
    if nested_names & set(verdict):
        candidate = verdict.get("candidate_binding")
        _require(isinstance(candidate, dict) and candidate.get("head_sha") == head,
                 "CRITIC_BINDING_INVALID: Candidate binding disagrees with the verdict reviewed head")
        artifact = verdict.get("critic_artifact_binding")
        _require(isinstance(artifact, dict)
                 and artifact.get("critic_run_id") == run_id
                 and artifact.get("head_sha") == head,
                 "CRITIC_BINDING_INVALID: Critic artifact binding disagrees with the verdict run or reviewed head")
        if "result_sha256" in artifact:
            _require(artifact.get("result_sha256") == digest,
                     "CRITIC_BINDING_INVALID: Critic artifact binding disagrees with the verdict result digest")
        review = verdict.get("critic_review")
        _require(isinstance(review, dict) and review.get("run_id") == run_id
                 and review.get("head_sha") == head,
                 "CRITIC_BINDING_INVALID: Embedded critic review disagrees with the verdict run or reviewed head")
        if "result_sha256" in review:
            _require(review.get("result_sha256") == digest,
                     "CRITIC_BINDING_INVALID: Embedded critic review disagrees with the verdict result digest")
    carrier = verdict.get("critic_run_carrier")
    _require(carrier is not None,
             "CRITIC_LINE_INVALID: Critic run carrier must be explicit")
    parsed = parse_critic_run_carrier(carrier)
    _require(parsed["run_id"] == run_id,
             "CRITIC_LINE_INVALID: Critic run carrier disagrees with the verdict run")
    result = {"critic_run_id": run_id, "head_sha": head,
              "trailer": parsed["line"]}
    result["result_sha256"] = digest
    return result


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
             and len(str(record["who"]).encode("utf-8")) <= 255
             and not any(ord(char) < 32 or 0x7f <= ord(char) <= 0x9f
                         or ord(char) in {0x2028, 0x2029} for char in str(record["who"])),
             "Provenance override needs an owner identity")
    timestamp(record["when"])
    _require(isinstance(record["why"], str) and bool(record["why"])
             and record["why"].strip() == record["why"]
             and len(record["why"]) <= 4096
             and len(record["why"].encode("utf-8")) <= 4096
             and not any(ord(char) < 32 or 0x7f <= ord(char) <= 0x9f
                         or ord(char) in {0x2028, 0x2029} for char in record["why"]),
             "Provenance override needs a bounded reason")
    _require(type(record["pr"]) is int and record["pr"] > 0,
             "Provenance override needs a positive PR number")
    shas = record["covered_shas"]
    _require(isinstance(shas, list) and shas and len(shas) <= 256
             and shas == sorted(set(shas)),
             "Provenance override needs sorted unique covered commit SHAs within 256")
    for value in shas:
        _sha(value, "Covered commit SHA")
    _require(len({len(value) for value in shas}) == 1,
             "Covered commit SHAs must use one repository object format")
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
    _require(len(predecessors) <= 31,
             "Provenance override chain exceeds 32 records")
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
    prior_time = None
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
        observed_time = timestamp(verified["when"])
        _require(prior_time is None or observed_time >= prior_time,
                 "Provenance override chain is not chronological")
        prior_time = observed_time
    result = verify_owner_override(
        record, trusted_owner_ids=trusted_owner_ids, pr=pr,
        supersedes=current,
    )
    _require(prior_time is None or timestamp(result["when"]) >= prior_time,
             "Provenance override chain is not chronological")
    return result


def verify_provider_owner_override(record, observation, *, repository_id=None,
                                   config_sha256=None):
    """Bind an override to reviewed numeric owners and one fresh comment."""
    _require(isinstance(observation, dict) and observation.get("version") == 1,
             "Provider provenance observation is missing")
    _require(type(observation.get('repository_id')) is int
             and observation['repository_id'] > 0
             and (repository_id is None
                  or observation['repository_id'] == repository_id),
             "Provider override repository identity mismatch")
    _require(isinstance(observation.get('config_sha256'), str)
             and _DIGEST.fullmatch(observation['config_sha256'])
             and (config_sha256 is None
                  or observation['config_sha256'] == config_sha256),
             "Provider override reviewed configuration mismatch")
    owners = observation.get("trusted_owner_ids")
    _require(isinstance(owners, list) and all(type(value) is int and value > 0
                                              for value in owners),
             "Reviewed trusted owner ids are missing")
    _require(type(record.get("who")) is int,
             "Provider overrides require a numeric GitHub actor id")
    verify_owner_override(record, trusted_owner_ids=owners,
                          pr=observation.get("pr"))
    rendered = render_owner_override(record)
    matches = [comment for comment in observation.get("comments", [])
               if comment.get("actor_id") == record["who"]
               and comment.get("body") == rendered
               and comment.get("body_sha256") == sha256(rendered.encode()).hexdigest()]
    _require(len(matches) == 1,
             "Override requires exactly one freshly observed trusted owner comment")
    _require(FALLBACK_LABEL in observation.get("labels", []),
             f"Override requires exact current '{FALLBACK_LABEL}' label")
    return {"record": record, "provider_comment_id": matches[0]["id"],
            "provider_comment_url": matches[0].get("url"),
            "body_sha256": matches[0]["body_sha256"],
            "config_sha256": observation.get("config_sha256")}


class _Git:
    def __init__(self, root):
        path = Path(root).resolve()
        _require(path.is_dir(),
                 "REPOSITORY_INVALID: repository root must be an existing directory")
        self.root = path
        self._identity_ready = False
        top = Path(self.text("rev-parse", "--show-toplevel")).resolve()
        _require(top == path,
                 "REPOSITORY_ROOT: path must be the canonical Git worktree root")
        self.git_dir = Path(self.text("rev-parse", "--absolute-git-dir")).resolve()
        common = Path(self.text("rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.root / common
        self.common_dir = common.resolve()
        objects = Path(self.text("rev-parse", "--git-path", "objects"))
        if not objects.is_absolute():
            objects = self.root / objects
        self.objects_dir = objects.resolve()
        _require(self.git_dir.is_dir() and self.common_dir.is_dir()
                 and self.objects_dir.is_dir(),
                 "REPOSITORY_IDENTITY_INVALID: Git directories are unavailable")
        self.object_format = self.text("rev-parse", "--show-object-format")
        _require(self.object_format in {"sha1", "sha256"},
                 "REPOSITORY_FORMAT: unsupported Git object format")
        self.oid_length = 40 if self.object_format == "sha1" else 64

        identity_paths = {self.root, self.git_dir, self.common_dir, self.objects_dir}
        marker = self.root / ".git"
        if marker.exists():
            identity_paths.add(marker)
        self._identity = {
            path: self._path_identity(path) for path in sorted(identity_paths, key=str)
        }
        watched = {
            self.common_dir / "config",
            self.git_dir / "config.worktree",
            self.git_dir / "commondir",
            self.common_dir / "shallow",
            self.git_dir / "shallow",
            self.objects_dir / "info" / "alternates",
            self.common_dir / "info" / "grafts",
            self.common_dir / "info" / "attributes",
        }
        if marker.is_file():
            watched.add(marker)
        self._repository_files = {
            item: self._file_bytes(item) for item in sorted(watched, key=str)
        }
        alternates = self._repository_files[self.objects_dir / "info" / "alternates"]
        grafts = self._repository_files[self.common_dir / "info" / "grafts"]
        attributes = self._repository_files[self.common_dir / "info" / "attributes"]
        _require(not alternates.strip(),
                 "GIT_ALTERNATES_UNSAFE: repository object alternates are forbidden")
        _require(not grafts.strip(),
                 "GIT_GRAFTS_UNSAFE: repository grafts are forbidden")
        _require(not attributes.strip(),
                 "GIT_ATTRIBUTES_UNSAFE: repository-local attributes are forbidden")
        self.shallow_boundaries = self._read_shallow_boundaries()
        self._validate_local_config()
        self._identity_ready = True

    @staticmethod
    def _path_identity(path):
        try:
            stat = path.stat()
        except OSError as exc:
            raise ValidationError(
                "REPOSITORY_IDENTITY_INVALID: repository path is unavailable"
            ) from exc
        return (str(path.resolve()), stat.st_dev, stat.st_ino,
                stat.st_mode, path.is_dir(), path.is_file())

    @staticmethod
    def _file_bytes(path):
        if not path.exists():
            return b""
        _require(path.is_file(),
                 "REPOSITORY_IDENTITY_INVALID: repository metadata is not a file")
        try:
            before = path.stat()
            _require(before.st_size <= _REPOSITORY_FILE_LIMIT,
                     "REPOSITORY_METADATA_SIZE: repository metadata exceeds 1 MiB")
            raw = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            raise ValidationError(
                "REPOSITORY_IDENTITY_INVALID: repository metadata is unreadable"
            ) from exc
        _require(len(raw) == before.st_size
                 and (before.st_dev, before.st_ino, before.st_size,
                      before.st_mtime_ns)
                 == (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns),
                 "REPOSITORY_IDENTITY_CHANGED: repository metadata changed while read")
        return raw

    def _read_shallow_boundaries(self):
        raw = self._repository_files.get(self.common_dir / "shallow", b"")
        if self.git_dir != self.common_dir:
            raw += self._repository_files.get(self.git_dir / "shallow", b"")
        if not raw:
            return frozenset()
        try:
            lines = raw.decode("ascii", "strict").splitlines()
        except UnicodeDecodeError as exc:
            raise ValidationError("SHALLOW_INVALID: shallow boundary is not ASCII") from exc
        _require(all(len(value) == self.oid_length and _SHA.fullmatch(value)
                     for value in lines),
                 "SHALLOW_INVALID: shallow boundary has an invalid object id")
        return frozenset(lines)

    def _validate_local_config(self):
        configured = self.run(
            "config", "--local", "--null", "--name-only", "--list",
            binary=True,
        ).stdout
        try:
            keys = [value.decode("ascii", "strict").casefold()
                    for value in configured.split(b"\0") if value]
        except UnicodeDecodeError as exc:
            raise ValidationError("GIT_CONFIG_UNSAFE: local config key is not ASCII") from exc
        unsafe_exact = {
            "core.attributesfile", "core.alternaterefscommand",
            "core.usereplacerefs", "diff.external", "diff.ignoresubmodules",
            "diff.renamelimit", "diff.renames",
        }
        unsafe = [key for key in keys if (
            key in unsafe_exact
            or key.startswith(("include.", "includeif.", "merge."))
            or re.fullmatch(r"diff\..+\.(?:command|textconv)", key)
            or re.fullmatch(r"submodule\..+\.ignore", key)
        )]
        _require(not unsafe,
                 "GIT_CONFIG_UNSAFE: repository-local Git behavior is not closed")

    def _verify_repository_identity(self):
        for path, expected in self._identity.items():
            _require(self._path_identity(path) == expected,
                     "REPOSITORY_IDENTITY_CHANGED: repository path identity changed")
        for path, expected in self._repository_files.items():
            _require(self._file_bytes(path) == expected,
                     "REPOSITORY_IDENTITY_CHANGED: repository metadata changed")

    def run(self, *args, binary=False, allow_failure=False, environment=None,
            input_bytes=None):
        if self._identity_ready:
            self._verify_repository_identity()
        try:
            completed = run_bounded(
                ["git", "--no-replace-objects", "--literal-pathspecs",
                 "-c", "core.useReplaceRefs=false",
                 "-c", "core.quotepath=false", "-c", "i18n.commitEncoding=UTF-8",
                 "-c", "i18n.logOutputEncoding=UTF-8",
                  "-c", "core.ignoreCase=false", "-c", "core.precomposeUnicode=false",
                  "-c", "diff.renames=false",
                 "-c", "safe.directory=" + str(self.root),
                 "-C", str(self.root), *args],
                timeout=60, stdout_limit=_GIT_OUTPUT_LIMIT,
                stderr_limit=_GIT_OUTPUT_LIMIT,
                stdin=input_bytes,
                env=child_env(isolated_git_env(extra=environment)),
            )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise ValidationError(
                f"Git provenance inspection failed ({args[0]}): {type(exc).__name__}"
            ) from exc
        if not binary:
            try:
                completed.stdout = completed.stdout.decode("utf-8", "strict")
                completed.stderr = completed.stderr.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise ValidationError("GIT_UTF8: Git text output is not strict UTF-8") from exc
        if completed.returncode and not allow_failure:
            raise ValidationError(
                f"GIT_COMMAND: {args[0]} failed rc={completed.returncode} "
                f"stderr_sha256={sha256(completed.stderr if isinstance(completed.stderr, bytes) else completed.stderr.encode()).hexdigest()}"
            )
        return completed

    def text(self, *args):
        return self.run(*args).stdout.strip()

    def resolve(self, value, field, *, allow_missing=False):
        validate_ref_locator(value, field)
        candidate = value
        if value.startswith("refs/"):
            located = self.run(
                "rev-parse", "--verify", "--quiet", "--end-of-options", value,
                allow_failure=True, binary=True,
            )
            if located.returncode == 1 and not located.stdout:
                if allow_missing:
                    return None
                raise ValidationError(f"REF_MISSING: {field} does not exist")
            _require(located.returncode == 0,
                     f"REF_RESOLUTION: {field} could not be resolved exactly")
            try:
                candidate = located.stdout.decode("ascii", "strict").strip()
            except UnicodeDecodeError as exc:
                raise ValidationError(
                    f"REF_FORMAT: {field} resolved to non-ASCII output"
                ) from exc
        else:
            _require(len(value) == self.oid_length,
                     f"REF_FORMAT: {field} OID has the wrong object format")
        resolved = self.text("rev-parse", "--verify", "--end-of-options",
                             f"{candidate}^{{commit}}")
        _require(len(resolved) == self.oid_length and _SHA.fullmatch(resolved),
                 f"REF_FORMAT: {field} resolved to the wrong object format")
        return resolved

    def blob(self, commit, path):
        entry = self.entry(commit, path)
        return None if entry is None else entry["content"]

    def entry(self, treeish, path, *, environment=None):
        result = self.run(
            "ls-tree", "-z", treeish, "--", path,
            binary=True, allow_failure=True, environment=environment,
        )
        if result.returncode:
            raise ValidationError("TREE_ENTRY_COMMAND: ls-tree failed")
        if not result.stdout:
            return None
        rows = [row for row in result.stdout.split(b"\0") if row]
        _require(result.stdout.endswith(b"\0") and len(rows) == 1
                 and b"\t" in rows[0],
                 "TREE_ENTRY_FRAMING: ls-tree returned an invalid record")
        metadata, raw_path = rows[0].split(b"\t", 1)
        try:
            tokens = metadata.decode("ascii", "strict").split()
        except UnicodeDecodeError as exc:
            raise ValidationError("TREE_ENTRY_META: ls-tree metadata is not ASCII") from exc
        _require(len(tokens) == 3, "TREE_ENTRY_META: ls-tree metadata is invalid")
        mode, object_type, object_id = tokens
        try:
            decoded_path = raw_path.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError(_safe_reason(
                "TREE_ENTRY_PATH_UTF8", ordinal=1, rejected=raw_path,
            )) from exc
        _require(decoded_path == path,
                 "TREE_ENTRY_PATH_MISMATCH: ls-tree returned a different path")
        _require(mode in {"100644", "100755", "120000", "160000"}
                 and object_type in {"blob", "commit"}
                 and len(object_id) == self.oid_length
                 and _SHA.fullmatch(object_id),
                 "TREE_ENTRY_META: ls-tree returned invalid mode/type/OID")
        content = self.run(
            "cat-file", object_type, object_id,
            binary=True, allow_failure=True, environment=environment,
        )
        _require(content.returncode == 0,
                 "TREE_ENTRY_CONTENT: committed object is unavailable")
        return {
            "mode": mode,
            "type": object_type,
            "object_id": object_id,
            "content": content.stdout,
        }

    def object_directory(self):
        self._verify_repository_identity()
        return self.objects_dir


def _commit_details(git, commit):
    raw = git.run("cat-file", "commit", commit, binary=True).stdout
    _require(len(raw) <= _GIT_OUTPUT_LIMIT, "COMMIT_OBJECT_SIZE: raw commit exceeds 32 MiB")
    algorithm = hashlib.new(git.object_format)
    algorithm.update(f"commit {len(raw)}\0".encode("ascii"))
    algorithm.update(raw)
    _require(algorithm.hexdigest() == commit,
             "COMMIT_OBJECT_ID: raw commit bytes do not hash to the frozen OID")
    marker = raw.find(b"\n\n")
    _require(marker > 0, "COMMIT_HEADER_FRAMING: raw commit has no header separator")
    header_bytes, message_bytes = raw[:marker], raw[marker + 2:]
    _require(len(message_bytes) <= _MESSAGE_LIMIT,
             "MESSAGE_SIZE: raw commit message exceeds 1 MiB")
    header_lines = header_bytes.split(b"\n")
    _require(len(header_lines) <= _MESSAGE_LINE_LIMIT,
             "COMMIT_HEADER_LINES: raw commit has too many headers")
    headers = []
    for ordinal, line in enumerate(header_lines, 1):
        if line.startswith(b" "):
            _require(headers and headers[-1][0] in {b"gpgsig", b"gpgsig-sha256", b"mergetag"},
                     _safe_reason("COMMIT_HEADER_CONTINUATION", ordinal=ordinal, rejected=line))
            _require(len(line) <= 16384,
                     _safe_reason("COMMIT_HEADER_SIZE", ordinal=ordinal, rejected=line))
            continue
        _require(b" " in line and len(line) <= 16384,
                 _safe_reason("COMMIT_HEADER_INVALID", ordinal=ordinal, rejected=line))
        key, value = line.split(b" ", 1)
        _require(key in {b"tree", b"parent", b"author", b"committer", b"encoding",
                         b"gpgsig", b"gpgsig-sha256", b"mergetag"},
                 _safe_reason("COMMIT_HEADER_UNKNOWN", ordinal=ordinal, rejected=key))
        headers.append((key, value))
    values = lambda key: [value for name, value in headers if name == key]
    _require(len(values(b"tree")) == 1 and len(values(b"author")) == 1
             and len(values(b"committer")) == 1 and len(values(b"encoding")) <= 1,
             "COMMIT_HEADER_MULTIPLICITY: required header multiplicity is invalid")
    for key in (b"tree", b"parent"):
        for value in values(key):
            _require(len(value) == git.oid_length and re.fullmatch(b"[0-9a-f]+", value),
                     "COMMIT_HEADER_OID: header OID has the wrong object format")
    for key in (b"author", b"committer"):
        value = values(key)[0]
        _require(len(value) <= 4096, "COMMIT_IDENTITY_SIZE: identity header exceeds 4 KiB")
        try:
            identity = value.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError("COMMIT_IDENTITY_UTF8: identity is not strict UTF-8") from exc
        _require(not any(ord(char) < 32 or 0x7f <= ord(char) <= 0x9f for char in identity),
                 "COMMIT_IDENTITY_CONTROL: identity contains controls")
        _require(re.fullmatch(r".+ <[^<>\r\n]+> [0-9]+ [+-][0-9]{4}", identity) is not None,
                 "COMMIT_IDENTITY_GRAMMAR: malformed author/committer header")
    if values(b"encoding"):
        _require(values(b"encoding")[0] == b"UTF-8",
                 "COMMIT_ENCODING: only exact UTF-8 is accepted")
    try:
        message = message_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("MESSAGE_UTF8: raw commit message is not strict UTF-8") from exc
    validate_commit_message(message)
    parents = [value.decode("ascii") for value in values(b"parent")]
    if parents:
        changed = git.run("diff", "--raw", "-r", "-z", "--no-renames",
                          "--ignore-submodules=none", "--no-abbrev",
                          parents[0], commit, binary=True).stdout
    else:
        changed = git.run("diff-tree", "--root", "--no-commit-id", "--raw",
                          "-r", "-z", "--no-renames", "--ignore-submodules=none",
                          "--no-abbrev", commit, binary=True).stdout
    entries = _parse_raw_diff(changed, git.oid_length)
    return {"sha": commit, "tree": values(b"tree")[0].decode("ascii"),
            "parents": parents, "message": message,
            "changed_files": sorted(entry["path"] for entry in entries),
            "changed_entries": entries}


def read_commit_message_bytes(root, commit):
    """Read one raw commit message through bounded, config-isolated plumbing."""
    git = _Git(root)
    _require(isinstance(commit, str) and len(commit) == git.oid_length
             and _SHA.fullmatch(commit), "COMMIT_OID: invalid commit object id")
    raw = git.run('cat-file', 'commit', commit, binary=True).stdout
    marker = raw.find(b'\n\n')
    _require(marker > 0, 'COMMIT_HEADER_FRAMING: raw commit has no message boundary')
    message = raw[marker + 2:]
    _require(len(message) <= _MESSAGE_LIMIT,
             'MESSAGE_SIZE: raw commit message exceeds 1 MiB')
    return message


def create_canonical_commit(root, parent, branch, message):
    """Create one exact-message commit from the staged index and CAS the branch."""
    git = _Git(root)
    _require(isinstance(parent, str) and len(parent) == git.oid_length
             and _SHA.fullmatch(parent), 'COMMIT_PARENT: invalid parent OID')
    ref = validate_ref_locator('refs/heads/' + branch, 'Publication branch')
    _require(git.resolve(ref, 'Publication branch') == parent,
             'Publication branch moved before commit creation')
    canonical = validate_commit_message(message)
    _require(canonical == message, 'EMITTER_INVALID: commit message is not LF-canonical')
    try:
        writer = parse_writer_trailer(canonical)
    except ValidationError as exc:
        raise ValidationError('PUBLISHED_MESSAGE_INVALID: ' + str(exc)) from exc
    _require(writer is not None,
             'PUBLISHED_MESSAGE_INVALID: canonical publisher message has no Writer trailer')
    tree = git.text('write-tree')
    created = git.run('commit-tree', tree, '-p', parent, binary=True,
                      input_bytes=message.encode('utf-8')).stdout
    try:
        oid = created.decode('ascii', 'strict').strip()
    except UnicodeDecodeError as exc:
        raise ValidationError('COMMIT_CREATE: commit-tree returned non-ASCII') from exc
    _require(len(oid) == git.oid_length and _SHA.fullmatch(oid),
             'COMMIT_CREATE: commit-tree returned invalid OID')
    _require(read_commit_message_bytes(root, oid) == message.encode('utf-8'),
             'PUBLISHED_MESSAGE_MISMATCH: commit-tree changed canonical bytes')
    git.run('update-ref', ref, oid, parent)
    return oid


def _mode_type(mode):
    return {"000000": None, "100644": "blob", "100755": "blob",
            "120000": "symlink", "160000": "gitlink"}.get(mode)


def _parse_raw_diff(raw, oid_length):
    _require(isinstance(raw, bytes) and len(raw) <= _GIT_OUTPUT_LIMIT,
             "RAW_DIFF_SIZE: raw diff exceeds 32 MiB")
    if not raw:
        return []
    _require(raw.endswith(b"\0"), "RAW_DIFF_FRAMING: raw diff lacks terminal NUL")
    fields = raw.split(b"\0")[:-1]
    entries = []
    seen = set()
    index = 0
    while index < len(fields):
        ordinal = len(entries) + 1
        meta = fields[index]
        index += 1
        try:
            tokens = meta.decode("ascii", "strict").split()
        except UnicodeDecodeError as exc:
            raise ValidationError(_safe_reason("RAW_DIFF_META", ordinal=ordinal,
                                               rejected=meta)) from exc
        _require(len(tokens) == 5 and tokens[0].startswith(":"),
                 _safe_reason("RAW_DIFF_META", ordinal=ordinal, rejected=meta))
        old_mode, new_mode, old_oid, new_oid, status = tokens[0][1:], *tokens[1:]
        _require(not status.startswith(("R", "C")),
                 _safe_reason("RENAME_COLLAPSED", ordinal=ordinal, rejected=meta))
        _require(status in {"A", "D", "M", "T"},
                 _safe_reason("RAW_DIFF_STATUS", ordinal=ordinal, rejected=meta))
        _require(index < len(fields),
                 _safe_reason("RAW_DIFF_FRAMING", ordinal=ordinal, rejected=meta))
        path_raw = fields[index]
        index += 1
        _require(len(entries) < _PATH_RECORD_LIMIT,
                 "RAW_DIFF_COUNT: raw diff exceeds 100000 records")
        _require(_mode_type(old_mode) is not None or old_mode == "000000",
                 _safe_reason("RAW_DIFF_MODE", ordinal=ordinal, rejected=meta))
        _require(_mode_type(new_mode) is not None or new_mode == "000000",
                 _safe_reason("RAW_DIFF_MODE", ordinal=ordinal, rejected=meta))
        _require(all(len(oid) == oid_length and re.fullmatch(r"[0-9a-f]+", oid)
                     for oid in (old_oid, new_oid)),
                 _safe_reason("RAW_DIFF_OID", ordinal=ordinal, rejected=meta))
        zero = "0" * oid_length
        valid_shape = {
            "A": old_mode == "000000" and old_oid == zero
                 and new_mode != "000000" and new_oid != zero,
            "D": new_mode == "000000" and new_oid == zero
                 and old_mode != "000000" and old_oid != zero,
            "M": old_mode != "000000" and new_mode != "000000"
                 and old_oid != zero and new_oid != zero,
            "T": old_mode != "000000" and new_mode != "000000"
                 and old_oid != zero and new_oid != zero
                 and _mode_type(old_mode) != _mode_type(new_mode),
        }[status]
        _require(valid_shape,
                 _safe_reason("ENTRY_INVALID", ordinal=ordinal, rejected=meta))
        try:
            path = path_raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError(_safe_reason("PATH_UTF8", ordinal=ordinal,
                                               rejected=path_raw)) from exc
        try:
            _validate_object_path(path)
        except ValidationError as exc:
            raise ValidationError(_safe_reason("PATH_INVALID", ordinal=ordinal,
                                               rejected=path_raw)) from exc
        key = _portable_path_key(path)
        _require(key not in seen,
                 _safe_reason("PATH_DUPLICATE", ordinal=ordinal, rejected=path_raw))
        seen.add(key)
        entries.append({"path": path, "action": status,
                        "old_mode": old_mode, "new_mode": new_mode,
                        "old_type": _mode_type(old_mode), "new_type": _mode_type(new_mode),
                        "old_oid": old_oid, "new_oid": new_oid})
    return entries


def _decode_merge_tree_output(output, oid_length):
    _require(isinstance(output, bytes) and len(output) <= _GIT_OUTPUT_LIMIT,
             "MERGE_TREE_SIZE: merge-tree output exceeds 32 MiB")
    _require(output.endswith(b"\0"),
             "MERGE_TREE_FRAMING: merge-tree output lacks terminal NUL")
    fields = output.split(b"\0")[:-1]
    _require(bool(fields) and bool(fields[0]),
             "MERGE_TREE_FRAMING: merge-tree output has no tree object id")
    try:
        tree = fields[0].decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("MERGE_TREE_OID: merge-tree OID is not ASCII") from exc
    _require(len(tree) == oid_length and _SHA.fullmatch(tree) is not None,
             "MERGE_TREE_OID: merge-tree OID has the wrong object format")
    try:
        paths = decode_git_paths(
            b"\0".join(fields[1:]) + (b"\0" if len(fields) > 1 else b""),
            failure="commit",
        )
    except ValidationError as exc:
        raise ValidationError("MERGE_TREE_PATH: " + str(exc)) from exc
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
        return False, "GENERATOR_UNREGISTERED: generated outputs lack a reproducer"
    try:
        reproduced = reproduce_generated(details["sha"], list(paths))
    except Exception as exc:
        return False, f"GENERATOR_ERROR: reproducer raised {type(exc).__name__}"
    if not isinstance(reproduced, dict) or set(reproduced) != set(paths):
        return False, "GENERATOR_OUTPUT_INVALID: reproducer keys do not match changed paths"
    total = 0
    for path in paths:
        expected = _expected_generated_entry(reproduced[path])
        if expected is None:
            return False, "GENERATOR_OUTPUT_INVALID: mode/type/content entry is invalid"
        if len(expected["content"]) > _GENERATED_BLOB_LIMIT:
            return False, "GENERATOR_BLOB_SIZE: reproduced output exceeds 32 MiB"
        total += len(expected["content"])
        if total > _GENERATED_TOTAL_LIMIT:
            return False, "GENERATOR_TOTAL_SIZE: reproduced output exceeds 512 MiB"
        actual = git.entry(details["sha"], path)
        if actual is None:
            return False, "GENERATED_ENTRY_MISSING: committed tree entry is absent"
        if (expected["mode"], expected["type"]) != (actual["mode"], actual["type"]):
            return False, "GENERATED_ENTRY_INVALID: mode/type did not reproduce"
        if expected["content"] != actual["content"]:
            return False, "GENERATOR_BYTES_MISMATCH: output did not reproduce byte-for-byte"
    return True, "byte-for-byte generated outputs with matching mode/type"


def _origin_main_merge(git, details, origin_main_oid, generated_paths,
                       reproduce_generated):
    parents = details["parents"]
    if len(parents) != 2:
        return False, "commit is not a two-parent merge"
    if parents[1] != origin_main_oid:
        return False, "second parent is not the frozen observed default-branch OID"
    source_objects = git.object_directory()
    actual = git.text("rev-parse", f"{details['sha']}^{{tree}}")
    _require(len(actual) == git.oid_length and _SHA.fullmatch(actual),
             "MERGE_TREE_OID: committed tree has the wrong object format")
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
        if reproduced.returncode not in {0, 1}:
            return False, "MERGE_TREE_COMMAND: merge reproduction returned an invalid status"
        tree, conflict_paths = _decode_merge_tree_output(
            reproduced.stdout, git.oid_length,
        )
        if reproduced.returncode == 0:
            if conflict_paths:
                return False, "origin/main merge reproduction returned unexpected conflict paths"
            if tree != actual:
                return False, "origin/main merge tree differs from Git's automatic merge result"
            return True, "origin/main merge"
        if reproduced.returncode != 1 or not conflict_paths:
            return False, "origin/main merge reproduction failed in a disposable object database"
        if not set(conflict_paths).issubset(generated_paths):
            return False, "MERGE_TREE_CONFLICT_SCOPE: merge has non-generated conflicts"
        differences = git.run(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", tree, actual,
            binary=True, environment=environment,
        ).stdout
        changed_from_automatic = set(decode_git_paths(differences, failure="commit"))
        if not changed_from_automatic.issubset(set(conflict_paths)):
            return False, ("MERGE_TREE_RESOLUTION_SCOPE: conflict resolution changed "
                           "non-conflicted paths")
        matched, reason = _verify_generated_entries(
            git, details, sorted(set(conflict_paths)), reproduce_generated
        )
        if not matched:
            return False, "generated conflict resolution " + reason
        return True, "origin/main merge with reproduced generated conflict resolution"


def _generated_commit(git, details, generated_paths, reproduce_generated):
    paths = details["changed_files"]
    if not paths or not set(paths).issubset(generated_paths):
        listed = ", ".join(paths) if paths else "<none>"
        return False, ("GENERATED_PATH_SCOPE: changed paths are not limited to "
                       "declared generated outputs: " + listed)[:_REASON_LIMIT]
    entries = details.get("changed_entries", [])
    if any(entry["action"] not in {"A", "M"}
           or entry["old_type"] not in {None, "blob"}
           or entry["new_mode"] not in {"100644", "100755"}
           or entry["new_type"] != "blob" for entry in entries):
        return False, ("GENERATED_ENTRY_INVALID: deletion, type change, symlink, "
                       "gitlink, or unsupported mode")
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


def _run_log_status(verifier, run_id, sha, inventory, required, *, strict=False):
    if verifier is None:
        if strict:
            return False, "RUN_LOG_VERIFIER_UNREGISTERED"
        return (not required,
                "required run-log verification is not configured" if required
                else "optional run log unobserved")
    try:
        result = verifier(run_id, sha, list(inventory))
    except Exception as exc:
        return False, "RUN_LOG_VERIFIER_ERROR:" + type(exc).__name__
    if strict:
        fields = {'version', 'status', 'run_id', 'commit_sha', 'entries',
                  'evidence_sha256', 'reason_code'}
        if not isinstance(result, dict) or set(result) != fields:
            return False, "RUN_LOG_RECORD_INVALID"
        status = result.get('status')
        reason = result.get('reason_code')
        digest = result.get('evidence_sha256')
        if (result.get('version') != 1
                or status not in {'MATCH', 'ABSENT', 'MISMATCH'}
                or result.get('run_id') != run_id
                or result.get('commit_sha') != sha
                or result.get('entries') != list(inventory)
                or not isinstance(reason, str) or not reason
                or len(reason) > 128
                or any(character not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for character in reason)
                or (digest is not None
                    and (not isinstance(digest, str)
                         or not re.fullmatch(r'[0-9a-f]{64}', digest)))):
            return False, "RUN_LOG_RECORD_INVALID"
        if status in {'MATCH', 'MISMATCH'} and digest is None:
            return False, "RUN_LOG_RECORD_INVALID"
        if status == 'MATCH':
            return True, reason
        if status == 'ABSENT' and not required:
            return True, reason
        return False, reason
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


def _validated_generated_paths(value):
    _require(isinstance(value, (list, tuple, set, frozenset)),
             "GENERATED_PATH_INVALID: generated_paths must be a collection")
    _require(len(value) <= _PATH_RECORD_LIMIT,
             "GENERATED_PATH_COUNT: generated_paths exceeds 100000 paths")
    result = []
    seen = set()
    for ordinal, path in enumerate(value, 1):
        try:
            _validate_object_path(path)
        except (ValidationError, UnicodeError, AttributeError, TypeError) as exc:
            rejected = (path.encode("utf-8", "surrogatepass")
                        if isinstance(path, str)
                        else str(type(path)).encode("utf-8", "backslashreplace"))
            raise ValidationError(_safe_reason(
                "GENERATED_PATH_INVALID", ordinal=ordinal, rejected=rejected,
            )) from exc
        key = _portable_path_key(path)
        _require(key not in seen, _safe_reason(
            "GENERATED_PATH_DUPLICATE", ordinal=ordinal,
            rejected=path.encode("utf-8"),
        ))
        seen.add(key)
        result.append(path)
    return tuple(result)


def _validate_reviewed_provenance_policy(policy):
    _require(isinstance(policy, dict)
             and set(policy) == {"require_run_logs", "default_ref",
                                 "generated_paths", "generators"}
             and type(policy["require_run_logs"]) is bool,
             "RUN_LOG_POLICY_UNBOUND: reviewed provenance policy is invalid")
    default_ref = validate_ref_locator(policy["default_ref"], "Reviewed default ref")
    _require(default_ref.startswith("refs/heads/"),
             "DEFAULT_REF_UNBOUND: reviewed default must be a heads ref")
    generated = _validated_generated_paths(policy["generated_paths"])
    _require(isinstance(policy["generators"], dict),
             "GENERATOR_REGISTRY_INVALID: reviewed generators must be an object")
    generated_keys = {_portable_path_key(path) for path in generated}
    for script_id, entry in policy["generators"].items():
        _require(isinstance(script_id, str) and len(script_id) <= 64
                 and _SCRIPT.fullmatch(script_id),
                 "GENERATOR_REGISTRY_INVALID: generator script id is invalid")
        _require(isinstance(entry, dict)
                 and set(entry) == {"paths", "reproducer_sha256"}
                 and isinstance(entry["reproducer_sha256"], str)
                 and _DIGEST.fullmatch(entry["reproducer_sha256"]),
                 "GENERATOR_REGISTRY_INVALID: generator registration is invalid")
        paths = _validated_generated_paths(entry["paths"])
        _require({_portable_path_key(path) for path in paths} <= generated_keys,
                 "GENERATOR_REGISTRY_INVALID: generator paths are undeclared")
    return generated


def check_commit_range(root, base, head, *, origin_main="origin/main",
                       generated_paths=(), reproduce_generated=None,
                       run_log_verifier=None, require_run_logs=False,
                       override=None, trusted_owner_ids=(), pr_number=None,
                       pr_labels=(), override_predecessors=(),
                       reviewed_policy=None, provider_observation=None,
                       runtime_registry=None):
    """Check every ``base..head`` commit and report a refusal reason per commit.

    Optional run logs fail only when configured as required; an observed mismatch
    always fails.  A sealed owner override applies only to its named SHAs and only
    on a PR carrying the exact fallback label.  A superseding override must be
    accompanied by its ordered, oldest-first predecessor chain so its digest
    link cannot be accepted in isolation.
    """
    if reviewed_policy is not None:
        generated_paths = _validate_reviewed_provenance_policy(reviewed_policy)
        _require(isinstance(provider_observation, dict)
                 and provider_observation.get('version') == 1,
                 "PROVIDER_IDENTITY_INVALID: fresh provider observation is required")
        expected_default = reviewed_policy['default_ref'].removeprefix('refs/heads/')
        _require(provider_observation.get('base_ref') == expected_default,
                 "DEFAULT_REF_UNBOUND: provider default ref differs from reviewed policy")
        require_run_logs = reviewed_policy['require_run_logs']
        origin_main = provider_observation.get('base')
        pr_number = provider_observation.get('pr')
        pr_labels = provider_observation.get('labels', [])
        trusted_owner_ids = provider_observation.get('trusted_owner_ids', [])
        registry = runtime_registry if isinstance(runtime_registry, dict) else {}
        run_log_verifier = registry.get('run_log_verifier')
        reproduce_generated = None
    else:
        registry = {}
    _require(type(require_run_logs) is bool, "require_run_logs must be Boolean")
    git = _Git(root)
    base_sha = git.resolve(base, "Range base")
    head_sha = git.resolve(head, "Range head")
    # The 1.9.4 public default is retained as a locator adapter only.  The live
    # host passes its reviewed fully-qualified default ref and observed OID.
    origin_main_sha = None
    origin_locators = (["refs/remotes/origin/main", "refs/heads/origin/main"]
                       if origin_main == "origin/main" else [origin_main])
    for origin_locator in origin_locators:
        origin_main_sha = git.resolve(
            origin_locator, "origin/main ref",
            allow_missing=origin_main == "origin/main",
        )
        if origin_main_sha is not None:
            break
    ancestry = git.run("merge-base", "--is-ancestor", base_sha, head_sha,
                       allow_failure=True)
    if ancestry.returncode:
        _require(ancestry.returncode == 1,
                 "RANGE_ANCESTRY_COMMAND: Git could not inspect range ancestry")
        _require(not git.shallow_boundaries,
                 "SHALLOW_RANGE_BOUNDARY: range crosses or is obscured by a shallow boundary")
        raise ValidationError("RANGE_ANCESTRY: range base is not an ancestor of range head")
    commits_text = git.text("rev-list", "--reverse", "--topo-order",
                            "--max-count=" + str(_COMMIT_LIMIT + 1),
                            f"{base_sha}..{head_sha}")
    commits = commits_text.splitlines() if commits_text else []
    _require(len(commits) <= _COMMIT_LIMIT,
             "RANGE_COUNT: commit range exceeds 10000 commits")
    override_record = None
    provider_override_evidence = None
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
        if reviewed_policy is not None:
            provider_override_evidence = verify_provider_owner_override(
                override_record, provider_observation,
                repository_id=provider_observation.get('repository_id'),
                config_sha256=provider_observation.get('config_sha256'))
        covered = set(override_record["covered_shas"])
        _require(covered.issubset(set(commits)),
                 "Provenance override names commits outside the checked PR range")
    else:
        _require(not override_predecessors,
                 "Provenance override predecessors require an override record")

    generated_paths = _validated_generated_paths(generated_paths)
    allowed_generated = set(DEFAULT_GENERATED_PATHS)
    default_keys = {_portable_path_key(path) for path in DEFAULT_GENERATED_PATHS}
    for ordinal, path in enumerate(generated_paths, 1):
        _require(_portable_path_key(path) not in default_keys or path in allowed_generated,
                 _safe_reason("GENERATED_PATH_DUPLICATE", ordinal=ordinal,
                              rejected=path.encode("utf-8")))
    allowed_generated.update(generated_paths)
    results = []
    refusals = []
    used_overrides = set()
    for commit in commits:
        item = {
            "sha": commit,
            "writer": None,
            "changed_files": [],
            "changed_entries": [],
            "mechanical": False,
            "mechanical_reason": None,
            "run_log": None,
            "status": "PASS",
            "reasons": [],
        }
        try:
            details = _commit_details(git, commit)
            item["changed_files"] = details["changed_files"]
            item["changed_entries"] = details["changed_entries"]
        except (ValidationError, OSError, subprocess.SubprocessError) as exc:
            item["reasons"].append(str(exc)[:_REASON_LIMIT])
            item["status"] = "REFUSE"
            refusals.append({"sha": commit, "reason": item["reasons"][0]})
            results.append(item)
            continue
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
                    if len(details["parents"]) > 1:
                        _require(origin_main_sha is not None,
                                 "DEFAULT_REF_UNBOUND: no reviewed default-branch OID")
                    reproducer = reproduce_generated
                    if reviewed_policy is not None:
                        declared = reviewed_policy['generators'].get(trailer['identity'])
                        registered_generators = registry.get('generators')
                        _require(isinstance(registered_generators, dict),
                                 "GENERATOR_UNREGISTERED: runtime generator registry is missing")
                        registered = registered_generators.get(trailer['identity'])
                        _require(isinstance(declared, dict)
                                 and isinstance(registered, dict)
                                 and set(registered) == {'reproducer_sha256', 'callable'}
                                 and registered['reproducer_sha256'] == declared.get('reproducer_sha256')
                                 and callable(registered['callable']),
                                 "GENERATOR_UNREGISTERED: script id has no reviewed runtime reproducer")
                        _require(set(details['changed_files']) <= set(declared.get('paths', [])),
                                 "GENERATOR_PATH_UNAUTHORIZED: commit changes undeclared generator paths")
                        reproducer = registered['callable']
                    mechanical, reason = classify_mechanical_commit(
                        git, details, origin_main=origin_main_sha,
                        generated_paths=allowed_generated,
                        reproduce_generated=reproducer,
                    )
                except (ValidationError, OSError, subprocess.SubprocessError) as exc:
                    mechanical, reason = False, str(exc)
                reason = str(reason)[:_REASON_LIMIT]
                item["mechanical"] = mechanical
                item["mechanical_reason"] = reason
                if not mechanical:
                    item["reasons"].append(
                        ("script-tagged commit is not reproducibly mechanical: "
                         + reason)[:_REASON_LIMIT]
                    )
            else:
                matched, reason = _run_log_status(
                    run_log_verifier, trailer["identity"], commit,
                    (details["changed_entries"] if reviewed_policy is not None
                     else details["changed_files"]),
                    require_run_logs, strict=reviewed_policy is not None,
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
        "object_format": git.object_format,
        "origin_main": origin_main_sha,
        "commits": results,
        "refusals": refusals,
        "override_sha256": (override_record or {}).get("override_sha256"),
        "provider_override_evidence": provider_override_evidence,
        "run_logs_required": require_run_logs,
    }
