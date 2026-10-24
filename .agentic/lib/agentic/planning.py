"""Deterministic project planning and governed lane sizing.

The planner is deliberately side-effect free except for caller-supplied AWF
state dictionaries.  It does not fetch Jira, inspect Git, launch roles, or
assign in-flight work.  Adapters provide observed snapshots; the controller
stores the sealed records returned here.
"""
from __future__ import annotations

import copy
import re
import uuid

from . import ValidationError
from .canonical import fingerprint, sha256, timestamp
from .intake_assignment import classify_ticket, _canonical_repo_path
from .operating import configured_lane_ceiling, operating_ceiling, stream_labels, validate_snapshot


MANIFEST_OUTPUTS = {"MANIFEST.json", "MANIFEST.md"}
MANIFEST_OUTPUT_IDENTITIES = {path.casefold() for path in MANIFEST_OUTPUTS}
ACTIVE_STATUSES = {"in_flight", "working", "dispatched", "in_progress"}
READY_STATUSES = {"open", "ready", "backlog"}
CLOSED_STATUSES = {"closed", "done", "merged", "cancelled", "canceled"}
RECOMPUTE_TRIGGERS = {
    "project_start", "intake", "pr_merged", "files_changed",
    "ticket_added", "ticket_closed", "override_changed", "round_ended",
}


def _path(value):
    path = _canonical_repo_path(value)
    if any(mark in path for mark in "*?[]{}") or path.endswith("/"):
        raise ValidationError(
            "Planning file scopes must be exact repository file paths; globs and directories are not lane evidence")
    return path


def _path_identity(path):
    """Return the portable identity used to reject Windows case aliases."""
    return path.casefold()


def _string_list(value, field, *, allow_empty=True):
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValidationError(f"{field} must be a list of non-empty strings")
    return value


def _ticket_paths(ticket):
    """Return exact files and every observed source for each file."""
    observations = []
    brief = ticket.get("brief")
    if brief is not None:
        if not isinstance(brief, dict):
            raise ValidationError("Ticket brief must be an object")
        observations.append(("brief", brief.get("write_paths", [])))
    observations.append(("brief", ticket.get("write_paths", [])))
    observations.append(("open_pr", ticket.get("open_pr_files", [])))
    open_pr = ticket.get("open_pr")
    if open_pr is not None:
        if not isinstance(open_pr, dict):
            raise ValidationError("Ticket open_pr must be an object")
        observations.append(("open_pr", open_pr.get("files", [])))
    observations.append(("worktree_diff", ticket.get("worktree_diff", [])))
    result = {}
    for source, values in observations:
        for raw in _string_list(values, source + " file list"):
            path = _path(raw)
            if _path_identity(path) in MANIFEST_OUTPUT_IDENTITIES:
                continue
            result.setdefault(path, set()).add(source)
    return result


def _generator_outputs(ticket):
    values = ticket.get("generated_outputs", ticket.get("generates", []))
    result = {_path(item) for item in _string_list(values, "generated_outputs")}
    return sorted(path for path in result if _path_identity(path) not in MANIFEST_OUTPUT_IDENTITIES)


def _dependency_edges(ticket):
    key = ticket["key"]
    edges = []
    for field in ("dependencies", "declared_dependencies", "depends_on"):
        for blocker in _string_list(ticket.get(field, []), field):
            edges.append((blocker, key, "declared:" + field))
    for link in ticket.get("jira_links", []):
        if not isinstance(link, dict):
            raise ValidationError("jira_links entries must be objects")
        link_type = str(link.get("type", link.get("name", ""))).casefold()
        if "block" not in link_type:
            continue
        direction = str(link.get("direction", link.get("role", ""))).casefold()
        other = link.get("key")
        if not isinstance(other, str) or not other:
            issue = link.get("outwardIssue") or link.get("inwardIssue")
            other = issue.get("key") if isinstance(issue, dict) else None
        if not isinstance(other, str) or not other:
            raise ValidationError("Jira Blocks links need the other ticket key")
        if direction in {"outward", "blocks"} or "outwardIssue" in link:
            edges.append((key, other, "jira:Blocks"))
        elif direction in {"inward", "blocked_by", "is blocked by"} or "inwardIssue" in link:
            edges.append((other, key, "jira:Blocks"))
        else:
            raise ValidationError("Jira Blocks links need an inward/outward direction")
    for dependent in _string_list(ticket.get("blocks", []), "blocks"):
        edges.append((key, dependent, "jira:Blocks"))
    for blocker in _string_list(ticket.get("blocked_by", []), "blocked_by"):
        edges.append((blocker, key, "jira:Blocks"))
    return edges


BACKLOG_BINDING_KEYS = {
    "project_id", "repository_id", "scope_sha256", "repository_head_sha",
    "project_config_sha256", "cycle_id",
}
BACKLOG_PROVIDER_KEYS = {
    "provider_id", "implementation_sha256", "verifier_implementation_sha256",
}
BACKLOG_TICKET_KEYS = {
    "key", "status", "dependencies", "declared_dependencies", "depends_on",
    "jira_links", "blocks", "blocked_by", "generated_outputs", "generates",
    "expected_rounds", "round_time_minutes", "risk_tier", "risk_flags",
    "summary", "description", "change_kind",
}


