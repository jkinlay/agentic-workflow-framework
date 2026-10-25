"""Opt-in report admission and append-only records in existing AWF state.

Supplied records are verified, not live-observed. Host authenticators are
callables supplied by trusted controller code, never fields in report input.
This module neither creates a database nor initializes or repairs its schema.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3
import uuid

from . import ValidationError
from .canonical import MAX_DOCUMENT_BYTES, canonical, fingerprint, load, loads, timestamp
from .contracts import Contracts
from .intake_assignment import verify_assignment_record
from .operating import read_operating, validate_snapshot
from .path_containment import is_within
from .planning import verify_override_state, verify_plan_record
from .planning_graph import validate_ticket_plan
from .provider_identity import (ProviderIdentityError, github_identity_preflight,
                                jira_identity_preflight)
from .status_report import (build_report, inputs_sha256, loads_report, render_json)
from .store import Store


def _unknown(reason):
    return {'status': 'UNKNOWN', 'reason': reason}


def _require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def _release(config):
    jira = config['jira']
    return dict(project_id=config['project']['id'],
        workflow_version=config['template']['expected_workflow_version'],
        jira_enabled=jira.get('enabled', True),
        scope_selector=deepcopy(jira['scope']) if jira.get('enabled', True) else None)


def report_binding(config):
    """Bind the project, both providers and the existing release selector."""
    return dict(project_id=config['project']['id'], repository_id=config['github']['repository_id'],
        repository=config['github']['repository'], cloud_id=config['jira']['cloud_id'],
        provider_project_id=config['jira']['provider_project_id'], project_key=config['jira']['project_key'],
        release_sha256=inputs_sha256(_release(config)))


def _identity(config, request):
    if config.get('jira', {}).get('enabled') is False:
        return 'JIRA_DISABLED: identity binding is unavailable'
    if request.get('github_identity') is None or request.get('jira_connections') is None:
        return 'IDENTITY_UNOBSERVED'
    if request.get('binding') != report_binding(config):
        return 'REPORT_BINDING_MISMATCH'
    try:
        github_identity_preflight(config, request['github_identity'])
        jira_identity_preflight(config, request['jira_connections'])
    except ProviderIdentityError as exc:
        return exc.code + ': ' + str(exc)
    return None


def _verified(value, binding, verifier, label):
    if value is None:
        return _unknown('MISSING_' + label)
    if not callable(verifier):
        return _unknown('UNVERIFIED_' + label)
    try:
        observed = verifier(deepcopy(value), deepcopy(binding))
    except Exception as exc:
        raise ValidationError(label + '_VERIFIER_FAILED') from exc
    _require(isinstance(observed, dict) and observed == value,
             label + '_VERIFIER_MISMATCH')
    return deepcopy(observed)


def _inventory(config, request, verifier):
    inventory = _verified(request.get('inventory'), request['binding'], verifier, 'INVENTORY')
    if inventory.get('status') == 'UNKNOWN':
        return inventory
    terminal = {config['jira']['status_map']['done']: 'DONE', 'Cancelled': 'CANCELLED'}
    for ticket in inventory['tickets']:
        ticket['status'] = terminal.get(ticket['status'], ticket['status'])
    return inventory


def _assignments(records):
    if records is None:
        return _unknown('MISSING_ASSIGNMENTS')
    _require(isinstance(records, list), 'Assignment records must be an array')
    projected = []
    for record in records:
        verify_assignment_record(record)
        projected.append(dict(key=record['ticket_key'], tier=record['tier'],
                              cap=record['round_cap'], source=record['assignment_sha256']))
    return dict(status='KNOWN', records=projected)


def _lanes(config, request, operating, inventory, verify_owner_source):
    plan = request.get('plan')
    if plan is None:
        return _unknown('MISSING_PLAN')
    verify_plan_record(plan)
    _require(plan.get('schema_version') == 2, 'Plan lacks current capacity binding')
    capacity = plan['inputs']['capacity']
    _require(capacity.get('project_config_sha256') == fingerprint('accepted-project-config', config),
             'PLAN_CONFIGURATION_MISMATCH')
    _require(operating is not None, 'MISSING_OPERATING_BINDING')
    validate_snapshot(operating, config)
    _require(capacity.get('operating_hash') == operating.operating_hash, 'PLAN_OPERATING_MISMATCH')
    _require(inventory['status'] == 'KNOWN', 'UNVERIFIED_INVENTORY')
    _require(plan['inputs'].get('jira_snapshot_sha256') == fingerprint(
        'planning_ticket_snapshot', request['inventory']['tickets']), 'PLAN_INVENTORY_MISMATCH')
    recommendation = capacity['safe_lane_count']
    constraints = plan['estimate'].get('constraints')
    _require(isinstance(constraints, dict) and constraints and min(constraints.values()) == recommendation,
             'PLAN_CONSTRAINT_MISMATCH')
    decision = plan.get('owner_decision')
    _require(isinstance(decision, dict), 'MISSING_OWNER_DECISION')
    override = None
    if decision.get('kind') == 'owner_override':
        current = verify_override_state(config, request.get('override_state'), verify_owner_source)
        _require(current is not None and current['override_id'] == decision.get('override_id')
                 and current['mode'] == decision.get('mode') and current['value'] == decision.get('value'),
                 'PLAN_OVERRIDE_MISMATCH')
        override = dict(mode={'fixed':'fixed_count','max':'max_cap'}[current['mode']],
            value=current['value'], actor=str(current['change_audit']['who']),
            decided_at=current['created_at'], source=current['override_sha256'])
    else:
        _require(decision == {'kind':'accepted_default','value':recommendation}
                 and request.get('override_state') is None, 'PLAN_OWNER_DECISION_MISMATCH')
    return {'baseline': dict(status='KNOWN', recommendation=recommendation,
        effective=plan['lane_count'], owner_override=override, source=plan['plan_sha256'],
        constraints=[dict(name=name, value=value, actor=None, observed_at=plan['created_at'],
                          source=plan['plan_sha256'], reason='sealed supplied plan constraint')
                     for name, value in sorted(constraints.items())]), 'rejected_override': None}


def _eta(value, lanes, inventory, assignments):
    if value is None:
        return _unknown('MISSING_ETA_INPUTS')
    _require(isinstance(value, dict) and set(value) <= {'p50','p80','rounds','forecast_anchor_at'},
             'Invalid planning graph envelope')
    # Validate both producer records before considering their projection.
    graphs = {name: validate_ticket_plan(value[name]) for name in ('p50','p80')}
    _require(isinstance(lanes, dict) and 'baseline' in lanes, 'UNVERIFIED_LANES')
    effective = lanes['baseline']['effective']
    _require(inventory['status'] == 'KNOWN', 'UNVERIFIED_INVENTORY')
    outstanding = build_report({'inventory': inventory})['outstanding']['tickets']
    _require(assignments.get('status') == 'KNOWN', 'UNVERIFIED_ASSIGNMENTS')
    assigned = {row['key']: row for row in assignments['records']}
    rounds = value['rounds']
    _require(isinstance(rounds, (list, dict)), 'Missing round and owner-touchpoint basis')
    if isinstance(rounds, dict):
        _require(set(rounds) == {'p50','p80'}, 'Missing quantile round basis')
    scenarios = {}
    for name, graph in graphs.items():
        scenario_rounds = rounds[name] if isinstance(rounds, dict) else rounds
        _require(isinstance(scenario_rounds, list), 'Missing scenario round basis')
        by_key = {row['key']: row for row in scenario_rounds}
        _require(len(by_key) == len(scenario_rounds) and set(by_key) == set(outstanding), 'ROUND_INVENTORY_MISMATCH')
        _require(graph['lane_cap'] == effective and {t['key'] for t in graph['tickets']} == set(outstanding),
                 'GRAPH_LANE_OR_INVENTORY_MISMATCH')
        tasks = []
        for ticket in graph['tickets']:
            basis = by_key[ticket['key']]
            _require(set(basis) == {'key','rounds','round_minutes','owner_touchpoint_minutes'}, 'Invalid round basis')
            _require(ticket['risk_tier'] == assigned[ticket['key']]['tier']
                     and ticket['round_cap'] == assigned[ticket['key']]['cap'], 'GRAPH_ASSIGNMENT_MISMATCH')
            _require(basis['rounds'] <= ticket['round_cap'] and ticket['duration_minutes'] ==
                     basis['rounds'] * basis['round_minutes'] + basis['owner_touchpoint_minutes'],
                     'GRAPH_DURATION_OR_CAP_MISMATCH')
            tasks.append(dict(**basis, predecessors=sorted({edge['from'] for edge in graph['edges']
                if edge['to'] == ticket['key']}), scheduled_start_minute=ticket['earliest_start_minute'],
                owner_review_window=deepcopy(ticket['owner_review_window'])))
        scenarios[name] = dict(source='supplied-ticket-plan:' + inputs_sha256(graph), tasks=tasks)
    _require(graphs['p50']['start_gap_minutes'] == graphs['p80']['start_gap_minutes'], 'GRAPH_GAP_MISMATCH')
    return dict(scenario_format='AWF55_INTEGER_SCENARIOS_V1', effective_lanes=effective,
        start_gap_minutes=graphs['p50']['start_gap_minutes'], scenarios=scenarios,
        forecast_anchor_at=value.get('forecast_anchor_at'))


def _routing(config, operating):
    declarations = []
    def declare(role, route, source, *, kind='role', ident=None, stream=None):
        declarations.append(dict(role=role, scope=dict(kind=kind, id=ident, stream=stream),
            model=route['model'], reasoning_effort=route['reasoning_effort'], source=source,
            pinned=route.get('pinned'), enabled=route.get('enabled')))
    roles = config.get('execution', {}).get('roles')
    _require(isinstance(roles, dict) and {'controller','worker','critic','specialist'} <= roles.keys(),
             'MISSING_ROUTING_DECLARATIONS')
    for role, route in sorted(roles.items()):
        declare(role, route, '.agentic/PROJECT_CONFIG.yaml#/execution/roles/' + role)
    if operating is not None:
        validate_snapshot(operating, config)
        choices = operating.config
        for role in ('controller','critic','specialist','simple_worker'):
            if role in choices:
                # A role-level operating choice supersedes its role declaration.
                declarations[:] = [row for row in declarations if row['role'] != role]
                declare(role, choices[role], 'OPERATING_CONFIG.yaml#/' + role)
        for stream, routes in sorted(choices['streams'].items()):
            if stream == 'count':
                continue
            for role, route in sorted(routes.items()):
                declare('critic' if role == 'reviewer' else role, route,
                        'OPERATING_CONFIG.yaml#/streams/' + stream + '/' + role, kind='stream', stream=stream)
        for epic, override in sorted(choices.get('epic_overrides', {}).items()):
            for role in ('controller','critic','specialist'):
                if role in override:
                    declare(role, override[role], 'OPERATING_CONFIG.yaml#/epic_overrides/' + epic + '/' + role,
                            kind='ticket', ident=epic)
            for stream, routes in sorted(override.get('streams', {}).items()):
                for role, route in sorted(routes.items()):
                    declare('critic' if role == 'reviewer' else role, route,
                        'OPERATING_CONFIG.yaml#/epic_overrides/' + epic + '/streams/' + stream + '/' + role,
                        kind='epic_stream', ident=epic, stream=stream)
    return dict(binding=None, declarations=declarations, observations=[],
        proposals=[dict(role=role, source='effective-route-context', binding=None,
                        value=_unknown('MISSING_EFFECTIVE_ROUTING_INPUTS'))
                   for role in sorted({row['role'] for row in declarations})])


def prepare_report(config, request, *, operating=None, verify_inventory=None,
                   verify_owner_source=None, verify_merge_train=None, trigger='on-demand'):
    """Verify supplied sources and freeze the original inputs before projection."""
    reason = _identity(config, request)
    if reason is not None:
        return dict(status='NOT_RUN', reason=reason)
    _require(trigger in {'on-demand','activation'}, 'Invalid report trigger')
    _require(str(uuid.UUID(request['record_id'])) == request['record_id'], 'Invalid report identity')
    timestamp(request['created_at'])
    original = deepcopy(dict(request=request, configuration=config,
                             operating=operating.config if operating is not None else None))
    digest = inputs_sha256(original)
    projection = {'release_binding': _release(config)}

    def project(key, builder, label):
        try:
            projection[key] = builder()
            if key == 'lane_evidence' and projection[key].get('status') == 'UNKNOWN':
                projection['plan'] = projection.pop(key)
            # Validate each metric using the delivered pure builder; malformed
            # optional evidence must not discard other accepted observations.
            build_report(projection)
        except (ValidationError, KeyError, ValueError, TypeError, OverflowError) as exc:
            if key == 'lane_evidence':
                projection.pop(key, None)
                projection['plan'] = _unknown('INVALID_' + label + ': ' + str(exc))
            else:
                projection[key] = _unknown('INVALID_' + label + ': ' + str(exc))

    project('inventory', lambda: _inventory(config, request, verify_inventory), 'INVENTORY')
    project('assignments', lambda: _assignments(request.get('assignments')), 'ASSIGNMENTS')
    project('lane_evidence', lambda: _lanes(config, request, operating, projection['inventory'], verify_owner_source), 'PLAN')
    project('eta_inputs', lambda: _eta(request.get('planning_graph'), projection.get('lane_evidence'),
                                     projection['inventory'], projection['assignments']), 'GRAPH')
    project('merge_train', lambda: _verified(request.get('merge_train'), request['binding'], verify_merge_train, 'MERGE_TRAIN'), 'MERGE_TRAIN')
    project('routing', lambda: _routing(config, operating), 'ROUTING')
    report = build_report(projection)
    report['inputs_sha256'] = digest
    record = dict(format='awf-status-report-record-1', record_id=request['record_id'],
        created_at=request['created_at'], trigger=trigger, project_id=config['project']['id'],
        source_mode='SUPPLIED_RECORDS_ONLY', inputs=original, inputs_sha256=digest,
        projection=deepcopy(projection), values_json=render_json(report))
    return dict(status='PREPARED', report=report, record=record)


def replay_report(record):
    """Replay the retained projection and verify its original input digest."""
    _require(set(record) == {'format','record_id','created_at','trigger','project_id','source_mode',
                            'inputs','inputs_sha256','projection','values_json'}
             and record.get('format') == 'awf-status-report-record-1', 'Invalid report record format')
    _require(set(record['inputs']) == {'request','configuration','operating'}, 'Invalid report snapshot')
    _require(inputs_sha256(record['inputs']) == record['inputs_sha256'], 'Report input snapshot hash mismatch')
    request = record['inputs']['request']
    reason = _identity(record['inputs']['configuration'], request)
    _require(reason is None, reason)
    _require(str(uuid.UUID(record['record_id'])) == record['record_id'], 'Invalid report identity')
    timestamp(record['created_at'])
    _require(record['record_id'] == request['record_id'] and record['created_at'] == request['created_at']
             and record['project_id'] == record['inputs']['configuration']['project']['id']
             and record['source_mode'] == 'SUPPLIED_RECORDS_ONLY'
             and record['trigger'] in {'activation','on-demand'}, 'Report metadata mismatch')
    report = build_report(record['projection'])
    report['inputs_sha256'] = record['inputs_sha256']
    _require(report == loads_report(record['values_json']) and render_json(report) == record['values_json'],
             'Report values differ from replay')
    return report


class ReportStore(Store):
    """Use only the existing immutable event journal; never run Store's DDL."""
    def __init__(self, path, project_id, worktree_roots=()):
        self.path = Path(path).absolute()
        for parent in (self.path, *self.path.parents):
            _require(not parent.is_symlink() and not getattr(parent, 'is_junction', lambda: False)(),
                     'Report store traverses a link/reparse point')
        _require(self.path.is_file() and self.path.stat().st_nlink == 1, 'Existing report store required')
        _require(not any(is_within(self.path, Path(root).resolve()) for root in worktree_roots),
                 'Report store must be outside worker worktrees')
        self.project_id = project_id
        with self.connection() as db:
            _require(db.execute("SELECT value FROM meta WHERE key='project'").fetchone()[0] == project_id,
                     'Report store belongs to another project')
            _require(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == '3',
                     'Unsupported AWF state schema')
            triggers = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            _require({'events_no_update','events_no_delete'} <= triggers, 'Immutable event triggers required')
        self.verify_chain()

    @contextmanager
    def connection(self):
        # mode=rw is intentional: a disappeared path must never create state.
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def append(self, record):
        raw = canonical(record)
        _require(len(raw) <= MAX_DOCUMENT_BYTES, 'Report record exceeds 8 MiB')
        record = loads(raw.decode('utf-8'))
        replay_report(record)
        _require(record['project_id'] == self.project_id, 'Report project mismatch')
        with self.transaction() as db:
            return self._event(db, 'status_report', record,
                external_id='status-report:' + record['record_id'], now=record['created_at'])


def run_report(root, input_path, store_path):
    """CLI seam with explicit files and no implicit state initialization."""
    root = Path(root)
    request = load(Path(input_path))
    contracts = Contracts(Path(__file__).resolve().parents[2] / 'schemas')
    contracts.validate('status-report-input', request)
    config = load(root / '.agentic/PROJECT_CONFIG.yaml')
    try:
        operating = read_operating(root, config)
    except (ValidationError, OSError, ValueError):
        operating = None
    prepared = prepare_report(config, request, operating=operating)
    if prepared['status'] != 'PREPARED':
        return prepared
    contracts.validate('status-report', prepared['report'])
    sequence = ReportStore(store_path, config['project']['id'], worktree_roots=[root]).append(prepared['record'])
    return dict(status='RECORDED', record_id=request['record_id'], sequence=sequence,
                created_at=request['created_at'], trigger='on-demand', source_mode='SUPPLIED_RECORDS_ONLY',
                report=prepared['report'])
