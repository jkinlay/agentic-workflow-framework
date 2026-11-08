"""Pure Sprint Mode proposals, calendar arithmetic and ranking (record version 1).

This module checks structure and declared provenance against caller-supplied
planning inputs. It authenticates neither owner acceptance nor implementation
truth. A seal is consistency evidence only. No state, adapter or scheduler is
changed here; slices remain verification units inside their parent authority.

Stage 3 generator registration must mirror the closed shapes below as
``sprint-milestone-plan`` / ``awf-sprint-milestone-plan-1``, schema version 1,
including nested additionalProperties=false, nullable predecessor, provenance
enums and plan_sha256. Semantic checks and external bindings remain mandatory.
No acceptance or execution-plan schema is defined by this proposal format.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone as utc_timezone
import heapq
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ValidationError
from .canonical import canonical, fingerprint, timestamp, validate_value

FORMAT = 'awf-sprint-milestone-plan-1'
WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
CALENDAR_FIELDS = frozenset({
    'plan_start', 'resolved_anchor', 'anchor_provenance', 'length_days',
    'timezone', 'review_weekday', 'review_time', 'review_time_provenance',
})
RECORD_FIELDS = frozenset({
    'format', 'schema_version', 'record_id', 'created_at', 'predecessor',
    'project_binding', 'planning_binding', 'calendar', 'planned_finish',
    'pipeline', 'slices', 'milestones', 'plan_sha256',
})
BINDING_FIELDS = frozenset({'project_id', 'repository_id', 'scope_sha256',
                            'accepted_settings_sha256', 'settings_source_commit'})
STRUCTURAL_FIELDS = frozenset({
    'key', 'contract_sha256', 'requirements_sha256', 'scope', 'acceptance_criteria',
    'write_paths', 'dependencies', 'generator_dependencies', 'risk_tier', 'closure_standard',
})
SLICE_FIELDS = frozenset({
    'id', 'parent_ticket', 'contract_sha256', 'requirements_sha256', 'acceptance_criteria',
    'included_scope', 'remaining_scope', 'dependencies', 'generator_dependencies',
    'risk_tier', 'closure_standard', 'write_paths', 'completion_predicates', 'artifact_refs',
})
INTERVAL_FIELDS = frozenset({'start_date', 'end_date', 'review_date', 'review_at'})
MILESTONE_FIELDS = INTERVAL_FIELDS | {
    'id', 'ordinal', 'goal', 'targets', 'prerequisites', 'pipeline_runs', 'completion_predicates',
}


class SprintPlanningError(ValidationError):
    """A stable refusal reason with a sanitized structural diagnostic."""

    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f'{reason}: {detail}')


def _fail(reason, detail):
    raise SprintPlanningError(reason, detail)


def _closed(value, fields, context):
    if not isinstance(value, dict):
        _fail('INVALID_FIELD', f'{context} must be an object')
    if set(value) - set(fields):
        _fail('UNKNOWN_FIELD', f'{context} has undeclared fields')
    if set(fields) - set(value):
        _fail('MISSING_REQUIRED_FIELD', f'{context} lacks required fields')


def _text(value, context):
    if not isinstance(value, str) or not value.strip():
        _fail('INVALID_FIELD', f'{context} must be nonempty text')


def _strings(value, context, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        _fail('INVALID_FIELD', f'{context} must be a list')
    for item in value:
        _text(item, context)
    if len(set(value)) != len(value):
        _fail('DUPLICATE_REFERENCE', f'{context} must be unique')


def _objects(value, context, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        _fail('INVALID_FIELD', f'{context} must be an object list')
    if any(not isinstance(item, dict) for item in value):
        _fail('INVALID_FIELD', f'{context} must contain objects')


def _digest(value, context, length=64):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', value):
        _fail('INVALID_DIGEST', f'{context} must be a lowercase digest')


def _positive(value, context):
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        _fail('INVALID_FIELD', f'{context} must be a positive safe integer')


def _path(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or any(c in value for c in '*?[]{}')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _fail('INVALID_PATH', 'write paths must be exact portable repository-relative files')


def _paths(value):
    _strings(value, 'write_paths', nonempty=False)
    for item in value:
        _path(item)
    if len({item.casefold() for item in value}) != len(value):
        _fail('INVALID_PATH', 'case aliases are not exact path subsets')


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        _fail('INVALID_CALENDAR', 'local date must use ISO date syntax')
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail('INVALID_CALENDAR', 'local date is invalid')


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        _fail('INVALID_CALENDAR', 'review time must use HH:MM')
    try:
        return time.fromisoformat(value)
    except ValueError:
        _fail('INVALID_CALENDAR', 'review time is invalid')


def _zone(value):
    _text(value, 'timezone')
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        _fail('INVALID_TIMEZONE', 'timezone must be an available IANA zone')


def _add_days(value, count):
    try:
        return value + timedelta(days=count)
    except (OverflowError, ValueError):
        _fail('CALENDAR_OVERFLOW', 'calendar arithmetic exceeds supported dates')


def local_datetime_utc(local_date, local_time, timezone):
    """Convert after local-date arithmetic; refuse DST gaps and ambiguous folds."""
    naive = datetime.combine(_date(local_date), _time(local_time))
    zone = _zone(timezone)
    candidates = set()
    try:
        for fold in (0, 1):
            instant = naive.replace(tzinfo=zone, fold=fold).astimezone(utc_timezone.utc)
            if instant.astimezone(zone).replace(tzinfo=None) == naive:
                candidates.add(instant)
    except (OverflowError, ValueError):
        _fail('CALENDAR_OVERFLOW', 'UTC conversion exceeds supported dates')
    if not candidates:
        _fail('NONEXISTENT_LOCAL_TIME', 'review falls in a timezone clock gap')
    if len(candidates) != 1:
        _fail('AMBIGUOUS_LOCAL_TIME', 'review falls in a timezone clock fold')
    return candidates.pop()


def _check_calendar(value):
    _closed(value, CALENDAR_FIELDS, 'calendar')
    start, anchor = _date(value['plan_start']), _date(value['resolved_anchor'])
    _positive(value['length_days'], 'length_days')
    _zone(value['timezone'])
    _time(value['review_time'])
    if value['review_weekday'] not in WEEKDAYS:
        _fail('INVALID_CALENDAR', 'review weekday is unknown')
    for field in ('anchor_provenance', 'review_time_provenance'):
        if value[field] not in ('supplied', 'assumed'):
            _fail('INVALID_CALENDAR', 'calendar provenance must be supplied or assumed')
    if (value['anchor_provenance'] == 'assumed'
            and anchor != _add_days(start, (-start.weekday()) % 7)):
        _fail('CALENDAR_MISMATCH', 'assumed anchor must be resolved from plan start once')


def resolve_calendar(plan_start, *, first_sprint_start=None, length_days=14,
                     timezone='Europe/London', review_weekday='friday',
                     review_time, review_time_provenance):
    """Resolve once. The caller supplies the accepted calendar's assumed time.

    There is deliberately no guessed operating-calendar API or time default.
    A supplied anchor may precede plan start; sprint ordinals start at that
    anchor, and work before the anchor is not prohibited by this module.
    """
    start = _date(plan_start)
    anchor = (_add_days(start, (-start.weekday()) % 7).isoformat()
              if first_sprint_start is None else first_sprint_start)
    result = dict(plan_start=plan_start, resolved_anchor=anchor,
                  anchor_provenance='assumed' if first_sprint_start is None else 'supplied',
                  length_days=length_days, timezone=timezone, review_weekday=review_weekday,
                  review_time=review_time, review_time_provenance=review_time_provenance)
    _check_calendar(result)
    sprint_interval(result, 1)
    return result


def sprint_interval(calendar, ordinal):
    """Half-open local dates; review is the last selected weekday inside them."""
    _check_calendar(calendar)
    _positive(ordinal, 'ordinal')
    start = _add_days(_date(calendar['resolved_anchor']), (ordinal - 1) * calendar['length_days'])
    end = _add_days(start, calendar['length_days'])
    last = _add_days(end, -1)
    review = _add_days(last, -((last.weekday() - WEEKDAYS.index(calendar['review_weekday'])) % 7))
    if review < start:
        _fail('NO_REVIEW_DATE', 'sprint has no occurrence of the review weekday')
    instant = local_datetime_utc(review.isoformat(), calendar['review_time'], calendar['timezone'])
    return dict(start_date=start.isoformat(), end_date=end.isoformat(), review_date=review.isoformat(),
                review_at=instant.isoformat().replace('+00:00', 'Z'))


def sprints_through(calendar, planned_finish):
    """Cover the exclusive planned finish; at least one sprint is required."""
    _check_calendar(calendar)
    finish, anchor = _date(planned_finish), _date(calendar['resolved_anchor'])
    if finish <= anchor or finish <= _date(calendar['plan_start']):
        _fail('INVALID_CALENDAR', 'planned finish must follow plan start and anchor')
    count = ((finish - anchor).days + calendar['length_days'] - 1) // calendar['length_days']
    return [sprint_interval(calendar, n) for n in range(1, count + 1)]


def _ticket_map(tickets):
    _objects(tickets, 'tickets')
    by_key = {}
    for ticket in tickets:
        _closed(ticket, STRUCTURAL_FIELDS | {'duration_minutes', 'status'}, 'ticket authority')
        _text(ticket['key'], 'ticket key')
        if ticket['key'] in by_key:
            _fail('DUPLICATE_REFERENCE', 'ticket key is repeated')
        for field in ('contract_sha256', 'requirements_sha256'):
            _digest(ticket[field], field)
        for field in ('scope', 'acceptance_criteria', 'dependencies', 'generator_dependencies'):
            _strings(ticket[field], field, nonempty=field in ('scope', 'acceptance_criteria'))
        _paths(ticket['write_paths'])
        if type(ticket['risk_tier']) is not int or ticket['risk_tier'] not in (1, 2, 3):
            _fail('INVALID_FIELD', 'risk tier is unknown')
        if ticket['closure_standard'] not in ('FULL', 'DECLARED_LIMITATIONS'):
            _fail('INVALID_FIELD', 'closure standard is unknown')
        _positive(ticket['duration_minutes'], 'duration_minutes')
        _text(ticket['status'], 'status')
        by_key[ticket['key']] = ticket
    return by_key


def _topology(tickets):
    by_key = _ticket_map(tickets)
    parents = {key: set(t['dependencies']) | set(t['generator_dependencies']) for key, t in by_key.items()}
    children = {key: set() for key in by_key}
    for key, dependencies in parents.items():
        if dependencies - set(by_key):
            _fail('UNKNOWN_TICKET', 'dependency references an unknown ticket')
        for dependency in dependencies:
            children[dependency].add(key)
    counts = {key: len(value) for key, value in parents.items()}
    ready = [key for key, count in counts.items() if not count]
    heapq.heapify(ready)
    order = []
    while ready:
        key = heapq.heappop(ready)
        order.append(key)
        for child in sorted(children[key]):
            counts[child] -= 1
            if not counts[child]:
                heapq.heappush(ready, child)
    if len(order) != len(by_key):
        _fail('DEPENDENCY_CYCLE', 'ticket graph must be acyclic')
    return by_key, parents, children, order


def structural_graph_digest(tickets):
    """Bind authority and generator precedence, excluding mutable progress."""
    by_key, _, _, _ = _topology(tickets)
    projected = []
    for key in sorted(by_key):
        ticket = {field: deepcopy(by_key[key][field]) for field in STRUCTURAL_FIELDS}
        for field, value in ticket.items():
            if isinstance(value, list):
                ticket[field] = sorted(value)
        projected.append(ticket)
    return fingerprint('sprint-structural-graph', projected)


def source_plan_digest(source_ticket_plan):
    """Bind the entire already-sealed source record, without a reverse reference.

    The caller must verify the source record through its own legacy validator
    and acceptance route. This fingerprint authenticates neither of those.
    """
    from .planning import verify_plan_record

    try:
        verify_plan_record(source_ticket_plan)
    except (ValidationError, TypeError, ValueError, KeyError):
        _fail('SOURCE_PLAN_SEAL', 'source ticket plan must have a valid existing planning seal')
    return fingerprint('sprint-source-ticket-plan', source_ticket_plan)


def _closure(parents, targets):
    _strings(targets, 'target tickets')
    if set(targets) - set(parents):
        _fail('UNKNOWN_TICKET', 'target references an unknown ticket')
    seen, pending = set(), list(targets)
    while pending:
        key = pending.pop()
        if key not in seen:
            seen.add(key)
            pending.extend(parents[key])
    return sorted(seen)


def prerequisite_closure(tickets, target_keys):
    _, parents, _, _ = _topology(tickets)
    return _closure(parents, target_keys)


def validate_slice(value, tickets):
    """Exact parent subsets; never a dispatch or merge authorization."""
    by_key = _ticket_map(tickets)
    _closed(value, SLICE_FIELDS, 'slice')
    _text(value['id'], 'slice id')
    _text(value['parent_ticket'], 'slice parent')
    if value['parent_ticket'] not in by_key:
        _fail('UNKNOWN_TICKET', 'slice parent is unknown')
    parent = by_key[value['parent_ticket']]
    for field in ('contract_sha256', 'requirements_sha256'):
        _digest(value[field], field)
        if value[field] != parent[field]:
            _fail('SLICE_PARENT_BINDING', 'slice has a different parent contract or requirements')
    for field in ('acceptance_criteria', 'included_scope', 'remaining_scope', 'completion_predicates', 'artifact_refs'):
        _strings(value[field], field, nonempty=field != 'remaining_scope')
    if set(value['acceptance_criteria']) - set(parent['acceptance_criteria']):
        _fail('UNKNOWN_CRITERION', 'slice criterion is outside parent authority')
    included, remaining, scope = set(value['included_scope']), set(value['remaining_scope']), set(parent['scope'])
    if (included | remaining) - scope:
        _fail('UNKNOWN_SCOPE', 'slice scope is outside parent authority')
    if included & remaining or included | remaining != scope:
        _fail('SLICE_SCOPE_PARTITION', 'included and remaining scope must partition parent scope')
    _paths(value['write_paths'])
    if set(value['write_paths']) - set(parent['write_paths']):
        _fail('SLICE_WRITE_PATHS', 'slice paths must be an exact parent subset')
    for field in ('dependencies', 'generator_dependencies'):
        _strings(value[field], field, nonempty=False)
        if set(value[field]) != set(parent[field]):
            _fail('SLICE_INHERITANCE', 'slice must retain parent prerequisites')
    for field in ('risk_tier', 'closure_standard'):
        if type(value[field]) is not type(parent[field]) or value[field] != parent[field]:
            _fail('SLICE_INHERITANCE', 'slice must retain parent risk and review requirements')
    return deepcopy(value)


def _pipeline(pipeline, by_key):
    _closed(pipeline, {'pipeline_id', 'entry_stage', 'essential_stages', 'terminal_artifacts', 'connections', 'stages'}, 'pipeline')
    _text(pipeline['pipeline_id'], 'pipeline id')
    _text(pipeline['entry_stage'], 'entry stage')
    _strings(pipeline['essential_stages'], 'essential stages')
    _objects(pipeline['stages'], 'stages')
    stages = {}
    for stage in pipeline['stages']:
        _closed(stage, {'id', 'ticket_key', 'scope_refs', 'acceptance_criteria', 'kind', 'implementation'}, 'stage')
        _text(stage['id'], 'stage id')
        if stage['id'] in stages:
            _fail('DUPLICATE_REFERENCE', 'stage id is repeated')
        _text(stage['ticket_key'], 'stage ticket')
        if stage['ticket_key'] not in by_key:
            _fail('UNKNOWN_TICKET', 'pipeline stage has an unknown ticket')
        parent = by_key[stage['ticket_key']]
        _strings(stage['scope_refs'], 'stage scope')
        _strings(stage['acceptance_criteria'], 'stage criteria')
        if set(stage['scope_refs']) - set(parent['scope']):
            _fail('UNKNOWN_SCOPE', 'stage scope is outside ticket authority')
        if set(stage['acceptance_criteria']) - set(parent['acceptance_criteria']):
            _fail('UNKNOWN_CRITERION', 'stage criterion is outside ticket authority')
        if stage['kind'] not in ('foundation', 'execution'):
            _fail('INVALID_FIELD', 'stage kind is unknown')
        implementation = stage['implementation']
        _closed(implementation, {'kind', 'reference', 'sha256'}, 'implementation')
        if implementation['kind'] not in ('real', 'mock', 'stub', 'fixture', 'unknown'):
            _fail('UNKNOWN_IMPLEMENTATION_PROVENANCE', 'implementation kind is unknown')
        try:
            _text(implementation['reference'], 'implementation reference')
            _digest(implementation['sha256'], 'implementation sha256')
        except SprintPlanningError:
            _fail('UNKNOWN_IMPLEMENTATION_PROVENANCE', 'stage lacks a concrete implementation reference and digest')
        stages[stage['id']] = stage
    if ({pipeline['entry_stage']} | set(pipeline['essential_stages'])) - set(stages):
        _fail('DISCONNECTED_PIPELINE', 'entry or essential stage is unknown')
    _objects(pipeline['connections'], 'connections', nonempty=False)
    edges = set()
    for edge in pipeline['connections']:
        _closed(edge, {'source', 'target'}, 'stage connection')
        _text(edge['source'], 'connection source')
        _text(edge['target'], 'connection target')
        if edge['source'] not in stages or edge['target'] not in stages:
            _fail('DISCONNECTED_PIPELINE', 'connection references an unknown stage')
        pair = edge['source'], edge['target']
        if pair in edges:
            _fail('DUPLICATE_REFERENCE', 'connection is repeated')
        edges.add(pair)
    _objects(pipeline['terminal_artifacts'], 'terminal artifacts')
    terminals = {}
    for terminal in pipeline['terminal_artifacts']:
        _closed(terminal, {'id', 'producer_stage'}, 'terminal artifact')
        _text(terminal['id'], 'artifact id')
        _text(terminal['producer_stage'], 'terminal producer')
        if terminal['id'] in terminals:
            _fail('DUPLICATE_REFERENCE', 'terminal artifact is repeated')
        if terminal['producer_stage'] not in stages:
            _fail('TERMINAL_ARTIFACT', 'terminal producer is unknown')
        terminals[terminal['id']] = terminal['producer_stage']
    return stages, edges, terminals


def _reachable(seeds, edges):
    seen, pending = set(), list(seeds)
    adjacency = {}
    for left, right in edges:
        adjacency.setdefault(left, []).append(right)
    while pending:
        key = pending.pop()
        if key not in seen:
            seen.add(key)
            pending.extend(adjacency.get(key, []))
    return seen


def _run(run, pipeline, stages, edges, terminals, closure, target_scopes):
    _closed(run, {'stage_ids', 'artifacts'}, 'pipeline run')
    _strings(run['stage_ids'], 'run stages')
    selected = set(run['stage_ids'])
    if selected - set(stages):
        _fail('DISCONNECTED_PIPELINE', 'run includes an unknown stage')
    if not any(stages[key]['kind'] == 'execution' for key in selected):
        _fail('FOUNDATION_ONLY', 'run contains only foundation layers')
    for key in selected:
        stage = stages[key]
        kind = stage['implementation']['kind']
        if kind == 'unknown':
            _fail('UNKNOWN_IMPLEMENTATION_PROVENANCE', 'run lacks known implementation provenance')
        if kind != 'real':
            _fail('MOCK_ONLY', 'demonstration route includes a mock, stub or fixture')
        if stage['ticket_key'] not in closure:
            _fail('PREREQUISITE_CLOSURE', 'run stage is outside milestone work')
        if stage['ticket_key'] in target_scopes:
            scope, criteria = target_scopes[stage['ticket_key']]
            if set(stage['scope_refs']) - scope or set(stage['acceptance_criteria']) - criteria:
                _fail('SLICE_PIPELINE_SCOPE', 'run exceeds included slice scope or criteria')
    required = {pipeline['entry_stage']} | set(pipeline['essential_stages']) | set(terminals.values())
    route_edges = {(left, right) for left, right in edges if left in selected and right in selected}
    forward = _reachable([pipeline['entry_stage']], route_edges)
    reverse = _reachable(terminals.values(), {(right, left) for left, right in route_edges})
    if required - selected or selected - (forward & reverse):
        _fail('DISCONNECTED_PIPELINE', 'all stages must connect entry to a required terminal artifact')
    _objects(run['artifacts'], 'run artifacts')
    artifacts = {}
    for artifact in run['artifacts']:
        _closed(artifact, {'id', 'producer_stage', 'reference', 'demonstrate', 'completion_predicates'}, 'artifact')
        for field in ('id', 'producer_stage', 'reference', 'demonstrate'):
            _text(artifact[field], field)
        _strings(artifact['completion_predicates'], 'artifact completion predicates')
        if artifact['id'] in artifacts:
            _fail('DUPLICATE_REFERENCE', 'artifact id is repeated')
        if artifact['producer_stage'] not in selected:
            _fail('TERMINAL_ARTIFACT', 'artifact producer is outside demonstrated route')
        artifacts[artifact['id']] = artifact['producer_stage']
    if any(artifacts.get(key) != producer for key, producer in terminals.items()):
        _fail('TERMINAL_ARTIFACT', 'required real terminal artifact is missing or misattributed')
    return set(artifacts)


def _validate_body(record, *, tickets, source_ticket_plan, pipeline, bindings):
    try:
        validate_value(record)
    except ValidationError:
        _fail('INVALID_FIELD', 'record must contain canonical JSON values')
    _closed(record, RECORD_FIELDS, 'milestone plan')
    if record['format'] != FORMAT or type(record['schema_version']) is not int or record['schema_version'] != 1:
        _fail('UNSUPPORTED_VERSION', 'milestone record version is unsupported')
    _text(record['record_id'], 'record id')
    try:
        timestamp(record['created_at'])
    except ValidationError:
        _fail('INVALID_FIELD', 'creation time must be RFC3339')
    if record['predecessor'] is not None:
        _closed(record['predecessor'], {'record_id', 'plan_sha256'}, 'predecessor')
        _text(record['predecessor']['record_id'], 'predecessor id')
        _digest(record['predecessor']['plan_sha256'], 'predecessor digest')
        if record['predecessor']['record_id'] == record['record_id']:
            _fail('INVALID_FIELD', 'proposal cannot precede itself')
    by_key, parents, _, _ = _topology(tickets)
    project = record['project_binding']
    _closed(project, BINDING_FIELDS | {'contracts'}, 'project binding')
    _closed(bindings, BINDING_FIELDS, 'expected project binding')
    for field in ('project_id', 'repository_id'):
        _text(project[field], field)
    for field in ('scope_sha256', 'accepted_settings_sha256'):
        _digest(project[field], field)
    _digest(project['settings_source_commit'], 'settings source commit', 40)
    if {field: project[field] for field in BINDING_FIELDS} != bindings:
        _fail('PROJECT_BINDING', 'proposal differs from supplied project/settings binding')
    _objects(project['contracts'], 'contract bindings')
    contracts = []
    for item in project['contracts']:
        _closed(item, {'ticket_key', 'contract_sha256', 'requirements_sha256'}, 'contract binding')
        _text(item['ticket_key'], 'contract ticket')
        _digest(item['contract_sha256'], 'contract digest')
        _digest(item['requirements_sha256'], 'requirements digest')
        contracts.append(item)
    expected = [dict(ticket_key=key, contract_sha256=by_key[key]['contract_sha256'],
                     requirements_sha256=by_key[key]['requirements_sha256']) for key in sorted(by_key)]
    if sorted(contracts, key=lambda c: c['ticket_key']) != expected:
        _fail('PROJECT_BINDING', 'proposal contract bindings differ from ticket authority')
    planning = record['planning_binding']
    _closed(planning, {'source_ticket_plan_sha256', 'structural_graph_sha256'}, 'planning binding')
    if (planning['source_ticket_plan_sha256'] != source_plan_digest(source_ticket_plan)
            or planning['structural_graph_sha256'] != structural_graph_digest(tickets)):
        _fail('PLANNING_BINDING', 'proposal differs from sealed source plan or structural graph')
    stages, edges, terminals = _pipeline(record['pipeline'], by_key)
    if record['pipeline'] != pipeline:
        _fail('PIPELINE_BINDING', 'proposal differs from supplied accepted pipeline definition')
    intervals = sprints_through(record['calendar'], record['planned_finish'])
    _objects(record['slices'], 'slices', nonempty=False)
    slices = {}
    for value in record['slices']:
        validate_slice(value, tickets)
        if value['id'] in slices:
            _fail('DUPLICATE_REFERENCE', 'slice id is repeated')
        slices[value['id']] = value
    _objects(record['milestones'], 'milestones')
    if len(record['milestones']) != len(intervals):
        _fail('MILESTONE_COVERAGE', 'exactly one milestone is required per sprint through finish')
    seen, used = set(), set()
    for ordinal, (milestone, interval) in enumerate(zip(record['milestones'], intervals), 1):
        _closed(milestone, MILESTONE_FIELDS, 'milestone')
        _text(milestone['id'], 'milestone id')
        if milestone['id'] in seen:
            _fail('DUPLICATE_REFERENCE', 'milestone id is repeated')
        seen.add(milestone['id'])
        if type(milestone['ordinal']) is not int or milestone['ordinal'] != ordinal:
            _fail('MILESTONE_COVERAGE', 'milestone ordinals must be consecutive')
        if {field: milestone[field] for field in INTERVAL_FIELDS} != interval:
            _fail('CALENDAR_MISMATCH', 'milestone dates differ from resolved calendar')
        _text(milestone['goal'], 'sprint goal')
        _strings(milestone['completion_predicates'], 'milestone completion predicates')
        _objects(milestone['targets'], 'milestone targets')
        targets, target_pairs, target_scopes, target_artifacts = set(), set(), {}, set()
        whole_tickets = set()
        for target in milestone['targets']:
            _closed(target, {'ticket_key', 'slice_id'}, 'milestone target')
            _text(target['ticket_key'], 'target ticket')
            if target['ticket_key'] not in by_key:
                _fail('UNKNOWN_TICKET', 'milestone target ticket is unknown')
            slice_id, key = target['slice_id'], target['ticket_key']
            if slice_id is not None:
                _text(slice_id, 'target slice')
                if slice_id not in slices or slices[slice_id]['parent_ticket'] != key:
                    _fail('UNKNOWN_SLICE', 'target slice does not belong to target ticket')
                value = slices[slice_id]
                scope, criteria = target_scopes.setdefault(key, (set(), set()))
                scope.update(value['included_scope'])
                criteria.update(value['acceptance_criteria'])
                target_artifacts.update(value['artifact_refs'])
                used.add(slice_id)
            else:
                whole_tickets.add(key)
            if (key, slice_id) in target_pairs:
                _fail('DUPLICATE_REFERENCE', 'milestone target is repeated')
            target_pairs.add((key, slice_id))
            targets.add(key)
        if whole_tickets & set(target_scopes):
            _fail('DUPLICATE_REFERENCE', 'whole ticket and its slices overlap in a milestone')
        closure = _closure(parents, sorted(targets))
        _strings(milestone['prerequisites'], 'prerequisites')
        if milestone['prerequisites'] != closure:
            _fail('PREREQUISITE_CLOSURE', 'milestone must declare exact sorted prerequisite closure')
        _objects(milestone['pipeline_runs'], 'pipeline runs')
        artifacts = set()
        for run in milestone['pipeline_runs']:
            artifacts.update(_run(run, record['pipeline'], stages, edges, terminals, closure, target_scopes))
        if target_artifacts - artifacts:
            _fail('TERMINAL_ARTIFACT', 'slice artifact reference is not produced by a real route')
    if used != set(slices):
        _fail('UNKNOWN_SLICE', 'proposal contains unused slice definitions')


def seal_milestone_plan(record, **context):
    """Seal a validated proposal; creates no acceptance record or authority."""
    result = deepcopy(record)
    if not isinstance(result, dict):
        _fail('INVALID_FIELD', 'proposal must be an object')
    result['plan_sha256'] = '0' * 64
    _validate_body(result, **context)
    result['plan_sha256'] = fingerprint(FORMAT, {key: value for key, value in result.items() if key != 'plan_sha256'})
    return result


def validate_milestone_plan(record, **context):
    _validate_body(record, **context)
    _digest(record['plan_sha256'], 'plan digest')
    if record['plan_sha256'] != fingerprint(FORMAT, {key: value for key, value in record.items() if key != 'plan_sha256'}):
        _fail('SEAL_MISMATCH', 'milestone proposal digest is stale')
    return deepcopy(record)


def serialize_milestone_plan(record, **context):
    return canonical(validate_milestone_plan(record, **context)) + b'\n'


def milestone_priorities(tickets, milestones):
    """Earliest prerequisite closure and downstream scores within each closure.

    Input milestones are a small ranking projection: ordinal and ticket_keys.
    The caller must derive them from a validated, independently accepted plan.
    Scores include generator prerequisites and never mutate the input graph.
    """
    by_key, parents, children, order = _topology(tickets)
    _objects(milestones, 'ranking milestones')
    effective, scores = {}, {}
    for ordinal, milestone in enumerate(milestones, 1):
        _closed(milestone, {'ordinal', 'ticket_keys'}, 'ranking milestone')
        if type(milestone['ordinal']) is not int or milestone['ordinal'] != ordinal:
            _fail('MILESTONE_COVERAGE', 'ranking ordinals must be consecutive')
        closure = set(_closure(parents, milestone['ticket_keys']))
        score = {}
        for key in reversed(order):
            if key in closure:
                score[key] = by_key[key]['duration_minutes'] + max(
                    (score[child] for child in children[key] if child in closure), default=0)
                if score[key] > 9007199254740991:
                    _fail('SCORE_OVERFLOW', 'critical score exceeds safe integer range')
                effective.setdefault(key, ordinal)
        scores[ordinal] = score
    return effective, scores


def milestone_rank_key(ticket_key, effective, scores, existing_tie_break):
    """Preserve the entry point's existing deterministic tie-break fields."""
    if ticket_key not in effective or ticket_key not in scores.get(effective[ticket_key], {}):
        _fail('UNASSIGNED_TICKET', 'ticket has no effective milestone priority')
    if not isinstance(existing_tie_break, tuple):
        _fail('INVALID_FIELD', 'existing tie break must be a tuple')
    ordinal = effective[ticket_key]
    return (ordinal, -scores[ordinal][ticket_key], *existing_tie_break)