def tickets_from_provider_snapshot(
        snapshot, config, *, expected_binding, expected_provider, now,
        verify_provider_snapshot=None, rejected=None, observed_pr_paths=None):
    """Return attributed tickets only after exact-cycle authentication.

    The trusted host supplies reviewed provider/verifier pins, controller-known
    bindings, current cycle time, and the verifier callable loaded from that
    pinned implementation. None may come from ticket or snapshot content.
    The verifier authenticates an immutable provider observation (including
    PR/diff-to-ticket attribution) and returns its complete independent body,
    not an echoed request, boolean or caller-computed digest. Empty components
    explicitly attest no files; omitted components are incomplete evidence.
    Call build_backlog_graph only after this reader succeeds. No live I/O or
    adapter registration occurs here.

    Optional dictionary collectors are updated only after all global checks
    pass. With rejected supplied, authenticated ticket-content errors omit that
    ticket and map its key to the validation reason; the default still raises.
    observed_pr_paths maps each authenticated key to {"paths": [...],
    "complete": bool}, retaining exact PR paths (including manifests) even for
    rejected tickets. Unsafe PR entries make coverage incomplete; known paths
    alone cannot establish a complete safety hold. Missing evidence is never
    an assertion of an empty PR, and this evidence grants no admission authority.
    """
    for name, collector in (("rejected", rejected), ("observed_pr_paths", observed_pr_paths)):
        if collector is not None and not isinstance(collector, dict):
            raise ValidationError(f"Backlog {name} collector must be a dictionary")
    if rejected is not None and observed_pr_paths is rejected:
        raise ValidationError("Backlog rejection and PR evidence collectors must be distinct")
    staged_rejections, staged_pr_paths = {}, {}
    snapshot = copy.deepcopy(snapshot)
    expected_binding = copy.deepcopy(expected_binding)
    expected_provider = copy.deepcopy(expected_provider)
    _closed(snapshot, {"format", "complete", "provider", "binding", "observed_at",
                       "tickets", "authentication"}, "backlog snapshot")
    _closed(expected_binding, BACKLOG_BINDING_KEYS, "expected backlog binding")
    _closed(expected_provider, BACKLOG_PROVIDER_KEYS, "expected backlog provider")
    repository_id = config.get("github", {}).get("repository_id")
    valid_repository_id = ((isinstance(repository_id, str) and bool(repository_id))
                           or (type(repository_id) is int and repository_id > 0))
    if (not valid_repository_id
            or not all(isinstance(value, str) and value for value in expected_binding.values())
            or not LOWER_SHA256.fullmatch(expected_binding["scope_sha256"])
            or not GIT_SHA.fullmatch(expected_binding["repository_head_sha"])
            or expected_binding["project_config_sha256"]
            != fingerprint("accepted-project-config", config)
            or expected_binding["project_id"] != config.get("project", {}).get("id")
            or expected_binding["repository_id"]
            != str(repository_id)):
        raise ValidationError("Backlog binding differs from accepted project/repository/configuration")
    if (not isinstance(expected_provider["provider_id"], str)
            or not RESOURCE_NAME.fullmatch(expected_provider["provider_id"])
            or not all(isinstance(expected_provider[key], str)
                       and LOWER_SHA256.fullmatch(expected_provider[key])
                       for key in ("implementation_sha256", "verifier_implementation_sha256"))):
        raise ValidationError("Backlog provider/verifier must have reviewed implementation pins")
    timestamp(now)
    if (snapshot["format"] != "awf-backlog-provider-snapshot-1"
            or snapshot["complete"] is not True
            or snapshot["provider"] != expected_provider
            or snapshot["binding"] != expected_binding
            or snapshot["observed_at"] != now):
        raise ValidationError("Backlog snapshot is incomplete, unpinned, foreign or stale")
    authentication = snapshot["authentication"]
    _closed(authentication, {"evidence_id"}, "backlog authentication")
    if (not isinstance(authentication["evidence_id"], str)
            or not 1 <= len(authentication["evidence_id"]) <= 128
            or not callable(verify_provider_snapshot)):
        raise ValidationError("Backlog snapshot requires independent provider authentication")
    request = {"provider": expected_provider, "binding": expected_binding,
               "observed_at": now}
    try:
        verified = verify_provider_snapshot(copy.deepcopy(authentication), copy.deepcopy(request))
    except Exception as exc:
        raise ValidationError("Backlog provider authentication failed") from exc
    _closed(verified, {"status", "evidence_id", "snapshot"}, "authenticated backlog evidence")
    body = {key: value for key, value in snapshot.items() if key != "authentication"}
    if (verified["status"] != "VERIFIED"
            or verified["evidence_id"] != authentication["evidence_id"]
            or verified["snapshot"] != body):
        raise ValidationError("Backlog snapshot differs from independent authenticated provider evidence")

    if not isinstance(snapshot["tickets"], list):
        raise ValidationError("Backlog snapshot tickets must be a complete list")
    tickets, seen = [], set()
    for item in snapshot["tickets"]:
        _closed(item, {"ticket", "components"}, "backlog ticket envelope")
        ticket = item["ticket"]
        if not isinstance(ticket, dict) or set(ticket) - BACKLOG_TICKET_KEYS:
            raise ValidationError("Backlog ticket metadata cannot supply PR/diff file attribution")
        key = ticket.get("key")
        if not isinstance(key, str) or not key or key in seen:
            raise ValidationError("Backlog ticket keys must be non-empty and unique")
        seen.add(key)
        components = item["components"]
        if not isinstance(components, list) or len(components) != 3:
            raise ValidationError("Backlog ticket needs complete brief, PR and diff observations")
        collected = copy.deepcopy(ticket)
        kinds = set()
        for component in components:
            _closed(component, {"kind", "ticket_key", "binding", "observed_at", "files"},
                    "backlog path component")
            kind = component["kind"]
            if (not isinstance(kind, str) or kind not in {"brief", "open_pr", "worktree_diff"}
                    or kind in kinds or component["ticket_key"] != key
                    or component["binding"] != expected_binding
                    or component["observed_at"] != now
                    or not isinstance(component["files"], list)):
                raise ValidationError("Backlog component is partial, foreign, stale or wrongly attributed")
            kinds.add(kind)
            field = {"brief": "write_paths", "open_pr": "open_pr_files",
                     "worktree_diff": "worktree_diff"}[kind]
            collected[field] = copy.deepcopy(component["files"])
        # Capture authenticated PR coverage before ticket or manifest filtering.
        if observed_pr_paths is not None:
            pr_paths, complete = set(), True
            for raw in collected["open_pr_files"]:
                try:
                    pr_paths.add(_path(raw))
                except ValidationError:
                    complete = False
            staged_pr_paths[key] = {"paths": sorted(pr_paths), "complete": complete}
        # Only authenticated, attributed content validation is ticket-local.
        # Keep separate lists so the graph retains each observed source.
        try:
            paths = _ticket_paths(collected)
            if not paths:
                raise ValidationError(f"Backlog ticket {key} has no observed exact write files")
            for field in ("write_paths", "open_pr_files", "worktree_diff"):
                collected[field] = sorted({_path(raw) for raw in collected[field]
                                           if _path_identity(_path(raw)) not in MANIFEST_OUTPUT_IDENTITIES})
        except ValidationError as exc:
            if rejected is None:
                raise
            staged_rejections[key] = str(exc)
            continue
        tickets.append(collected)
    if rejected is not None:
        rejected.update(staged_rejections)
    if observed_pr_paths is not None:
        observed_pr_paths.update(staged_pr_paths)
    return tickets


