"""Pure event projections. The sole controller persists state and owns cadence.

Inputs must already be admitted observations, not ticket instructions. Returned
events grant no authority. Persist events, new state and report intent atomically.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import ValidationError
from ..canonical import fingerprint, timestamp
from .delivery import require


def utc(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError('REPORT_TIMEZONE_UNAVAILABLE') from None


def event(kind, scope, value):
    return dict(id=fingerprint('report-event-1', [kind, scope, value]), kind=kind,
                scope=scope, record=deepcopy(value))


def owner_events(scope, actions, previous):
    """Complete current open-action set; omission means observed resolution.

    Feed a complete verified snapshot only. Unavailable input must not be passed
    as an empty list. Caller retains history; this state is only the open set.
    """
    require(isinstance(scope, str) and scope and isinstance(actions, list), 'REPORT_ACTION_INPUT')
    require(not previous or previous.get('format') == 'awf-owner-actions-1'
            and previous.get('scope') == scope, 'REPORT_ACTION_STATE_SCOPE')
    prior = previous.get('open', {})
    sequence = previous.get('sequence', 0)
    current, events = {}, []
    def emit(kind, action):
        nonlocal sequence
        sequence += 1
        notice = event(kind, scope, dict(sequence=sequence, action=action))
        events.append(notice)
    for action in actions:
        require(isinstance(action, dict) and {'id', 'decision', 'owner', 'due', 'reason', 'options', 'resume'} <= action.keys(),
                'REPORT_ACTION_INPUT')
        key = action['id']
        require(isinstance(key, str) and key and key not in current, 'REPORT_ACTION_DUPLICATE')
        current[key] = deepcopy(action)
        if prior.get(key) != action:
            emit('owner-action-updated' if key in prior else 'owner-action', action)
    for key in sorted(set(prior) - set(current)):
        emit('owner-action-resolved', prior[key])
    return events, dict(format='awf-owner-actions-1', scope=scope, sequence=sequence, open=current)


TUPLE = ('project', 'repository', 'pr', 'head', 'base', 'scope_revision', 'gate')


def readiness_events(candidates, previous):
    """Combine implementation Jira-review and final-human-review into one event.

    Gate booleans represent results from trusted current-head collectors. This
    function does not authenticate them. A full snapshot includes nonready PRs;
    absent formerly ready PRs are withdrawn, never silently retained.
    """
    # Retain the sequence even when every readiness is withdrawn. Legacy maps
    # migrate on their next projection; callers persist the entire returned state.
    if previous.get('format') == 'awf-readiness-2':
        prior, sequence = previous['ready'], previous['sequence']
        require(isinstance(prior, dict) and type(sequence) is int and sequence >= 0, 'REPORT_READINESS_STATE')
    else:
        prior, sequence = previous, 0
    current, events, seen = {}, [], set()
    def emit(kind, key, record):
        nonlocal sequence
        sequence += 1
        notice = event(kind, key, dict(record, occurrence=sequence))
        events.append(notice)
        return notice
    for candidate in candidates:
        require(isinstance(candidate, dict) and all(k in candidate for k in TUPLE), 'REPORT_READINESS_INPUT')
        key = fingerprint('report-pr-1', [candidate[k] for k in ('project', 'repository', 'pr')])
        require(key not in seen, 'REPORT_READINESS_DUPLICATE')
        seen.add(key)
        ready = (candidate.get('stage') == 'READY_FOR_OWNER_AUTHORIZATION'
                 and all(candidate.get(k) is True for k in ('critic', 'specialists', 'final_gate')))
        identity = {k: candidate[k] for k in TUPLE}
        old = prior.get(key)
        if old and (not ready or old != identity):
            emit('correction', key, dict(withdrawn=old, current=identity, reason='READINESS_INVALIDATED'))
        if ready:
            current[key] = identity
            if old != identity:
                notice = emit('final-human-review', key, identity)
                notice['includes_jira_review'] = True
                notice['merge_authorized'] = False
    for key in sorted(set(prior) - seen):
        emit('correction', key, dict(withdrawn=prior[key], reason='NO_CURRENT_OBSERVATION'))
    return events, dict(format='awf-readiness-2', sequence=sequence, ready=current)


def approval_event(scope, revision, approval):
    """One baseline identity from a supplied independently verified approval."""
    require(approval.get('verified') is True and approval.get('revision') == revision
            and approval.get('evidence_id'), 'REPORT_APPROVAL_UNVERIFIED')
    return event('jira-review', scope, dict(revision=revision, approval=approval))


def withdrawal_event(scope, revision, approval):
    """Explicit revocation from an independently verified source envelope.

    A missing/unverified approval alone cannot establish a withdrawal.
    """
    require(approval.get('verified') is False and approval.get('status') == 'REVOKED'
            and approval.get('revision') == revision and approval.get('evidence_id'),
            'REPORT_APPROVAL_WITHDRAWAL_UNVERIFIED')
    return event('baseline-approval-withdrawn', scope, dict(revision=revision, approval=approval))


def local_boundary(day, hour, minute, tz):
    """Later fold; gaps advance to first valid local minute, calendar contract."""
    naive = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    for offset in range(24 * 60 + 1):
        probe = naive + timedelta(minutes=offset)
        valid = []
        for fold in (0, 1):
            candidate = probe.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(tz).replace(tzinfo=None) == probe:
                valid.append(candidate)
        if valid:
            return max(valid)
    raise ValidationError('REPORT_LOCAL_BOUNDARY_UNAVAILABLE')


def due_periods(after, now, operating_root, *, effective_calendar):
    """Return boundaries in (after, now], including labelled missed periods.

    effective_calendar is lane AWF-244's callable over the existing operating
    root. No defaults/validation are duplicated here. UTC identities distinguish
    repeated hours; nonexistent local hours produce no invented report.
    """
    start, end = timestamp(after), timestamp(now)
    require(start <= end and end - start <= timedelta(days=366), 'REPORT_PERIOD_RANGE')
    calendar = effective_calendar(deepcopy(operating_root))
    tz = zone(calendar['timezone'])
    results = []
    # Enumerate real UTC minutes: hourly means local wall clock minute zero,
    # including zones whose offsets are not whole hours.
    cursor = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while cursor <= end:
        local = cursor.astimezone(tz)
        if local.minute == 0:
            previous = cursor - timedelta(hours=1)
            results.append(dict(kind='hourly', start=utc(previous), end=utc(cursor),
                                local_end=local.isoformat(), timezone=calendar['timezone']))
        cursor += timedelta(minutes=1)
    day = start.astimezone(tz).date() - timedelta(days=1)
    last_day = end.astimezone(tz).date()
    hour, minute = map(int, calendar['eod']['time'].split(':'))
    while day <= last_day:
        boundary = local_boundary(day, hour, minute, tz)
        if day.isoweekday() in calendar['eod']['weekdays'] and start < boundary <= end:
            results.append(dict(kind='eod', start=utc(local_boundary(day, 0, 0, tz)), end=utc(boundary),
                                local_end=boundary.astimezone(tz).isoformat(), timezone=calendar['timezone']))
        day += timedelta(days=1)
    for period in results:
        period['id'] = fingerprint('report-period-1', period)
        # A late tick labels lateness even for the latest boundary.
        period['catch_up'] = timestamp(period['end']) < end
    return sorted(results, key=lambda p: (p['end'], p['kind']))


def daily_views(end, previous_daily_end, timezone_name):
    finish = timestamp(end)
    previous = timestamp(previous_daily_end) if previous_daily_end is not None else None
    require(previous is None or previous < finish, 'REPORT_DAILY_ORDER')
    tz = zone(timezone_name)
    return dict(today=dict(start=utc(local_boundary(finish.astimezone(tz).date(), 0, 0, tz)), end=utc(finish)),
                since_previous=dict(start=utc(previous), end=utc(finish)) if previous is not None else None, additive=False,
                label='Overlapping reconciliation views; never add together', interval='(start, end]')
