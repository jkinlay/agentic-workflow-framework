"""Deterministic, fail-closed ticket planning graph records.

This module is deliberately side-effect free.  It accepts a reviewed ticket
snapshot and emits the generated ``ticket-plan`` contract consumed by the
lane-fill controller; it neither observes live lanes nor dispatches work.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import re


TICKET_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
IDLE_LANE_REASONS = {
    "NO_READY_TICKET", "PREDECESSOR_UNMERGED", "FILE_CLASH",
    "OWNERSHIP_CONFLICT", "GENERATOR_ORDER_BUSY", "START_GAP_PENDING",
    "HOLD", "CAP_REACHED", "PLAN_UNREADABLE", "MERGE_FREEZE",
    "RESYNC_PENDING",
}
PRESTAGE_BLOCKERS = {"MERGE_FREEZE", "RESYNC_PENDING"}
LANE_CLASSES = {"critical", "feeder", "controller"}
STATUS_VERDICTS = {"OK", "FAIL"}
MAX_SAFE_INTEGER = 9007199254740991
INPUT_FIELDS = {
    "key", "dependencies", "write_paths", "duration_minutes", "risk_tier",
    "round_cap", "owner_review_window", "lane_class", "owner_lane",
    "blockers", "idle_lane_reason", "phase", "status", "in_lane",
}
DERIVED_FIELDS = {
    "earliest_start_minute", "critical_path", "critical_path_position",
    "serial_path_id", "serial_predecessor", "independent",
    "generator_order_slot", "prestage_allowed",
}
OUTPUT_TICKET_FIELDS = INPUT_FIELDS | DERIVED_FIELDS
PLAN_FIELDS = {
    "format", "schema_version", "lane_cap", "start_gap_minutes",
    "tier3_round_cap", "generator_order", "critical_path",
    "duration_minutes", "tickets", "edges", "observed_minute",
    "lane_status", "mermaid",
}
EDGE_FIELDS = {"from", "to", "kind", "paths"}
LANE_OBSERVATION_FIELDS = {"owner_lane", "idle_since_minute"}
LANE_STATUS_FIELDS = LANE_OBSERVATION_FIELDS | {
    "idle_minutes", "status_verdict", "ready_tickets", "idle_lane_reasons",
}


class PlanningGraphError(ValueError):
    """A ticket plan cannot be constructed or trusted."""

    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _fail(reason, detail):
    raise PlanningGraphError(reason, detail)


def _is_int(value, minimum=0, maximum=MAX_SAFE_INTEGER):
    return type(value) is int and minimum <= value <= maximum


def _check_fields(value, expected, context):
    if not isinstance(value, dict):
        _fail("INVALID_FIELD", f"{context} must be an object")
    missing = sorted(expected - set(value))
    if missing:
        _fail("MISSING_REQUIRED_FIELD", f"{context} is missing {', '.join(missing)}")
    unknown = sorted(set(value) - expected)
    if unknown:
        _fail("UNKNOWN_FIELD", f"{context} has unknown fields {', '.join(unknown)}")


def _valid_key(value):
    return isinstance(value, str) and TICKET_KEY.fullmatch(value) is not None


def _check_string(value, field):
    if not isinstance(value, str) or not value:
        _fail("INVALID_FIELD", f"{field} must be a non-empty string")


def _check_string_list(value, field, *, keys=False, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _fail("INVALID_FIELD", f"{field} must be a unique list")
    for item in value:
        if (not _valid_key(item)) if keys else (not isinstance(item, str) or not item):
            _fail("INVALID_FIELD", f"{field} contains an invalid value")
    if len(value) != len(set(value)):
        _fail("INVALID_FIELD", f"{field} must be a unique list")


def _check_reason(value, field="idle_lane_reason"):
    if value is not None and (not isinstance(value, str) or value not in IDLE_LANE_REASONS):
        _fail("UNKNOWN_IDLE_LANE_REASON", f"{field} {value!r} is not recognized")


def _check_blockers(value):
    if not isinstance(value, list):
        _fail("INVALID_FIELD", "blockers must be a unique list")
    for blocker in value:
        if not isinstance(blocker, str) or blocker not in IDLE_LANE_REASONS:
            _fail("UNKNOWN_IDLE_LANE_REASON", f"blocker {blocker!r} is not recognized")
    if len(value) != len(set(value)):
        _fail("INVALID_FIELD", "blockers must be a unique list")


def _check_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or value.startswith("/") or re.match(r"^[A-Za-z]:", value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        _fail("INVALID_FIELD", "write_paths must contain portable repository-relative paths")


def _path_identity(value):
    return value.casefold()


def _check_window(value):
    if value is None:
        return
    _check_fields(value, {"opens_at_minute", "closes_at_minute"}, "owner_review_window")
    opens = value["opens_at_minute"]
    closes = value["closes_at_minute"]
    if not _is_int(opens) or not _is_int(closes) or closes < opens:
        _fail("INVALID_FIELD", "owner_review_window must be an ordered non-negative interval")


def _check_lane_fill(ticket):
    if type(ticket["critical_path"]) is not bool:
        _fail("INVALID_FIELD", "critical_path must be boolean")
    position = ticket["critical_path_position"]
    if ticket["critical_path"]:
        if not _is_int(position, 1):
            _fail("INVALID_FIELD", "critical_path_position must be 1-based on the path")
    elif position is not None:
        _fail("INVALID_FIELD", "critical_path_position must be null off the path")
    _check_string(ticket["serial_path_id"], "serial_path_id")
    predecessor = ticket["serial_predecessor"]
    if predecessor is not None and not _valid_key(predecessor):
        _fail("INVALID_FIELD", "serial_predecessor must be a ticket key or null")
    if not isinstance(ticket["lane_class"], str) or ticket["lane_class"] not in LANE_CLASSES:
        _fail("INVALID_FIELD", "lane_class is not recognized")
    if type(ticket["independent"]) is not bool:
        _fail("INVALID_FIELD", "independent must be boolean")
    slot = ticket["generator_order_slot"]
    if slot is not None and not _is_int(slot, 1):
        _fail("INVALID_FIELD", "generator_order_slot must be 1-based or null")
    _check_string(ticket["owner_lane"], "owner_lane")
    _check_blockers(ticket["blockers"])
    _check_reason(ticket["idle_lane_reason"])
    if type(ticket["prestage_allowed"]) is not bool:
        _fail("INVALID_FIELD", "prestage_allowed must be boolean")
    _check_string(ticket["phase"], "phase")
    _check_string(ticket["status"], "status")
    if type(ticket["in_lane"]) is not bool:
        _fail("INVALID_FIELD", "in_lane must be boolean")


def _normalize_ticket(raw, tier3_round_cap):
    _check_fields(raw, INPUT_FIELDS, "ticket")
    ticket = deepcopy(raw)
    if not _valid_key(ticket["key"]):
        _fail("INVALID_FIELD", "ticket key is invalid")
    _check_string_list(ticket["dependencies"], "dependencies", keys=True)
    _check_string_list(ticket["write_paths"], "write_paths", nonempty=True)
    for path in ticket["write_paths"]:
        _check_path(path)
    identities = [_path_identity(path) for path in ticket["write_paths"]]
    if len(identities) != len(set(identities)):
        _fail("PATH_ALIAS_COLLISION", "write_paths contain portable case aliases")
    ticket["dependencies"] = sorted(ticket["dependencies"])
    ticket["write_paths"] = sorted(ticket["write_paths"], key=lambda path: (_path_identity(path), path))
    if not _is_int(ticket["duration_minutes"], 1):
        _fail("INVALID_FIELD", "duration_minutes must be a positive integer")
    if type(ticket["risk_tier"]) is not int or ticket["risk_tier"] not in {1, 2, 3}:
        _fail("INVALID_RISK_TIER", "risk_tier must be 1, 2, or 3")
    if not _is_int(ticket["round_cap"], 1):
        _fail("TIER_ROUND_CAP_VIOLATION", "round_cap must be a positive integer")
    if ticket["risk_tier"] == 3 and ticket["round_cap"] > tier3_round_cap:
        _fail("TIER_ROUND_CAP_VIOLATION", "Tier 3 round_cap exceeds the reviewed cap")
    _check_window(ticket["owner_review_window"])
    if not isinstance(ticket["lane_class"], str) or ticket["lane_class"] not in LANE_CLASSES:
        _fail("INVALID_FIELD", "lane_class is not recognized")
    _check_string(ticket["owner_lane"], "owner_lane")
    _check_blockers(ticket["blockers"])
    ticket["blockers"] = sorted(ticket["blockers"])
    _check_reason(ticket["idle_lane_reason"])
    _check_string(ticket["phase"], "phase")
    _check_string(ticket["status"], "status")
    if type(ticket["in_lane"]) is not bool:
        _fail("INVALID_FIELD", "in_lane must be boolean")
    prestage_allowed = bool(ticket["blockers"]) and set(ticket["blockers"]) <= PRESTAGE_BLOCKERS
    if prestage_allowed:
        ticket["in_lane"] = True
        ticket["status"] = "prestaged-hold"
    return ticket


def _generator_ticket(ticket):
    return any(
        _path_identity(path) == "scripts/generate_contracts.py"
        or (_path_identity(path).startswith(".agentic/schemas/")
            and _path_identity(path).endswith(".schema.json"))
        for path in ticket["write_paths"]
    )


def _assert_acyclic(keys, adjacency):
    state = {}

    def visit(key):
        if state.get(key) == 1:
            _fail("DEPENDENCY_CYCLE", f"cycle includes {key}")
        if state.get(key) == 2:
            return
        state[key] = 1
        for dependent in sorted(adjacency[key]):
            visit(dependent)
        state[key] = 2

    for key in sorted(keys):
        visit(key)


def _reachable(start, target, adjacency):
    pending = [start]
    seen = set()
    while pending:
        key = pending.pop()
        if key == target:
            return True
        if key in seen:
            continue
        seen.add(key)
        pending.extend(adjacency[key] - seen)
    return False


def _graph(tickets, generator_order):
    by_key = {ticket["key"]: ticket for ticket in tickets}
    keys = set(by_key)
    dependency_adjacency = {key: set() for key in keys}
    edges = []
    for ticket in tickets:
        for blocker in ticket["dependencies"]:
            if blocker not in keys:
                _fail("DANGLING_DEPENDENCY", f"{ticket['key']} depends on absent {blocker}")
            dependency_adjacency[blocker].add(ticket["key"])
            edges.append({"from": blocker, "to": ticket["key"], "kind": "dependency", "paths": []})
    _assert_acyclic(keys, dependency_adjacency)

    order_slot = {key: index for index, key in enumerate(generator_order, 1)}
    generators = [ticket["key"] for ticket in tickets if _generator_ticket(ticket)]
    missing = sorted(set(generators) - set(order_slot))
    if missing:
        _fail("GENERATOR_ORDER_MISSING", f"generator order omits {', '.join(missing)}")

    precedence = {key: set(values) for key, values in dependency_adjacency.items()}
    ordered_generators = sorted(generators, key=lambda key: (order_slot[key], key))
    for before, after in zip(ordered_generators, ordered_generators[1:]):
        precedence[before].add(after)
        edges.append({"from": before, "to": after, "kind": "generator_order", "paths": []})
    ordered_keys = sorted(keys)
    for index, left in enumerate(ordered_keys):
        for right in ordered_keys[index + 1:]:
            left_paths = {_path_identity(path) for path in by_key[left]["write_paths"]}
            right_paths = {_path_identity(path) for path in by_key[right]["write_paths"]}
            shared = sorted(left_paths & right_paths)
            if not shared:
                continue
            if _reachable(left, right, precedence):
                before, after = left, right
            elif _reachable(right, left, precedence):
                before, after = right, left
            elif left in order_slot and right in order_slot:
                before, after = sorted((left, right), key=lambda key: (order_slot[key], key))
            else:
                before, after = left, right
            precedence[before].add(after)
            edges.append({"from": before, "to": after, "kind": "file_overlap", "paths": shared})

    _assert_acyclic(keys, precedence)
    edges.sort(key=lambda edge: (edge["from"], edge["to"], edge["kind"], edge["paths"]))
    return by_key, precedence, edges, order_slot


def _predecessors(keys, adjacency):
    result = {key: set() for key in keys}
    for key, dependents in adjacency.items():
        for dependent in dependents:
            result[dependent].add(key)
    return result


def _critical_path(by_key, adjacency):
    scores = {}
    choices = {}

    def score(key):
        if key not in scores:
            if adjacency[key]:
                chosen = min(adjacency[key], key=lambda item: (-score(item), item))
                downstream = score(chosen)
            else:
                downstream, chosen = 0, None
            scores[key] = by_key[key]["duration_minutes"] + downstream
            choices[key] = chosen
        return scores[key]

    for key in sorted(by_key):
        score(key)
    start = min(by_key, key=lambda key: (-scores[key], key))
    path = []
    while start is not None:
        path.append(start)
        start = choices[start]
    return path, scores


def _owner_windows_feasible(
        by_key, predecessors, unscheduled, finishes, running, minute,
        last_start, lane_cap, start_gap_minutes, candidate):
    """Return whether launching candidate preserves every owner-window deadline.

    The feasibility search includes all future windowed tickets and their
    unscheduled predecessors.  This reserves aggregate capacity rather than
    considering each future ticket independently.
    """
    trial_unscheduled = set(unscheduled)
    trial_unscheduled.remove(candidate)
    trial_finishes = dict(finishes)
    candidate_finish = minute + by_key[candidate]["duration_minutes"]
    if candidate_finish > MAX_SAFE_INTEGER:
        return False
    trial_finishes[candidate] = candidate_finish
    trial_running = dict(running)
    trial_running[candidate] = candidate_finish

    required = {
        key for key in trial_unscheduled
        if by_key[key]["owner_review_window"] is not None
    }
    pending = list(required)
    while pending:
        key = pending.pop()
        for predecessor in predecessors[key]:
            if predecessor in trial_unscheduled and predecessor not in required:
                required.add(predecessor)
                pending.append(predecessor)
    if not required:
        return True

    failed_states = set()

    def search(remaining, known_finishes, active, at_minute, prior_start):
        if not remaining:
            return True
        active = {key: end for key, end in active.items() if end > at_minute}
        state = (
            tuple(sorted(remaining)),
            tuple(sorted(known_finishes.items())),
            tuple(sorted(active.items())),
            at_minute,
            prior_start,
        )
        if state in failed_states:
            return False

        ready = []
        future = []
        for key in remaining:
            if not predecessors[key] <= set(known_finishes):
                continue
            release = max(
                (known_finishes[item] + start_gap_minutes for item in predecessors[key]),
                default=0,
            )
            window = by_key[key]["owner_review_window"]
            if window is not None:
                release = max(release, window["opens_at_minute"])
                latest_start = window["closes_at_minute"] - by_key[key]["duration_minutes"]
                if release > latest_start or at_minute > latest_start:
                    failed_states.add(state)
                    return False
            if release <= at_minute:
                ready.append(key)
            else:
                future.append(release)

        ready.sort(key=lambda key: (
            by_key[key]["owner_review_window"]["closes_at_minute"]
            if by_key[key]["owner_review_window"] is not None else float("inf"),
            key,
        ))
        launch_unlocked = prior_start is None or at_minute >= prior_start + start_gap_minutes
        if len(active) < lane_cap and launch_unlocked:
            for key in ready:
                finish = at_minute + by_key[key]["duration_minutes"]
                window = by_key[key]["owner_review_window"]
                if finish > MAX_SAFE_INTEGER or (
                        window is not None and finish > window["closes_at_minute"]):
                    continue
                next_finishes = dict(known_finishes)
                next_finishes[key] = finish
                next_active = dict(active)
                next_active[key] = finish
                if search(
                        remaining - {key}, next_finishes, next_active,
                        at_minute, at_minute):
                    return True

        next_times = [end for end in active.values() if end > at_minute]
        next_times.extend(release for release in future if release > at_minute)
        if ready and prior_start is not None:
            unlock = prior_start + start_gap_minutes
            if unlock > at_minute:
                next_times.append(unlock)
        if next_times:
            next_minute = min(next_times)
            if search(remaining, known_finishes, active, next_minute, prior_start):
                return True

        failed_states.add(state)
        return False

    return search(
        required,
        trial_finishes,
        trial_running,
        minute,
        minute,
    )


def _schedule(by_key, adjacency, lane_cap, start_gap_minutes, scores):
    predecessors = _predecessors(by_key, adjacency)
    unscheduled = set(by_key)
    starts = {}
    finishes = {}
    running = {}
    minute = 0
    last_start = None
    while unscheduled:
        running = {key: end for key, end in running.items() if end > minute}
        available = lane_cap - len(running)
        ready = []
        future = []
        for key in unscheduled:
            if not predecessors[key] <= set(finishes):
                continue
            release = max(
                (finishes[item] + start_gap_minutes for item in predecessors[key]),
                default=0,
            )
            window = by_key[key]["owner_review_window"]
            if window is not None:
                release = max(release, window["opens_at_minute"])
            if release <= minute:
                ready.append(key)
            else:
                future.append(release)
        ready.sort(key=lambda key: (
            by_key[key]["owner_review_window"]["closes_at_minute"]
            if by_key[key]["owner_review_window"] is not None else float("inf"),
            -scores[key],
            key,
        ))
        launch_unlocked = last_start is None or minute >= last_start + start_gap_minutes
        for key in ready:
            if minute + by_key[key]["duration_minutes"] > MAX_SAFE_INTEGER:
                _fail("INVALID_FIELD", f"{key} scheduling exceeds the maximum safe integer")
            window = by_key[key]["owner_review_window"]
            if window is not None and minute + by_key[key]["duration_minutes"] > window["closes_at_minute"]:
                _fail("OWNER_REVIEW_WINDOW_MISSED", f"{key} cannot finish in its owner review window")
        candidates = [
            key for key in ready
            if _owner_windows_feasible(
                by_key, predecessors, unscheduled, finishes, running, minute,
                last_start, lane_cap, start_gap_minutes, key,
            )
        ]
        if available and candidates and launch_unlocked:
            key = candidates[0]
            duration = by_key[key]["duration_minutes"]
            window = by_key[key]["owner_review_window"]
            if window is not None and minute + duration > window["closes_at_minute"]:
                _fail("OWNER_REVIEW_WINDOW_MISSED", f"{key} cannot finish in its owner review window")
            starts[key] = minute
            finishes[key] = minute + duration
            running[key] = finishes[key]
            unscheduled.remove(key)
            last_start = minute
            if start_gap_minutes == 0:
                continue
        if unscheduled:
            next_times = [end for end in running.values() if end > minute]
            next_times.extend(value for value in future if value > minute)
            if available and ready and last_start is not None:
                next_times.append(last_start + start_gap_minutes)
            if not next_times:
                _fail("INVALID_PLAN", "scheduler made no progress")
            minute = min(value for value in next_times if value > minute)
    return starts, finishes


def _serial_metadata(keys, adjacency, finishes):
    predecessors = _predecessors(keys, adjacency)
    undirected = {key: set(adjacency[key]) | predecessors[key] for key in keys}
    components = []
    unseen = set(keys)
    while unseen:
        start = min(unseen)
        component = set()
        pending = [start]
        while pending:
            key = pending.pop()
            if key in component:
                continue
            component.add(key)
            pending.extend(undirected[key] - component)
        unseen -= component
        components.append(component)
    result = {}
    for index, component in enumerate(sorted(components, key=lambda item: min(item)), 1):
        path_id = f"serial-{index:03d}"
        for key in component:
            prior = predecessors[key]
            predecessor = max(prior, key=lambda item: (finishes[item], item)) if prior else None
            result[key] = (path_id, predecessor)
    return result


def _independence(key, by_key):
    if by_key[key]["dependencies"] or by_key[key]["blockers"]:
        return False
    paths = {_path_identity(path) for path in by_key[key]["write_paths"]}
    return not any(
        other != key and ticket["in_lane"]
        and paths.intersection(_path_identity(path) for path in ticket["write_paths"])
        for other, ticket in by_key.items()
    )


def _normalize_lane_observations(value, observed_minute):
    if not isinstance(value, list):
        _fail("INVALID_FIELD", "lane_observations must be a list")
    result = []
    lanes = set()
    for observation in value:
        _check_fields(observation, LANE_OBSERVATION_FIELDS, "lane observation")
        lane = observation["owner_lane"]
        idle_since = observation["idle_since_minute"]
        _check_string(lane, "owner_lane")
        if not _is_int(idle_since) or idle_since > observed_minute:
            _fail("INVALID_FIELD", "idle_since_minute must not exceed observed_minute")
        if lane in lanes:
            _fail("INVALID_FIELD", "lane observations must have unique owner_lane values")
        lanes.add(lane)
        result.append({"owner_lane": lane, "idle_since_minute": idle_since})
    return sorted(result, key=lambda item: item["owner_lane"])


def _idle_lane_status(tickets, observation, observed_minute):
    lane = observation["owner_lane"]
    idle_since = observation["idle_since_minute"]
    queued = [ticket for ticket in tickets if ticket["owner_lane"] == lane and not ticket["in_lane"]]
    ready = []
    reasons = set()
    for ticket in queued:
        ticket_reasons = set(ticket["blockers"])
        if ticket["dependencies"] or ticket["serial_predecessor"] is not None:
            ticket_reasons.add("PREDECESSOR_UNMERGED")
        if ticket_reasons:
            reasons.update(ticket_reasons)
            if ticket["idle_lane_reason"] is not None:
                reasons.add(ticket["idle_lane_reason"])
        else:
            ready.append(ticket["key"])
    ready.sort()
    reasons = sorted(reasons)
    idle_minutes = observed_minute - idle_since
    failed = idle_minutes > 10 and bool(ready)
    if failed and not reasons:
        reasons = ["PLAN_UNREADABLE"]
    elif not ready and not reasons:
        reasons = ["NO_READY_TICKET"]
    return {
        "owner_lane": lane,
        "idle_since_minute": idle_since,
        "idle_minutes": idle_minutes,
        "status_verdict": "FAIL" if failed else "OK",
        "ready_tickets": ready,
        "idle_lane_reasons": reasons,
    }


def _render_mermaid(plan):
    lines = ["flowchart LR"]
    for ticket in sorted(plan["tickets"], key=lambda item: item["key"]):
        node = re.sub(r"[^A-Za-z0-9_]", "_", ticket["key"])
        lines.append(f'  {node}["{ticket["key"]} · t={ticket["earliest_start_minute"]}"]')
    for edge in plan["edges"]:
        before = re.sub(r"[^A-Za-z0-9_]", "_", edge["from"])
        after = re.sub(r"[^A-Za-z0-9_]", "_", edge["to"])
        lines.append(f"  {before} -->|{edge['kind']}| {after}")
    return "\n".join(lines) + "\n"


def build_ticket_plan(
        tickets, *, lane_cap, start_gap_minutes, tier3_round_cap, generator_order,
        observed_minute=0, lane_observations=None):
    """Build a deterministic ticket-plan record from a reviewed snapshot."""
    if not isinstance(tickets, list) or not tickets:
        _fail("INVALID_FIELD", "tickets must be a non-empty list")
    if not _is_int(lane_cap, 1):
        _fail("INVALID_FIELD", "lane_cap must be a positive integer")
    if not _is_int(start_gap_minutes):
        _fail("INVALID_FIELD", "start_gap_minutes must be a non-negative integer")
    if not _is_int(tier3_round_cap, 1) or tier3_round_cap > 3:
        _fail("TIER_ROUND_CAP_VIOLATION", "tier3_round_cap must be between 1 and the frozen maximum of 3")
    if not _is_int(observed_minute):
        _fail("INVALID_FIELD", "observed_minute must be a non-negative integer")
    observations = _normalize_lane_observations(
        [] if lane_observations is None else lane_observations,
        observed_minute,
    )
    _check_string_list(generator_order, "generator_order", keys=True)
    normalized = [_normalize_ticket(ticket, tier3_round_cap) for ticket in tickets]
    keys = [ticket["key"] for ticket in normalized]
    if len(keys) != len(set(keys)):
        _fail("INVALID_FIELD", "ticket keys must be unique")
    by_key, adjacency, edges, order_slot = _graph(normalized, generator_order)
    critical_path, scores = _critical_path(by_key, adjacency)
    starts, finishes = _schedule(by_key, adjacency, lane_cap, start_gap_minutes, scores)
    serial = _serial_metadata(set(by_key), adjacency, finishes)
    positions = {key: index for index, key in enumerate(critical_path, 1)}
    output_tickets = []
    for key in sorted(by_key):
        item = deepcopy(by_key[key])
        item.update({
            "earliest_start_minute": starts[key],
            "critical_path": key in positions,
            "critical_path_position": positions.get(key),
            "serial_path_id": serial[key][0],
            "serial_predecessor": serial[key][1],
            "independent": _independence(key, by_key),
            "generator_order_slot": order_slot.get(key) if _generator_ticket(item) else None,
            "prestage_allowed": bool(item["blockers"]) and set(item["blockers"]) <= PRESTAGE_BLOCKERS,
        })
        output_tickets.append(item)
    plan = {
        "format": "awf-ticket-plan-1",
        "schema_version": 3,
        "lane_cap": lane_cap,
        "start_gap_minutes": start_gap_minutes,
        "tier3_round_cap": tier3_round_cap,
        "generator_order": list(generator_order),
        "critical_path": critical_path,
        "duration_minutes": max(finishes.values()),
        "tickets": output_tickets,
        "edges": edges,
        "observed_minute": observed_minute,
        "lane_status": [
            _idle_lane_status(output_tickets, observation, observed_minute)
            for observation in observations
        ],
        "mermaid": "",
    }
    plan["mermaid"] = _render_mermaid(plan)
    _check_output_structure(plan)
    return plan


def _check_output_structure(plan):
    _check_fields(plan, PLAN_FIELDS, "ticket plan")
    if plan["format"] != "awf-ticket-plan-1" or plan["schema_version"] != 3:
        _fail("INVALID_FIELD", "ticket plan format or schema_version is invalid")
    if not _is_int(plan["lane_cap"], 1) or not _is_int(plan["start_gap_minutes"]):
        _fail("INVALID_FIELD", "ticket plan scheduling limits are invalid")
    if not _is_int(plan["tier3_round_cap"], 1) or plan["tier3_round_cap"] > 3:
        _fail("TIER_ROUND_CAP_VIOLATION", "tier3_round_cap exceeds the frozen maximum of 3")
    _check_string_list(plan["generator_order"], "generator_order", keys=True)
    _check_string_list(plan["critical_path"], "critical_path", keys=True, nonempty=True)
    if not _is_int(plan["duration_minutes"], 1):
        _fail("INVALID_FIELD", "duration_minutes must be positive")
    if not isinstance(plan["tickets"], list) or not plan["tickets"]:
        _fail("INVALID_FIELD", "tickets must be a non-empty list")
    for ticket in plan["tickets"]:
        _check_fields(ticket, OUTPUT_TICKET_FIELDS, "planned ticket")
        _normalize_ticket({field: ticket[field] for field in INPUT_FIELDS}, plan["tier3_round_cap"])
        if not _is_int(ticket["earliest_start_minute"]):
            _fail("INVALID_FIELD", "earliest_start_minute must be non-negative")
        _check_lane_fill(ticket)
    if not isinstance(plan["edges"], list):
        _fail("INVALID_FIELD", "edges must be a list")
    for edge in plan["edges"]:
        _check_fields(edge, EDGE_FIELDS, "edge")
        if not _valid_key(edge["from"]) or not _valid_key(edge["to"]):
            _fail("INVALID_FIELD", "edge endpoints must be ticket keys")
        if not isinstance(edge["kind"], str) or edge["kind"] not in {"dependency", "file_overlap", "generator_order"}:
            _fail("INVALID_FIELD", "edge kind is invalid")
        _check_string_list(edge["paths"], "edge paths")
        for path in edge["paths"]:
            _check_path(path)
    if not _is_int(plan["observed_minute"]):
        _fail("INVALID_FIELD", "observed_minute must be a non-negative integer")
    if not isinstance(plan["lane_status"], list):
        _fail("INVALID_FIELD", "lane_status must be a list")
    lanes = set()
    for status in plan["lane_status"]:
        _check_fields(status, LANE_STATUS_FIELDS, "lane status")
        _check_string(status["owner_lane"], "owner_lane")
        if status["owner_lane"] in lanes:
            _fail("INVALID_FIELD", "lane_status must have unique owner_lane values")
        lanes.add(status["owner_lane"])
        if (not _is_int(status["idle_since_minute"])
                or status["idle_since_minute"] > plan["observed_minute"]
                or not _is_int(status["idle_minutes"])):
            _fail("INVALID_FIELD", "lane status idle minutes are invalid")
        if (not isinstance(status["status_verdict"], str)
                or status["status_verdict"] not in STATUS_VERDICTS):
            _fail("INVALID_FIELD", "status_verdict must be OK or FAIL")
        _check_string_list(status["ready_tickets"], "ready_tickets", keys=True)
        _check_blockers(status["idle_lane_reasons"])
    _check_string(plan["mermaid"], "mermaid")


def validate_ticket_plan(plan):
    """Validate structure and all derived fields by deterministic replay."""
    _check_output_structure(plan)
    rebuilt = build_ticket_plan(
        [{field: ticket[field] for field in INPUT_FIELDS} for ticket in plan["tickets"]],
        lane_cap=plan["lane_cap"],
        start_gap_minutes=plan["start_gap_minutes"],
        tier3_round_cap=plan["tier3_round_cap"],
        generator_order=plan["generator_order"],
        observed_minute=plan["observed_minute"],
        lane_observations=[
            {field: status[field] for field in LANE_OBSERVATION_FIELDS}
            for status in plan["lane_status"]
        ],
    )
    if rebuilt != plan:
        _fail("INVALID_PLAN", "derived ticket-plan fields do not match deterministic replay")
    return deepcopy(plan)


def _runtime_string_set(value, field, *, keys=False):
    _check_string_list(value, field, keys=keys)
    return set(value)


def refill_rank(
        plan, *, owner_lanes, merged_tickets, live_write_paths,
        generator_busy, hold, start_lock_free):
    """Return a deterministic, side-effect-free lane refill decision.

    The caller supplies observed gates; this function ranks only tickets that
    are ready, independently writable, and not already occupying a lane.
    """
    checked = validate_ticket_plan(plan)
    owners = _runtime_string_set(owner_lanes, "owner_lanes")
    merged = _runtime_string_set(merged_tickets, "merged_tickets", keys=True)
    _check_string_list(live_write_paths, "live_write_paths")
    for path in live_write_paths:
        _check_path(path)
    live_paths = {_path_identity(path) for path in live_write_paths}
    if type(generator_busy) is not bool or type(hold) is not bool or type(start_lock_free) is not bool:
        _fail("INVALID_FIELD", "refill gates must be boolean")

    incomplete_generator_slots = {
        ticket["generator_order_slot"]
        for ticket in checked["tickets"]
        if ticket["generator_order_slot"] is not None and ticket["key"] not in merged
    }
    earliest_generator_slot = min(incomplete_generator_slots, default=None)
    ordering_predecessors = {ticket["key"]: set() for ticket in checked["tickets"]}
    for edge in checked["edges"]:
        ordering_predecessors[edge["to"]].add(edge["from"])
    ready = []
    blocked = []
    for ticket in checked["tickets"]:
        if ticket["in_lane"] or ticket["key"] in merged:
            continue
        reasons = set(ticket["blockers"])
        if any(predecessor not in merged for predecessor in ordering_predecessors[ticket["key"]]):
            reasons.add("PREDECESSOR_UNMERGED")
        if ticket["owner_lane"] not in owners:
            reasons.add("OWNERSHIP_CONFLICT")
        paths = {_path_identity(path) for path in ticket["write_paths"]}
        if paths & live_paths:
            reasons.add("FILE_CLASH")
        slot = ticket["generator_order_slot"]
        if slot is not None and (generator_busy or slot != earliest_generator_slot):
            reasons.add("GENERATOR_ORDER_BUSY")
        if hold:
            reasons.add("HOLD")
        if not start_lock_free:
            reasons.add("START_GAP_PENDING")
        if reasons:
            blocked.append({"key": ticket["key"], "reasons": sorted(reasons)})
        else:
            ready.append(ticket)

    ready.sort(key=lambda ticket: (
        0 if ticket["critical_path"] else 1,
        ticket["critical_path_position"] or 9007199254740991,
        ticket["generator_order_slot"] or 9007199254740991,
        ticket["earliest_start_minute"],
        ticket["key"],
    ))
    blocked.sort(key=lambda item: item["key"])
    if ready:
        reason = None
    elif not start_lock_free:
        reason = "START_GAP_PENDING"
    elif hold:
        reason = "HOLD"
    else:
        all_reasons = sorted({reason for item in blocked for reason in item["reasons"]})
        reason = all_reasons[0] if all_reasons else "NO_READY_TICKET"
    return {
        "selected_ticket": ready[0]["key"] if ready else None,
        "idle_lane_reason": reason,
        "ranked_tickets": [ticket["key"] for ticket in ready],
        "blocked_tickets": blocked,
    }


def serialize_ticket_plan(plan):
    """Return canonical, byte-deterministic JSON for a validated plan."""
    checked = validate_ticket_plan(plan)
    return json.dumps(checked, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def render_mermaid(plan):
    """Render the stable documentation diagram for a validated plan."""
    checked = validate_ticket_plan(plan)
    return _render_mermaid(checked)