def build_backlog_graph(tickets, config):
    """Build a source-attributed dependency and exact-file overlap graph."""
    if not isinstance(tickets, list):
        raise ValidationError("Planning requires a ticket snapshot list")
    keys = [item.get("key") for item in tickets if isinstance(item, dict)]
    if len(keys) != len(tickets) or any(not isinstance(key, str) or not key for key in keys):
        raise ValidationError("Every planning ticket needs a non-empty key")
    if len(keys) != len(set(keys)):
        raise ValidationError("Planning ticket keys must be unique")

    nodes = {}
    generator_owners = {}
    path_spellings = {}
    raw_dependencies = []
    for ticket in tickets:
        key = ticket["key"]
        paths = _ticket_paths(ticket)
        generated = _generator_outputs(ticket)
        for path in generated:
            owner = generator_owners.get(path)
            if owner is not None and owner != key:
                raise ValidationError(f"Generated output {path} has multiple generator owners: {owner}, {key}")
            generator_owners[path] = key
            paths.setdefault(path, set()).add("generator_output")
        for path in sorted(paths):
            identity = _path_identity(path)
            prior = path_spellings.setdefault(identity, path)
            if prior != path:
                raise ValidationError(
                    f"Planning file scopes contain case-aliased paths: {prior}, {path}")
        classification_ticket = copy.deepcopy(ticket)
        classification_ticket["write_paths"] = sorted(paths)
        if not classification_ticket["write_paths"]:
            raise ValidationError(f"Planning ticket {key} has no observed exact write files")
        classification = classify_ticket(config, classification_ticket)
        rounds = ticket.get("expected_rounds", 1)
        duration = ticket.get("round_time_minutes", 30)
        if type(rounds) is not int or rounds < 1 or type(duration) not in {int, float} or duration <= 0:
            raise ValidationError(f"Planning ticket {key} needs positive expected_rounds and round_time_minutes")
        status = ticket.get("status", "open")
        if not isinstance(status, str) or not status:
            raise ValidationError(f"Planning ticket {key} needs a status")
        nodes[key] = {
            "key": key,
            "status": status.casefold(),
            "write_paths": sorted(paths),
            "path_sources": {path: sorted(sources) for path, sources in sorted(paths.items())},
            "generator_outputs": generated,
            "risk_tier": classification["tier"],
            "protected_path_floor": classification["protected_path_floor"],
            "protected_paths": classification["protected_paths"],
            "expected_rounds": rounds,
            "round_time_minutes": duration,
        }
        raw_dependencies.extend(_dependency_edges(ticket))

    dependencies = {}
    for blocker, dependent, source in raw_dependencies:
        if blocker == dependent:
            raise ValidationError(f"Ticket {dependent} cannot depend on itself")
        dependencies.setdefault((blocker, dependent), set()).add(source)
    dependency_records = [
        {"blocker": blocker, "dependent": dependent, "sources": sorted(sources)}
        for (blocker, dependent), sources in sorted(dependencies.items())
    ]

    overlaps = []
    ordered = sorted(nodes)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            shared = sorted(set(nodes[left]["write_paths"]) & set(nodes[right]["write_paths"]))
            if shared:
                overlaps.append({"left": left, "right": right, "paths": shared,
                                 "sources": {path: {left: nodes[left]["path_sources"][path],
                                                     right: nodes[right]["path_sources"][path]}
                                             for path in shared}})
    return {
        "schema_version": 1,
        "tickets": nodes,
        "dependencies": dependency_records,
        "overlaps": overlaps,
        "generator_owners": dict(sorted(generator_owners.items())),
        "manifest_regeneration_ignored": sorted(MANIFEST_OUTPUTS),
    }


def _dependency_maps(graph):
    blockers = {key: set() for key in graph["tickets"]}
    dependents = {key: set() for key in graph["tickets"]}
    for edge in graph["dependencies"]:
        blocker, dependent = edge["blocker"], edge["dependent"]
        blockers.setdefault(dependent, set()).add(blocker)
        dependents.setdefault(blocker, set()).add(dependent)
    return blockers, dependents


def _critical_scores(graph, dependents):
    nodes = graph["tickets"]
    visiting = set()
    scores = {}

    def score(key):
        if key in scores:
            return scores[key]
        if key in visiting:
            raise ValidationError("Planning dependency graph contains a cycle")
        visiting.add(key)
        node = nodes.get(key)
        own = (node["expected_rounds"] * node["round_time_minutes"]) if node else 0
        downstream = max((score(item) for item in dependents.get(key, ()) if item in nodes), default=0)
        visiting.remove(key)
        scores[key] = own + downstream
        return scores[key]

    for key in nodes:
        score(key)
    return scores


def _ready_keys(graph, blockers):
    nodes = graph["tickets"]
    ready, blocked = [], {}
    for key, node in sorted(nodes.items()):
        status = node["status"]
        unmet = sorted(blocker for blocker in blockers.get(key, ())
                       if blocker not in nodes or nodes[blocker]["status"] not in CLOSED_STATUSES)
        if status not in CLOSED_STATUSES and unmet:
            blocked[key] = unmet
        elif status in ACTIVE_STATUSES or status in READY_STATUSES:
            ready.append(key)
    return ready, blocked


def _planning_overlap_allowances(graph, config, records, binding, now, authenticate):
    """Consume controller-validated records, never authenticate owner approvals.

    The trusted controller callback returns the already validated record from
    validate_overlap_allowance_observation (not a candidate-authored status).
    Its expected context supplies current PR identities/files, repository and
    cycle. Integrity/closed-shape checks here only protect that handoff; provider
    and owner validation remain exclusively in the controller.
    """
    if not isinstance(records, (list, tuple)) or not records or not callable(authenticate):
        return {}
    try:
        if (not isinstance(binding, dict)
                or set(binding) != {"repository_id", "cycle_id", "pull_requests"}
                or binding["repository_id"] != str(config["github"]["repository_id"])
                or not isinstance(binding["cycle_id"], str) or not binding["cycle_id"]):
            return {}
        prs = binding["pull_requests"]
        pr_fields = {"number", "ticket", "head_sha", "changed_files"}
        if (not isinstance(prs, list)
                or any(not isinstance(pr, dict) or set(pr) != pr_fields for pr in prs)):
            return {}
        by_ticket = {pr["ticket"]: pr for pr in prs}
        if len(by_ticket) != len(prs) or len({pr["number"] for pr in prs}) != len(prs):
            return {}
        current = timestamp(now)
    except (KeyError, TypeError, ValueError, ValidationError):
        return {}
    accepted = {}
    record_fields = {"schema_version", "allowance_id", "source_id", "binding",
                     "approval", "expires_at", "provider_binding_sha256", "allowance_sha256"}
    binding_fields = {"repository_id", "cycle_id", "path", "tickets",
                      "pull_requests", "heads", "changed_files"}
    approval_fields = {"actor_id", "actor_login", "comment_id", "comment_node_id",
                       "created_at", "body", "body_sha256"}
    for record in records:
        try:
            if (not isinstance(record, dict) or set(record) != record_fields
                    or type(record["schema_version"]) is not int or record["schema_version"] != 1
                    or not isinstance(record["binding"], dict)
                    or set(record["binding"]) != binding_fields
                    or not isinstance(record["approval"], dict)
                    or set(record["approval"]) != approval_fields):
                continue
            if record["allowance_sha256"] != fingerprint(
                    "controller-overlap-allowance-record",
                    {key: value for key, value in record.items() if key != "allowance_sha256"}):
                continue
            if not timestamp(record["approval"]["created_at"]) <= current < timestamp(record["expires_at"]):
                continue
            path = record["binding"]["path"]
            owners = sorted(key for key, node in graph["tickets"].items()
                            if path in node["write_paths"])
            if len(owners) != 2 or any(key not in by_ticket for key in owners):
                continue
            pair = sorted((by_ticket[key] for key in owners), key=lambda pr: pr["number"])
            expected = {"repository_id": binding["repository_id"], "cycle_id": binding["cycle_id"],
                        "path": path, "tickets": [pr["ticket"] for pr in pair],
                        "pull_requests": [pr["number"] for pr in pair],
                        "heads": [pr["head_sha"] for pr in pair],
                        "changed_files": [pr["changed_files"] for pr in pair]}
            if record["binding"] != expected:
                continue
            validated = authenticate(copy.deepcopy(record), copy.deepcopy(binding))
            if validated != record:
                continue
            key = (*owners, path)
            # Deterministic selection when independently validated approvals
            # cover the same pair/path; input order must not alter a sealed plan.
            if key not in accepted or (record["allowance_id"], record["allowance_sha256"]) < (accepted[key]["allowance_id"], accepted[key]["allowance_sha256"]):
                accepted[key] = copy.deepcopy(record)
        except Exception:
            # Malformed or unavailable allowance evidence grants no exception
            # to normal file-overlap serialization, including verifier failures.
            continue
    return accepted


