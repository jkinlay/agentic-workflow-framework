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
from .lane_labels import MAX_LANES, STREAM_LABELS
from .canonical import canonical, fingerprint, loads as _canonical_loads, now_text, sha256, timestamp
from .canonical import load_yaml as load_governance_yaml
from .installer import INSTALLED, verify_installed
from .safeio import Tree, relative_parts
from .budgeting import resolve_settings
from .protected_settings import audit_matches, unprotected_hash, load_operating_yaml as load_yaml
from .operating_calendar import effective_calendar, SETTING_PATHS

CONFIG = "OPERATING_CONFIG.yaml"
GOVERNANCE = ".agentic/PROJECT_CONFIG.yaml"
STATE = ".agentic-state/operating"
LOCK = STATE + "/lock"
JOURNAL = STATE + "/pending.json"
DEFAULT_MAX_LANES = 6
DEFAULT_MAX_LANES_SOURCE = ".agentic/schemas/operating-config.schema.json#/properties/streams/properties/count/maximum"
EPIC_ID = r"[A-Z][A-Z0-9_]*-[1-9][0-9]*"
STAGING_NAME = r"\.awf-operating-[0-9a-f]{32}\.tmp"
MAX_BYTES = 1024 * 1024
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
SOURCE_MANIFEST = "MANIFEST.json"
SOURCE_MARKER = "scripts/build_release.py"


def loads(text):
    """Operating JSON diagnostics must not carry parser-selected input keys."""
    try:
        return _canonical_loads(text)
    except (ValidationError, ValueError, UnicodeError):
        raise ValidationError('Invalid operating JSON: <key>; malformed or unsupported values') from None


def stream_labels(count):
    """Return stable spreadsheet-style stream labels (A..Z, AA..)."""
    if type(count) is not int or count < 0:
        raise ValidationError("Stream label count must be a non-negative integer")
    if count <= MAX_LANES:
        return STREAM_LABELS[:count]
    labels = []
    for index in range(count):
        value, label = index + 1, ""
        while value:
            value, remainder = divmod(value - 1, 26)
            label = chr(ord("A") + remainder) + label
        labels.append(label)
    return tuple(labels)


def filesystem_reader(root):
    """Return a bounded repository-relative byte reader for a filesystem root."""
    root = Path(root)

    def read(name):
        path = root / name
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                return None
            return path.read_bytes()
        except OSError:
            return None

    return read


def tree_reader(tree):
    """Adapt a checked filesystem tree to the source-identity byte reader."""
    def read(name):
        return tree.read(name, maximum=MAX_BYTES) if tree.inspect(name) is not None else None

    return read


def is_awf_source_repository(read):
    """Recognize an uninstalled AWF source tree from repository-relative bytes."""
    try:
        if read(INSTALLED) is not None or read(SOURCE_MARKER) is None:
            return False
        raw = read(SOURCE_MANIFEST)
        if raw is None:
            return False
        manifest = loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return (isinstance(manifest, dict) and manifest.get("format") == "awf-manifest-1"
            and isinstance(manifest.get("files"), dict)
            and SOURCE_MARKER in manifest["files"])


class OperatingError(ValidationError):
    def __init__(self, refusals):
        self.refusals = refusals
        super().__init__(CONFIG + ": " + "; ".join(item["path"] + ": " + item["reason"] for item in refusals))


def refusal(path, reason, governance_path=None, governance_value=None):
    return {"file": CONFIG, "path": path, "reason": reason, "governance_path": governance_path,
            "governance_value": governance_value,
            "remedy": ("Change the binding governance value through a reviewed PR to " + GOVERNANCE
                       if governance_path else "Correct the named operating input; preserve recovery evidence when present")}


def fail(path, reason, governance_path=None, governance_value=None):
    raise OperatingError([refusal(_input_path(path), reason, governance_path, governance_value)])


def _input_path(path):
    """Display field names, never identities selected by input mapping keys."""
    path = re.sub(r'(\.epic_overrides\.)[^.]+', r'\1<epic>', path)
    path = re.sub(r'(\.epics\.)[^.]+', r'\1<epic>', path)
    return re.sub(r'(\.streams\.)(?!count(?:\.|$))[^.]+', r'\1<stream>', path)


def _pair(model, effort):
    return {"model": model, "reasoning_effort": effort}


def _routing_policy(governance):
    from .model_routing import policy_from_config

    return policy_from_config(governance)


def _stream(policy=None):
    if policy is None:
        from .model_routing import default_policy

        policy = default_policy()
    return {"worker": deepcopy(policy["role_defaults"]["worker"])}


def default_operating(policy=None, *, count=3):
    from .model_routing import default_policy, validate_policy
    from .budgeting import fresh_settings

    policy = default_policy() if policy is None else validate_policy(deepcopy(policy))
    defaults = policy["role_defaults"]
    stream = _stream(policy)
    return {"version": 1, "source": "default", "budgeting": fresh_settings(),
            "operating_calendar": effective_calendar({}),
            "streams": {"count": count,
                        **{label: deepcopy(stream) for label in stream_labels(count)}},
            "critic": deepcopy(defaults["critic"]),
            "controller": deepcopy(defaults["controller"]),
            "specialist": deepcopy(defaults["specialist"]),
            "simple_worker": {"enabled": True, **deepcopy(policy["simple_worker"])}}


def operating_applicability(governance):
    """Report whether project governance supplies the policy operating choices bind to."""
    execution = governance.get("execution") if isinstance(governance, dict) else None
    if not isinstance(execution, dict) or "model_routing" not in execution:
        return {"status": "NOT_APPLICABLE",
                "reason": "Project configuration does not define a governing execution.model_routing policy; operating initialization is not applicable",
                "governance_path": "$.execution.model_routing"}
    return {"status": "APPLICABLE",
            "reason": "Project configuration defines execution.model_routing; operating initialization and semantic validation apply",
            "governance_path": "$.execution.model_routing"}


def _upgrade_safe_operating(governance):
    """Derive initial choices from retained governance; never broaden an older role allowlist."""
    execution = governance.get("execution", {}) if isinstance(governance, dict) else {}
    policy = execution.get("model_routing", {}) if isinstance(execution, dict) else {}
    defaults = policy.get("role_defaults", {}) if isinstance(policy, dict) else {}
    simple = policy.get("simple_worker") if isinstance(policy, dict) else None
    if not all(isinstance(defaults.get(role), dict)
               for role in ("controller", "worker", "critic", "specialist")) or not isinstance(simple, dict):
        legacy = default_operating()
        legacy.pop('budgeting')
        legacy.pop('operating_calendar')
        return legacy
    stream = {"worker": dict(defaults["worker"])}
    return {"version": 1, "source": "default",
            "streams": {"count": 3, **{label: deepcopy(stream) for label in stream_labels(3)}},
            "critic": dict(defaults["critic"]),
            "controller": dict(defaults["controller"]),
            "specialist": dict(defaults["specialist"]),
            "simple_worker": {"enabled": True, **dict(simple)}}


