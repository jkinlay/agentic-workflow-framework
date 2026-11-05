"""Version-2 supplied snapshots alongside the unchanged version-1 AWF-55 path.

Hashes establish replay consistency, never source authenticity or readiness.
Only the host-owned verifier can admit observations. No report implies approval,
merge, Jira reconciliation, chat activation or dispatch authority.
"""
from copy import deepcopy
from datetime import date
import re

from .. import ValidationError
from ..canonical import MAX_DOCUMENT_BYTES, canonical, fingerprint, timestamp
from .delivery import require
from .metrics import project_metrics

REPORT_TYPES = {'on-demand', 'owner-action', 'backlog-review', 'implementation-ready',
                'hourly', 'eod', 'correction'}


def validate_request(request, accepted_binding):
    require(isinstance(request, dict) and set(request) == {
        'record_id', 'created_at', 'binding', 'candidate', 'included', 'excluded', 'report_type',
        'period', 'baseline_revision', 'forecast_revision', 'summary', 'owner_action', 'sources', 'tickets'},
        'REPORT_SNAPSHOT_SHAPE')
    require(len(canonical(request)) <= MAX_DOCUMENT_BYTES, 'REPORT_SNAPSHOT_SIZE')
    binding = request['binding']
    require(isinstance(binding, dict) and set(binding) == {'project', 'repository', 'scope_revision'}
            and all(isinstance(v, str) and v for v in binding.values())
            and binding == accepted_binding, 'REPORT_SCOPE_BINDING_MISMATCH')
    for field in ('record_id', 'baseline_revision', 'forecast_revision', 'summary', 'owner_action'):
        require(isinstance(request[field], str) and bool(request[field].strip()), 'REPORT_TEXT_REQUIRED')
    timestamp(request['created_at'])
    require(request['report_type'] in REPORT_TYPES, 'REPORT_TYPE_INVALID')
    candidate = request['candidate']
    require(isinstance(candidate, dict) and set(candidate) == {'head', 'base', 'pr'}
            and type(candidate['pr']) is int and candidate['pr'] > 0
            and all(isinstance(candidate[k], str) and re.fullmatch('[0-9a-f]{40}', candidate[k])
                    for k in ('head', 'base')), 'REPORT_CANDIDATE_INVALID')
    period = request['period']
    require(isinstance(period, dict) and set(period) == {'start', 'end', 'catch_up'}
            and type(period['catch_up']) is bool, 'REPORT_PERIOD_INVALID')
    require(timestamp(period['start']) < timestamp(period['end']) <= timestamp(request['created_at']),
            'REPORT_PERIOD_ORDER')
    for key in ('included', 'excluded'):
        values = request[key]
        require(isinstance(values, list) and all(isinstance(k, str) and k for k in values)
                and len(set(values)) == len(values), 'REPORT_SCOPE_KEYS_INVALID')
    require(not set(request['included']) & set(request['excluded']), 'REPORT_SCOPE_OVERLAP')
    tickets = request['tickets']
    require(isinstance(tickets, list) and all(isinstance(t, dict) for t in tickets), 'REPORT_TICKETS_INVALID')
    keys = [t.get('key') for t in tickets]
    require(all(isinstance(k, str) and k for k in keys) and len(set(keys)) == len(keys)
            and set(keys) == set(request['included']), 'REPORT_TICKET_SCOPE_MISMATCH')
    by_key = {t['key']: t for t in tickets}
    for t in tickets:
        require({'key', 'kind', 'epic', 'parent', 'priority', 'size', 'state', 'cohort'} <= t.keys()
                and t.keys() <= {'key', 'kind', 'epic', 'parent', 'priority', 'size', 'state', 'cohort',
                    'conditional', 'planned_start', 'planned_end', 'first_started_at', 'required_prs', 'reconciled_at'},
                'REPORT_TICKET_SHAPE')
        require(t['kind'] in {'epic', 'implementation'} and t['state'] in {
                    'BACKLOG', 'READY', 'WORKING', 'REVIEWING', 'REVIEW_PASSED', 'OWNER_APPROVED',
                    'MERGED', 'OWNER_WAITING', 'BLOCKED', 'PAUSED', 'DONE', 'CANCELLED'}
                and isinstance(t['cohort'], str) and t['cohort'], 'REPORT_TICKET_CLASSIFICATION')
        require(all(t[k] is None or isinstance(t[k], str) for k in ('priority', 'size')), 'REPORT_PRIORITY_SIZE')
        require(type(t.get('conditional', False)) is bool, 'REPORT_CONDITIONAL_INVALID')
        require(t['epic'] is None or (t['epic'] in by_key and by_key[t['epic']]['kind'] == 'epic'),
                'REPORT_EPIC_SCOPE_MISMATCH')
        require(t['parent'] is None or t['parent'] in by_key, 'REPORT_PARENT_SCOPE_MISMATCH')
        seen, current = set(), t
        while current.get('parent'):
            require(current['key'] not in seen, 'REPORT_PARENT_CYCLE')
            seen.add(current['key'])
            current = by_key[current['parent']]
        for field in ('planned_start', 'planned_end'):
            if t.get(field) is not None:
                try:
                    require(date.fromisoformat(t[field]).isoformat() == t[field], 'REPORT_PLANNED_DATE_INVALID')
                except (TypeError, ValueError):
                    require(False, 'REPORT_PLANNED_DATE_INVALID')
        for field in ('first_started_at', 'reconciled_at'):
            if t.get(field):
                require(timestamp(t[field]) <= timestamp(request['created_at']), 'REPORT_FUTURE_ACTUAL')
        prs = t.get('required_prs', [])
        require(isinstance(prs, list), 'REPORT_REQUIRED_PRS_INVALID')
        ids = []
        for pr in prs:
            require(isinstance(pr, dict) and set(pr) == {'id', 'merged_at'}
                    and isinstance(pr['id'], str) and pr['id'], 'REPORT_REQUIRED_PR_INVALID')
            ids.append(pr['id'])
            if pr['merged_at'] is not None:
                require(timestamp(pr['merged_at']) <= timestamp(request['created_at']), 'REPORT_FUTURE_MERGE')
        require(len(set(ids)) == len(ids), 'REPORT_REQUIRED_PR_DUPLICATE')
    require(isinstance(request['sources'], list), 'REPORT_SOURCES_INVALID')
    for source in request['sources']:
        require(isinstance(source, dict) and set(source) == {'field', 'source', 'observed_at', 'stale'}
                and isinstance(source['field'], str) and source['field']
                and isinstance(source['source'], str) and source['source']
                and type(source['stale']) is bool, 'REPORT_SOURCE_INVALID')
        require(timestamp(source['observed_at']) <= timestamp(request['created_at']), 'REPORT_SOURCE_FROM_FUTURE')