def _lane_components(graph, ready, allowances=None):
    ready_set = set(ready)
    adjacent = {key: set() for key in ready}
    for edge in graph["overlaps"]:
        left, right = edge["left"], edge["right"]
        if left in ready_set and right in ready_set:
            if allowances and all((left, right, path) in allowances for path in edge["paths"]):
                continue
            adjacent[left].add(right)
            adjacent[right].add(left)
    result, seen = [], set()
    for start in sorted(ready):
        if start in seen:
            continue
        component, pending = set(), [start]
        while pending:
            key = pending.pop()
            if key in component:
                continue
            component.add(key)
            pending.extend(adjacent[key] - component)
        seen.update(component)
        result.append(component)
    return result


PLANNING_RESOURCE_KEYS = {
    "format", "complete", "producer", "observed_at", "binding",
    "host_capacity", "resources", "authentication", "observation_sha256",
}
PLANNING_PRODUCER_KEYS = {"kind", "operation", "implementation_sha256"}
PLANNING_AUTHENTICATION_KEYS = {
    "producer_id", "evidence_id", "evidence_sha256", "request_sha256",
}
PLANNING_HOST_OBSERVATION_KEYS = {
    "status", *PLANNING_AUTHENTICATION_KEYS,
    "observed_at", "host_capacity", "resources",
}
PLANNING_BINDING_KEYS = {
    "project_id", "repository_id", "scope_sha256", "repository_head_sha",
    "repository_tree_sha", "project_config_sha256", "operating_config_sha256",
}
PLANNING_RESOURCE_ITEM_KEYS = {"name", "capacity"}
LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_SHA = re.compile(r"[0-9a-f]{40}")
RESOURCE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def _closed(value, keys, name):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValidationError(f"Planning resource {name} must be a closed object")


def _planning_resource_request(binding, observed_at, producer_implementation_sha256):
    return {
        "operation": "observe_planning_resources",
        "binding": copy.deepcopy(binding),
        "observed_at": observed_at,
        "producer_implementation_sha256": producer_implementation_sha256,
    }


def _validate_planning_capacities(host_capacity, resources):
    if type(host_capacity) is not int or not 0 <= host_capacity <= 1000:
        raise ValidationError("Planning observed host capacity must be an integer from zero through 1000")
    if not isinstance(resources, list) or not 1 <= len(resources) <= 32:
        raise ValidationError("Planning resource observation needs 1 through 32 bounded resources")
    names = set()
    for item in resources:
        _closed(item, PLANNING_RESOURCE_ITEM_KEYS, "inventory item")
        name, capacity = item["name"], item["capacity"]
        if not isinstance(name, str) or not RESOURCE_NAME.fullmatch(name):
            raise ValidationError("Planning resource name must be a bounded lowercase identifier")
        if name in names:
            raise ValidationError("Planning resource observation contains a duplicate resource name")
        if type(capacity) is not int or not 0 <= capacity <= 1000:
            raise ValidationError("Planning resource capacity must be an integer from zero through 1000")
        names.add(name)


def validate_planning_resource_observation(
        observation, *, expected_binding, expected_producer_sha256, now,
        verify_host_observation=None):
    """Validate one exact-cycle observation from the pinned reviewed adapter.

    The caller supplies controller-known bindings and the implementation digest
    obtained from the already pinned adapter.  A self-hash alone never grants a
    caller-authored capacity dictionary authority.
    """
    _closed(observation, PLANNING_RESOURCE_KEYS, "observation")
    if observation["format"] != "awf-planning-resource-observation-1":
        raise ValidationError("Planning resource observation format is unsupported")
    if observation["complete"] is not True:
        raise ValidationError("Planning resource observation must be complete")

    producer = observation["producer"]
    _closed(producer, PLANNING_PRODUCER_KEYS, "producer")
    if (producer["kind"] != "reviewed_controller_adapter"
            or producer["operation"] != "observe_planning_resources"
            or not isinstance(expected_producer_sha256, str)
            or not LOWER_SHA256.fullmatch(expected_producer_sha256)
            or producer["implementation_sha256"] != expected_producer_sha256):
        raise ValidationError("Planning resource observation producer is not the pinned reviewed adapter")

    timestamp(observation["observed_at"])
    timestamp(now)
    if observation["observed_at"] != now:
        raise ValidationError("Planning resource observation differs from this controller cycle")

    binding = observation["binding"]
    _closed(binding, PLANNING_BINDING_KEYS, "binding")
    _closed(expected_binding, PLANNING_BINDING_KEYS, "expected binding")
    if binding != expected_binding:
        raise ValidationError("Planning resource observation binding differs from controller-known inputs")
    if (not all(isinstance(binding[name], str) and binding[name]
                for name in ("project_id", "repository_id"))
            or not LOWER_SHA256.fullmatch(binding["scope_sha256"])
            or not GIT_SHA.fullmatch(binding["repository_head_sha"])
            or not GIT_SHA.fullmatch(binding["repository_tree_sha"])
            or not LOWER_SHA256.fullmatch(binding["project_config_sha256"])
            or not LOWER_SHA256.fullmatch(binding["operating_config_sha256"])):
        raise ValidationError("Planning resource observation binding is malformed")

    host_capacity = observation["host_capacity"]
    resources = observation["resources"]
    _validate_planning_capacities(host_capacity, resources)

    authentication = observation["authentication"]
    _closed(authentication, PLANNING_AUTHENTICATION_KEYS, "authentication")
    if (not isinstance(authentication["producer_id"], str)
            or not RESOURCE_NAME.fullmatch(authentication["producer_id"])
            or not isinstance(authentication["evidence_id"], str)
            or not 1 <= len(authentication["evidence_id"]) <= 128
            or not LOWER_SHA256.fullmatch(authentication["evidence_sha256"])):
        raise ValidationError("Planning resource authentication identity is malformed")
    request = _planning_resource_request(binding, observation["observed_at"],
                                         producer["implementation_sha256"])
    request_sha256 = fingerprint("planning_resource_request", request)
    if authentication["request_sha256"] != request_sha256:
        raise ValidationError("Planning resource authentication differs from the exact observation request")

    digest = observation["observation_sha256"]
    expected_digest = fingerprint(
        "planning_resource_observation",
        {key: value for key, value in observation.items() if key != "observation_sha256"})
    if not isinstance(digest, str) or digest != expected_digest:
        raise ValidationError("Planning resource observation digest is missing or stale")
    if not callable(verify_host_observation):
        raise ValidationError("Planning resource observation requires authenticated host evidence")
    try:
        verified = verify_host_observation(copy.deepcopy(authentication), copy.deepcopy(request))
    except Exception as exc:
        raise ValidationError("Planning resource authenticated host evidence lookup failed") from exc
    _closed(verified, PLANNING_HOST_OBSERVATION_KEYS, "authenticated host evidence")
    expected_verified = {
        "status": "VERIFIED", **copy.deepcopy(authentication),
        "observed_at": observation["observed_at"],
        "host_capacity": host_capacity, "resources": copy.deepcopy(resources),
    }
    if verified != expected_verified:
        raise ValidationError(
            "Planning resource capacities differ from authenticated host evidence")
    return copy.deepcopy(observation)