def _validated_lane_ceiling(configured):
    if type(configured) is not int or not 1 <= configured <= MAX_LANES:
        fail("$.streams.count", "Configured $.execution.max_lanes must be an exact integer in 1..12",
             "$.execution.max_lanes", configured)
    return configured


def configured_lane_ceiling(governance):
    """Validate the structural lane bound; only absence selects the default six."""
    execution = governance.get("execution") if isinstance(governance, dict) else None
    if not isinstance(execution, dict):
        fail("$.streams.count", "A governing execution configuration is required", "$.execution", execution)
    return _validated_lane_ceiling(execution.get("max_lanes", DEFAULT_MAX_LANES))


def operating_ceiling(governance):
    """Configured stream bound, independent of observed host slots or ownership."""
    execution = governance.get("execution") if isinstance(governance, dict) else None
    if not isinstance(execution, dict):
        fail("$.streams.count", "A governing execution configuration is required", "$.execution", execution)
    project = execution.get("max_parallel_tickets")
    if type(project) is not int or project < 1:
        fail("$.streams.count", "Invalid configured writer ceiling", "$.execution.max_parallel_tickets", project)
    configured = configured_lane_ceiling(governance)
    lane_source = "$.execution.max_lanes" if "max_lanes" in execution else DEFAULT_MAX_LANES_SOURCE
    limits = [(project, "$.execution.max_parallel_tickets"), (configured, lane_source)]
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


def _schema(name, value, *, stream_ceiling=MAX_LANES):
    from .contracts import checker
    schema = loads((SCHEMAS / (name + ".schema.json")).read_text(encoding="utf-8"))
    if name == "operating-config":
        # Specialization can only narrow the shipped twelve-lane contract.
        stream_ceiling = _validated_lane_ceiling(stream_ceiling)
        streams = schema["properties"]["streams"]
        route = deepcopy(streams["properties"]["A"])
        streams["properties"]["count"]["maximum"] = stream_ceiling
        labels = stream_labels(stream_ceiling)
        for label in list(streams["properties"]):
            if re.fullmatch(r"[A-Z]+", label) and label not in labels:
                del streams["properties"][label]
        for label in labels:
            streams["properties"].setdefault(label, deepcopy(route))
        scoped = schema["properties"]["epic_overrides"]["patternProperties"]
        epic = next(iter(scoped.values()))
        scoped_streams = epic["properties"]["streams"]
        scoped_route = deepcopy(next(iter(scoped_streams["patternProperties"].values())))
        scoped_streams["patternProperties"] = {
            "^(?:" + "|".join(re.escape(label) for label in labels) + ")$": scoped_route
        }
    errors = sorted(Draft202012Validator(schema, format_checker=checker()).iter_errors(value), key=lambda e: str(list(e.absolute_path)))
    def safe_path(parts):
        # Only literal schema properties are displayable. Pattern-matched map
        # identities are input too, even when their spelling is syntactically valid.
        node, path = schema, "$"
        for part in parts:
            if isinstance(part, int):
                path += "[item]"
                node = node.get("items", {})
            elif part in node.get("properties", {}):
                path += "." + part
                node = node["properties"][part]
            else:
                matches = [child for pattern, child in node.get("patternProperties", {}).items()
                           if re.search(pattern, str(part))]
                if not matches:
                    break
                path += "[entry]"
                node = matches[0]
        return path

    kinds = {"type": "wrong type", "additionalProperties": "Forbidden unknown key",
             "unevaluatedProperties": "Forbidden unknown key", "propertyNames": "unknown key",
             "required": "required field missing", "minimum": "out of range", "maximum": "out of range",
             "minLength": "out of range", "maxLength": "out of range", "minItems": "out of range",
             "maxItems": "out of range", "minProperties": "out of range", "maxProperties": "out of range"}
    return [refusal(safe_path(e.absolute_path),
                    "wrong type" if e.validator == 'const' and type(e.instance) is not type(e.validator_value)
                    else kinds.get(e.validator, "not allowed")) for e in errors[:30]]


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


def _calendar_problem_path(value):
    from .operating_calendar import _zone
    try:
        _zone(value.get('timezone', 'Europe/London'))
    except ValidationError:
        return '$.operating_calendar.timezone'
    return '$.operating_calendar.review_batching.presentation_time'


