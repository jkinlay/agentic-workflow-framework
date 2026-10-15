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
    "HOLD", "CAP_REACHED", "PLAN_UNREADABLE",
}
LANE_CLASSES = {"critical", "feeder", "controller"}
INPUT_FIELDS = {
    "key", "dependencies", "write_paths", "duration_minutes", "risk_tier",
    "round_cap", "owner_review_window", "lane_class", "owner_lane",
    "idle_lane_reason", "phase", "status", "in_lane",
}
DERIVED_FIELDS = {
    "earliest_start_minute", "critical_path", "critical_path_position",
    "serial_path_id", "serial_predecessor", "independent",
    "generator_order_slot",
}
OUTPUT_TICKET_FIELDS = INPUT_FIELDS | DERIVED_FIELDS
PLAN_FIELDS = {
    "format", "schema_version", "lane_cap", "start_gap_minutes",
    "tier3_round_cap", "generator_order", "critical_path",
    "duration_minutes", "tickets", "edges", "mermaid",
}
EDGE_FIELDS = {"from", "to", "kind", "paths"}


class PlanningGraphError(ValueError):
    """A ticket plan cannot be constructed or trusted."""

    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _fail(reason, detail):
    raise PlanningGraphError(reason, detail)


def _is_int(value, minimum=0):
    return type(value) is int and value >= minimum


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
    if (not isinstance(value, list) or (nonempty and not value)
            or len(value) != len(set(value))):
        _fail("INVALID_FIELD", f"{field} must be a unique list")
    for item in value:
        if (not _valid_key(item)) if keys else (not isinstance(item, str) or not item):
            _fail("INVALID_FIELD", f"{field} contains an invalid value")