def produce_planning_resource_observation(
        *, binding, observed_at, producer_implementation_sha256,
        observe_host_resources, verify_host_observation):
    """Produce the one closed planning-resource observation shape.

    The caller supplies no capacity values.  A trusted host observer collects
    them for the exact request and an independent authenticator re-verifies its
    retained evidence before the values can reach planning.
    """
    if not callable(observe_host_resources):
        raise ValidationError("Planning resource production requires a trusted host observer")
    request = _planning_resource_request(
        binding, observed_at, producer_implementation_sha256)
    observer_request = copy.deepcopy(request)
    request_before = copy.deepcopy(observer_request)
    try:
        observed = observe_host_resources(observer_request)
    except Exception as exc:
        raise ValidationError("Planning resource host observation failed") from exc
    if observer_request != request_before:
        raise ValidationError("Planning resource host observer mutated its request")
    _closed(observed, PLANNING_HOST_OBSERVATION_KEYS, "host observation")
    if (observed["status"] != "OBSERVED"
            or observed["observed_at"] != observed_at
            or observed["request_sha256"]
            != fingerprint("planning_resource_request", request)):
        raise ValidationError("Planning resource host observation is not bound to the exact request")
    body = {
        "format": "awf-planning-resource-observation-1",
        "complete": True,
        "producer": {
            "kind": "reviewed_controller_adapter",
            "operation": "observe_planning_resources",
            "implementation_sha256": producer_implementation_sha256,
        },
        "observed_at": observed_at,
        "binding": copy.deepcopy(binding),
        "host_capacity": observed["host_capacity"],
        "resources": copy.deepcopy(observed["resources"]),
        "authentication": {
            key: observed[key] for key in PLANNING_AUTHENTICATION_KEYS
        },
    }
    body["observation_sha256"] = fingerprint("planning_resource_observation", body)
    return validate_planning_resource_observation(
        body, expected_binding=binding,
        expected_producer_sha256=producer_implementation_sha256,
        now=observed_at, verify_host_observation=verify_host_observation)


def _sealed_capacity_inputs(config, observation, expected_binding,
                            expected_producer_sha256, operating_snapshot, now,
                            verify_host_observation):
    """Bind authenticated capacity inputs before lane selection or override."""
    if observation is None:
        raise ValidationError("Planning requires an authenticated resource observation")
    validate_snapshot(operating_snapshot, config)
    _closed(expected_binding, PLANNING_BINDING_KEYS, "expected binding")
    accepted_config = fingerprint("accepted-project-config", config)
    project_id = config.get("project", {}).get("id")
    repository_id = config.get("github", {}).get("repository_id")
    valid_repository_id = ((isinstance(repository_id, str) and bool(repository_id))
                           or (type(repository_id) is int and repository_id > 0))
    if (not isinstance(project_id, str) or not project_id or not valid_repository_id
            or expected_binding["project_id"] != project_id
            or expected_binding["repository_id"] != str(repository_id)
            or expected_binding["project_config_sha256"] != accepted_config
            or expected_binding["operating_config_sha256"] != operating_snapshot.operating_hash):
        raise ValidationError("Planning capacity binding differs from accepted project or operating configuration")
    checked = validate_planning_resource_observation(
        observation, expected_binding=expected_binding,
        expected_producer_sha256=expected_producer_sha256, now=now,
        verify_host_observation=verify_host_observation)
    resources = sorted(checked["resources"], key=lambda item: item["name"])
    ceiling = operating_ceiling(config)
    return {
        "observation_sha256": checked["observation_sha256"],
        "producer_implementation_sha256": expected_producer_sha256,
        "host_capacity": checked["host_capacity"],
        "operating_hash": operating_snapshot.operating_hash,
        "stream_capacity": operating_snapshot.count,
        "stream_capacity_source": {
            "kind": "validated_operating_snapshot",
            "operating_hash": operating_snapshot.operating_hash,
            "governance_hash": operating_snapshot.governance_hash,
            "source": operating_snapshot.source,
            "last_change_id": operating_snapshot.last_change_id,
        },
        "resources": resources,
        "resource_capacity": min(item["capacity"] for item in resources),
        "project_config_sha256": accepted_config,
        "governed_ceiling": ceiling["effective_ceiling"],
        "governance_sources": copy.deepcopy(ceiling["effective_ceiling_sources"]),
    }