def validate_operating(value, governance):
    """Validate choices against both allowlists/floors; never silently clamp."""
    try:
        canonical(value)
    except ValidationError as exc:
        reason = ('out of range: integer exceeds interoperable JSON range'
                  if str(exc) == 'Integer exceeds interoperable JSON range' else 'wrong type or out of range')
        raise OperatingError([refusal('$', reason)]) from None
    execution = governance.get("execution") if isinstance(governance, dict) else None
    if not isinstance(execution, dict) or not isinstance(execution.get("model_routing"), dict):
        fail("$", "A complete governing execution/model_routing policy is required", "$.execution", execution)
    capacity = operating_ceiling(governance)
    ceiling = capacity["effective_ceiling"]
    structural_ceiling = configured_lane_ceiling(governance)
    errors = _schema("operating-config", value, stream_ceiling=structural_ceiling)
    # Structural refusals precede semantic consumers so malformed routes cannot
    # become dictionary lookups, identifiers or diagnostic interpolation.
    if not isinstance(value, dict) or not isinstance(value.get('streams'), dict):
        raise OperatingError(errors)
    if any(error['path'].startswith(('$.budgeting', '$.operating_calendar')) for error in errors):
        raise OperatingError(errors)
    if isinstance(value, dict):
        if 'operating_calendar' in value:
            try:
                effective_calendar(value)
            except ValidationError:
                fail(_calendar_problem_path(value['operating_calendar']), "not allowed")
        if 'budgeting' in value:
            from .budgeting import validate_settings
            try:
                validate_settings(value['budgeting'])
            except ValidationError:
                fail('$.budgeting', 'not allowed: enforcement requires accounting')

    configured_policy = execution["model_routing"]
    from .model_routing import EFFORTS
    try:
        policy = _routing_policy(governance)
    except ValidationError as exc:
        fail("$", str(exc), "$.execution.model_routing", configured_policy)
    streams = value.get("streams", {}) if isinstance(value, dict) else {}
    if not isinstance(streams, dict):
        raise OperatingError(errors or [refusal("$.streams", "Streams must be an object")])
    count = streams.get("count")
    if type(count) is int and (count > ceiling or count < 1):
        errors.append(refusal("$.streams.count", "out of range: Stream count must be 1.." + str(ceiling) + " within the effective governance ceiling; binding limits: "
                              + ", ".join(capacity["effective_ceiling_sources"]), capacity["effective_ceiling_governance_path"], ceiling))
    reviewers = execution.get("independent_reviewers", {})
    if not isinstance(reviewers, dict) or not isinstance(execution.get("roles"), dict):
        fail("$", "Malformed reviewer/role governance", "$.execution", execution)
    if "count" in reviewers and reviewers["count"] != count:
        errors.append(refusal("$.streams.count", "Legacy fixed reviewer count disagrees; remove/update it by governance PR",
                              "$.execution.independent_reviewers.count", reviewers["count"]))
    if type(count) is int and 1 <= count <= ceiling:
        for label in stream_labels(count):
            if label not in streams:
                errors.append(refusal("$.streams." + label, "Active stream requires a worker route"))
            elif "critic" not in value and (not isinstance(streams.get(label), dict)
                                              or "reviewer" not in streams[label]):
                errors.append(refusal("$.streams." + label + ".reviewer",
                                      "A shared critic route or a legacy reviewer route for every active stream is required"))
    pairs = [("$." + role, role, value.get(role)) for role in ("controller", "specialist", "critic")] if isinstance(value, dict) else []
    if isinstance(value, dict):
        pairs.append(("$.simple_worker", "worker", value.get("simple_worker")))
    if isinstance(streams, dict):
        # These labels are literal schema fields, not input-selected identities.
        for label in stream_labels(structural_ceiling):
            entry = streams.get(label)
            if isinstance(entry, dict):
                pairs.extend(("$.streams." + label + "." + key, role, entry.get(key))
                             for key, role in (("worker", "worker"), ("reviewer", "critic")))
    overrides = value.get("epic_overrides", {}) if isinstance(value, dict) else {}
    if isinstance(overrides, dict):
        for epic_id, scoped in overrides.items():
            if not re.fullmatch(EPIC_ID, epic_id) or len(epic_id) > 80 or not isinstance(scoped, dict):
                continue
            prefix = "$.epic_overrides.<epic>"
            pairs.extend((prefix + "." + role, role, scoped[role]) for role in ("controller", "critic", "specialist") if role in scoped)
            scoped_streams = scoped.get("streams", {})
            if isinstance(scoped_streams, dict):
                for label, entry in scoped_streams.items():
                    if label in stream_labels(structural_ceiling) and isinstance(entry, dict):
                        pairs.extend((prefix + ".streams.<stream>." + key, role, entry[key])
                                     for key, role in (("worker", "worker"), ("reviewer", "critic")) if key in entry)
    order = policy["model_order"]
    for path, role, pair in pairs:
        if not isinstance(pair, dict):
            continue
        model, effort = pair.get("model"), pair.get("reasoning_effort")
        if not isinstance(model, str) or not isinstance(effort, str):
            continue  # The schema already retained this missing/wrong-type refusal.
        if not isinstance(execution["roles"].get(role), dict):
            errors.append(refusal(path, "Role governance must declare its approved model IDs", "$.execution.roles." + role, execution["roles"].get(role)))
            continue
        for binding, allowed in (("$.execution.model_routing.role_allowed_models." + role, policy["role_allowed_models"].get(role, [])),
                                 ("$.execution.roles." + role + ".approved_model_ids", (execution["roles"].get(role) or {}).get("approved_model_ids", []))):
            if model not in allowed:
                errors.append(refusal(path + ".model", "not allowed: Model is outside the governing role allowlist", binding, allowed))
        choices = policy["models"].get(model, {}).get("reasoning_efforts", []) if isinstance(model, str) else []
        if effort not in choices:
            errors.append(refusal(path + ".reasoning_effort", "not allowed: Unsupported model/effort pair",
                                  ("$.execution.model_routing.models." + next(key for key in policy['models'] if key == model)
                                   + ".reasoning_efforts") if model in policy['models'] else "$.execution.model_routing.models", choices))
        floor = policy["review_floor"]
        if role in {"critic", "specialist"} and model in order and effort in EFFORTS and (
                order.index(model) < order.index(floor["model"]) or EFFORTS.index(effort) < EFFORTS.index(floor["reasoning_effort"])):
            errors.append(refusal(path, "not allowed: Reviewer route is below the review floor", "$.execution.model_routing.review_floor", floor))
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
    if is_awf_source_repository(tree_reader(tree)):
        fail("$", "Use an installed project outside the immutable release source for operating state")
    if tree.inspect(SOURCE_MANIFEST) is not None and tree.inspect(INSTALLED) is not None:
        # An adopted source checkout legitimately retains the release build
        # manifests.  Its receipt must verify before those manifests may stop
        # identifying the checkout as an immutable, uninstalled release source.
        verify_installed(tree.root)
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
    actual = load_governance_yaml(tree.read(GOVERNANCE, maximum=MAX_BYTES))
    if canonical(actual) != canonical(governance):
        fail("$", "Governance changed during preparation; reload and revalidate", "$.execution", None)
    return actual


def _events(tree, *, additional_bytes=0):
    directory = tree.root / STATE / "changes"
    if not directory.exists():
        return []
    with Tree(directory) as records:
        names = records.file_list()
        if len(names) + bool(additional_bytes) > 10000:
            fail("$", "Operating history exceeds bounded inventory; archive through an explicit reviewed migration")
        events, total = [], 0
        for name in names:
            staged = re.fullmatch(STAGING_NAME, name)
            if not staged and not re.fullmatch(r"C-[0-9a-f]{32}\.json", name):
                fail("$", "Unexpected operating audit member")
            raw = records.read(name, maximum=MAX_BYTES)
            total += len(raw)
            if total + additional_bytes > 8 * MAX_BYTES:
                fail("$", "Operating history exceeds aggregate byte bound")
            if staged:
                # A killed writer can leave an unpublished staging file. Retain
                # its bytes as evidence; it is never an audit event or authority.
                continue
            event = loads(raw.decode("utf-8"))
            if isinstance(event, dict) and "owner_admission" in event:
                fail("$", "1.9.6 pre-release confirmation records are unsupported; preserve this record and restore a supported 1.9.5 baseline before upgrade")
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
        if (previous and event["before_hash"] != previous["after_hash"]
                and not (previous.get("unprotected_after_hash") is not None
                         and event.get("unprotected_before_hash") == previous["unprotected_after_hash"])
                and not (isinstance(event.get("legacy_before_configuration"), dict)
                         and fingerprint("operating", event["legacy_before_configuration"]) == event["before_hash"]
                         and unprotected_hash(event["legacy_before_configuration"]) == event.get("unprotected_before_hash")
                         and audit_matches(event["legacy_before_configuration"], previous))):
            fail("$", "Operating audit hash chain differs")
        previous = event
    return events