def _values(request, admission):
    ticket_sources = [s for s in request['sources'] if s['field'] == 'tickets']
    freshness = ('MISSING' if not ticket_sources else 'STALE' if any(s['stale'] for s in ticket_sources)
                 else 'FRESH' if admission == 'VERIFIED' else 'UNVERIFIED')
    metrics = project_metrics(request['tickets']) if freshness == 'FRESH' else dict(
        status='UNKNOWN', reason='TICKETS_' + freshness)
    return dict(binding=deepcopy(request['binding']), candidate=deepcopy(request['candidate']),
        report_type=request['report_type'], period=deepcopy(request['period']),
        summary=request['summary'], owner_action=request['owner_action'], freshness=freshness,
        sources=deepcopy(request['sources']), baseline_revision=request['baseline_revision'],
        forecast_revision=request['forecast_revision'], metrics=metrics,
        authority='REPORT_ONLY; readiness, owner approval, merge and reconciled Done remain separate')


def prepare_snapshot(request, accepted_binding, *, verify_sources=None):
    request = deepcopy(request)
    validate_request(request, accepted_binding)
    admission = 'UNVERIFIED'
    if callable(verify_sources):
        try:
            observed = verify_sources(deepcopy(request), deepcopy(accepted_binding))
        except Exception:
            raise ValidationError('REPORT_SOURCE_VERIFIER_FAILED') from None
        require(observed == request, 'REPORT_SOURCE_VERIFIER_MISMATCH')
        admission = 'VERIFIED'
    return dict(format='awf-status-report-record-2', record_id=request['record_id'],
        project_id=request['binding']['project'], created_at=request['created_at'], inputs=request,
        inputs_sha256=fingerprint('report-snapshot-2', request), admission=admission,
        values=_values(request, admission))


def replay_snapshot(record):
    require(isinstance(record, dict) and set(record) == {'format', 'record_id', 'project_id', 'created_at',
        'inputs', 'inputs_sha256', 'admission', 'values'} and record['format'] == 'awf-status-report-record-2',
        'REPORT_RECORD_FORMAT')
    request = record['inputs']
    validate_request(request, request['binding'])
    require(record['inputs_sha256'] == fingerprint('report-snapshot-2', request), 'REPORT_INPUT_HASH_MISMATCH')
    require(record['admission'] in {'VERIFIED', 'UNVERIFIED'} and record['record_id'] == request['record_id']
            and record['project_id'] == request['binding']['project'] and record['created_at'] == request['created_at'],
            'REPORT_METADATA_MISMATCH')
    values = _values(request, record['admission'])
    require(values == record['values'], 'REPORT_REPLAY_MISMATCH')
    return values


def readiness_current(record, candidate, accepted_binding):
    """Tuple freshness only; this predicate never establishes gate readiness."""
    values = replay_snapshot(record)
    return (values['binding'] == accepted_binding and values['candidate'] == candidate
            and record['admission'] == 'VERIFIED' and values['freshness'] == 'FRESH')