def _authenticated_override(record, config, previous, verify_owner_source):
    """Validate and re-authenticate one complete owner decision in its chain."""
    if not isinstance(record, dict):
        raise ValidationError("Planning override record must be an object")
    digest = record.get("override_sha256")
    expected = fingerprint("planning_override_record", {k: v for k, v in record.items() if k != "override_sha256"})
    if not isinstance(digest, str) or digest != expected:
        raise ValidationError("Planning override record digest is missing or stale")
    if record.get("mode") not in {"fixed", "max"} or type(record.get("value")) is not int or record["value"] < 1:
        raise ValidationError("Planning override must be a positive fixed or max value")
    timestamp(record.get("created_at"))
    if record.get("schema_version") != 1 or not isinstance(record.get("override_id"), str) or not record["override_id"]:
        raise ValidationError("Planning override record identity is incomplete")

    previous_id = previous.get("override_id") if previous else None
    if record.get("previous_override_id") != previous_id:
        raise ValidationError("Planning override audit chain is incomplete or out of order")
    audit = record.get("change_audit")
    source = record.get("owner_source")
    verified = record.get("owner_verification")
    if not isinstance(audit, dict) or not isinstance(source, dict) or not isinstance(verified, dict):
        raise ValidationError("Planning override requires the complete owner-source and audit chain")

    actor = source.get("actor_id")
    owners = set(config.get("merge_gate", {}).get("trusted_owner_ids", []))
    project_owner = config.get("github", {}).get("expected_actor_id")
    if project_owner is not None:
        owners.add(project_owner)
    if actor not in owners or audit.get("who") != actor:
        raise ValidationError("Planning override audit does not identify an authenticated owner or trusted owner")
    reason = audit.get("why")
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise ValidationError("Planning override audit needs the exact non-empty owner reason")
    old = {"mode": previous.get("mode"), "value": previous.get("value")} if previous else {"mode": None, "value": None}
    if (audit.get("when") != record["created_at"] or audit.get("old") != old
            or audit.get("new") != {"mode": record["mode"], "value": record["value"]}):
        raise ValidationError("Planning override audit old/new values or timestamp do not bind the chain")

    binding = {"current_override_id": previous_id}
    command = owner_override_command(previous_id, record["mode"], record["value"], reason)
    command_hash = sha256(command.encode("utf-8"))
    if (source.get("channel") != "github_pr_comment"
            or type(source.get("comment_id")) is not int or source["comment_id"] < 1
            or source.get("binding") != binding
            or source.get("created_at") != source.get("updated_at")
            or source.get("raw_body") != command
            or source.get("raw_body_sha256") != command_hash):
        raise ValidationError("Planning override owner-source does not immutably bind the audited request")
    timestamp(source.get("created_at"))
    if (audit.get("owner_source_id") != verified.get("source_id")
            or audit.get("owner_source_sha256") != command_hash
            or (source.get("source_id") is not None and source.get("source_id") != verified.get("source_id"))
            or verified.get("status") != "VERIFIED"
            or verified.get("actor_id") != actor
            or verified.get("binding") != binding
            or verified.get("raw_body_sha256") != command_hash
            or not isinstance(verified.get("source_id"), str) or not verified["source_id"]
            or not isinstance(verified.get("observed_at"), str)):
        raise ValidationError("Planning override owner-source verification does not bind the audit")
    timestamp(verified["observed_at"])
    seed = {"created_at": record["created_at"], "actor_id": actor,
            "mode": record["mode"], "value": record["value"],
            "previous_override_id": previous_id,
            "owner_source_id": verified["source_id"]}
    expected_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, "awf-planning-override:" + fingerprint("planning_override", seed)))
    if record["override_id"] != expected_id:
        raise ValidationError("Planning override identity does not bind its owner-source audit")
    if not callable(verify_owner_source):
        raise ValidationError("Planning override scheduling requires owner-source re-authentication")
    verification_request = {
        "binding": binding, "actor_id": actor, "raw_body_sha256": command_hash,
        "requested": {"mode": record["mode"], "value": record["value"]},
        "now": record["created_at"],
    }
    try:
        observed = verify_owner_source(copy.deepcopy(source), copy.deepcopy(verification_request))
    except Exception as exc:
        raise ValidationError("Planning override owner-source authentication failed") from exc
    for field in ("status", "actor_id", "binding", "raw_body_sha256", "source_id"):
        if not isinstance(observed, dict) or observed.get(field) != verified.get(field):
            raise ValidationError("Planning override owner-source re-authentication differs from its controller record")
    if not isinstance(observed.get("observed_at"), str):
        raise ValidationError("Planning override re-authentication lacks an observation timestamp")
    timestamp(observed["observed_at"])
    return True


def verify_override_record(record, config=None, previous=None, verify_owner_source=None):
    """Verify one override only with its governance and owner authenticator."""
    if config is None or not callable(verify_owner_source):
        raise ValidationError("Planning override verification requires authenticated controller context")
    return _authenticated_override(record, config, previous, verify_owner_source)


def verify_override_state(config, state, verify_owner_source):
    """Return the current override only from a complete controller-owned chain."""
    if not isinstance(state, dict):
        raise ValidationError("Planning overrides require controller-owned AWF state")
    current = state.get("planning_override")
    history = state.get("planning_override_history", [])
    if current is None:
        if history:
            raise ValidationError("Planning override audit history has no current controller record")
        return None
    if not isinstance(history, list) or not history:
        raise ValidationError("Planning override requires a complete controller-owned audit history")
    previous = None
    identities = set()
    sources = set()
    for record in history:
        _authenticated_override(record, config, previous, verify_owner_source)
        if record["override_id"] in identities or record["owner_verification"]["source_id"] in sources:
            raise ValidationError("Planning override audit chain reuses an override or owner-source identity")
        identities.add(record["override_id"])
        sources.add(record["owner_verification"]["source_id"])
        previous = record
    if current != previous:
        raise ValidationError("Planning override current value is not the authenticated audit-chain tip")
    return copy.deepcopy(current)


def _estimate(graph, config, independent_count, capacity_inputs):
    capacity = operating_ceiling(config)
    execution = config["execution"]
    constraints = {
        "dependency_graph/file_overlap": independent_count,
        "configured_ceiling": configured_lane_ceiling(config),
        "max_parallel_tickets": execution["max_parallel_tickets"],
    }
    broker = execution.get("host_broker")
    if isinstance(broker, dict) and broker.get("enabled") is True:
        constraints["host_broker"] = broker["max_workers"]
    constraints["reviewed_operating_streams"] = capacity_inputs["stream_capacity"]
    constraints["observed_host_capacity"] = capacity_inputs["host_capacity"]
    constraints.update({"resource:" + item["name"]: item["capacity"]
                        for item in capacity_inputs["resources"]})
    if any(type(value) is not int or value < 0 for value in constraints.values()):
        raise ValidationError("Every planning capacity component must be a nonnegative integer")
    safe = min(constraints.values()) if constraints else 0
    binding = sorted(name for name, value in constraints.items() if value == safe)
    sealed = copy.deepcopy(capacity_inputs)
    sealed["safe_lane_count"] = safe
    sealed["capacity_binding_sha256"] = fingerprint("planning_capacity_binding", sealed)
    return {
        "independent_lane_count": independent_count,
        "safe_lane_count": safe,
        "binding_constraints": binding,
        "constraints": constraints,
        "governance_sources": capacity["effective_ceiling_sources"],
        "capacity": sealed,
        "reasoning": (
            f"{independent_count} independent lane(s); safe count {safe}; binding: "
            + (", ".join(binding) if binding else "none")
            + "; authenticated host, reviewed stream, and resource capacities are sealed"
        ),
    }