def _snapshot(tree, governance, *, working_only=False):
    if _read(tree, JOURNAL) is not None:
        fail("$", "Pending operating transaction: inspect and recover using its independent --expected-journal-sha256 before dispatch")
    raw = _read(tree, CONFIG)
    if raw is None:
        fail("$", "OPERATING_CONFIG.yaml is missing; initialize operating defaults or explicitly restore reviewed configuration")
    working = load_yaml(raw)
    protected = None
    if not working_only:
        from .protected_settings import resolve_protected
        effective, protected = resolve_protected(tree.root, governance, working)
    else:
        effective = working
    snapshot = validate_operating(effective, governance)
    events = _events(tree)
    if events and not audit_matches(working, events[-1]):
        fail("$", "Operating file differs from the last audited change; reconcile the external edit before dispatch")
    snapshot = replace(snapshot, last_change_id=events[-1]["id"] if events else None,
                   _provenance_json=canonical({"path": str(tree.root / CONFIG), "raw_sha256": sha256(raw),
                                               "audit": "recorded" if events else "unrecorded_project_owned",
                                               "audit_sha256": sha256(canonical(events))}))
    if working_only:
        return snapshot
    return replace(snapshot, _provenance_json=canonical({**snapshot.provenance, 'protected_settings': protected}))


@contextmanager
def locked_operating(project_root, governance):
    with Tree(project_root) as tree, _lock(tree):
        _bound_governance(tree, governance)
        yield _snapshot(tree, governance)


def load_operating(project_root, governance):
    with locked_operating(project_root, governance) as snapshot:
        return snapshot


def read_operating(project_root, governance):
    """Read a stable operating snapshot without creating or opening a lock.

    Two independently pinned reads must agree.  A pending journal is rejected by
    ``_snapshot`` and a concurrent change is reported rather than racing a writer.
    Mutating entry points continue to use ``locked_operating``.
    """
    observed = []
    for _attempt in range(2):
        with Tree(project_root) as tree:
            _bound_governance(tree, governance)
            observed.append(_snapshot(tree, governance))
    first, second = observed
    if (first.operating_hash, first.governance_hash, first.last_change_id, first._provenance_json) != (
            second.operating_hash, second.governance_hash, second.last_change_id, second._provenance_json):
        fail("$", "CONCURRENT_CHANGE: operating files changed during the read-only observation; run the command again")
    return second


def _budget_admission_projection(value, protected):
    # Legacy diagnostics do not claim that the new controls are active. A
    # refused accepted-policy check must remain visible even without a block.
    admission = protected.get('budget_admission', True)
    # The fetched default branch may positively establish an absent file. Its
    # accepted defaults/legacy state is admissible even without a budget block.
    accepted_absence = (protected.get('commit') and
        protected.get('policy_observation', '').startswith('file absent at default-branch commit;'))
    return admission if admission is False or 'budgeting' in value or accepted_absence else None


def _budgeting_projection(value):
    if 'budgeting' in value:
        return resolve_settings(value)
    return {'disposition': 'LEGACY_RETAINED', 'settings_persisted': False,
            'diagnostic_fallback': resolve_settings(value),
            'diagnostic_fallback_meaning': 'Compatibility markers only; neither switch reports active 1.9.6 accounting or enforcement',
            'legacy_enforcement': 'UNOBSERVED',
            'history_import': 'EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED',
            'owner_adoption_required': True}


def inspect_operating(project_root, governance):
    try:
        value = read_operating(project_root, governance)
        return {"status": "ACCEPTED", "budget_admission": _budget_admission_projection(value.config, value.provenance.get('protected_settings', {})), "protected_settings": value.provenance.get("protected_settings", {}), "hash": value.operating_hash, "source": value.source,
                "streams": value.count, "last_change_id": value.last_change_id, "refusals": [],
                "budgeting": _budgeting_projection(value.config),
                **operating_ceiling(governance)}
    except OSError as exc:
        from .activation import access_diagnostic
        diagnostic = access_diagnostic(exc, "OPERATING_CONFIG.yaml or .agentic-state/operating")
        return {"status": "ACCESS_UNAVAILABLE", "hash": None, "source": None, "streams": None,
                "last_change_id": None, "refusals": [], "diagnostic": diagnostic,
                **operating_ceiling(governance)}
    except (ValidationError, ValueError) as exc:
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


def _prepare_commit(tree, governance, before, value, instruction, changed, applied_from, *, created_at=None):
    """Validate complete unprotected transaction bytes before publication."""
    after = validate_operating(value, _bound_governance(tree, governance))
    events = _events(tree)
    if _read(tree, JOURNAL) is not None:
        fail("$", "Pending transaction must be recovered before another operating change")
    current = _read(tree, CONFIG)
    if (before is None and current is not None) or (before is not None and current != before):
        fail("$", "Operating bytes changed during preparation")
    before_hash = fingerprint("operating", load_yaml(before)) if before is not None else None
    if events and not audit_matches(load_yaml(before), events[-1]):
        fail("$", "Current operating bytes disagree with the audit chain")
    event = {"id": "C-" + uuid.uuid4().hex, "created_at": created_at or now_text(), "instruction": instruction,
             "before_hash": before_hash, "after_hash": after.operating_hash, "changes": changed,
             "source": value["source"], "applied_from": applied_from, "sequence": len(events) + 1,
             "previous_change_id": events[-1]["id"] if events else None, "governance_hash": after.governance_hash}
    event['unprotected_before_hash'] = unprotected_hash(load_yaml(before)) if before is not None else None
    event['unprotected_after_hash'] = unprotected_hash(value)
    if events and events[-1].get('unprotected_after_hash') != unprotected_hash(load_yaml(before)) and before_hash != events[-1]['after_hash']:
        # Preserve authentic old records. Bind the exact pre-change configuration
        # so readers can independently verify the legacy/fresh projection bridge.
        event['legacy_before_configuration'] = load_yaml(before)
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
    # The proposed event must fit the existing inventory/aggregate limits before
    # either the lock or publication can leave state behind on a refusal.
    _events(tree, additional_bytes=len(event_raw))
    return event, raw, event_raw, journal_raw


def _commit(tree, governance, before, value, instruction, changed, applied_from, *, created_at=None):
    event, raw, event_raw, journal_raw = _prepare_commit(
        tree, governance, before, value, instruction, changed, applied_from,
        created_at=created_at)
    from .protected_settings import resolve_protected, require_mutation_policy
    effective, protected = resolve_protected(tree.root, governance, value)
    require_mutation_policy(protected, value)
    effective = validate_operating(effective, governance)
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
    recorded = _snapshot(tree, governance, working_only=True)
    return replace(effective, last_change_id=recorded.last_change_id,
                   _provenance_json=canonical({**recorded.provenance, 'protected_settings': protected}))


