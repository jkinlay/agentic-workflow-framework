"""Pure AWF-55 supplied-input projections and isolated report serialization.

These functions establish no source trust, acquire no state and dispatch no work.
Production admission, persistence and CLI integration belong to later slices.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import re

from . import ValidationError
from .canonical import MAX_DOCUMENT_BYTES, canonical, timestamp, validate_value

MAX_INTEGER = 2**53 - 1


def _require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def _shape(value, keys):
    _require(isinstance(value, dict) and set(value) == set(keys.split()),
             'Invalid closed report record: ' + keys)


def _text(value):
    _require(isinstance(value, str) and bool(value.strip()), 'Expected nonblank string')


def _integer(value, minimum=0):
    _require(type(value) is int and minimum <= value <= MAX_INTEGER,
             'Expected bounded integer')
    return value


def _unknown(reason):
    return {'status': 'UNKNOWN', 'reason': reason}


def _is_unknown(value):
    if isinstance(value, dict) and value.get('status') == 'UNKNOWN':
        _shape(value, 'status reason')
        _text(value['reason'])
        return True
    return False


def validate_report_input(value):
    """Validate integer-only normalized JSON; never allow output fractions."""
    validate_value(value)
    _require(isinstance(value, dict), 'Report input must be an object')


def validate_report_value(value):
    """Walk report values with root-zero depth and two exact float exceptions."""
    def walk(current, path=(), depth=0):
        _require(depth <= 64, 'Document nesting exceeds 64 levels')
        if path == ('eta', 'confidence', 'quantiles'):
            _require(isinstance(current, list) and len(current) == 2,
                     'Report quantiles must be exactly [0.5,0.8]')
            _require(all(type(v) is float and v == expected
                         for v, expected in zip(current, (0.5, 0.8))),
                     'Report quantiles must be exactly [0.5,0.8]')
        if type(current) is float:
            allowed = {('eta', 'confidence', 'quantiles', 0): 0.5,
                       ('eta', 'confidence', 'quantiles', 1): 0.8}
            _require(path in allowed and current == allowed[path],
                     'Only integer numbers or exact report quantile fractions are supported')
        elif isinstance(current, dict):
            for key, child in current.items():
                _require(isinstance(key, str), 'Mapping keys must be strings')
                validate_value(key, depth + 1)
                walk(child, path + (key,), depth + 1)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                walk(child, path + (index,), depth + 1)
        else:
            validate_value(current, depth)
    walk(value)


def loads_report(data):
    """Decode an object-root report within the inclusive 8 MiB UTF-8 limit."""
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'Duplicate mapping key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValidationError('Nonfinite JSON number: ' + value)

    try:
        _require(isinstance(data, (str, bytes)), 'Expected UTF-8 text or bytes')
        raw = data.encode('utf-8', errors='strict') if isinstance(data, str) else data
        _require(len(raw) <= MAX_DOCUMENT_BYTES, 'Document exceeds 8 MiB')
        text = raw.decode('utf-8', errors='strict')
        _require(not text.startswith('\ufeff'), 'JSON BOM is forbidden')
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        _require(isinstance(value, dict), 'Report must be an object')
        validate_report_value(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f'Invalid report JSON: {exc}') from exc


def canonical_report(value):
    """Return sorted compact UTF-8 bytes without a domain prefix or newline."""
    validate_report_value(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def inputs_sha256(value):
    """Hash every supplied input before projection, sorting or default insertion."""
    validate_report_input(value)
    return hashlib.sha256(canonical(value)).hexdigest()


def _release(value):
    if value is None:
        return _unknown('MISSING_RELEASE_BINDING')
    _shape(value, 'project_id workflow_version jira_enabled scope_selector')
    for key in ('project_id', 'workflow_version'):
        _text(value[key])
    _require(type(value['jira_enabled']) is bool, 'jira_enabled must be boolean')
    scope = value['scope_selector']
    if value['jira_enabled']:
        _shape(scope, 'allow_entire_project selector_mode included_epics labels_any components_any additional_jql ownership_required')
        for key in ('allow_entire_project', 'ownership_required'):
            _require(type(scope[key]) is bool, 'Selector boolean required')
        _text(scope['selector_mode'])
        _require(isinstance(scope['additional_jql'], str), 'additional_jql must be string')
        for key in ('included_epics', 'labels_any', 'components_any'):
            _require(isinstance(scope[key], list), 'Selector array required')
            for item in scope[key]:
                _text(item)
    else:
        _require(scope is None, 'Disabled Jira requires null scope_selector')
    return {'status': 'KNOWN', 'inputs': deepcopy(value), 'sha256': inputs_sha256(value)}


def _decision(value, rejected=False):
    keys = 'mode value actor decided_at source' + (' reason' if rejected else '')
    _shape(value, keys)
    _require(value['mode'] in ('fixed_count', 'max_cap'), 'Invalid owner override mode')
    _integer(value['value'], 1)
    for key in ('actor', 'source') + (('reason',) if rejected else ()):
        _text(value[key])
    timestamp(value['decided_at'])


def _lanes(inputs):
    if 'lane_evidence' not in inputs:
        plan = inputs.get('plan')
        if plan is None:
            return _unknown('MISSING_PLAN'), None
        if _is_unknown(plan):
            return deepcopy(plan), None
        # Legacy normalized projection remains distinct from constraint evidence.
        recommendation = _integer(plan['recommendation'])
        decision = plan.get('owner_override')
        effective = _effective(recommendation, decision)
        return dict(status='KNOWN', recommendation=recommendation, owner_override=deepcopy(decision),
                    effective=effective, binding_constraint=plan['binding_constraint'],
                    source=plan['source']), None
    evidence = inputs['lane_evidence']
    _shape(evidence, 'baseline rejected_override')
    rejected = evidence['rejected_override']
    if rejected is not None:
        _decision(rejected, rejected=True)
    observations = {'rejected_override': deepcopy(rejected)}
    baseline = evidence['baseline']
    if _is_unknown(baseline):
        return deepcopy(baseline), observations
    _shape(baseline, 'status recommendation effective owner_override source constraints')
    _require(baseline['status'] == 'KNOWN', 'Expected known lane baseline')
    _text(baseline['source'])
    records = baseline['constraints']
    _require(isinstance(records, list) and bool(records), 'Constraints must be nonempty')
    names = set()
    for record in records:
        _shape(record, 'name value actor observed_at source reason')
        for key in ('name', 'source', 'reason'):
            _text(record[key])
        _require(record['name'] not in names, 'Duplicate lane constraint')
        names.add(record['name'])
        _integer(record['value'])
        if record['actor'] is not None:
            _text(record['actor'])
        if record['observed_at'] is not None:
            timestamp(record['observed_at'])
    recommendation = _integer(baseline['recommendation'])
    _require(recommendation == min(r['value'] for r in records), 'Inconsistent constraint minimum')
    effective = _effective(recommendation, baseline['owner_override'])
    _require(_integer(baseline['effective']) == effective, 'Inconsistent effective lanes')
    details = sorted(deepcopy(records), key=lambda r: r['name'])
    bindings = [r['name'] for r in details if r['value'] == recommendation]
    return dict(status='KNOWN', recommendation=recommendation, effective=effective,
                owner_override=deepcopy(baseline['owner_override']), source=baseline['source'],
                binding_constraint=bindings[0], binding_constraints=bindings,
                constraint_details=details), observations


def _effective(recommendation, decision):
    if decision is None:
        return recommendation
    _decision(decision)
    if decision['mode'] == 'fixed_count':
        _require(decision['value'] <= recommendation, 'Fixed count exceeds recommendation')
        return decision['value']
    return min(recommendation, decision['value'])


def _train(value):
    if value is None:
        return _unknown('MISSING_MERGE_TRAIN')
    if _is_unknown(value):
        return deepcopy(value)
    _require(value.get('status') == 'KNOWN', 'Expected known train')
    _require(type(value.get('enabled')) is bool, 'Train enabled must be boolean')
    _text(value.get('source'))
    _require(value.get('source_owner_review') == 'normal_tier_final_gate', 'Invalid owner review policy')
    result = deepcopy(value)
    cadence = value.get('cadence_minutes')
    if cadence is not None:
        _integer(cadence, 1)
    schedule = value.get('schedule')
    if schedule is not None:
        _require(isinstance(schedule, dict), 'Schedule object required')
        kind = schedule.get('kind')
        if kind == 'INTERVAL':
            _shape(schedule, 'kind minutes')
            minutes = _integer(schedule['minutes'], 1)
            _require(cadence is None or cadence == minutes, 'Conflicting train cadence')
            result['cadence_minutes'] = minutes
        elif kind == 'UTC_DAILY_SLOTS':
            _shape(schedule, 'kind timezone slots')
            _require(schedule['timezone'] == 'UTC', 'Schedule timezone must be UTC')
            slots = schedule['slots']
            _require(isinstance(slots, list) and bool(slots), 'Nonempty slots required')
            _require(all(isinstance(s, str) and re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', s)
                         for s in slots), 'Invalid UTC slot')
            _require(slots == sorted(set(slots)), 'Slots must be ascending and unique')
            result['cadence_minutes'] = None
        elif kind == 'UNKNOWN':
            _shape(schedule, 'kind reason')
            _text(schedule['reason'])
            result['cadence_minutes'] = None
        else:
            raise ValidationError('Invalid schedule kind')
    return result


def _binding(value):
    if value is None:
        return
    _shape(value, 'project_id ticket_id head policy_sha256 operating_hash')
    for key in ('project_id', 'ticket_id', 'head'):
        _text(value[key])
    for key in ('policy_sha256', 'operating_hash'):
        if key == 'operating_hash' and value[key] is None:
            continue
        _require(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}', value[key]),
                 'Invalid routing digest')


def _role(value):
    aliases = {'writer': 'worker', 'fix': 'worker', 'reviewer': 'critic'}
    _require(isinstance(value, str), 'Invalid role')
    value = aliases.get(value, value)
    _require(value in {'controller', 'worker', 'critic', 'specialist', 'simple_worker',
                       'design', 'spec_review'}, 'Invalid role')
    return value


def _scope(value):
    _shape(value, 'kind id stream')
    kind, ident, stream = value['kind'], value['id'], value['stream']
    if kind == 'role':
        _require(ident is None and stream is None, 'Invalid role scope')
    elif kind == 'tier':
        _require(ident in ('1', '2', '3') and stream is None, 'Invalid tier scope')
    elif kind == 'stream':
        _require(ident is None, 'Invalid stream scope')
        _text(stream)
    elif kind == 'epic_stream':
        _text(ident)
        _text(stream)
    elif kind in ('agent', 'ticket'):
        _text(ident)
        _require(stream is None, 'Invalid scoped stream')
    else:
        raise ValidationError('Invalid scope kind')


def _routing(value):
    if value is None:
        return _unknown('MISSING_ROUTING')
    if _is_unknown(value):
        return deepcopy(value)
    _shape(value, 'binding declarations proposals observations')
    _binding(value['binding'])
    result = deepcopy(value)
    result['status'] = 'KNOWN'
    for name in ('declarations', 'proposals', 'observations'):
        _require(isinstance(result[name], list), 'Routing records must be arrays')
        keyed = []
        for record in result[name]:
            record['role'] = _role(record.get('role'))
            _text(record.get('source'))
            if name == 'declarations':
                if 'effort' in record:
                    effort = record.pop('effort')
                    _require('reasoning_effort' not in record or record['reasoning_effort'] == effort,
                             'Conflicting effort spellings')
                    record['reasoning_effort'] = effort
                record.setdefault('pinned', None)
                record.setdefault('enabled', None)
                _shape(record, 'role scope model reasoning_effort source pinned enabled')
                _scope(record['scope'])
                for key in ('model', 'reasoning_effort'):
                    _text(record[key])
                for key in ('pinned', 'enabled'):
                    _require(record[key] is None or type(record[key]) is bool, 'Invalid declaration metadata')
                scope = record['scope']
                sortkey = (record['role'], scope['kind'], scope['id'] or '', scope['stream'] or '', record['source'])
            else:
                _shape(record, 'role source binding value')
                _binding(record['binding'])
                val = record['value']
                if not _is_unknown(val):
                    if name == 'proposals':
                        _shape(val, 'status model reasoning_effort dispatch_performed')
                        _require(val['dispatch_performed'] is False, 'Proposal must not dispatch')
                        for key in ('model', 'reasoning_effort'):
                            _text(val[key])
                    else:
                        _shape(val, 'status run_id requested_model requested_effort observed_model observed_effort codex_cli_version tokens outcome authentication')
                        for key in ('run_id', 'requested_model', 'requested_effort', 'observed_model',
                                    'observed_effort', 'codex_cli_version', 'outcome'):
                            _text(val[key])
                        _require(val['authentication'] == 'CALLER_ASSERTED', 'Observation must remain caller asserted')
                        if val['tokens'] != 'unknown':
                            _integer(val['tokens'])
                    _require(val['status'] == 'KNOWN', 'Invalid routing value status')
                    if value['binding'] is None or record['binding'] is None:
                        record['value'] = _unknown('MISSING_ROUTING_BINDING')
                    elif value['binding'] != record['binding']:
                        record['value'] = _unknown('ROUTING_BINDING_MISMATCH')
                sortkey = (record['role'], record['source'])
            keyed.append((sortkey, record))
        _require(len({key for key, _ in keyed}) == len(keyed), 'Duplicate routing sort key')
        result[name] = [record for _, record in sorted(keyed, key=lambda item: item[0])]
    return result


def _scenario(value, lanes, gap):
    _shape(value, 'source tasks')
    _text(value['source'])
    _require(isinstance(value['tasks'], list), 'Scenario tasks must be array')
    tasks = {}
    for task in value['tasks']:
        _shape(task, 'key predecessors scheduled_start_minute rounds round_minutes owner_touchpoint_minutes owner_review_window')
        _text(task['key'])
        _require(task['key'] not in tasks, 'Duplicate scenario task')
        tasks[task['key']] = task
    finishes, starts, graph = {}, {}, {}
    for key, task in sorted(tasks.items()):
        preds = task['predecessors']
        _require(isinstance(preds, list), 'Predecessors must be array')
        for pred in preds:
            _text(pred)
        _require(len(set(preds)) == len(preds) and all(p in tasks for p in preds), 'Invalid predecessors')
        graph[key] = tuple(sorted(preds))
        start = _integer(task['scheduled_start_minute'])
        rounds = _integer(task['rounds'], 1)
        minutes = _integer(task['round_minutes'], 1)
        touch = _integer(task['owner_touchpoint_minutes'])
        work = _integer(rounds * minutes, 1)
        duration = _integer(work + touch, 1)
        finish = _integer(start + duration)
        window = task['owner_review_window']
        if window is not None:
            _shape(window, 'opens_at_minute closes_at_minute')
            opens = _integer(window['opens_at_minute'])
            closes = _integer(window['closes_at_minute'])
            _require(opens <= closes and start + work >= opens and finish <= closes,
                     'Invalid owner review window')
        starts[key], finishes[key] = start, finish
    # Kahn traversal validates only; it never assigns or repairs a start time.
    pending = set(tasks)
    while pending:
        ready = {key for key in pending if not (set(graph[key]) & pending)}
        _require(bool(ready), 'Cyclic scenario graph')
        pending -= ready
    for key in tasks:
        _require(all(starts[key] >= finishes[pred] + gap for pred in graph[key]), 'Predecessor finish gap violation')
    ordered = sorted(starts.values())
    _require(all(right - left >= gap for left, right in zip(ordered, ordered[1:])), 'Start gap violation')
    events = sorted([(starts[k], 1) for k in tasks] + [(finishes[k], -1) for k in tasks])
    occupied = 0
    for _, delta in events:
        occupied += delta
        _require(occupied <= lanes, 'Lane occupancy violation')
    return max(finishes.values(), default=0), graph


def _forecast(anchor, p50, p80):
    if anchor is None:
        return _unknown('MISSING_FORECAST_ANCHOR')
    instant = timestamp(anchor)
    def text(value):
        return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    try:
        return dict(status='KNOWN', anchor_at=text(instant),
                    p50_completion_at=text(instant + timedelta(minutes=p50)),
                    p80_completion_at=text(instant + timedelta(minutes=p80)))
    except (OverflowError, ValueError):
        return _unknown('FORECAST_RANGE')


def _eta(value):
    if value is None:
        return _unknown('MISSING_ETA_INPUTS')
    if _is_unknown(value):
        return deepcopy(value)
    _require(isinstance(value, dict), 'ETA inputs must be an object')
    anchor = value.get('forecast_anchor_at')
    if anchor is not None:
        try:
            timestamp(anchor)
        except OverflowError as exc:
            raise ValidationError('Invalid forecast anchor: UTC instant is out of range') from exc
    # Type failures are invalid normalized input, not a semantic graph result.
    for key in ('effective_lanes', 'start_gap_minutes'):
        if key in value:
            _require(type(value[key]) is int, 'ETA integer required')
    scenarios = value.get('scenarios')
    if isinstance(scenarios, dict):
        for scenario in scenarios.values():
            if not isinstance(scenario, dict) or not isinstance(scenario.get('tasks'), list):
                continue
            for task in scenario['tasks']:
                if not isinstance(task, dict):
                    continue
                for key in ('scheduled_start_minute', 'rounds', 'round_minutes', 'owner_touchpoint_minutes'):
                    if key in task:
                        _require(type(task[key]) is int, 'ETA task integer required')
                window = task.get('owner_review_window')
                if isinstance(window, dict):
                    for key in ('opens_at_minute', 'closes_at_minute'):
                        if key in window:
                            _require(type(window[key]) is int, 'Owner window integer required')
    try:
        _require(value.get('scenario_format') == 'AWF55_INTEGER_SCENARIOS_V1', 'Unsupported normalized ETA graph')
        lanes = _integer(value['effective_lanes'], 1)
        gap = _integer(value['start_gap_minutes'])
        _shape(value['scenarios'], 'p50 p80')
        p50, graph50 = _scenario(value['scenarios']['p50'], lanes, gap)
        p80, graph80 = _scenario(value['scenarios']['p80'], lanes, gap)
        _require(graph50 == graph80, 'Scenario graph mismatch')
        _require(p80 >= p50, 'Scenario quantile order')
    except (ValidationError, KeyError, TypeError) as exc:
        return _unknown('INVALID_ETA_GRAPH: ' + str(exc))
    return dict(status='KNOWN', p50_minutes=p50, p80_minutes=p80, basis=deepcopy(value),
                confidence={'quantiles': [0.5, 0.8], 'source': 'supplied_integer_scenarios'},
                forecast=_forecast(anchor, p50, p80))


def _outstanding(value):
    if value is None:
        return _unknown('MISSING_INVENTORY')
    if _is_unknown(value):
        return deepcopy(value)
    _require(isinstance(value, dict) and value.get('status') == 'KNOWN', 'Invalid inventory')
    _text(value.get('source'))
    _require(isinstance(value.get('tickets'), list), 'Inventory tickets must be array')
    keys, outstanding = set(), []
    for ticket in value['tickets']:
        _require(isinstance(ticket, dict), 'Invalid ticket')
        _text(ticket.get('key'))
        _text(ticket.get('status'))
        _require(ticket['key'] not in keys, 'Duplicate inventory ticket')
        keys.add(ticket['key'])
        if ticket['status'] not in ('DONE', 'CANCELLED'):
            outstanding.append(ticket['key'])
    return dict(status='KNOWN', count=len(outstanding), tickets=sorted(outstanding), source=value['source'])


def _priority(value, outstanding):
    if value is None:
        return _unknown('MISSING_ASSIGNMENTS')
    if _is_unknown(value):
        return deepcopy(value)
    if outstanding['status'] == 'UNKNOWN':
        return _unknown(outstanding['reason'])
    _require(isinstance(value, dict) and value.get('status') == 'KNOWN', 'Invalid assignments')
    _require(isinstance(value.get('records'), list), 'Assignment records must be array')
    grouped, seen = {}, set()
    for record in value['records']:
        _shape(record, 'key tier cap source')
        _text(record['key'])
        _text(record['source'])
        _integer(record['tier'], 1)
        _require(record['tier'] <= 3, 'Invalid tier')
        _integer(record['cap'], 1)
        _require(record['key'] not in seen, 'Duplicate assignment')
        seen.add(record['key'])
        if record['key'] in outstanding['tickets']:
            grouped.setdefault((record['tier'], record['cap']), []).append(record)
    _require(set(outstanding['tickets']) <= seen, 'Incomplete supplied assignments')
    return {'status': 'KNOWN', 'groups': [dict(tier=tier, cap=cap, count=len(records),
            tickets=sorted(r['key'] for r in records), sources=sorted({r['source'] for r in records}))
            for (tier, cap), records in sorted(grouped.items())]}


def build_report(inputs):
    """Project a detached report from supplied normalized evidence only."""
    digest = inputs_sha256(inputs)
    outstanding = _outstanding(inputs.get('inventory'))
    lanes, observations = _lanes(inputs)
    report = dict(inputs_sha256=digest, outstanding=outstanding,
                  priority=_priority(inputs.get('assignments'), outstanding), lanes=lanes,
                  merge_train=_train(inputs.get('merge_train')), eta=_eta(inputs.get('eta_inputs')),
                  release_binding=_release(inputs.get('release_binding')), routing=_routing(inputs.get('routing')))
    if observations is not None:
        report['lane_observations'] = observations
    validate_report_value(report)
    return report


def render_json(report):
    """Render the report as UTF-8 JSON text."""
    return canonical_report(report).decode('utf-8')


def render_text(report):
    """Render independently labelled JSON metric values, one per line."""
    validate_report_value(report)
    return '\n'.join(key + ': ' + json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(',', ':'), allow_nan=False) for key, value in sorted(report.items()))