def plan_lanes(graph, config, *, resource_observation=None,
               expected_resource_binding=None,
               expected_resource_producer_sha256=None,
               operating_snapshot=None, observed_at=None, resources=None,
               verify_host_observation=None, controller_state=None,
               verify_owner_source=None, override=None, overlap_allowances=(),
               expected_overlap_binding=None, verify_overlap_allowance=None):
    """Return conflict-free ready lanes ranked by downstream critical path."""
    if not isinstance(graph, dict) or not isinstance(graph.get("tickets"), dict):
        raise ValidationError("Planning requires a backlog graph")
    if resources is not None:
        raise ValidationError(
            "Planning refuses operator-authored capacity dictionaries; an authenticated resource observation is required")
    capacity_inputs = _sealed_capacity_inputs(
        config, resource_observation, expected_resource_binding,
        expected_resource_producer_sha256, operating_snapshot, observed_at,
        verify_host_observation)
    blockers, dependents = _dependency_maps(graph)
    scores = _critical_scores(graph, dependents)
    ready, blocked = _ready_keys(graph, blockers)
    allowances = _planning_overlap_allowances(
        graph, config, overlap_allowances, expected_overlap_binding,
        observed_at, verify_overlap_allowance)
    components = _lane_components(graph, ready, allowances)
    lanes = []
    for component in components:
        ordered = sorted(component, key=lambda key: (-scores[key], key))
        lanes.append({
            "ticket_keys": ordered,
            "write_paths": sorted({path for key in component for path in graph["tickets"][key]["write_paths"]}),
            "critical_path_minutes": max(scores[key] for key in component),
            "risk_tier_floor": max(graph["tickets"][key]["risk_tier"] for key in component),
        })
    lanes.sort(key=lambda lane: (-lane["critical_path_minutes"], lane["ticket_keys"]))
    for label, lane in zip(stream_labels(len(lanes)), lanes):
        lane["label"] = label

    estimate = _estimate(graph, config, len(lanes), capacity_inputs)
    lane_count = estimate["safe_lane_count"]
    decision = {"kind": "accepted_default", "value": lane_count}
    warnings = []
    if override is not None:
        raise ValidationError("Planning overrides must come from authenticated controller-owned state")
    selected_override = (verify_override_state(config, controller_state, verify_owner_source)
                         if controller_state is not None else None)
    if selected_override is not None:
        ceiling = operating_ceiling(config)["effective_ceiling"]
        if selected_override["value"] > ceiling:
            raise ValidationError(f"Planning override exceeds the governed lane ceiling of {ceiling}")
        if selected_override["mode"] == "fixed":
            if selected_override["value"] > estimate["safe_lane_count"]:
                raise ValidationError(
                    "Planning fixed override exceeds the sealed safe capacity and cannot authorize a lane")
            lane_count = selected_override["value"]
        else:
            lane_count = min(lane_count, selected_override["value"])
        decision = {"kind": "owner_override", "mode": selected_override["mode"],
                    "value": selected_override["value"], "override_id": selected_override["override_id"],
                    "capacity_binding_sha256": estimate["capacity"]["capacity_binding_sha256"],
                    "applied_after_capacity_binding": True}
    result = {"lanes": lanes, "blocked": blocked, "critical_path_minutes": scores,
            "estimate": estimate, "owner_decision": decision,
            "capacity": copy.deepcopy(estimate["capacity"]),
            "lane_count": lane_count, "warnings": warnings}
    # Seal only evidence actually used to separate ready lanes. Preserve the
    # historical result bytes when no allowance changes a lane component.
    labels = {key: lane["label"] for lane in lanes for key in lane["ticket_keys"]}
    used = {record["allowance_id"]: record for (left, right, _path), record in allowances.items()
            if left in labels and right in labels and labels[left] != labels[right]}
    if used:
        result["overlap_allowances"] = [used[key] for key in sorted(used)]
    return result


def owner_override_command(current_override_id, mode, value, reason):
    current = current_override_id or "NONE"
    return (f"AWF LANE OVERRIDE current={current} mode={mode} value={value} "
            f"reason_sha256={sha256(reason.strip().encode('utf-8'))}")


def change_owner_override(config, state, *, mode, value, reason, now,
                          owner_source, verify_owner_source):
    """Apply one authenticated owner decision using AWF-48's audit shape."""
    if not isinstance(state, dict):
        raise ValidationError("AWF state must be an object")
    if mode not in {"fixed", "max"} or type(value) is not int or value < 1:
        raise ValidationError("Lane override needs mode fixed/max and a positive integer value")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("Lane override needs a non-empty reason")
    ceiling = operating_ceiling(config)["effective_ceiling"]
    if value > ceiling:
        raise ValidationError(f"Lane override exceeds the governed lane ceiling of {ceiling}")
    current = verify_override_state(config, state, verify_owner_source)
    current_id = current.get("override_id") if current else None
    if not isinstance(owner_source, dict) or not callable(verify_owner_source):
        raise ValidationError("Lane overrides require a verified immutable owner-source record")
    binding = {"current_override_id": current_id}
    if (owner_source.get("channel") != "github_pr_comment"
            or type(owner_source.get("comment_id")) is not int
            or owner_source["comment_id"] < 1
            or owner_source.get("binding") != binding
            or owner_source.get("created_at") != owner_source.get("updated_at")):
        raise ValidationError("Lane override owner source lacks an immutable bound provider identity")
    actor = owner_source.get("actor_id")
    owners = set(config.get("merge_gate", {}).get("trusted_owner_ids", []))
    project_owner = config.get("github", {}).get("expected_actor_id")
    if project_owner is not None:
        owners.add(project_owner)
    if actor not in owners:
        raise ValidationError("Lane overrides require an authenticated owner or trusted owner")
    timestamp(owner_source.get("created_at"))
    timestamp(now)
    command = owner_override_command(current_id, mode, value, reason)
    command_hash = sha256(command.encode("utf-8"))
    if owner_source.get("raw_body") != command or owner_source.get("raw_body_sha256") != command_hash:
        raise ValidationError("Lane override source does not bind the requested change")
    expected = {"binding": binding, "actor_id": actor, "raw_body_sha256": command_hash,
                "requested": {"mode": mode, "value": value}, "now": now}
    try:
        verified = verify_owner_source(copy.deepcopy(owner_source), copy.deepcopy(expected))
    except Exception as exc:
        raise ValidationError("Lane override owner-source authentication failed") from exc
    if (not isinstance(verified, dict) or verified.get("status") != "VERIFIED"
            or verified.get("actor_id") != actor or verified.get("binding") != binding
            or verified.get("raw_body_sha256") != command_hash
            or not isinstance(verified.get("source_id"), str) or not verified["source_id"]
            or not isinstance(verified.get("observed_at"), str)):
        raise ValidationError("Lane override verifier did not authenticate the exact request")
    timestamp(verified["observed_at"])
    old = {"mode": current.get("mode"), "value": current.get("value")} if current else {"mode": None, "value": None}
    audit = {"who": actor, "when": now, "why": reason.strip(),
             "owner_source_id": verified["source_id"],
             "owner_source_sha256": command_hash,
             "old": old, "new": {"mode": mode, "value": value}}
    seed = {"created_at": now, "actor_id": actor, "mode": mode, "value": value,
            "previous_override_id": current_id, "owner_source_id": verified["source_id"]}
    record = {"schema_version": 1,
              "override_id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                             "awf-planning-override:" + fingerprint("planning_override", seed))),
              "override_sha256": "", "created_at": now, "mode": mode, "value": value,
              "previous_override_id": current_id, "change_audit": audit,
              "owner_source": copy.deepcopy(owner_source),
              "owner_verification": copy.deepcopy(verified)}
    record["override_sha256"] = fingerprint(
        "planning_override_record", {k: v for k, v in record.items() if k != "override_sha256"})
    state["planning_override"] = copy.deepcopy(record)
    state.setdefault("planning_override_history", []).append(copy.deepcopy(record))
    return copy.deepcopy(record)