def plan_initialize_operating(project_root, governance, *, change_id=None, created_at=None, initial_operating=None):
    """Validate operating semantics and return the exact bootstrap writes without mutating the tree."""
    with Tree(project_root) as tree:
        if _read(tree, JOURNAL) is not None:
            fail("$", "Pending operating transaction: inspect and recover before upgrade")
        before = _read(tree, CONFIG)
        events = _events(tree)
        if before is None and events:
            fail("$", "Operating configuration is missing with retained audit history; restore its audited bytes before upgrade")
        if before is None:
            value = (deepcopy(initial_operating) if initial_operating is not None
                     else _upgrade_safe_operating(governance))
        else:
            snapshot = validate_operating(load_yaml(before), governance)
            if events and not audit_matches(snapshot.config, events[-1]):
                fail("$", "Operating file differs from the last audited change; reconcile the external edit before upgrade")
            if events:
                files = {} if tree.inspect(LOCK) is not None else {LOCK: b"0" if os.name == "nt" else b""}
                return {"files": files,
                        "actions": [{"path": path, "action": "create"} for path in sorted(files)], "inspection": {
                    "status": "ACCEPTED", "hash": snapshot.operating_hash,
                    "source": snapshot.source, "streams": snapshot.count,
                    "last_change_id": events[-1]["id"], "refusals": [],
                    **operating_ceiling(governance)}}
            value = snapshot.config
        after = validate_operating(value, governance)
        raw = before if before is not None and canonical(load_yaml(before)) == canonical(value) else _json(value)
        identifier = change_id or ("C-" + uuid.uuid4().hex)
        event = {"id": identifier, "created_at": created_at or now_text(), "instruction": None,
                 "before_hash": fingerprint("operating", load_yaml(before)) if before is not None else None,
                 "after_hash": after.operating_hash, "changes": [], "source": value["source"],
                 "applied_from": "bootstrap", "sequence": 1, "previous_change_id": None,
                 "governance_hash": after.governance_hash,
                 "unprotected_before_hash": unprotected_hash(load_yaml(before)) if before is not None else None,
                 "unprotected_after_hash": unprotected_hash(value)}
        errors = _schema("operating-change", event)
        if errors:
            raise OperatingError(errors)
        event_path = STATE + "/changes/" + identifier + ".json"
        if tree.inspect(event_path) is not None:
            fail("$", "Planned operating audit identity already exists")
        files = {event_path: _json(event)}
        if before is None:
            files[CONFIG] = raw
        if tree.inspect(LOCK) is None:
            files[LOCK] = b"0" if os.name == "nt" else b""
        actions = [{"path": path, "action": "create"} for path in sorted(files)]
        return {"files": files, "actions": actions, "inspection": {
            "status": "ACCEPTED", "hash": after.operating_hash, "source": after.source,
            "streams": after.count, "last_change_id": identifier, "refusals": [],
            **operating_ceiling(governance)}}


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
            value = _upgrade_safe_operating(governance)
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
        # Recovery of local transactions cannot publish protected changes.
        from .protected_settings import PROTECTED_DOMAINS
        prior_value = load_yaml(before) if before is not None else {}
        after_value = load_yaml(after)
        if any(prior_value.get(name, defaults()) != after_value.get(name, defaults())
               for name, defaults, _normalize in PROTECTED_DOMAINS):
            fail('$', 'Protected settings require a PR; local recovery cannot change them')
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
                events and events[-1]["after_hash"] != event["before_hash"] and not (events[-1].get("unprotected_after_hash") is not None and events[-1]["unprotected_after_hash"] == event.get("unprotected_before_hash"))):
            fail("$", "Recovery journal does not extend the current audit")
        if current != after:
            tree.write(CONFIG, after)
        if saved is None:
            _new(tree, path, _json(event))
        tree.unlink(JOURNAL)
        return _snapshot(tree, governance)


def _path(path, epic_id=None):
    if path.startswith('operating_calendar.') and path[19:] in SETTING_PATHS:
        if epic_id is not None:
            fail('$.operating_calendar', 'Calendar settings are project-wide')
        return path
    from .budgeting import SETTINGS_KEYS
    if path.startswith('budgeting.') and path[10:] in SETTINGS_KEYS:
        if epic_id is not None:
            fail('$.budgeting', 'Budget settings are project-wide')
        return path
    qualified = re.fullmatch(r"epic_overrides\.([^.]+)\.(.+)", path)
    if qualified:
        if epic_id is not None:
            fail("$.epic_overrides", "not allowed: Use either a fully qualified Epic path or --epic, never both")
        return _path(qualified[2], qualified[1])
    if re.fullmatch(r"[A-Z]+\.(worker|reviewer)(\.pinned)?", path):
        path = "streams." + path
    if path not in {"streams.count", "controller", "critic", "specialist", "simple_worker", "simple_worker.enabled",
                    "controller.pinned", "critic.pinned", "specialist.pinned", "simple_worker.pinned"} and not re.fullmatch(r"streams\.[A-Z]+\.(worker|reviewer)(\.pinned)?", path):
        fail("$", "unknown key: Unsupported operating path; use --epic with a stream/role route for an explicit Epic scope")
    label = re.match(r"streams\.([A-Z]+)\.", path)
    if label and label[1] not in STREAM_LABELS:
        fail('$.streams', 'unknown key: unsupported stream field')
    if epic_id is not None:
        if not isinstance(epic_id, str) or len(epic_id) > 80 or not re.fullmatch(EPIC_ID, epic_id):
            fail("$.epic", "Supply the exact observed Epic ID (for example PROJ-123); scope is never inferred")
        if path == "streams.count" or path.startswith("simple_worker"):
            fail("$." + path, "Epic scope supports worker/reviewer stream routes and controller/critic/specialist routes")
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
    if path.startswith('operating_calendar.'):
        if path.endswith('.enabled'):
            if text not in {'true', 'false'}:
                fail('$.' + path, 'Expected true or false')
            return text == 'true'
        if path.endswith('.weekdays'):
            try:
                return loads(text)
            except ValueError:
                fail('$.' + path, 'Expected a JSON list of ISO weekdays')
        return text
    if path.startswith('budgeting.'):
        if path.endswith('_enabled'):
            if text not in {'true', 'false'}:
                fail('$.' + path, 'Expected true or false')
            return text == 'true'
        if not re.fullmatch(r'[0-9]+', text):
            fail('$.' + path, 'Budget limit must be a positive integer')
        return int(text)
    if path == "streams.count":
        if not re.fullmatch(r"[0-9]+", text):
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


