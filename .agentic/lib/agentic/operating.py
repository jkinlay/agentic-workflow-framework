"""Project-owned operating choices; protected governance remains unchanged.

Snapshots are immutable values, not authorization. A cooperating native lock and
write-ahead journal serialize config/audit publication. Readers refuse incomplete
transactions. Recovery requires the operator's independent journal pin. This does
not isolate files from a malicious concurrent writer or authenticate chat words.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
import fnmatch
import json
import os
from pathlib import Path
import re
import sys
import uuid

from jsonschema import Draft202012Validator

from . import ValidationError, VERSION
from .canonical import canonical, fingerprint, load_yaml, loads, now_text, sha256, timestamp
from .safeio import Tree, relative_parts

CONFIG = "OPERATING_CONFIG.yaml"
GOVERNANCE = ".agentic/PROJECT_CONFIG.yaml"
STATE = ".agentic-state/operating"
LOCK = STATE + "/lock"
JOURNAL = STATE + "/pending.json"
LABELS = tuple("ABCDEF")
EPIC_ID = r"[A-Z][A-Z0-9_]*-[1-9][0-9]*"
STAGING_NAME = r"\.awf-operating-[0-9a-f]{32}\.tmp"
MAX_BYTES = 1024 * 1024
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


class OperatingError(ValidationError):
    def __init__(self, refusals):
        self.refusals = refusals
        super().__init__("; ".join(item["path"] + ": " + item["reason"] for item in refusals))


def refusal(path, reason, governance_path=None, governance_value=None):
    return {"path": path, "reason": reason, "governance_path": governance_path,
            "governance_value": governance_value,
            "remedy": ("Change the binding governance value through a reviewed PR to " + GOVERNANCE
                       if governance_path else "Correct the named operating input; preserve recovery evidence when present")}


def fail(path, reason, governance_path=None, governance_value=None):
    raise OperatingError([refusal(path, reason, governance_path, governance_value)])


def _pair(model, effort):
    return {"model": model, "reasoning_effort": effort}


def _stream():
    return {"worker": _pair("gpt-5.6-terra", "medium"), "reviewer": _pair("gpt-5.6-sol", "high")}


def default_operating():
    return {"version": 1, "source": "default", "streams": {"count": 3, **{s: _stream() for s in LABELS[:3]}},
            "controller": _pair("gpt-5.6-sol", "medium"), "specialist": _pair("gpt-5.6-sol", "high"),
            "simple_worker": {"enabled": True, **_pair("gpt-5.6-luna", "low")}}


def operating_ceiling(governance):
    """Configured stream bound, independent of observed host slots or ownership."""
    execution = governance.get("execution") if isinstance(governance, dict) else None
    if not isinstance(execution, dict):
        fail("$.streams.count", "A governing execution configuration is required", "$.execution", execution)
    project = execution.get("max_parallel_tickets")
    if type(project) is not int or project < 1:
        fail("$.streams.count", "Invalid configured writer ceiling", "$.execution.max_parallel_tickets", project)
    limits = [(project, "$.execution.max_parallel_tickets"),
              (6, ".agentic/schemas/operating-config.schema.json#/properties/streams/properties/count/maximum")]
    broker = execution.get("host_broker")
    if "host_broker" in execution:
        if not isinstance(broker, dict) or type(broker.get("enabled")) is not bool:
            fail("$.streams.count", "Host broker needs an explicit enabled Boolean", "$.execution.host_broker", broker)
        if broker["enabled"]:
            workers = broker.get("max_workers")
            if type(workers) is not int or workers < 1:
                fail("$.streams.count", "Enabled host broker needs a positive worker limit", "$.execution.host_broker.max_workers", workers)
            limits.append((workers, "$.execution.host_broker.max_workers"))
    ceiling = min(value for value, _ in limits)
    sources = [path for value, path in limits if value == ceiling]
    return {"effective_ceiling": ceiling,
            "effective_ceiling_governance_path": next((path for path in sources if path.startswith("$.execution.")), None),
            "effective_ceiling_sources": sources}


def _schema(name, value):
    from .contracts import checker
    schema = loads((SCHEMAS / (name + ".schema.json")).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=checker()).iter_errors(value), key=lambda e: str(list(e.absolute_path)))
    return [refusal("$" + "".join("." + str(p) for p in e.absolute_path), e.message) for e in errors[:30]]


@dataclass(frozen=True)
class OperatingSnapshot:
    _json: bytes
    operating_hash: str
    governance_hash: str
    last_change_id: str | None = None
    _provenance_json: bytes = b'{}'

    @property
    def config(self):
        return loads(self._json.decode("utf-8"))

    @property
    def provenance(self):
        return loads(self._provenance_json.decode("utf-8"))

    @property
    def count(self):
        return self.config["streams"]["count"]

    @property
    def source(self):
        return self.config["source"]


def validate_operating(value, governance):
    """Validate choices against both allowlists/floors; never silently clamp."""
    canonical(value)
    errors = _schema("operating-config", value)
    execution = governance.get("execution") if isinstance(governance, dict) else None
    if not isinstance(execution, dict) or not isinstance(execution.get("model_routing"), dict):
        fail("$", "A complete governing execution/model_routing policy is required", "$.execution", execution)
    policy = execution["model_routing"]
    from .model_routing import EFFORTS, validate_policy
    try:
        validate_policy(policy)
    except ValidationError as exc:
        fail("$", str(exc), "$.execution.model_routing", policy)
    streams = value.get("streams", {}) if isinstance(value, dict) else {}
    if not isinstance(streams, dict):
        raise OperatingError(errors or [refusal("$.streams", "Streams must be an object")])
    count, capacity = streams.get("count"), operating_ceiling(governance)
    ceiling = capacity["effective_ceiling"]
    if type(count) is int and (count > ceiling or count < 1):
        errors.append(refusal("$.streams.count", "Stream count must be 1.." + str(ceiling) + " within the effective governance ceiling and six-stream structure; binding limits: "
                              + ", ".join(capacity["effective_ceiling_sources"]), capacity["effective_ceiling_governance_path"], ceiling))
    reviewers = execution.get("independent_reviewers", {})
    if not isinstance(reviewers, dict) or not isinstance(execution.get("roles"), dict):
        fail("$", "Malformed reviewer/role governance", "$.execution", execution)
    if "count" in reviewers and reviewers["count"] != count:
        errors.append(refusal("$.streams.count", "Legacy fixed reviewer count disagrees; remove/update it by governance PR",
                              "$.execution.independent_reviewers.count", reviewers["count"]))
    if type(count) is int and 1 <= count <= 6:
        for label in LABELS[:count]:
            if label not in streams:
                errors.append(refusal("$.streams." + label, "Active stream requires worker and reviewer routes"))
    pairs = [("$." + role, role, value.get(role)) for role in ("controller", "specialist")] if isinstance(value, dict) else []
    if isinstance(value, dict):
        pairs.append(("$.simple_worker", "worker", value.get("simple_worker")))
    if isinstance(streams, dict):
        for label in LABELS:
            entry = streams.get(label)
            if isinstance(entry, dict):
                pairs.extend(("$.streams." + label + "." + key, role, entry.get(key))
                             for key, role in (("worker", "worker"), ("reviewer", "critic")))
    overrides = value.get("epic_overrides", {}) if isinstance(value, dict) else {}
    if isinstance(overrides, dict):
        for epic_id, scoped in overrides.items():
            if not isinstance(scoped, dict):
                continue
            prefix = "$.epic_overrides." + epic_id
            pairs.extend((prefix + "." + role, role, scoped[role]) for role in ("controller", "specialist") if role in scoped)
            scoped_streams = scoped.get("streams", {})
            if isinstance(scoped_streams, dict):
                for label, entry in scoped_streams.items():
                    if isinstance(entry, dict):
                        pairs.extend((prefix + ".streams." + label + "." + key, role, entry[key])
                                     for key, role in (("worker", "worker"), ("reviewer", "critic")) if key in entry)
    order = policy["model_order"]
    for path, role, pair in pairs:
        if not isinstance(pair, dict):
            continue
        model, effort = pair.get("model"), pair.get("reasoning_effort")
        if not isinstance(execution["roles"].get(role), dict):
            errors.append(refusal(path, "Role governance must declare its approved model IDs", "$.execution.roles." + role, execution["roles"].get(role)))
            continue
        for binding, allowed in (("$.execution.model_routing.role_allowed_models." + role, policy["role_allowed_models"].get(role, [])),
                                 ("$.execution.roles." + role + ".approved_model_ids", (execution["roles"].get(role) or {}).get("approved_model_ids", []))):
            if model not in allowed:
                errors.append(refusal(path + ".model", "Model is outside the governing role allowlist", binding, allowed))
        choices = policy["models"].get(model, {}).get("reasoning_efforts", []) if isinstance(model, str) else []
        if effort not in choices:
            errors.append(refusal(path + ".reasoning_effort", "Unsupported model/effort pair",
                                  "$.execution.model_routing.models." + str(model) + ".reasoning_efforts", choices))
        floor = policy["review_floor"]
        if role in {"critic", "specialist"} and model in order and effort in EFFORTS and (
                order.index(model) < order.index(floor["model"]) or EFFORTS.index(effort) < EFFORTS.index(floor["reasoning_effort"])):
            errors.append(refusal(path, "Reviewer route is below the review floor", "$.execution.model_routing.review_floor", floor))
    if errors:
        # Prefer the binding governance explanation to a duplicate structural cap error.
        bound = {e["path"] for e in errors if e["governance_path"]}
        raise OperatingError([e for e in errors if e["governance_path"] or e["path"] not in bound])
    return OperatingSnapshot(canonical(value), fingerprint("operating", value), fingerprint("operating-governance", governance))


def validate_snapshot(snapshot, governance):
    if not isinstance(snapshot, OperatingSnapshot):
        fail("$", "A validated OperatingSnapshot is required")
    checked = validate_operating(snapshot.config, governance)
    if checked.operating_hash != snapshot.operating_hash or checked.governance_hash != snapshot.governance_hash:
        fail("$", "Operating snapshot hash/governance binding differs")
    return snapshot


@contextmanager
def _lock(tree):
    if tree.inspect("MANIFEST.json") is not None:
        fail("$", "Use an installed project outside the immutable release source for operating state")
    parent, handle, name = tree.parent(LOCK, create=True)
    tree.inspect(LOCK)
    if os.name == "nt":
        import msvcrt
        from .safeio import win_open
        fd = msvcrt.open_osfhandle(win_open(parent / name, lock=True), os.O_RDWR | os.O_BINARY)
    else:
        fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=handle)
    held = False
    try:
        if os.fstat(fd).st_nlink != 1:
            fail("$", "Operating lock has multiple links")
        if os.name == "nt":
            if not os.fstat(fd).st_size:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = True
        yield
    except (BlockingIOError, PermissionError) as exc:
        fail("$", "Another operating writer holds the lock; retry after it finishes: " + type(exc).__name__)
    finally:
        if held:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read(tree, name):
    return tree.read(name, maximum=MAX_BYTES) if tree.inspect(name) else None


def _bound_governance(tree, governance):
    actual = load_yaml(tree.read(GOVERNANCE, maximum=MAX_BYTES))
    if canonical(actual) != canonical(governance):
        fail("$", "Governance changed during preparation; reload and revalidate", "$.execution", None)
    return actual


def _events(tree):
    directory = tree.root / STATE / "changes"
    if not directory.exists():
        return []
    with Tree(directory) as records:
        names = records.file_list()
        if len(names) > 10000:
            fail("$", "Operating history exceeds bounded inventory; archive through an explicit reviewed migration")
        events, total = [], 0
        for name in names:
            staged = re.fullmatch(STAGING_NAME, name)
            if not staged and not re.fullmatch(r"C-[0-9a-f]{32}\.json", name):
                fail("$", "Unexpected operating audit member")
            raw = records.read(name, maximum=MAX_BYTES)
            total += len(raw)
            if total > 8 * MAX_BYTES:
                fail("$", "Operating history exceeds aggregate byte bound")
            if staged:
                # A killed writer can leave an unpublished staging file. Retain
                # its bytes as evidence; it is never an audit event or authority.
                continue
            event = loads(raw.decode("utf-8"))
            errors = _schema("operating-change", event)
            if errors:
                raise OperatingError(errors)
            if name != event["id"] + ".json":
                fail("$", "Operating event identity differs from its filename")
            events.append(event)
    events.sort(key=lambda e: e["sequence"])
    previous = None
    for index, event in enumerate(events, 1):
        if event["sequence"] != index or event["previous_change_id"] != (previous["id"] if previous else None):
            fail("$", "Operating audit is not a complete append-only chain")
        if previous and event["before_hash"] != previous["after_hash"]:
            fail("$", "Operating audit hash chain differs")
        previous = event
    return events


def _snapshot(tree, governance):
    if _read(tree, JOURNAL) is not None:
        fail("$", "Pending operating transaction: inspect and recover using its independent --expected-journal-sha256 before dispatch")
    raw = _read(tree, CONFIG)
    if raw is None:
        fail("$", "OPERATING_CONFIG.yaml is missing; initialize operating defaults or explicitly restore reviewed configuration")
    snapshot = validate_operating(load_yaml(raw), governance)
    events = _events(tree)
    if events and events[-1]["after_hash"] != snapshot.operating_hash:
        fail("$", "Operating file differs from the last audited change; reconcile the external edit before dispatch")
    return replace(snapshot, last_change_id=events[-1]["id"] if events else None,
                   _provenance_json=canonical({"path": str(tree.root / CONFIG), "raw_sha256": sha256(raw),
                                              "audit": "recorded" if events else "unrecorded_project_owned"}))


@contextmanager
def locked_operating(project_root, governance):
    with Tree(project_root) as tree, _lock(tree):
        _bound_governance(tree, governance)
        yield _snapshot(tree, governance)


def load_operating(project_root, governance):
    with locked_operating(project_root, governance) as snapshot:
        return snapshot


def inspect_operating(project_root, governance):
    try:
        value = load_operating(project_root, governance)
        return {"status": "ACCEPTED", "hash": value.operating_hash, "source": value.source,
                "streams": value.count, "last_change_id": value.last_change_id, "refusals": [], **operating_ceiling(governance)}
    except (ValidationError, OSError, ValueError) as exc:
        return {"status": "REJECTED", "hash": None, "source": None, "streams": None, "last_change_id": None,
                "refusals": getattr(exc, "refusals", [refusal("$", str(exc) if isinstance(exc, ValidationError) else
                    "Operating configuration could not be read; restore the project file and correct filesystem/runtime access")]),
                "diagnostic": None if isinstance(exc, ValidationError) else str(exc)}


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _publish_new(parent, handle, temp, leaf):
    """Atomic no-replace rename: never overwrite a conflicting final record."""
    if os.name == "nt":
        os.rename(parent / temp, parent / leaf)
        return
    import ctypes
    import errno
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename, flag = getattr(library, "renameat2", None), 1  # RENAME_NOREPLACE
    elif sys.platform == "darwin":
        rename, flag = getattr(library, "renameatx_np", None), 4  # RENAME_EXCL
    else:
        rename, flag = None, None
    if rename is None:
        raise OSError(errno.ENOTSUP, "Atomic no-replace operating publication is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(handle, os.fsencode(temp), handle, os.fsencode(leaf), flag):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(parent / leaf))


def _new(tree, name, raw):
    parent, handle, leaf = tree.parent(name, create=True)
    if tree.inspect(name) is not None:
        raise FileExistsError("Operating record already exists: " + name)
    temp = ".awf-operating-" + uuid.uuid4().hex + ".tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(parent / temp, flags, 0o600) if os.name == "nt" else os.open(temp, flags, 0o600, dir_fd=handle)
    try:
        with os.fdopen(fd, "wb") as stream:
            if stream.write(raw) != len(raw):
                raise OSError("Incomplete operating staging write")
            stream.flush()
            os.fsync(stream.fileno())
        _publish_new(parent, handle, temp, leaf)
        if os.name != "nt":
            os.fsync(handle)
    finally:
        try:
            if os.name == "nt":
                os.unlink(parent / temp)
            else:
                os.unlink(temp, dir_fd=handle)
        except FileNotFoundError:
            pass


def _commit(tree, governance, before, value, instruction, changed, applied_from, *, created_at=None):
    after = validate_operating(value, _bound_governance(tree, governance))
    events = _events(tree)
    if _read(tree, JOURNAL) is not None:
        fail("$", "Pending transaction must be recovered before another operating change")
    current = _read(tree, CONFIG)
    if (before is None and current is not None) or (before is not None and current != before):
        fail("$", "Operating bytes changed during preparation")
    before_hash = fingerprint("operating", load_yaml(before)) if before is not None else None
    if events and events[-1]["after_hash"] != before_hash:
        fail("$", "Current operating bytes disagree with the audit chain")
    event = {"id": "C-" + uuid.uuid4().hex, "created_at": created_at or now_text(), "instruction": instruction,
             "before_hash": before_hash, "after_hash": after.operating_hash, "changes": changed,
             "source": value["source"], "applied_from": applied_from, "sequence": len(events) + 1,
             "previous_change_id": events[-1]["id"] if events else None, "governance_hash": after.governance_hash}
    errors = _schema("operating-change", event)
    if errors:
        raise OperatingError(errors)
    raw = before if before is not None and canonical(load_yaml(before)) == canonical(value) else _json(value)
    journal = {"format": "awf-operating-transaction-1", "before": None if before is None else before.decode("utf-8"),
               "after": raw.decode("utf-8"), "event": event}
    event_raw, journal_raw = _json(event), _json(journal)
    for path, data in (("$.configuration", raw), ("$.event", event_raw), ("$.journal", journal_raw)):
        if len(data) > MAX_BYTES:
            fail(path, "Serialized operating transaction member exceeds its 1 MiB read/recovery limit; reduce the change or use a reviewed migration")
    _new(tree, JOURNAL, journal_raw)
    # On any interruption retain the WAL. Never claim config/audit are a single rename.
    if _read(tree, CONFIG) != before:
        fail("$", "Operating bytes changed before commit; journal retained")
    _bound_governance(tree, governance)
    tree.write(CONFIG, raw)
    _new(tree, STATE + "/changes/" + event["id"] + ".json", event_raw)
    if _read(tree, CONFIG) != raw:
        fail("$", "Operating write readback differs; journal retained")
    tree.unlink(JOURNAL)
    return _snapshot(tree, governance)


def initialize_operating(project_root, governance):
    """Preserve valid project choices; honestly audit bootstrap initialization."""
    with Tree(project_root) as tree, _lock(tree):
        _bound_governance(tree, governance)
        before = _read(tree, CONFIG)
        if before is not None:
            snapshot = _snapshot(tree, governance)
            if snapshot.last_change_id:
                return snapshot
            value = snapshot.config
        else:
            value = default_operating()
        return _commit(tree, governance, before, value, None, [], "bootstrap")


def recover_operating(project_root, governance, expected_journal_sha256):
    """Complete only the exact externally pinned intent; never undo unmatched edits."""
    with Tree(project_root) as tree, _lock(tree):
        _bound_governance(tree, governance)
        raw = _read(tree, JOURNAL)
        if raw is None or sha256(raw) != expected_journal_sha256:
            fail("$", "Recovery needs the current independently inspected journal SHA-256")
        journal = loads(raw.decode("utf-8"))
        if not isinstance(journal, dict) or set(journal) != {"format", "before", "after", "event"} or journal["format"] != "awf-operating-transaction-1":
            fail("$", "Malformed operating recovery journal")
        event = journal["event"]
        errors = _schema("operating-change", event)
        if errors:
            raise OperatingError(errors)
        before = journal["before"].encode("utf-8") if isinstance(journal["before"], str) else None
        if not isinstance(journal["after"], str) or (journal["before"] is not None and before is None):
            fail("$", "Malformed journal file bytes")
        after = journal["after"].encode("utf-8")
        checked = validate_operating(load_yaml(after), governance)
        if checked.operating_hash != event["after_hash"] or checked.governance_hash != event["governance_hash"] or (
                (fingerprint("operating", load_yaml(before)) if before is not None else None) != event["before_hash"]):
            fail("$", "Journal content/hash/governance binding differs")
        current = _read(tree, CONFIG)
        if current not in (before, after):
            fail("$", "Operating bytes contain an unmatched external edit; preserve the journal")
        events = _events(tree)
        path = STATE + "/changes/" + event["id"] + ".json"
        saved = _read(tree, path)
        if saved is not None:
            if saved != _json(event) or not events or events[-1] != event or current != after:
                fail("$", "Recovery audit differs from the prepared terminal event")
        elif event["sequence"] != len(events) + 1 or event["previous_change_id"] != (events[-1]["id"] if events else None) or (
                events and events[-1]["after_hash"] != event["before_hash"]):
            fail("$", "Recovery journal does not extend the current audit")
        if current != after:
            tree.write(CONFIG, after)
        if saved is None:
            _new(tree, path, _json(event))
        tree.unlink(JOURNAL)
        return _snapshot(tree, governance)


def _path(path, epic_id=None):
    qualified = re.fullmatch(r"epic_overrides\.([^.]+)\.(.+)", path)
    if qualified:
        if epic_id is not None:
            fail("$." + path, "Use either a fully qualified Epic path or --epic, never both")
        return _path(qualified[2], qualified[1])
    if re.fullmatch(r"[A-F]\.(worker|reviewer)(\.pinned)?", path):
        path = "streams." + path
    if path not in {"streams.count", "controller", "specialist", "simple_worker", "simple_worker.enabled",
                    "controller.pinned", "specialist.pinned", "simple_worker.pinned"} and not re.fullmatch(r"streams\.[A-F]\.(worker|reviewer)(\.pinned)?", path):
        fail("$." + path, "Unsupported operating path; use --epic with a stream/role route for an explicit Epic scope")
    if epic_id is not None:
        if not isinstance(epic_id, str) or len(epic_id) > 80 or not re.fullmatch(EPIC_ID, epic_id):
            fail("$.epic", "Supply the exact observed Epic ID (for example PROJ-123); scope is never inferred")
        if path == "streams.count" or path.startswith("simple_worker"):
            fail("$." + path, "Epic scope supports only worker/reviewer stream routes and controller/specialist routes")
        return "epic_overrides." + epic_id + "." + path
    return path


def _get(value, path):
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return deepcopy(value)


def _put(value, path, selected):
    keys = path.split(".")
    cursor = value
    for key in keys[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[keys[-1]] = deepcopy(selected)


def _selection(path, text):
    if path == "streams.count":
        if not re.fullmatch(r"[0-9]{1,3}", text):
            fail("$." + path, "Stream count must be an integer")
        return int(text)
    if path.endswith((".pinned", ".enabled")):
        if text not in {"true", "false"}:
            fail("$." + path, "Expected true or false")
        return text == "true"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/(low|medium|high|xhigh|max|ultra)", text):
        fail("$." + path, "Route requires model/reasoning_effort")
    model, effort = text.split("/")
    return {**_pair(model, effort), "pinned": True}


def _editable_snapshot(tree, governance, initialize):
    if _read(tree, JOURNAL) is not None:
        fail("$", "Pending operating transaction requires pinned recovery before another change")
    raw = _read(tree, CONFIG)
    if raw is None:
        if not initialize:
            fail("$", "Operating file is absent; explicitly use --initialize with reviewed --set choices")
        if _events(tree):
            fail("$", "Missing audited operating bytes require recovery, not reinitialization")
        value = default_operating()
        return OperatingSnapshot(canonical(value), fingerprint("operating", value), fingerprint("operating-governance", governance)), None
    if initialize:
        fail("$", "--initialize cannot overwrite an existing operating file")
    try:
        return _snapshot(tree, governance), raw
    except OperatingError:
        # A new protected policy can invalidate previously audited choices. Only
        # an exact existing audit tip permits repair; dispatch still rejects it.
        value, events = load_yaml(raw), _events(tree)
        if not events or events[-1]["after_hash"] != fingerprint("operating", value):
            fail("$", "Invalid or externally edited operating file lacks its exact audited tip; preserve and reconcile it")
        if not isinstance(value, dict) or not isinstance(value.get("streams"), dict):
            fail("$", "Malformed operating structure requires explicit reviewed recovery")
        return OperatingSnapshot(canonical(value), fingerprint("operating", value), fingerprint("operating-governance", governance), events[-1]["id"]), raw


def set_operating(project_root, governance, instruction, assignments=(), *, recommendation_id=None, accept=None, initialize=False, epic_id=None):
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction.encode("utf-8")) > 65536:
        fail("$.instruction", "Record the nonempty bounded user instruction verbatim")
    if bool(assignments) == bool(recommendation_id) or bool(recommendation_id) != bool(accept):
        fail("$", "Choose --set pairs OR --recommendation with --accept")
    if initialize and recommendation_id:
        fail("$", "--initialize requires explicit custom --set choices, not a prior recommendation")
    if epic_id is not None and recommendation_id:
        fail("$.epic", "A project-wide recommendation cannot be reinterpreted as an Epic-scoped change")
    with Tree(project_root) as tree, _lock(tree):
        _bound_governance(tree, governance)
        if recommendation_id:
            snapshot, before = _snapshot(tree, governance), tree.read(CONFIG, maximum=MAX_BYTES)
        else:
            snapshot, before = _editable_snapshot(tree, governance, initialize)
        value, selected = snapshot.config, {}
        if recommendation_id:
            record = _load_recommendation(tree, recommendation_id, snapshot, governance)
            available = {row["path"]: row["recommended"] for row in record["rows"]}
            paths = list(available) if accept == "all" else [_path(p) for p in accept.split(",")]
            if not paths or len(paths) != len(set(paths)) or any(p not in available for p in paths):
                fail("$.accept", "Select unique named rows from this recommendation")
            selected = {p: deepcopy(available[p]) for p in paths}
            source = "recommendation:" + recommendation_id
        else:
            for assignment in assignments:
                if "=" not in assignment:
                    fail("$", "Each --set requires path=value")
                path, text = assignment.split("=", 1)
                path = _path(path, epic_id)
                if path in selected or any(path.startswith(p + ".") or p.startswith(path + ".") for p in selected):
                    fail("$." + path, "Duplicate or overlapping set paths")
                selected[path] = _selection(path, text)
            source = "user"
        count = selected.get("streams.count", value["streams"]["count"])
        if type(count) is int and 1 <= count <= 6:
            for label in LABELS[:count]:
                value["streams"].setdefault(label, _stream())
        for path, choice in selected.items():
            if path.startswith("epic_overrides.") and path.endswith(".pinned") and _get(value, path.rsplit(".", 1)[0]) is None:
                fail("$." + path, "Choose a complete scoped route before changing its pin")
            if path == "simple_worker" and isinstance(choice, dict):
                choice.setdefault("enabled", value["simple_worker"]["enabled"])
            _put(value, path, choice)
        value["source"] = source
        validate_operating(value, governance)
        paths = sorted(set(selected) | {"source"} | {"streams." + s for s in LABELS if s in value["streams"] and s not in snapshot.config["streams"]})
        changes = [{"path": p, "before": _get(snapshot.config, p), "after": _get(value, p)} for p in paths
                   if _get(snapshot.config, p) != _get(value, p)]
        return _commit(tree, governance, before, value, instruction, changes, "chat")


def _scope(paths, epic_id):
    if not paths:
        return None
    result = []
    unknown = False
    for path in paths:
        cleaned = path[:-3] if path.endswith("/**") else path[:-2] if path.endswith("/*") else path.removesuffix("/")
        if any(mark in cleaned for mark in "*?[]{}"):
            fail("$.epics." + epic_id + ".scope", "Epic " + epic_id + " scope value " + repr(path)
                 + " has unsupported glob syntax; use a bounded path prefix or a trailing /* or /**")
        try:
            relative_parts(cleaned)
            result.append(cleaned)
        except ValidationError:
            unknown = True
    return None if unknown else sorted(set(result))


def _overlap(left, right):
    a, b = left.casefold().split("/"), right.casefold().split("/")
    return a[:len(b)] == b or b[:len(a)] == a


def _epic_groups(epics, ceiling, scopes):
    unknown = next((epic for epic in epics if scopes[epic["id"]] is None), None)
    overlapping = any(_overlap(a, b) for i, epic in enumerate(epics) for other in epics[i + 1:]
                      for a in (scopes[epic["id"]] or []) for b in (scopes[other["id"]] or []))
    if unknown or overlapping:
        reason = "Overlapping Epic scope conservatively requires one stream"
        if unknown:
            paths = unknown["scope"]
            value = next((p for p in paths if _scope([p], unknown["id"]) is None), paths) if paths else paths
            reason = "Unknown Epic scope: " + unknown["id"] + " value " + repr(value) + "; conservatively requires one stream"
        return [{"label": "A", "epic_ids": sorted(scopes), "ticket_ids": [],
                 "write_paths": sorted({p for paths in scopes.values() for p in paths or []})}], reason
    count = min(len(epics), ceiling, 6)
    groups = [{"label": LABELS[i], "epic_ids": [], "ticket_ids": [], "write_paths": []} for i in range(count)]
    for i, epic in enumerate(epics):
        group = groups[i % count]
        group["epic_ids"].append(epic["id"])
        group["write_paths"].extend(scopes[epic["id"]])
    return groups, "Disjoint Epic path scopes: " + ", ".join(epic["id"] for epic in epics)


def _risk(epics, paths, governance):
    policy = governance["execution"]["model_routing"]
    flags = {flag for epic in epics for flag in epic.get("risk_flags", [])}
    high = sorted(flags.intersection(policy["high_risk_flags"]))
    text = " ".join(text for epic in epics for text in (epic["title"], epic.get("scope"), epic.get("_inventory_scope"))
                    if isinstance(text, str)).casefold()
    specialists = []
    for name, rule in sorted(governance.get("specialist_reviews", {}).items()):
        triggered = any(keyword.casefold() in text for keyword in rule.get("keywords", [])) or bool(flags.intersection(rule.get("risk_flags", [])))
        for pattern in rule.get("paths", []):
            fixed = re.split(r"[?*[]", pattern, maxsplit=1)[0].rstrip("/")
            triggered |= any(fnmatch.fnmatchcase(path, pattern) or (fixed and _overlap(path, fixed)) for path in paths)
        if triggered:
            specialists.append(name)
    simple = bool(epics) and all(epic.get("complexity") == "low" and epic.get("uncertainty") == "low"
                                and epic.get("verification") == "strong" and not epic.get("risk_flags") for epic in epics)
    return high, specialists, simple


def _route_key(route):
    return route["model"], route["reasoning_effort"]


def _governing_route(path, policy):
    role = path.rsplit(".", 1)[-1]
    role = "critic" if role == "reviewer" else role
    route = deepcopy(policy["role_defaults"][role])
    if role in {"critic", "specialist"}:
        from .model_routing import EFFORTS
        floor = policy["review_floor"]
        route["model"] = max((route["model"], floor["model"]), key=policy["model_order"].index)
        route["reasoning_effort"] = max((route["reasoning_effort"], floor["reasoning_effort"]), key=EFFORTS.index)
    return route


def _keep_pin(path, route, explanation):
    return {"path": path, "current": route, "recommended": deepcopy(route),
            "reason": "Keeps your pin unless you say otherwise; " + explanation
                      + ". Mandatory risk/review floors still apply at dispatch"}


def recommend_operating(epics_raw, snapshot, governance, *, inventory_raw=None, now=None, input_files=None):
    """Pure deterministic recommendation at an explicit timestamp; no authority."""
    validate_snapshot(snapshot, governance)
    if len(epics_raw) > MAX_BYTES or (inventory_raw is not None and len(inventory_raw) > MAX_BYTES):
        fail("$.inputs", "Recommendation input exceeds its byte limit")
    value = loads(epics_raw.decode("utf-8"))
    errors = _schema("operating-epics", value)
    if errors:
        raise OperatingError(errors)
    epics = sorted(value["epics"], key=lambda e: e["id"])
    if len({e["id"] for e in epics}) != len(epics):
        fail("$.epics", "Duplicate Epic identities")
    # Validate even when an inventory supplies grouping: unsupported scope
    # syntax must never silently become an unknown or unused scope.
    scopes = {epic["id"]: _scope(epic["scope"], epic["id"]) for epic in epics}
    created = now or now_text()
    timestamp(created)
    record = {"created_at": created, "operating_hash": snapshot.operating_hash, "governance_hash": snapshot.governance_hash,
              "inputs": {"epics": sha256(epics_raw), "inventory": sha256(inventory_raw) if inventory_raw is not None else None},
              "input_files": input_files or {"epics": None, "inventory": None},
              "input_content": {"epics": epics_raw.decode("utf-8"), "inventory": inventory_raw.decode("utf-8") if inventory_raw is not None else None},
              "rows": [], "epic_count": len(epics), "ticket_count": 0, "execution_authority": False, "applied": False}
    current, policy = snapshot.config, governance["execution"]["model_routing"]
    if epics:
        ceiling = operating_ceiling(governance)["effective_ceiling"]
        ticket_metadata = {}
        inventory_epics = {}
        if inventory_raw is not None:
            from .streams import recommendation_groups
            planned = recommendation_groups(inventory_raw, sha256(inventory_raw), created,
                                            execution=governance["execution"], allow_synthetic=True)
            inventory_value = loads(inventory_raw.decode("utf-8"))
            if inventory_value["project"]["repository"] != governance.get("github", {}).get("repository"):
                fail("$.inventory.project.repository", "Inventory repository differs from the governing project")
            known = {e["id"] for e in epics}
            owned_epics = {t["epic_id"] for t in inventory_value["tickets"] if t["dispatch_scope"] == "in_scope" and t["epic_id"] is not None}
            if not owned_epics <= known:
                fail("$.epics", "Epic snapshot omits owned ticket Epic identities from the complete inventory")
            ticket_metadata = {t["id"]: t for t in inventory_value["tickets"]}
            inventory_epics = {e["id"]: e for e in inventory_value["epics"]}
            group_count = planned["independent_group_count"]
            groups = [{"label": LABELS[i], "ticket_ids": [], "epic_ids": [], "write_paths": []} for i in range(group_count)]
            for index, group in enumerate(planned["groups"] if group_count else []):
                for key in ("ticket_ids", "epic_ids", "write_paths"):
                    groups[index % group_count][key] = sorted(set(groups[index % group_count][key]) | set(group[key]))
            record["ticket_count"] = len(inventory_value["tickets"])
            reason = "Planner independent future write groups; current readiness never creates extra authority"
        else:
            groups, reason = _epic_groups(epics, ceiling, scopes)
        if groups:
            proposed = deepcopy(current)
            count = min(len(groups), ceiling)
            proposed["streams"]["count"] = count
            choices = [("streams.count", count, reason)]
            for group in groups[:count]:
                label = group["label"]
                proposed["streams"].setdefault(label, _stream())
                relevant = []
                for item in epics:
                    if item["id"] not in group["epic_ids"]:
                        continue
                    observed = inventory_epics.get(item["id"], {})
                    # Merge each identity once: sparse inventory metadata may
                    # add risk/title evidence without erasing observed markers.
                    merged = {**observed, **item}
                    merged["risk_flags"] = sorted(set(observed.get("risk_flags", [])) | set(item.get("risk_flags", [])))
                    merged["title"] = item["title"] + " " + observed.get("title", "")
                    merged["_inventory_scope"] = observed.get("scope")
                    relevant.append(merged)
                relevant += [ticket_metadata[t] for t in group["ticket_ids"] if t in ticket_metadata]
                high, specialists, simple = _risk(relevant, group["write_paths"], governance)
                worker = deepcopy(policy["risk_route"] if high or specialists else _governing_route("worker", policy))
                reviewer = _governing_route("reviewer", policy)
                if high:
                    from .model_routing import EFFORTS
                    reviewer["model"] = max((reviewer["model"], policy["risk_route"]["model"]), key=policy["model_order"].index)
                    reviewer["reasoning_effort"] = max((reviewer["reasoning_effort"], "high"), key=EFFORTS.index)
                why = ("High-risk flags " + ", ".join(high) if high else "Specialist triggers " + ", ".join(specialists)
                       if specialists else "Entire scope is simple with strong verification; retain the default worker ceiling"
                       if simple else "Default route for the declared bounded scope")
                choices.extend((("streams." + label + ".worker", worker, why),
                                ("streams." + label + ".reviewer", reviewer, "High-risk review" if high else "Governing review floor")))
                if simple:
                    choices.append(("simple_worker.enabled", True, "Entire assigned scope is simple, low uncertainty and strongly verified"))
            controller = _governing_route("controller", policy)
            if count > 4:
                from .model_routing import EFFORTS
                controller["reasoning_effort"] = max((controller["reasoning_effort"], "high"), key=EFFORTS.index)
            choices.extend((("controller", controller,
                             "More than four streams" if count > 4 else "Default controller route"),
                            ("specialist", _governing_route("specialist", policy), "Default specialist route")))
            rows = {}
            for path, choice, explanation in choices:
                before = _get(current, path)
                if isinstance(choice, dict) and "model" in choice:
                    if before and before.get("pinned", False):
                        rows[path] = _keep_pin(path, before, explanation)
                        continue
                    # Absent and explicit false pin metadata have the same
                    # meaning. An unchanged route creates no new pin intent.
                    if before and _route_key(before) == _route_key(choice):
                        continue
                    choice = {**choice, "pinned": _route_key(choice) != _route_key(_governing_route(path, policy))}
                if before != choice or (path == "streams.count" and reason.startswith("Unknown Epic scope:")):
                    _put(proposed, path, choice)
                    rows[path] = {"path": path, "current": before, "recommended": choice, "reason": explanation}
            validate_operating(proposed, governance)
            record["rows"] = list(rows.values())
        for epic in epics:
            scoped = current.get("epic_overrides", {}).get(epic["id"], {})
            routes = [(role, scoped[role]) for role in ("controller", "specialist") if role in scoped]
            routes += [("streams." + label + "." + role, route) for label, entry in sorted(scoped.get("streams", {}).items())
                       for role, route in sorted(entry.items())]
            for path, route in routes:
                if route.get("pinned", False):
                    record["rows"].append(_keep_pin("epic_overrides." + epic["id"] + "." + path, route,
                                                    "Explicit route for Epic " + epic["id"] + " retains precedence"))
    record["id"] = "R-" + fingerprint("operating-recommendation", record)[:24]
    errors = _schema("operating-recommendation", record)
    if errors:
        raise OperatingError(errors)
    if len(_json(record)) > MAX_BYTES:
        fail("$.inputs", "Serialized recommendation and pinned input content exceed the aggregate 1 MiB record limit")
    return record


def save_recommendation(project_root, governance, epics_path, inventory_path=None, *, now=None):
    files = {"epics": str(Path(epics_path).absolute()), "inventory": str(Path(inventory_path).absolute()) if inventory_path else None}
    content = {}
    for key, path in files.items():
        if path:
            with Tree(Path(path).parent) as input_tree:
                content[key] = input_tree.read(Path(path).name, maximum=MAX_BYTES)
        else:
            content[key] = None
    with Tree(project_root) as tree, _lock(tree):
        _bound_governance(tree, governance)
        snapshot = _snapshot(tree, governance)
        record = recommend_operating(content["epics"], snapshot, governance, inventory_raw=content["inventory"], now=now, input_files=files)
        _new(tree, STATE + "/recommendations/" + record["id"] + ".json", _json(record))
        return record


def _load_recommendation(tree, identifier, snapshot, governance):
    if not re.fullmatch(r"R-[0-9a-f]{24}", identifier):
        fail("$.recommendation", "Invalid recommendation identity")
    record = loads(tree.read(STATE + "/recommendations/" + identifier + ".json", maximum=MAX_BYTES).decode("utf-8"))
    errors = _schema("operating-recommendation", record)
    if errors:
        raise OperatingError(errors)
    if record["operating_hash"] != snapshot.operating_hash or record["governance_hash"] != snapshot.governance_hash:
        fail("$.recommendation", "Recommendation is stale: operating/governance changed; request a new recommendation")
    for key, path in record["input_files"].items():
        if path:
            with Tree(Path(path).parent) as input_tree:
                raw = input_tree.read(Path(path).name, maximum=MAX_BYTES)
            if sha256(raw) != record["inputs"][key]:
                fail("$.recommendation.inputs." + key, "Input bytes changed after recommendation; review fresh inputs")
    expected = recommend_operating(record["input_content"]["epics"].encode("utf-8"), snapshot, governance,
                                  inventory_raw=record["input_content"]["inventory"].encode("utf-8") if record["input_content"]["inventory"] is not None else None,
                                  now=record["created_at"], input_files=record["input_files"])
    if record != expected or record["id"] != identifier:
        fail("$.recommendation", "Recommendation content differs from its pinned deterministic inputs")
    return record


def _display(value):
    if value is None:
        return "—"
    if isinstance(value, dict) and "model" in value:
        return value["model"] + " / " + value["reasoning_effort"] + (" (pinned)" if value.get("pinned") else "")
    return str(value).lower() if isinstance(value, bool) else str(value)


def render_operating(snapshot, governance, *, state, version=VERSION):
    validate_snapshot(snapshot, governance)
    value, policy = snapshot.config, governance["execution"]["model_routing"]
    capacity = operating_ceiling(governance)
    lines = [f"AWF {version}: {state} — operating configuration (source: {snapshot.source})",
             f"Streams: {snapshot.count} of ceiling {capacity['effective_ceiling']}, one reviewer per stream; binding limits: "
             + ", ".join(capacity["effective_ceiling_sources"]) + ". Observed host slots remain separate.",
             "| Stream | Worker | Reviewer |", "|---|---|---|"]
    for label in LABELS[:snapshot.count]:
        item = value["streams"][label]
        lines.append(f"| {label} | {_display(item['worker'])} | {_display(item['reviewer'])} |")
    for epic_id, scoped in sorted(value.get("epic_overrides", {}).items()):
        lines.extend(["", "Epic " + epic_id + " overrides (matching observed Epic only):",
                      "| Setting | Route |", "|---|---|"])
        for label, entry in sorted(scoped.get("streams", {}).items()):
            for role in ("worker", "reviewer"):
                if role in entry:
                    lines.append("| streams." + label + "." + role + " | " + _display(entry[role]) + " |")
        for role in ("controller", "specialist"):
            if role in scoped:
                lines.append("| " + role + " | " + _display(scoped[role]) + " |")
    lines.extend(["Controller " + _display(value["controller"]) + "; Specialist " + _display(value["specialist"]),
                  "Simple work (low risk, strong verification): " + (_display(value["simple_worker"]) if value["simple_worker"]["enabled"] else "disabled"),
                  f"Escalation: ≤{policy['escalation']['max_escalations_per_ticket']} per ticket, effort ceiling {policy['escalation']['effort_ceiling']}; "
                  + "review floor " + _display(policy["review_floor"]) + "; risk route " + _display(policy["risk_route"]),
                  "Options: keep | review Epics and recommend | custom (say what you want)"])
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])


def render_recommendation(record):
    if not record["epic_count"]:
        return "No Epics were supplied; keep current operating settings or choose custom."
    kept, changes = [], []
    for row in record["rows"]:
        unchanged_pin = (row["current"] == row["recommended"] and isinstance(row["current"], dict)
                         and row["current"].get("pinned") is True)
        (kept if unchanged_pin else changes).append(row)
    lines = [f"Recommendation {record['id']} (from {record['epic_count']} Epics, {record['ticket_count']} tickets)", ""]
    lines.extend(["| Setting | Current | Recommended | Why |", "|---|---|---|---|"] if changes else ["No operating changes recommended."])
    for row in changes:
        cells = [row["path"], _display(row["current"]), _display(row["recommended"]), row["reason"]]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    if kept:
        lines.extend(["", "Keeps your pins unless you say otherwise: " + ", ".join(row["path"] for row in kept)
                      + ". Mandatory risk/review floors still apply at dispatch."])
    lines.extend(["", "Reply: adopt all | adopt <rows> | keep current"])
    return "\n".join(lines)


def _local_state(root, governance):
    # Local adoption acceptance only; never calls GitHub or claims ACTIVE.
    from .installer import verify_installed
    from .configuration import inspect_config
    from .contracts import Contracts
    try:
        verify_installed(root)
        with Tree(root) as tree:
            workflow = load_yaml(tree.read(".agentic/workflow.yaml", maximum=MAX_BYTES))
        return "CONFIGURED" if inspect_config(governance, workflow, Contracts(Path(root) / ".agentic/schemas"))["status"] == "ACCEPTED" else "INSTALLED_UNCONFIGURED"
    except (ValidationError, OSError, ValueError):
        return "UNVERIFIED"


def main(argv=None, default_root=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Show, recommend or explicitly change project-owned operating choices; never change governance or launch models")
    parser.add_argument("--root", type=Path, default=default_root or Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("--json", action="store_true")
    setter = sub.add_parser("set")
    setter.add_argument("--instruction", required=True, help="Exact user words; local recording does not authenticate their source")
    setter.add_argument("--set", action="append", default=[], dest="assignments")
    setter.add_argument("--recommendation")
    setter.add_argument("--accept")
    setter.add_argument("--epic", help="Exact observed Epic ID; scope only the selected stream/role routes")
    setter.add_argument("--initialize", action="store_true", help="Explicitly start from Balanced defaults only when the root file is absent; validate all selected changes first")
    setter.add_argument("--json", action="store_true")
    recommend = sub.add_parser("recommend")
    recommend.add_argument("--epics", required=True, type=Path)
    recommend.add_argument("--inventory", type=Path)
    recommend.add_argument("--json", action="store_true")
    recover = sub.add_parser("recover", help="Complete a reviewed interrupted transaction using an independent journal pin")
    recover.add_argument("--expected-journal-sha256", required=True)
    recover.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        with Tree(args.root) as tree:
            governance = load_yaml(tree.read(GOVERNANCE, maximum=MAX_BYTES))
        if args.command == "recommend":
            record = save_recommendation(args.root, governance, args.epics, args.inventory)
            print(json.dumps(record, ensure_ascii=False, indent=2) if args.json else render_recommendation(record))
            return 0
        if args.command == "set":
            snapshot = set_operating(args.root, governance, args.instruction, args.assignments,
                                     recommendation_id=args.recommendation, accept=args.accept, initialize=args.initialize, epic_id=args.epic)
        elif args.command == "recover":
            snapshot = recover_operating(args.root, governance, args.expected_journal_sha256)
        else:
            snapshot = load_operating(args.root, governance)
        state = _local_state(args.root, governance)
        result = {"status": "ACCEPTED", "project_state": state, "operating_hash": snapshot.operating_hash,
                  "governance_hash": snapshot.governance_hash, "source": snapshot.source, "streams": snapshot.count,
                  **operating_ceiling(governance),
                  "last_change_id": snapshot.last_change_id, "configuration": snapshot.config,
                  "execution_authority": False, "table": render_operating(snapshot, governance, state=state)}
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["table"])
        return 0
    except (ValidationError, OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"status": "REJECTED", "refusals": getattr(exc, "refusals", [refusal("$", str(exc))]),
                          "execution_authority": False, "next_action": "Correct the named input or reconcile the pinned operating transaction; governance changes require a reviewed PR"}, ensure_ascii=False, indent=2))
        return 2