def verify_plan_record(record):
    if not isinstance(record, dict):
        raise ValidationError("Planning record must be an object")
    digest = record.get("plan_sha256")
    expected = fingerprint("planning_audit_record", {k: v for k, v in record.items() if k != "plan_sha256"})
    if not isinstance(digest, str) or digest != expected:
        raise ValidationError("Planning record digest is missing or stale")
    inputs = record.get("inputs")
    capacity = inputs.get("capacity") if isinstance(inputs, dict) else None
    if not isinstance(capacity, dict):
        raise ValidationError("Planning record lacks its sealed capacity binding")
    schema_version = record.get("schema_version")
    if schema_version == 1 and "capacity_binding_sha256" not in capacity:
        timestamp(record.get("created_at"))
        return True
    if schema_version not in {1, 2}:
        raise ValidationError("Planning record schema version is unsupported")
    capacity_digest = capacity.get("capacity_binding_sha256")
    expected_capacity_digest = fingerprint(
        "planning_capacity_binding",
        {key: value for key, value in capacity.items()
         if key != "capacity_binding_sha256"})
    if not isinstance(capacity_digest, str) or capacity_digest != expected_capacity_digest:
        raise ValidationError("Planning record capacity binding digest is missing or stale")
    estimate = record.get("estimate")
    if (not isinstance(estimate, dict)
            or estimate.get("capacity") != capacity
            or estimate.get("safe_lane_count") != capacity.get("safe_lane_count")):
        raise ValidationError("Planning record estimate differs from its sealed capacity binding")
    decision = record.get("owner_decision")
    if isinstance(decision, dict) and decision.get("kind") == "owner_override":
        if (decision.get("capacity_binding_sha256") != capacity_digest
                or decision.get("applied_after_capacity_binding") is not True):
            raise ValidationError("Planning owner override is not bound after sealed capacity")
    safe = capacity.get("safe_lane_count")
    if (type(safe) is not int or safe < 0 or type(record.get("lane_count")) is not int
            or not 0 <= record["lane_count"] <= safe):
        raise ValidationError("Planning lane count exceeds its sealed safe capacity")
    timestamp(record.get("created_at"))
    return True


def append_plan_record(state, record):
    """Append a sealed plan once; existing records are never replaced."""
    verify_plan_record(record)
    history = state.setdefault("planning_history", [])
    for existing in history:
        verify_plan_record(existing)
        if existing["plan_id"] == record["plan_id"]:
            raise ValidationError("Planning records are immutable and plan_id already exists")
    history.append(copy.deepcopy(record))
    state["current_plan"] = copy.deepcopy(record)
    return copy.deepcopy(record)


def recompute_plan(tickets, config, state, *, now, trigger,
                   resource_observation=None, expected_resource_binding=None,
                   expected_resource_producer_sha256=None,
                   operating_snapshot=None, observed_at=None, resources=None,
                   verify_host_observation=None, round_active=False,
                   verify_owner_source=None, override=None, overlap_allowances=(),
                   expected_overlap_binding=None, verify_overlap_allowance=None):
    """Re-plan at named boundaries, deferring any in-round reassignment."""
    if trigger not in RECOMPUTE_TRIGGERS:
        raise ValidationError("Unknown planning recompute trigger")
    if not isinstance(state, dict):
        raise ValidationError("AWF state must be an object")
    if override is not None:
        raise ValidationError("Planning overrides must come from authenticated controller-owned state")
    timestamp(now)
    if round_active:
        pending = state.setdefault("pending_planning_triggers", [])
        if trigger not in pending:
            pending.append(trigger)
        return {"status": "DEFERRED_UNTIL_ROUND_END", "trigger": trigger,
                "reason": "In-flight work is never reassigned mid-round"}
    if observed_at != now:
        raise ValidationError("Planning resource observation differs from the plan recompute cycle")
    graph = build_backlog_graph(tickets, config)
    result = plan_lanes(
        graph, config, resource_observation=resource_observation,
        expected_resource_binding=expected_resource_binding,
        expected_resource_producer_sha256=expected_resource_producer_sha256,
        operating_snapshot=operating_snapshot, observed_at=observed_at,
        resources=resources, verify_host_observation=verify_host_observation,
        controller_state=state,
        verify_owner_source=verify_owner_source, overlap_allowances=overlap_allowances,
        expected_overlap_binding=expected_overlap_binding,
        verify_overlap_allowance=verify_overlap_allowance)
    body = {"schema_version": 2, "created_at": now, "trigger": trigger,
            "inputs": {"jira_snapshot_sha256": fingerprint("planning_ticket_snapshot", tickets),
                       "tickets": copy.deepcopy(graph["tickets"]),
                       "dependencies": copy.deepcopy(graph["dependencies"]),
                       "overlaps": copy.deepcopy(graph["overlaps"]),
                       "generator_owners": copy.deepcopy(graph["generator_owners"]),
                       "capacity": copy.deepcopy(result["capacity"])},
            "lanes": copy.deepcopy(result["lanes"]), "blocked": copy.deepcopy(result["blocked"]),
            "ranking": copy.deepcopy(result["critical_path_minutes"]),
            "estimate": copy.deepcopy(result["estimate"]),
            "owner_decision": copy.deepcopy(result["owner_decision"]),
            "lane_count": result["lane_count"], "warnings": copy.deepcopy(result["warnings"]),
            "no_mid_round_reassignment": True}
    if "overlap_allowances" in result:
        body["inputs"]["overlap_allowances"] = copy.deepcopy(result["overlap_allowances"])
    body["plan_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                     "awf-planning-record:" + fingerprint("planning_audit_seed", body)))
    body["plan_sha256"] = fingerprint("planning_audit_record", body)
    append_plan_record(state, body)
    state["pending_planning_triggers"] = []
    return {"status": "PLANNED", "record": copy.deepcopy(body)}