def _prune_unrepresentable_routes(value, structural_ceiling):
    """Remove only labels made structurally invalid by a lowered max_lanes.

    The caller records every returned path in the normal append-only operating
    change event. Labels that remain representable are deliberately retained so
    ordinary count reductions keep their established draining-route behavior.
    """
    allowed = set(stream_labels(structural_ceiling))
    removed = []
    streams = value["streams"]
    for label in sorted(set(streams) - {"count"} - allowed):
        if re.fullmatch(r"[A-Z]+", label):
            del streams[label]
            removed.append("streams." + label)
    overrides = value.get("epic_overrides", {})
    if isinstance(overrides, dict):
        for epic_id in sorted(list(overrides)):
            scoped = overrides[epic_id]
            scoped_streams = scoped.get("streams") if isinstance(scoped, dict) else None
            if not isinstance(scoped_streams, dict):
                continue
            for label in sorted(set(scoped_streams) - allowed):
                if re.fullmatch(r"[A-Z]+", label):
                    del scoped_streams[label]
                    removed.append(f"epic_overrides.{epic_id}.streams.{label}")
            if not scoped_streams:
                del scoped["streams"]
            if not scoped:
                del overrides[epic_id]
    return removed


def _editable_snapshot(tree, governance, initialize, *, working_only=False):
    if _read(tree, JOURNAL) is not None:
        fail("$", "Pending operating transaction requires pinned recovery before another change")
    raw = _read(tree, CONFIG)
    if raw is None:
        if not initialize:
            fail("$", "Operating file is absent; explicitly use --initialize with reviewed --set choices")
        if _events(tree):
            fail("$", "Missing audited operating bytes require recovery, not reinitialization")
        value = _upgrade_safe_operating(governance)
        return OperatingSnapshot(canonical(value), fingerprint("operating", value), fingerprint("operating-governance", governance)), None
    if initialize:
        fail("$", "--initialize cannot overwrite an existing operating file")
    try:
        return _snapshot(tree, governance, working_only=working_only), raw
    except OperatingError:
        # A new protected policy can invalidate previously audited choices. Only
        # an exact existing audit tip permits repair; dispatch still rejects it.
        value, events = load_yaml(raw), _events(tree)
        if not events or events[-1]["after_hash"] != fingerprint("operating", value):
            fail("$", "Invalid or externally edited operating file lacks its exact audited tip; preserve and reconcile it")
        if not isinstance(value, dict) or not isinstance(value.get("streams"), dict):
            fail("$", "Malformed operating structure requires explicit reviewed recovery")
        return OperatingSnapshot(canonical(value), fingerprint("operating", value), fingerprint("operating-governance", governance), events[-1]["id"]), raw


def prepare_budgeting_adoption(project_root, governance, instruction):
    """Prepare only dormant defaults in a PR worktree; never publish policy."""
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction.encode('utf-8')) > 65536:
        fail('$.instruction', 'Record the nonempty bounded user instruction verbatim')
    from .protected_settings import _git
    from .budgeting import fresh_settings
    import yaml
    with Tree(project_root) as tree:
        _bound_governance(tree, governance)
        working = _snapshot(tree, governance, working_only=True)
        effective = _snapshot(tree, governance)
        report = effective.provenance['protected_settings']
        ref = report.get('ref')
        branch = _git(tree.root, 'symbolic-ref', '-q', 'HEAD', optional=True)
        try:
            branch = branch.decode('utf-8').rstrip('\n') if branch else None
        except UnicodeDecodeError:
            fail('$.budgeting', 'Adoption branch must be a valid UTF-8 Git reference')
        if branch:
            _git(tree.root, 'check-ref-format', branch)
        if (not ref or not report.get('commit') or not branch or
                branch == ref.replace('refs/remotes/origin/', 'refs/heads/', 1)):
            fail('$.budgeting', 'Prepare adoption on an upgrade/settings PR branch from the fetched default branch')
        if 'budgeting' in working.config or 'budgeting' in effective.config:
            fail('$.budgeting', 'Budgeting already exists; request settings changes through a separate PR')
        before = tree.read(CONFIG, maximum=MAX_BYTES)
        text = before.decode('utf-8')
        node = yaml.compose(text)
        defaults = fresh_settings()
        newline = '\r\n' if '\r\n' in text else '\n'
        if node.flow_style:
            position = node.end_mark.index - 1
            addition = ',' + newline + '  "budgeting": ' + json.dumps(defaults) + newline
        else:
            position = node.end_mark.index
            addition = ('' if text[:position].endswith(('\n', '\r')) else newline)
            addition += 'budgeting:' + newline
            addition += ''.join('  ' + key + ': ' + json.dumps(value) + newline for key, value in defaults.items())
        raw = (text[:position] + addition + text[position:]).encode('utf-8')
        if len(raw) > MAX_BYTES:
            fail('$.budgeting', 'Prepared operating file exceeds its 1 MiB bound')
        expected = {**working.config, 'budgeting': defaults}
        if load_yaml(raw) != expected:
            fail('$.budgeting', 'Cannot preserve this operating text safely; prepare the dormant block in the PR manually')
        validate_operating(expected, governance)
        # The protected change has no local audit transaction: Git/PR history is
        # its audit trail. Retain all existing events and installer receipts.
        with _lock(tree):
            if tree.read(CONFIG, maximum=MAX_BYTES) != before:
                fail('$.budgeting', 'Operating bytes changed during PR preparation; retry from the current branch')
            tree.write(CONFIG, raw)
            return _snapshot(tree, governance)