def _check_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or value.startswith("/") or re.match(r"^[A-Za-z]:", value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        _fail("INVALID_FIELD", "write_paths must contain portable repository-relative paths")


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
    if ticket["lane_class"] not in LANE_CLASSES:
        _fail("INVALID_FIELD", "lane_class is not recognized")
    if type(ticket["independent"]) is not bool:
        _fail("INVALID_FIELD", "independent must be boolean")
    slot = ticket["generator_order_slot"]
    if slot is not None and not _is_int(slot, 1):
        _fail("INVALID_FIELD", "generator_order_slot must be 1-based or null")
    _check_string(ticket["owner_lane"], "owner_lane")
    reason = ticket["idle_lane_reason"]
    if reason is not None and reason not in IDLE_LANE_REASONS:
        _fail("UNKNOWN_IDLE_LANE_REASON", f"idle lane reason {reason!r} is not recognized")
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
    ticket["dependencies"] = sorted(ticket["dependencies"])
    ticket["write_paths"] = sorted(ticket["write_paths"])
    if not _is_int(ticket["duration_minutes"], 1):
        _fail("INVALID_FIELD", "duration_minutes must be a positive integer")
    if type(ticket["risk_tier"]) is not int or ticket["risk_tier"] not in {1, 2, 3}:
        _fail("INVALID_RISK_TIER", "risk_tier must be 1, 2, or 3")
    if not _is_int(ticket["round_cap"], 1):
        _fail("TIER_ROUND_CAP_VIOLATION", "round_cap must be a positive integer")
    if ticket["risk_tier"] == 3 and ticket["round_cap"] > tier3_round_cap:
        _fail("TIER_ROUND_CAP_VIOLATION", "Tier 3 round_cap exceeds the reviewed cap")
    _check_window(ticket["owner_review_window"])
    if ticket["lane_class"] not in LANE_CLASSES:
        _fail("INVALID_FIELD", "lane_class is not recognized")
    _check_string(ticket["owner_lane"], "owner_lane")
    reason = ticket["idle_lane_reason"]
    if reason is not None and reason not in IDLE_LANE_REASONS:
        _fail("UNKNOWN_IDLE_LANE_REASON", f"idle lane reason {reason!r} is not recognized")
    _check_string(ticket["phase"], "phase")
    _check_string(ticket["status"], "status")
    if type(ticket["in_lane"]) is not bool:
        _fail("INVALID_FIELD", "in_lane must be boolean")
    return ticket


def _generator_ticket(ticket):
    return any(
        path == "scripts/generate_contracts.py"
        or (path.startswith(".agentic/schemas/") and path.endswith(".schema.json"))
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
    ordered_keys = sorted(keys)
    for index, left in enumerate(ordered_keys):
        for right in ordered_keys[index + 1:]:
            shared = sorted(set(by_key[left]["write_paths"]) & set(by_key[right]["write_paths"]))
            if not shared:
                continue
            if _reachable(left, right, dependency_adjacency):
                before, after = left, right
            elif _reachable(right, left, dependency_adjacency):
                before, after = right, left
            elif left in order_slot and right in order_slot:
                before, after = sorted((left, right), key=lambda key: (order_slot[key], key))
            else:
                before, after = left, right
            precedence[before].add(after)
            edges.append({"from": before, "to": after, "kind": "file_overlap", "paths": shared})

    ordered_generators = sorted(generators, key=lambda key: (order_slot[key], key))
    for before, after in zip(ordered_generators, ordered_generators[1:]):
        precedence[before].add(after)
        edges.append({"from": before, "to": after, "kind": "generator_order", "paths": []})
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


def _schedule(by_key, adjacency, lane_cap, start_gap_minutes, scores):
    predecessors = _predecessors(by_key, adjacency)
    unscheduled = set(by_key)
    starts = {}
    finishes = {}
    running = {}
    minute = 0
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
        ready.sort(key=lambda key: (-scores[key], key))
        for key in ready[:available]:
            duration = by_key[key]["duration_minutes"]
            window = by_key[key]["owner_review_window"]
            if window is not None and minute + duration > window["closes_at_minute"]:
                _fail("OWNER_REVIEW_WINDOW_MISSED", f"{key} cannot finish in its owner review window")
            starts[key] = minute
            finishes[key] = minute + duration
            running[key] = finishes[key]
            unscheduled.remove(key)
        if unscheduled:
            next_times = [end for end in running.values() if end > minute]
            next_times.extend(value for value in future if value > minute)
            if not next_times:
                _fail("INVALID_PLAN", "scheduler made no progress")
            minute = min(next_times)
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
    if by_key[key]["dependencies"]:
        return False
    paths = set(by_key[key]["write_paths"])
    return not any(
        other != key and ticket["in_lane"] and paths.intersection(ticket["write_paths"])
        for other, ticket in by_key.items()
    )


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


def build_ticket_plan(tickets, *, lane_cap, start_gap_minutes, tier3_round_cap, generator_order):
    """Build a deterministic ticket-plan record from a reviewed snapshot."""
    if not isinstance(tickets, list) or not tickets:
        _fail("INVALID_FIELD", "tickets must be a non-empty list")
    if not _is_int(lane_cap, 1):
        _fail("INVALID_FIELD", "lane_cap must be a positive integer")
    if not _is_int(start_gap_minutes):
        _fail("INVALID_FIELD", "start_gap_minutes must be a non-negative integer")
    if not _is_int(tier3_round_cap, 1):
        _fail("TIER_ROUND_CAP_VIOLATION", "tier3_round_cap must be positive")
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
        })
        output_tickets.append(item)
    plan = {
        "format": "awf-ticket-plan-1",
        "schema_version": 1,
        "lane_cap": lane_cap,
        "start_gap_minutes": start_gap_minutes,
        "tier3_round_cap": tier3_round_cap,
        "generator_order": list(generator_order),
        "critical_path": critical_path,
        "duration_minutes": max(finishes.values()),
        "tickets": output_tickets,
        "edges": edges,
        "mermaid": "",
    }
    plan["mermaid"] = _render_mermaid(plan)
    return plan


def _check_output_structure(plan):
    _check_fields(plan, PLAN_FIELDS, "ticket plan")
    if plan["format"] != "awf-ticket-plan-1" or plan["schema_version"] != 1:
        _fail("INVALID_FIELD", "ticket plan format or schema_version is invalid")
    if not _is_int(plan["lane_cap"], 1) or not _is_int(plan["start_gap_minutes"]):
        _fail("INVALID_FIELD", "ticket plan scheduling limits are invalid")
    if not _is_int(plan["tier3_round_cap"], 1):
        _fail("TIER_ROUND_CAP_VIOLATION", "tier3_round_cap must be positive")
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
        if edge["kind"] not in {"dependency", "file_overlap", "generator_order"}:
            _fail("INVALID_FIELD", "edge kind is invalid")
        _check_string_list(edge["paths"], "edge paths")
        for path in edge["paths"]:
            _check_path(path)
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
    )
    if rebuilt != plan:
        _fail("INVALID_PLAN", "derived ticket-plan fields do not match deterministic replay")
    return deepcopy(plan)


def serialize_ticket_plan(plan):
    """Return canonical, byte-deterministic JSON for a validated plan."""
    checked = validate_ticket_plan(plan)
    return json.dumps(checked, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def render_mermaid(plan):
    """Render the stable documentation diagram for a validated plan."""
    checked = validate_ticket_plan(plan)
    return _render_mermaid(checked)