def set_operating(project_root, governance, instruction, assignments=(), *, recommendation_id=None, accept=None, initialize=False, epic_id=None, adopt_budgeting=False):
    if type(adopt_budgeting) is not bool:
        fail("$.budgeting", "Budget adoption must be explicit")
    if adopt_budgeting:
        if assignments or recommendation_id or accept or initialize or epic_id is not None:
            fail("$.budgeting", "Adoption prepares OFF defaults only; request other settings in a separate PR")
        return prepare_budgeting_adoption(project_root, governance, instruction)
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction.encode("utf-8")) > 65536:
        fail("$.instruction", "Record the nonempty bounded user instruction verbatim")
    if bool(assignments) == bool(recommendation_id) or bool(recommendation_id) != bool(accept):
        fail("$", "Choose --set pairs OR --recommendation with --accept")
    if initialize and recommendation_id:
        fail("$", "--initialize requires explicit custom --set choices, not a prior recommendation")
    if epic_id is not None and recommendation_id:
        fail("$.epic", "A project-wide recommendation cannot be reinterpreted as an Epic-scoped change")
    with Tree(project_root) as tree:
        _bound_governance(tree, governance)
        if recommendation_id:
            snapshot, before = _snapshot(tree, governance, working_only=True), tree.read(CONFIG, maximum=MAX_BYTES)
        else:
            snapshot, before = _editable_snapshot(tree, governance, initialize, working_only=True)
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
        budget_changes = {p[10:]: v for p, v in selected.items() if p.startswith('budgeting.')}
        if budget_changes:
            if before is None:
                fail('$.budgeting', 'Initialize defaults first, then request protected changes with operating set')
            from .budgeting import transition_settings
            current_budget = snapshot.config.get('budgeting')
            if current_budget is None:
                fail('$.budgeting', 'Legacy budgeting requires versioned adoption with an explicit budget disposition')
            proposed = transition_settings(current_budget, budget_changes)
            selected.update({'budgeting.' + k: v for k, v in proposed.items() if v != current_budget[k]})
        count = selected.get("streams.count", value["streams"]["count"])
        structural_ceiling = configured_lane_ceiling(governance)
        ceiling = operating_ceiling(governance)["effective_ceiling"]
        if type(count) is int and 1 <= count <= ceiling:
            for label in stream_labels(count):
                value["streams"].setdefault(label, _stream(_routing_policy(governance)))
        migration_paths = _prune_unrepresentable_routes(value, structural_ceiling)
        for path, choice in selected.items():
            if path.startswith("epic_overrides.") and path.endswith(".pinned") and _get(value, path.rsplit(".", 1)[0]) is None:
                fail("$." + path, "Choose a complete scoped route before changing its pin")
            if path == "simple_worker" and isinstance(choice, dict):
                choice.setdefault("enabled", value["simple_worker"]["enabled"])
            _put(value, path, choice)
        value["source"] = source
        validate_operating(value, governance)
        paths = sorted(set(selected) | set(migration_paths) | {"source"} | {"streams." + s for s in stream_labels(ceiling) if s in value["streams"] and s not in snapshot.config["streams"]})
        changes = [{"path": p, "before": _get(snapshot.config, p), "after": _get(value, p)} for p in paths
                   if _get(snapshot.config, p) != _get(value, p)]
        if any(p.startswith(('budgeting.', 'operating_calendar.')) for p in selected):
            fail('$', 'Protected settings require a PR: open a PR changing only the requested OPERATING_CONFIG.yaml values; the owner merges, then fetch the default branch. The live file was not changed')
        # Complete all refusal checks before creating the lock/state directory.
        from .protected_settings import resolve_protected, require_mutation_policy
        effective, _report = resolve_protected(tree.root, governance, value)
        require_mutation_policy(_report, value)
        validate_operating(effective, governance)
        _prepare_commit(tree, governance, before, value, instruction, changes, "chat")
        with _lock(tree):
            return _commit(tree, governance, before, value, instruction, changes, "chat")


def _scope(paths, epic_id):
    if not paths:
        return None
    result = []
    unknown = False
    for path in paths:
        cleaned = path[:-3] if path.endswith("/**") else path[:-2] if path.endswith("/*") else path.removesuffix("/")
        if any(mark in cleaned for mark in "*?[]{}"):
            fail("$.epics.<epic>.scope", "Epic scope has unsupported glob syntax; use a bounded path prefix or a trailing /* or /**")
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
    count = min(len(epics), ceiling)
    labels = stream_labels(count)
    groups = [{"label": labels[i], "epic_ids": [], "ticket_ids": [], "write_paths": []} for i in range(count)]
    for i, epic in enumerate(epics):
        group = groups[i % count]
        group["epic_ids"].append(epic["id"])
        group["write_paths"].extend(scopes[epic["id"]])
    return groups, "Disjoint Epic path scopes: " + ", ".join(epic["id"] for epic in epics)


def _risk(epics, paths, governance):
    policy = _routing_policy(governance)
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
    current, policy = snapshot.config, _routing_policy(governance)
    if epics:
        ceiling = operating_ceiling(governance)["effective_ceiling"]
        ticket_metadata = {}
        inventory_epics = {}
        if inventory_raw is not None:
            from .streams import recommendation_groups
            try:
                planned = recommendation_groups(inventory_raw, sha256(inventory_raw), created,
                                                execution=governance["execution"], allow_synthetic=True)
            except ValidationError:
                fail('$.inventory', 'not allowed: Invalid recommendation inventory <key>')
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
            labels = stream_labels(group_count)
            groups = [{"label": labels[i], "ticket_ids": [], "epic_ids": [], "write_paths": []} for i in range(group_count)]
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
                proposed["streams"].setdefault(label, _stream(policy))
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
                    reviewer["reasoning_effort"] = max((reviewer["reasoning_effort"], policy["risk_route"]["reasoning_effort"]), key=EFFORTS.index)
                why = ("High-risk flags " + ", ".join(high) if high else "Specialist triggers " + ", ".join(specialists)
                       if specialists else "Entire scope is simple with strong verification; retain the default worker ceiling"
                       if simple else "Default route for the declared bounded scope")
                reviewer_path = "critic" if "critic" in current else "streams." + label + ".reviewer"
                choices.extend((("streams." + label + ".worker", worker, why),
                                (reviewer_path, reviewer, "High-risk review" if high else "Governing review floor")))
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
        snapshot = _snapshot(tree, governance, working_only=True)
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
    for key in ("epics", "inventory"):
        path = record["input_files"][key]
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
    value, policy = snapshot.config, _routing_policy(governance)
    capacity = operating_ceiling(governance)
    lines = [f"AWF {version}: {state} — operating configuration (source: {snapshot.source})",
             f"Streams: {snapshot.count} of ceiling {capacity['effective_ceiling']}; one shared independent critic/adversarial handler by default; binding limits: "
             + ", ".join(capacity["effective_ceiling_sources"]) + ". Observed host slots remain separate.",
             "| Stream | Worker | Legacy per-stream reviewer |", "|---|---|---|"]
    for label in stream_labels(snapshot.count):
        item = value["streams"][label]
        lines.append(f"| {label} | {_display(item['worker'])} | {_display(item['reviewer']) if item.get('reviewer') else '—'} |")
    if value.get("critic"):
        lines.extend(["", "Shared critic/adversarial handler: " + _display(value["critic"])])
    for epic_id, scoped in sorted(value.get("epic_overrides", {}).items()):
        lines.extend(["", "Epic " + epic_id + " overrides (matching observed Epic only):",
                      "| Setting | Route |", "|---|---|"])
        for label, entry in sorted(scoped.get("streams", {}).items()):
            for role in ("worker", "reviewer"):
                if role in entry:
                    lines.append("| streams." + label + "." + role + " | " + _display(entry[role]) + " |")
        for role in ("controller", "critic", "specialist"):
            if role in scoped:
                lines.append("| " + role + " | " + _display(scoped[role]) + " |")
    lines.extend(["Controller " + _display(value["controller"]) + "; Specialist " + _display(value["specialist"]),
                  "Simple work (low risk, strong verification): " + (_display(value["simple_worker"]) if value["simple_worker"]["enabled"] else "disabled"),
                  f"Escalation: ≤{policy['escalation']['max_escalations_per_ticket']} per ticket, effort ceiling {policy['escalation']['effort_ceiling']}; "
                  + "review floor " + _display(policy["review_floor"]) + "; risk route " + _display(policy["risk_route"]),
                  "Options: keep | review Epics and recommend | custom (say what you want)"])
    from .budgeting import resolve_settings
    if 'budgeting' not in value:
        lines.extend(['Budgeting: LEGACY_RETAINED; existing engine and reviewed limits remain.',
                      'Switches: non-persisted diagnostic fallback only; legacy enforcement UNOBSERVED.',
                      'EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED before enabling; owner adoption through a PR adds dormant OFF controls.'])
    else:
        budget = resolve_settings(value)
        lines.extend(['Budgeting (effective):',
                      'Accounting: ' + ('ON' if budget['accounting_enabled'] else 'OFF'),
                      'Enforcement: ' + ('ON' if budget['enforcement_enabled'] else 'OFF'),
                      f"Token limit per ticket: {format(budget['max_tokens_per_ticket'], ',') if budget['max_tokens_per_ticket'] is not None else 'legacy reviewed limit'}",
                      f"Token limit per project lifetime: {format(budget['max_tokens_per_project'], ',') if budget['max_tokens_per_project'] is not None else 'legacy reviewed limit'}"])
    lines.append("Operating calendar (effective settings): " + json.dumps(effective_calendar(value), sort_keys=True))
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
    from .contracts import Contracts
    from .output import configure_streams, json_text
    configure_streams()
    parser = argparse.ArgumentParser(description="Show, recommend or explicitly change project-owned operating choices; never change governance or launch models")
    parser.add_argument("--root", type=Path, default=default_root or Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("--json", action="store_true")
    validator = sub.add_parser('validate', help='Validate proposed operating bytes without applying them')
    validator.add_argument('--file', required=True, type=Path)
    validator.add_argument('--json', action='store_true')
    setter = sub.add_parser("set")
    setter.add_argument("--instruction", required=True, help="Exact user words; local recording does not authenticate their source")
    setter.add_argument("--set", action="append", default=[], dest="assignments")
    setter.add_argument("--recommendation")
    setter.add_argument("--accept")
    setter.add_argument("--epic", help="Exact observed Epic ID; scope only the selected stream/role routes")
    setter.add_argument("--initialize", action="store_true", help="Explicitly start from Balanced defaults only when the root file is absent; validate all selected changes first")
    setter.add_argument("--adopt-budgeting", action="store_true", help="Prepare dormant OFF defaults on the upgrade/settings PR branch; takes effect after owner merge and fetch")
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
            governance = load_governance_yaml(tree.read(GOVERNANCE, maximum=MAX_BYTES))
        Contracts(SCHEMAS).validate("project-config", governance)
        if args.command == 'validate':
            if args.file.stat().st_size > MAX_BYTES:
                fail('$', 'Proposed operating file exceeds its 1 MiB bound')
            proposed = validate_operating(load_yaml(args.file.read_bytes()), governance)
            print(json_text(dict(status='ACCEPTED', proposed_configuration=proposed.config,
                                 execution_authority=False, next_action='Open a PR; the owner reviews and merges')))
            return 0
        if args.command == "recommend":
            record = save_recommendation(args.root, governance, args.epics, args.inventory)
            print(json_text(record) if args.json else render_recommendation(record))
            return 0
        if args.command == "set":
            snapshot = set_operating(args.root, governance, args.instruction, args.assignments,
                                     recommendation_id=args.recommendation, accept=args.accept, initialize=args.initialize, epic_id=args.epic, adopt_budgeting=args.adopt_budgeting)
        elif args.command == "recover":
            snapshot = recover_operating(args.root, governance, args.expected_journal_sha256)
        else:
            snapshot = read_operating(args.root, governance)
        state = _local_state(args.root, governance)
        result = {"status": "ACCEPTED", "project_state": state, "operating_hash": snapshot.operating_hash,
                  "governance_hash": snapshot.governance_hash, "source": snapshot.source, "streams": snapshot.count,
                  **operating_ceiling(governance),
                  "last_change_id": snapshot.last_change_id, "configuration": snapshot.config,
                  "effective_operating_calendar": effective_calendar(snapshot.config),
                  "execution_authority": False, "table": render_operating(snapshot, governance, state=state)}
        from .protected_settings import repository_rules_warning
        protected = snapshot.provenance.get('protected_settings', {})
        result.update(protected_settings=protected, budget_admission=_budget_admission_projection(snapshot.config, protected),
                      budgeting=_budgeting_projection(snapshot.config),
                      repository_rules=repository_rules_warning())
        result['last_settings_pr'] = dict(status='UNOBSERVED', reason='Last settings PR and merger not observed; optional information only')
        result['table'] += '\nProtected settings: ' + str(protected.get('source', 'unobserved'))
        if protected.get('commit'):
            result['table'] += ' at ' + protected['commit']
        for domain, source in protected.get('sources', {}).items():
            result['table'] += '\n' + domain + ': ' + source
        for field in ('policy_observation', 'branch_observation', 'visibility'):
            if protected.get(field):
                result['table'] += '\n' + protected[field]
        result['table'] += '\nLast fetch attempt of any ref (FETCH_HEAD): ' + str(protected.get('fetch_attempt_at') or 'UNOBSERVED')
        if protected.get('unverified'):
            result['table'] += '\n' + protected['next_action']
        if protected.get('budget_refusal'):
            result['table'] += '\n' + protected['budget_refusal']
        if protected.get('differences'):
            result['table'] += '\n' + protected['next_action']
            for difference in protected['differences']:
                result['table'] += ('\n' + difference['path'] + ': working ' + json.dumps(difference['working'], sort_keys=True)
                                    + '; effective ' + json.dumps(difference['effective'], sort_keys=True))
        result['table'] += '\n' + result['repository_rules']['warning']
        result['table'] += '\n' + result['last_settings_pr']['reason']
        print(json_text(result) if args.json else result["table"])
        return 0
    except (ValidationError, OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, OSError):
            from .activation import access_diagnostic
            output = {"status": "ACCESS_UNAVAILABLE", "refusals": [],
                      "diagnostic": access_diagnostic(exc, "OPERATING_CONFIG.yaml or .agentic-state/operating"),
                      "execution_authority": False,
                      "next_action": "Restore read access to the named operating paths and run operating show again"}
        else:
            output = {"status": "REJECTED", "refusals": getattr(exc, "refusals", [refusal("$", str(exc))]),
                      "execution_authority": False,
                      "next_action": "Correct the named input or reconcile the pinned operating transaction; governance changes require a reviewed PR"}
        output['file'] = args.file.name if args.command == 'validate' else CONFIG
        for item in output.get('refusals', []):
            item['file'] = output['file']
        print(json_text(output))
        return 2
