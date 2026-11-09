"""Reporting extension of the sole controller and outbox, never a service loop.

Context/observations and provider objects are trusted executable host ports.
One serialized controller cycle collects them before this atomic projection.
No usage write, Jira client or dispatch lives here. Admission resolves sender
references transiently through the shared credential filter.
"""
from copy import copy, deepcopy
from dataclasses import dataclass
from datetime import timedelta
import threading
import uuid
from time import monotonic

from .. import ValidationError
from ..canonical import canonical, fingerprint, loads, timestamp
from ..operating_calendar import effective_calendar, next_review_presentation
from .bodies import prepare_body, render_body
from .bounded import DeliveryBudget, MAX_ADVANCES, source_timeout
from .compose import compose_report, describe, number
from .delivery import DeliveryOutbox, delivery_intent, require, same_destination
from .events import approval_event, withdrawal_event, due_periods, owner_events, readiness_events, utc
from .settings import effective_reporting
from .safety import safe_report


def _bindings(operating, binding):
    settings = effective_reporting(operating, binding=binding)
    selected = {'teams', 'slack'} if settings['mode'] == 'both' else {settings['mode']} - {'off'}
    destinations = [b for b in settings['bindings'] if b['platform'] in selected]
    require(safe_report(binding) == binding and all(safe_report(d) == d for d in destinations),
            'REPORT_CREDENTIAL_WITHHELD')
    return settings, destinations


def _readmit_reporting(operating, binding, load_operating):
    require(callable(load_operating), 'REPORT_POLICY_CHECK_REQUIRED')
    _, retained = _bindings(operating, binding)
    current = load_operating()
    settings, destinations = _bindings(current, binding)
    require(settings['mode'] != 'off', 'REPORTING_OFF')
    require(destinations == retained, 'REPORT_DESTINATION_MISMATCH')
    return current


@dataclass(frozen=True)
class PreparedReport:
    encoded: bytes
    deliveries: tuple
    created_at: str


def _preparation_callback(callback):
    def checked(*args, **kwargs):
        source_timeout()
        value = callback(*args, **kwargs)
        source_timeout()
        return value
    return checked


def _preparation_senders(senders):
    prepared = []
    for sender in senders:
        if callable(getattr(sender, 'resolve_secret', None)):
            sender = copy(sender)
            sender.resolve_secret = _preparation_callback(sender.resolve_secret)
        prepared.append(sender)
    return prepared


def _prepare_report(report_id, kind, body, binding, destinations, now, *, extra=None, senders=(), threads=None):
    senders = _preparation_senders(senders)
    data = safe_report(body['data'], trusted_keys=True, senders=senders)
    if data != body['data']:
        body = prepare_body(kind, data)
    require(safe_report(binding, senders=senders) == binding and safe_report(extra, senders=senders) == extra,
            'REPORT_CREDENTIAL_WITHHELD')
    record = dict(report_id=report_id, kind=kind, binding=deepcopy(binding), body=body,
                  delivery_ids=[], **(extra or {}))
    deliveries = []
    for destination in destinations:
        intent = delivery_intent(report_id, destination, render_body(body, destination['platform']),
                                 thread_id=(threads or {}).get(destination['platform']))
        sender = next((s for s in senders if getattr(s, 'binding', None) == destination), None)
        deliveries.append(DeliveryOutbox.prepare_enqueue(intent, resolve_secret=getattr(sender, 'resolve_secret', None)))
        record['delivery_ids'].append(intent['delivery_id'])
    return PreparedReport(canonical(record), tuple(deliveries), now)


# Retain the legacy preparation seam used by historical-record fixtures.
# It prepares bytes only; a database connection never enters this hook.
_retain = _prepare_report


def _retain_prepared(db, prepared):
    record = loads(prepared.encoded.decode())
    prior = db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?', (record['report_id'],)).fetchone()
    if not prior and (record.get('review_restoration') or record.get('review_correction')):
        from ..review_batching import ReviewBatchStore
        ReviewBatchStore.validate_review_notice(db, record)
    for delivery in prepared.deliveries:
        DeliveryOutbox.enqueue_in_transaction(db, loads(delivery.encoded.decode()), prepared=delivery)
    raw = prepared.encoded.decode()
    if prior:
        require(prior[0] == raw, 'REPORT_IDENTITY_REUSED_WITH_DIFFERENT_BYTES')
    else:
        db.execute('INSERT INTO controller_reports VALUES(?,?,?,?)',
                   (record['report_id'], record['kind'], prepared.created_at, raw))
        from ..review_batching import ReviewBatchStore
        ReviewBatchStore.append_review_report(db, record, raw)
    return record


def _projection_inputs(db, binding):
    # Membership, ordered row identities and original bytes are dependencies.
    # Review status is controller-wide, so its complete input sets are retained.
    key = 'reporting:' + fingerprint('reporting-scope', binding)
    row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
    reports = list(list(r) for r in db.execute("""SELECT rowid,* FROM controller_reports
        WHERE (json_extract(payload_json,'$.binding.project')=? AND
               json_extract(payload_json,'$.binding.repository')=?) OR kind='final-human-review' ORDER BY rowid""",
        (binding['project'], binding['repository'])))
    ids = canonical([r[1] for r in reports]).decode()
    deliveries = list(list(r) for r in db.execute("""SELECT rowid,* FROM controller_report_delivery
        WHERE (json_extract(intent_json,'$.binding.project')=? AND
               json_extract(intent_json,'$.binding.repository')=?) OR
              json_extract(intent_json,'$.report_id') IN (SELECT value FROM json_each(?)) ORDER BY rowid""",
        (binding['project'], binding['repository'], ids)))
    review = []
    for table in ('review_presentation_intent_v1', 'review_presentation_detail_v1',
                  'review_presentation_outbox_v1', 'review_presentation_phase_v1'):
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        review.append([table, list(list(r) for r in db.execute('SELECT rowid,* FROM ' + table + ' ORDER BY rowid'))
                       if exists else None])
    deferred = list(list(r) for r in db.execute(
        "SELECT key,value FROM controller_meta WHERE key LIKE 'report-review-deferred:%' ORDER BY key"))
    return dict(cursor=row[0] if row else None, reports=reports, deliveries=deliveries,
                review=review, deferred=deferred)


def _read_projection_inputs(store, binding, now, calendar):
    from ..review_batching import ReviewBatchStore
    with store.connection() as db:
        db.execute('BEGIN')
        try:
            expected = _projection_inputs(db, binding)
            view = ReviewBatchStore.view_from_db(db, now, calendar)
        finally:
            db.execute('ROLLBACK')
    return expected, view


def _projection_attention(snapshot, binding):
    deferred = {loads(raw)['report_id']: loads(raw) for _, raw in snapshot['deferred']}
    states = {r[1]: loads(r[3]) for r in snapshot['deliveries']}
    intents = {r[1]: loads(r[2]) for r in snapshot['deliveries']}
    notices = []
    for row in snapshot['reports']:
        report = loads(row[4])
        if report['binding'] != binding:
            continue
        destinations = [dict(delivery_id=i, platform=intents[i]['binding']['platform'],
            status=states[i]['status'], attempts=len(states[i]['attempts'])) for i in report['delivery_ids']]
        if any(d['status'] in {'FAILED', 'UNKNOWN', 'IN_FLIGHT'} for d in destinations) or (
                report['report_id'] in deferred and any(d['status'] != 'DELIVERED' for d in destinations)):
            notices.append(dict(report_id=report['report_id'], kind=report['kind'], destinations=destinations,
                deferred=deferred.get(report['report_id']),
                owner_action='Inspect delivery evidence, current gate, calendar and destination health; do not repost uncertain sends'))
    return notices


@dataclass(frozen=True)
class PreparedProjection:
    expected: bytes
    cursor: bytes
    reports: tuple
    withdrawals: tuple


def _commit_projection(store, binding, prepared):
    with store.transaction() as db:
        require(canonical(_projection_inputs(db, binding)) == prepared.expected, 'REPORT_PROJECTION_STATE_CHANGED')
        for report in prepared.reports:
            _retain_prepared(db, report)
        for delivery_id, raw in prepared.withdrawals:
            DeliveryOutbox._save(db, delivery_id, loads(raw.decode()))
        key = 'reporting:' + fingerprint('reporting-scope', binding)
        db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, prepared.cursor.decode()))


def _body(kind, binding, now, calendar, context, period=None, action=None):
    supplied = deepcopy(context.get('records'))
    if supplied is not None:
        require(supplied.get('binding') == binding and supplied.get('created_at') == now,
                'REPORT_SOURCE_BINDING_MISMATCH')
        supplied.update(timezone=calendar['timezone'], period=period or {})
        for field in ('capacity', 'accounting'):
            if field in context:
                supplied[field] = deepcopy(context[field])
        supplied['implementation_observed'] = any(s.get('state') == 'WORKING' for s in context.get('streams') or [])
        if context.get('previous_daily_end'):
            supplied['previous_daily_end'] = context['previous_daily_end']
        body = compose_report(kind, supplied, binding, verify_sources=lambda value, accepted: value,
                              verify_link=context.get('verify_link'))
        data = deepcopy(body['data'])
    else:
        data = dict(project=binding['project'], observed_at=now, timezone=calendar['timezone'],
                    scope=binding['scope_revision'], candidate=describe(context.get('candidate')),
                    summary='Unknown: source records unavailable', sections={}, freshness='UNAVAILABLE',
                    period=describe(period), catch_up=bool(period and period['catch_up']))
    data['coverage'] = deepcopy(context.get('coverage', {'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'}))
    data['observations'] = {k: deepcopy(context.get(k)) for k in ('streams', 'gates', 'graph', 'capacity', 'accounting_summary')}
    data['source_records'] = deepcopy(context.get('records'))
    data['sections']['Sources and freshness'] = data['sections'].get('Sources and freshness', 'UNAVAILABLE') + '\nCoverage: ' + describe(data['coverage'])
    if context.get('delivery_attention'):
        data['delivery_attention'] = deepcopy(context['delivery_attention'])
        data['sections']['Sources and freshness'] += '\nDelivery needs owner attention: ' + describe(context['delivery_attention'])
    if context.get('review_batching') is not None:
        data['review_batching'] = deepcopy(context['review_batching'])
        review_text = '\nReview presentation: ' + describe(context['review_batching'])
        title = 'Capacity and review queue' if kind == 'hourly' else 'Sources and freshness'
        data['sections'][title] = data['sections'].get(title, '') + review_text
    if kind == 'hourly':
        if context.get('capacity') is not None and supplied is None:
            data['sections']['Capacity and review queue'] = data['sections'].get('Capacity and review queue', '') + '\nController capacity: ' + describe(context['capacity'])
        if context.get('streams') is not None:
            # Inventory details can be UNKNOWN while controller work is observed.
            # The displayed WIP must follow that observation, not ticket metrics.
            tickets = {t['key']: t for t in (supplied or {}).get('tickets', [])}
            data['current'] = []
            for stream in context['streams']:
                if not stream.get('ticket') or stream['state'] == 'COMPLETE':
                    continue
                ticket = deepcopy(tickets.get(stream['ticket'], {}))
                if not ticket:
                    ticket['planning_observed'] = False
                if not ticket.get('key') or ticket.get('state') == 'UNKNOWN':
                    ticket['title'] = 'Ticket details unavailable'
                ticket.update(key=stream['ticket'], stage=stream['state'],
                    actor=stream['stream'] + ' / ' + stream['actor'],
                    activity=stream['activity'], next_gate=stream['verification_gate'])
                if ticket.get('first_started_at'):
                    try:
                        elapsed = (timestamp(now) - timestamp(ticket['first_started_at'])).total_seconds()
                        ticket['elapsed'] = number(elapsed / 3600) + ' h since first start' if elapsed >= 0 else 'UNAVAILABLE: future start'
                    except (ValidationError, ValueError, TypeError):
                        ticket['elapsed'] = 'UNAVAILABLE: invalid start'
                data['current'].append(ticket)
        if context.get('gates') is not None:
            data['sections']['Changes since the previous report'] = data['sections'].get('Changes since the previous report', '') + '\nGates: ' + describe(context['gates'])
    if kind == 'eod' and context.get('accounting_summary') is not None and not supplied:
        data['sections']['Usage (tokens)'] = describe(context['accounting_summary'])
    if action is not None:
        record = action['record']['action']
        resolved = action['kind'] == 'owner-action-resolved'
        data.update(decision=record['decision'], decision_owner=record['owner'],
                    requested_response=record['due'], options=record['options'], resume=record['resume'],
                    delay_consequence=record['reason'], summary=action['kind'],
                    owner_action=record['decision'], action_resolved=resolved,
                    recommendation=record.get('recommendation', 'Unknown'))
        data['sections']['Owner action'] = ('No further action; resolved: ' if resolved else '') + record['decision']
        data['sections']['Decision / readiness'] = 'Owner action resolved' if resolved else record['decision']
        if resolved:
            data['summary'] = 'Owner action resolved: ' + record['decision']
            data['sections']['Next action'] = 'No further action for this resolved decision'
        data['sections']['Evidence'] = ('Resolution observed for action ' + record['id']
            + '; event: ' + action['id'] if resolved else describe(action))
    return prepare_body(kind, data, verify_link=context.get('verify_link'))


def reporting_status(store, operating, binding, *, report_ids=None):
    settings = effective_reporting(operating, binding=binding)
    reports, deliveries = [], []
    with store.connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controller_reports'").fetchone():
            rows = []
        else:
            ids = canonical(sorted(report_ids)).decode() if report_ids is not None else None
            rows = db.execute('''SELECT payload_json FROM controller_reports
                WHERE (? IS NULL OR report_id IN (SELECT value FROM json_each(?))) ORDER BY rowid''', (ids, ids))
        for row in rows:
            record = loads(row[0])
            if record['binding'] != binding:
                continue
            reports.append(record)
            for delivery_id in record['delivery_ids']:
                intent, state = DeliveryOutbox._load(db, delivery_id)
                deliveries.append(dict(delivery_id=delivery_id, platform=intent['binding']['platform'],
                    destination=intent['binding']['destination'], report_id=record['report_id'], **state))
    coverage = reports[-1]['body']['data'].get('coverage', {}) if reports else {'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'}
    refs = [d['secret_ref'] for d in settings['bindings'] if d['secret_ref'].startswith('env:')]
    return safe_report(dict(settings=settings, reports=reports, deliveries=deliveries, coverage=coverage,
                channel_delivery=bool(deliveries) and all(d['status'] == 'DELIVERED' for d in deliveries),
                execution_authority=False), trusted_keys=True, secret_refs=refs if settings['mode'] != 'off' else ())


def drain(store, operating, binding, providers, *, report_ids=None, allow_send=True, budget=None, before_send=None,
          reserve_review=False, reserve_reconciliation=False):
    settings, destinations = _bindings(operating, binding)
    if settings['mode'] == 'off':
        return []
    budget = budget or DeliveryBudget()
    try:
        budget.remaining()
    except ValidationError:
        return []  # An exhausted shared cycle cannot launch another worker.
    outbox, results = DeliveryOutbox(store), []
    lock = threading.Lock()
    destinations = [d for d in destinations if providers.get(d['platform']) is not None]

    def destination_work(destination):
        platform = destination['platform']
        provider = providers.get(platform)
        if provider is None:
            return
        with store.connection() as db:
            ids = canonical(sorted(report_ids)).decode() if report_ids is not None else None
            rows = db.execute("""WITH actionable AS (
                SELECT r.payload_json,d.delivery_id,r.kind,r.rowid AS sequence,
                    json_extract(d.state_json,'$.status') AS status,
                    json_array_length(json_extract(d.state_json,'$.history')) AS observations,
                    (json_extract(d.state_json,'$.status') IN ('PENDING','ABSENT','THROTTLED')
                     AND coalesce(json_extract(d.state_json,'$.retry_action'),'SEND')<>'RECONCILE') AS fresh
                FROM controller_reports r JOIN controller_report_delivery d
                  ON json_extract(d.intent_json,'$.report_id')=r.report_id
                WHERE json_extract(d.intent_json,'$.binding.platform')=?
                  AND (json_extract(d.intent_json,'$.binding')=json(?) OR
                    (json_remove(json_extract(d.intent_json,'$.binding'),'$.scope_revision')=json(?) AND
                     (json_type(r.payload_json,'$.review_correction') IS NOT NULL OR
                      json_type(r.payload_json,'$.baseline_correction') IS NOT NULL OR
                      json_type(d.state_json,'$.basis_withdrawal') IS NOT NULL OR
                      json_type(d.state_json,'$.approval_withdrawal') IS NOT NULL OR
                      json_extract(r.payload_json,'$.owner_action_event.kind')='owner-action-resolved')))
                  AND json_extract(d.state_json,'$.status') NOT IN ('DELIVERED','FAILED','WITHDRAWN')
                  AND (? OR r.kind<>'final-human-review' OR json_type(d.state_json,'$.basis_withdrawal') IS NOT NULL)
                  AND (? OR json_extract(d.state_json,'$.status') NOT IN ('PENDING','ABSENT','THROTTLED')
                       OR json_extract(d.state_json,'$.retry_action')='RECONCILE')
                  AND NOT EXISTS (SELECT 1 FROM controller_report_delivery dep
                    WHERE dep.delivery_id IN (SELECT value FROM json_each(r.payload_json,'$.after_deliveries'))
                      AND json_extract(dep.intent_json,'$.binding.platform')=json_extract(d.intent_json,'$.binding.platform')
                      AND json_extract(dep.state_json,'$.status') NOT IN ('DELIVERED','WITHDRAWN','FAILED'))
                  AND (? IS NULL OR r.report_id IN (SELECT value FROM json_each(?))))
                SELECT * FROM (SELECT *,row_number() OVER (
                    PARTITION BY CASE WHEN fresh THEN kind ELSE 'history' END
                    ORDER BY CASE WHEN fresh THEN sequence ELSE observations END,sequence) AS position
                    FROM actionable)
                WHERE position<=? ORDER BY fresh DESC,
                    CASE kind WHEN 'owner-action' THEN 0 WHEN 'hourly' THEN 1 ELSE 2 END,position""",
                (platform, canonical(destination).decode(), canonical({k: v for k, v in destination.items()
                    if k != 'scope_revision'}).decode(), report_ids is not None, allow_send, ids, ids,
                 MAX_ADVANCES)).fetchall()
        fresh = [r for r in rows if r['fresh']]
        history = sorted([r for r in rows if not r['fresh']],
                         key=lambda r: (r['observations'], r[3]))[:1]
        if reserve_reconciliation:
            history = []
        slots = MAX_ADVANCES - bool(reserve_review)
        alerts = [r for r in fresh if r[2] == 'owner-action']
        hourly = [r for r in fresh if r[2] == 'hourly']
        daily = [r for r in fresh if r[2] == 'eod']
        # One protected admission for each class, in priority order. Oldest
        # first within a class prevents sustained arrivals starving a backlog.
        selected = alerts[:1] + hourly[:1] + daily[:1]
        if history and len(selected) == slots:
            # Four classes share three reporting slots when review is queued.
            # Alerts retain their first admission; rotate the other classes.
            groups = [group for group in (hourly[:1], daily[:1], history) if group]
            with store.transaction() as db:
                key = 'report-delivery-class-turn:' + platform
                row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
                turn = int(row[0]) if row else 0
                rotated = groups[turn % len(groups):] + groups[:turn % len(groups)]
                chosen = {r[1] for group in rotated[:slots - bool(alerts)] for r in group}
                selected = alerts[:1] + [r for group in groups for r in group if r[1] in chosen]
                db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (key, str(turn + 1)))
        selected_ids = {r[1] for r in selected}
        # Historical reconciliation also gets a turn under sustained pressure.
        if history and len(selected) < slots:
            selected += history
        selected_ids.update(r[1] for r in selected)
        selected += [r for r in fresh if r[1] not in selected_ids][:slots - len(selected)]
        work = selected[:slots]
        # Time can run out before the last class even when count admission is
        # fair. Its next turn omits competing periodic classes, preserving
        # alert-first ordering while giving the deferred class a full chance.
        defer_key = 'report-delivery-time-turn:' + fingerprint('report-destination-turn', destination)
        with store.connection() as db:
            row = db.execute('SELECT value FROM controller_meta WHERE key=?', (defer_key,)).fetchone()
            deferred = row[0] if row else None
        category = lambda row: row[2] if row['fresh'] else 'history'
        if deferred in {'hourly', 'eod', 'history'} and any(category(r) == deferred for r in work):
            work = [r for r in work if category(r) in {'owner-action', deferred}]

        def remember(kind):
            with store.transaction() as db:
                db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (defer_key, kind))

        with budget.destination(platform):
            for row in work:
                record = loads(row[0])
                delivery_provider = provider
                if record['binding'] != binding:
                    # Only retained corrections/resolutions or withdrawn
                    # attempts can cross a scope revision. No retargeting,
                    # credential change, readiness or new request is admitted.
                    with store.connection() as db:
                        intent, retained = DeliveryOutbox._load(db, row[1])
                    from .platform import PlatformReportingAdapter
                    if not (same_destination(intent['binding'], destination)
                            and isinstance(provider, PlatformReportingAdapter)):
                        continue
                    delivery_provider = copy(provider)
                    delivery_provider.binding = deepcopy(intent['binding'])
                    delivery_provider._retained_scope_binding = True
                if not budget.admit(platform, reconciliation=not row['fresh']):
                    if monotonic() >= budget.deadline:
                        remember(category(row))
                    continue
                try:
                    state = outbox.advance(row[1], delivery_provider, allow_send=allow_send,
                                           before_send=before_send if before_send is not None else
                                               lambda: _readmit_reporting(operating, binding, getattr(provider, 'load_policy', None)),
                                           clock=getattr(provider, 'clock', None))
                except Exception:
                    state = dict(status='UNKNOWN', reason='REPORT_PROVIDER_UNAVAILABLE')
                if monotonic() >= budget.deadline:
                    remember(category(row))
                else:
                    # One actual advance (including UNKNOWN readback) earns
                    # the next class a turn; uncertainty cannot monopolize it.
                    with store.transaction() as db:
                        db.execute('DELETE FROM controller_meta WHERE key=? AND value=?', (defer_key, category(row)))
                with lock:
                    results.append(dict(delivery_id=row[1], platform=platform, **state))

    failures = []

    def run_destination(destination):
        try:
            destination_work(destination)
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=run_destination, args=(d,),
                               name='awf-report-' + d['platform'], daemon=False) for d in destinations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0, budget.deadline - monotonic()))
    if any(thread.is_alive() for thread in threads):
        budget.cancel()
        # Revocation comes first; spend the already documented request grace
        # joining workers, rather than returning while rechecks can still send.
        for thread in threads:
            thread.join(max(0, budget.join_deadline(thread) - monotonic()))
    alive = [d['platform'] for d, t in zip(destinations, threads) if t.is_alive()]
    if alive:
        # A trusted port violating its timeout must be visible after restart.
        # The shared token stays revoked even when that port eventually returns.
        with store.transaction() as db:
            db.execute('INSERT INTO controller_meta VALUES(?,?)',
                ('reporting-worker-timeout:' + str(uuid.uuid4()), canonical(dict(
                    reason='REPORT_DESTINATION_WORKER_NOT_QUIESCENT', platforms=alive,
                    cancellation_revoked=True, execution_authority=False)).decode()))
        raise ValidationError('REPORT_DESTINATION_DEADLINE')
    if failures:
        raise ValidationError('REPORT_DESTINATION_STAGE_FAILED') from None
    return results


def delivery_attention(store, operating, binding):
    """Read retained outcomes; never infer absence or reset send attempts."""
    status = reporting_status(store, operating, binding)
    with store.connection() as db:
        deferred = {loads(r[0])['report_id']: loads(r[0]) for r in db.execute(
            "SELECT value FROM controller_meta WHERE key LIKE 'report-review-deferred:%'")}
    notices = []
    for report in status['reports']:
        states = [dict(delivery_id=d['delivery_id'], platform=d['platform'], status=d['status'],
                       attempts=len(d['attempts'])) for d in status['deliveries']
                  if d['report_id'] == report['report_id']]
        if any(d['status'] in {'FAILED', 'UNKNOWN', 'IN_FLIGHT'} for d in states) or (
                report['report_id'] in deferred and any(d['status'] != 'DELIVERED' for d in states)):
            notices.append(dict(report_id=report['report_id'], kind=report['kind'], destinations=states,
                deferred=deferred.get(report['report_id']),
                owner_action='Inspect delivery evidence, current gate, calendar and destination health; do not repost uncertain sends'))
    return notices


def _prepare_projection(snapshot, operating, binding, now, providers, context, calendar, destinations):
    existing = {r[1]: loads(r[4]) for r in snapshot['reports']}
    delivery_values = {r[1]: (loads(r[2]), loads(r[3])) for r in snapshot['deliveries']}
    originals = {i: canonical(v[1]) for i, v in delivery_values.items()}
    prepared_reports, generated = [], []
    def retain(*args, **kwargs):
        prepared = _retain(*args, **kwargs)
        record = loads(prepared.encoded.decode())
        if record['report_id'] in existing:
            require(existing[record['report_id']] == record, 'REPORT_IDENTITY_REUSED_WITH_DIFFERENT_BYTES')
        prepared_reports.append(prepared)
        existing[record['report_id']] = record
        for delivery in prepared.deliveries:
            intent = loads(delivery.encoded.decode())
            identity = intent['delivery_id']
            if identity not in delivery_values:
                state = dict(status='PENDING', attempts=[], history=[], fence=None)
                delivery_values[identity] = (intent, state)
                originals[identity] = canonical(state)
        return record
    def withdraw(delivery_id, notice, *, basis=False):
        intent, state = delivery_values[delivery_id]
        delivery_values[delivery_id] = (intent, DeliveryOutbox.withdraw_state(state, notice, basis=basis))
    attention = _projection_attention(snapshot, binding)
    context['delivery_attention'] = attention
    ordered = sorted(attention, key=lambda n: not any(d['status'] == 'FAILED' for d in n['destinations']))
    context_for_body = dict(context, delivery_attention=dict(total=len(attention), records=ordered[:4],
        remaining='Inspect reporting-status for all retained delivery identities') if attention else None)
    state = loads(snapshot['cursor']) if snapshot['cursor'] is not None else dict(cursor=utc(timestamp(now) - timedelta(microseconds=1)), owner={})
    require(timestamp(state['cursor']) <= timestamp(now), 'REPORT_CLOCK_REGRESSION')
    if state.pop('suspended', False):
        state['cursor'] = utc(timestamp(now) - timedelta(microseconds=1))
    # Bound calendar generation as well as sends. Further downtime remains
    # behind the durable cursor for the next controller cycle.
    end = utc(min(timestamp(now), timestamp(state['cursor']) + timedelta(days=7)))
    periods = due_periods(state['cursor'], end, operating, effective_calendar=effective_calendar)
    hours = [p for p in periods if p['kind'] == 'hourly']
    daily = [p for p in periods if p['kind'] == 'eod']
    summary = hours[-1] if len(hours) > 1 else None
    summarized = [p for p in periods if p['kind'] == 'hourly' or p in daily[:-2]] if summary else []
    for period in periods:
        period['catch_up'] = timestamp(period['end']) < timestamp(now) or period == summary
        report_id = fingerprint('controller-report-period', [binding, period['id']])
        extra = dict(period=deepcopy(period))
        try:
            body = _body(period['kind'], binding, now, calendar,
                         dict(context_for_body, previous_daily_end=state.get('daily_end')), period)
        except ValidationError as exc:
            # Bad merge evidence fails this daily projection explicitly;
            # rendering/identity failures still roll back atomically.
            if str(exc) not in {'REPORT_MERGE_TIMESTAMP_INVALID', 'REPORT_FUTURE_MERGE'}:
                raise
            extra['generation_error'] = str(exc)
            body = _body(period['kind'], binding, now, calendar, dict(context, records=None), period)
            data = dict(body['data'], summary='UNAVAILABLE: ' + str(exc), generation_error=str(exc))
            body = prepare_body(period['kind'], data)
        if period == summary:
            extra['catch_up_periods'] = deepcopy(summarized)
            data = deepcopy(body['data'])
            data['sections']['Changes since the previous report'] = (
                data['sections'].get('Changes since the previous report', '')
                + '\nCATCH-UP: missed hourly periods ending ' + ', '.join(p['end'] for p in hours)
                + '\nSpan: ' + hours[0]['start'] + ' to ' + hours[-1]['end']
                + '\nOlder End of Day dates (retained, no separate post): '
                + (', '.join(p['local_end'][:10] for p in daily[:-2]) or 'None'))
            body = prepare_body('hourly', data, verify_link=context.get('verify_link'))
        send = (period['kind'] == 'hourly' and (summary is None or period == summary)
                or period['kind'] == 'eod' and period in daily[-2:])
        if not send:
            extra['delivery_policy'] = 'LISTED_IN_CATCH_UP'
        generated.append(retain(report_id, period['kind'], body, binding,
                                 destinations if send else [], now, extra=extra,
                                 senders=(providers or {}).values()))
        if period['kind'] == 'eod':
            state['daily_end'] = period['end']
    notice = context.get('baseline_approval')
    if notice is not None:
        require(notice == approval_event(binding['scope_revision'], notice['record']['revision'],
                                        notice['record']['approval']), 'REPORT_APPROVAL_SCOPE_MISMATCH')
        report_id = fingerprint('report-approved-baseline-1', [binding, notice['id']])
        # Prepare intent and delivery identities for atomic retention. A
        # repeated observation never rebuilds a body with a newer time,
        # even after intervening approvals or controller restarts.
        if not existing.get(report_id):
            generated.append(retain(report_id, 'jira-review',
                _body('jira-review', binding, now, calendar, context), binding, destinations, now,
                extra={'baseline_approval': notice}, senders=(providers or {}).values()))
    withdrawal = context.get('baseline_withdrawal')
    if withdrawal is not None:
        require(withdrawal == withdrawal_event(binding['scope_revision'], withdrawal['record']['revision'],
                withdrawal['record']['approval']), 'REPORT_APPROVAL_SCOPE_MISMATCH')
    # Immutable baseline reports remain history. Delivery withdrawal is
    # durable even if later sources disappear, or readback arrives later.
    for prior in [r for r in existing.values() if r['kind'] == 'jira-review']:
        approval = prior.get('baseline_approval')
        if any(prior['binding'][k] != binding[k] for k in ('project', 'repository')) or not approval:
            continue
        scope_changed = prior['binding'] != binding
        for delivery_id in prior['delivery_ids']:
            if scope_changed:
                withdraw(delivery_id,
                    dict(reason='SCOPE_CHANGED', prior=prior['binding'], current=binding))
            elif withdrawal and approval['record']['revision'] == withdrawal['record']['revision']:
                withdraw(delivery_id, withdrawal)
            intent, delivery = delivery_values[delivery_id]
            invalidated = delivery.get('approval_withdrawal')
            destination = next((d for d in destinations if same_destination(d, intent['binding'])), None)
            if not invalidated or delivery['status'] != 'DELIVERED' or destination is None:
                continue
            correction_id = fingerprint('report-baseline-correction-1', delivery_id)
            if existing.get(correction_id):
                continue
            readback = next(h for h in reversed(delivery['history']) if h.get('status') == 'DELIVERED')
            notice = dict(prior_report=prior['report_id'], delivery_id=delivery_id, withdrawal=invalidated)
            body = prepare_body('owner-action', dict(project=binding['project'], observed_at=now,
                timezone=calendar['timezone'], scope=binding['scope_revision'], candidate=approval['record']['revision'],
                summary='Correction: prior backlog approval withdrawn', sections={
                    'Evidence': describe(notice),
                    'Owner action': 'Do not rely on prior backlog approval; await current owner approval'}))
            generated.append(retain(correction_id, 'owner-action', body, binding, [destination], now,
                extra={'baseline_correction': notice},
                threads={intent['binding']['platform']: intent.get('thread_id') or readback['message_id']},
                senders=(providers or {}).values()))
    # A prior scope's readiness may already have landed without readback.
    # Fence new sends, but keep reconciling its immutable attempts so a
    # later visible receipt can seed the original-thread correction.
    for prior in [r for r in existing.values() if r['kind'] == 'final-human-review']:
        if prior['binding'] == binding or any(prior['binding'][k] != binding[k] for k in ('project', 'repository')):
            continue
        for delivery_id in prior['delivery_ids']:
            withdraw(delivery_id,
                dict(reason='SCOPE_CHANGED', prior=prior['binding'], current=binding), basis=True)
    if context.get('owner_actions') is not None:
        events, state['owner'] = owner_events(fingerprint('reporting-scope', binding), context['owner_actions'], state['owner'])
        current = {a['id']: a for a in context['owner_actions']}
        predecessors = {}
        for prior in [r for r in existing.values() if r['kind'] == 'owner-action']:
            event = prior.get('owner_action_event')
            if any(
                    prior['binding'][k] != binding[k] for k in ('project', 'repository')):
                continue
            if not event:
                # Pre-metadata immutable intents carry the original action
                # fields in the body. Preserve a still-current request;
                # otherwise fence it without rewriting historical bytes.
                data = prior['body']['data']
                if data.get('action_resolved'):
                    reopened = [a for a in current.values() if data.get('sections', {}).get('Evidence') ==
                        'Resolution observed for action ' + a['id'] + '; event: ' + prior['report_id']]
                    for action in reopened:
                        predecessors.setdefault(action['id'], []).extend(prior['delivery_ids'])
                        for delivery_id in prior['delivery_ids']:
                            withdraw(delivery_id,
                                dict(reason='OWNER_ACTION_REOPENED', observed_at=now, action_id=action['id']), basis=True)
                    continue
                if data.get('summary') not in {'owner-action', 'owner-action-updated'}:
                    continue
                def matches(action):
                    return all(data.get(field) == action.get(key, 'Unknown' if key == 'recommendation' else None)
                        for field, key in (
                        ('decision', 'decision'), ('decision_owner', 'owner'), ('requested_response', 'due'),
                        ('delay_consequence', 'reason'), ('options', 'options'), ('resume', 'resume'),
                        ('recommendation', 'recommendation')))
                if prior['binding'] == binding and any(matches(a) for a in current.values()):
                    continue
                for notice in events:
                    if matches(notice['record']['action']):
                        predecessors.setdefault(notice['record']['action']['id'], []).extend(prior['delivery_ids'])
                for delivery_id in prior['delivery_ids']:
                    withdraw(delivery_id,
                        dict(reason='OWNER_ACTION_BASIS_CHANGED', observed_at=now, prior_report=prior['report_id']), basis=True)
                continue
            action = event['record']['action']
            resolved = event['kind'] == 'owner-action-resolved'
            if (resolved and action['id'] not in current or not resolved
                    and prior['binding'] == binding and current.get(action['id']) == action):
                continue
            predecessors.setdefault(action['id'], []).extend(prior['delivery_ids'])
            for delivery_id in prior['delivery_ids']:
                withdraw(delivery_id,
                    dict(reason='OWNER_ACTION_BASIS_CHANGED', observed_at=now, action_id=action['id']), basis=True)
        for action in events:
            generated.append(retain(action['id'], 'owner-action',
                _body('owner-action', binding, now, calendar, context, action=action), binding, destinations, now,
                extra={'owner_action_event': action,
                       'after_deliveries': predecessors.get(action['record']['action']['id'], [])},
                senders=(providers or {}).values()))
    for notice in context['delivery_attention']:
        # Failures of an owner-action alert remain in periodic diagnostics;
        # recursively alerting about alerts would create an infinite queue.
        if notice['kind'] != 'final-human-review' or not (notice.get('deferred') or any(
                d['status'] == 'FAILED' or d['status'] == 'UNKNOWN' and d['attempts'] == 2
                for d in notice['destinations'])):
            continue
        notice_id = fingerprint('report-delivery-attention', [binding, notice['report_id']])
        if existing.get(notice_id):
            continue
        body = prepare_body('owner-action', dict(project=binding['project'], observed_at=now,
            timezone=calendar['timezone'], scope=binding['scope_revision'], candidate=notice['report_id'],
            summary='Report delivery needs owner attention', sections={
                'Owner action': notice['owner_action'], 'Evidence': describe(notice)}))
        generated.append(retain(notice_id, 'owner-action', body, binding, destinations, now,
                                 senders=(providers or {}).values()))
    for notice in context.get('review_corrections', []):
        destination = next((d for d in destinations if same_destination(d, notice['binding'])), None)
        if destination is None:
            continue  # Never retarget a correction after channel rotation.
        if notice.get('restored'):
            body = prepare_body('final-human-review', dict(project=binding['project'], observed_at=now,
                timezone=calendar['timezone'], scope=binding['scope_revision'],
                candidate=describe([m['candidate'] for m in notice['review_batch']['members']]),
                summary='Readiness restored: current candidate ready for final human review',
                sections={'Evidence': describe(notice),
                          'Owner action': 'Review the current candidates; merge remains an owner decision'}))
            generated.append(retain(notice['id'], 'final-human-review', body, binding, [destination], now,
                extra={'review_restoration': notice, 'review_batch': notice['review_batch']},
                threads={destination['platform']: notice['thread_id']}, senders=(providers or {}).values()))
            continue
        body = prepare_body('owner-action', dict(project=binding['project'], observed_at=now,
            timezone=calendar['timezone'], scope=binding['scope_revision'],
            candidate=describe(notice['invalidated']), summary='Correction: prior readiness withdrawn',
            sections={'Evidence': describe(notice),
                      'Owner action': 'Do not rely on prior readiness; await current candidate review'}))
        generated.append(retain(notice['id'], 'owner-action', body, binding, [destination], now,
            extra={'review_correction': notice}, threads={destination['platform']: notice['thread_id']},
            senders=(providers or {}).values()))
    candidates = context.get('readiness_candidates')
    if candidates is not None:
        require(all(all(c.get(k) == binding[k] for k in binding) for c in candidates), 'REPORT_SCOPE_BINDING_MISMATCH')
        events, state['readiness'] = readiness_events(candidates, state.get('readiness', {}))
        for notice in events:
            if notice['kind'] != 'correction':
                continue  # Lane C exclusively presents final-review readiness.
            body = prepare_body('owner-action', dict(project=binding['project'], observed_at=now,
                timezone=calendar['timezone'], scope=binding['scope_revision'], candidate=describe(notice['record']),
                summary='Correction: prior readiness withdrawn', sections={'Evidence': describe(notice),
                    'Owner action': 'Do not rely on prior readiness; await current candidate review'}))
            generated.append(retain(notice['id'], 'owner-action', body, binding, destinations, now,
                                     senders=(providers or {}).values()))
    state['cursor'] = end
    withdrawals = tuple((i, canonical(v[1])) for i, v in delivery_values.items() if canonical(v[1]) != originals[i])
    return PreparedProjection(canonical(snapshot), canonical(state), tuple(prepared_reports), withdrawals), generated


def reporting_cycle(store, *, operating, binding, now, providers=None, context=None, delivery_budget=None,
                    defer_delivery=True, load_operating=None):
    settings, destinations = _bindings(operating, binding)
    key = 'reporting:' + fingerprint('reporting-scope', binding)
    if settings['mode'] == 'off':
        with store.connection() as db:
            existing = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
        if existing is None:
            return dict(state='OFF', generated=0, reports=[], deliveries=[], coverage={'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})
        timestamp(now)
        with store.transaction() as db:
            row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
            if row is None:
                return dict(state='OFF', generated=0, reports=[], deliveries=[])
            state = loads(row[0])
            require('cursor' not in state or timestamp(state['cursor']) <= timestamp(now), 'REPORT_CLOCK_REGRESSION')
            state.update(cursor=now, suspended=True)
            db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, canonical(state).decode()))
        return dict(state='OFF', generated=0, reports=[], deliveries=[], coverage={'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})
    timestamp(now)
    calendar = effective_calendar(operating)
    delivery_budget = delivery_budget or DeliveryBudget()
    with delivery_budget.checking():
        delivery_budget.remaining()
        snapshot, review_view = _read_projection_inputs(store, binding, now, calendar)
        context = safe_report(context or {}, senders=_preparation_senders((providers or {}).values()))
        if callable(context.get('verify_link')):
            context['verify_link'] = _preparation_callback(context['verify_link'])
        delivery_budget.remaining()
        context['review_batching'] = review_view
        prepared, generated = _prepare_projection(snapshot, operating, binding, now, providers, context, calendar, destinations)
        delivery_budget.remaining()
    _commit_projection(store, binding, prepared)
    deliveries = [] if defer_delivery else drain(store, operating, binding, providers or {}, budget=delivery_budget,
        before_send=(lambda: _readmit_reporting(operating, binding, load_operating)) if load_operating is not None else None)
    return dict(state='ENABLED', generated=len(generated), reports=generated, deliveries=deliveries,
                coverage=deepcopy(context.get('coverage', {'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})))


class ReviewReportingPort:
    """Lane C's exact batch seam; only channel evidence acknowledges success.

    Reporting owns per-destination retry identities after durable handoff.
    Exhaustion closes FAILED without claiming absence or multiplying retries.
    """
    def __init__(self, store, operating, binding, providers, clock, *, observe_candidate=None, delivery_budget=None,
                 load_operating=None):
        self.store, self.operating, self.binding = store, operating, binding
        self.providers, self.clock = providers, clock
        self.observe_candidate = observe_candidate
        self.delivery_budget = delivery_budget
        self.load_operating = load_operating

    def _readmit(self):
        if self.load_operating is not None:
            return _readmit_reporting(self.operating, self.binding, self.load_operating)
        require(bool(self.providers), 'REPORT_POLICY_CHECK_REQUIRED')
        for provider in self.providers.values():
            current = _readmit_reporting(self.operating, self.binding, getattr(provider, 'load_policy', None))
        return current

    def _id(self, payload):
        return 'review-' + payload['batch_id']

    def _defer(self, report_id, reason, *, retry_at=None):
        with self.store.transaction() as db:
            db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                ('report-review-deferred:' + report_id, canonical(dict(
                    report_id=report_id, reason='REPORT_DESTINATION_BUDGET' if reason == 'REPORT_DESTINATION_BUDGET'
                    else reason if reason == 'REPORT_REVIEW_WINDOW_EXHAUSTED'
                    else 'REPORT_REVIEW_CHECK_REFUSED', observed_at=self.clock(),
                    retry_at=retry_at,
                    owner_action='Inspect review observation latency, gate and calendar; newer presentations remain independent')).decode()))

    def _check(self, payload, calendar, observe_candidate, *, budget=None, restoration=False,
               restoration_report_id=None, restoration_window=None):
        from ..review_batching import ReviewBatchStore
        require(all(str(m['candidate']['repository_id']) == self.binding['repository'] for m in payload['members']),
                'REPORT_SCOPE_BINDING_MISMATCH')
        from .bounded import source_timeout
        budget = budget or self.delivery_budget or DeliveryBudget()
        with budget.checking():
            now, checked = self.clock(), {}
            # Observe outside SQLite's write transaction: no remote callback
            # may hold persistence hostage or prevent a durable timeout record.
            for member in payload['members'] if observe_candidate is not None else ():
                source_timeout()
                candidate = member['candidate']
                value = observe_candidate(deepcopy(candidate), now)
                source_timeout()
                checked[fingerprint('review-candidate', candidate)] = value
            if restoration and observe_candidate is not None:
                from ..review_batching import _observation_valid
                require(all(_observation_valid(checked[fingerprint('review-candidate', m['candidate'])],
                    m['candidate'], timestamp(now)) for m in payload['members']), 'REPORT_REVIEW_CHECK_EXPIRED')
            elif observe_candidate is not None:
                checked_payload = ReviewBatchStore(self.store).recheck(payload['batch_id'], now, calendar,
                    lambda candidate: checked[fingerprint('review-candidate', candidate)])
                require(checked_payload == payload, 'REPORT_REVIEW_BATCH_MISMATCH')
            calendar = effective_calendar(self._readmit())
            send_at = self.clock()
            window = next_review_presentation(send_at, calendar)
            require(not window['held'] and (restoration or window['calendar_sha256'] == payload['calendar_sha256']) and
                    all(timestamp(v['expires_at']) > timestamp(send_at) for v in checked.values()),
                    'REPORT_REVIEW_CHECK_EXPIRED')
            ReviewBatchStore(self.store).check_delivery_window(payload['batch_id'], send_at, calendar,
                reporting=True, restoration=restoration, restoration_report_id=restoration_report_id,
                restoration_window=restoration_window)
            return calendar

    def _retain_review_batch(self, payload, *, budget=None):
        """Retain the exact reporting handoff without provider side effects."""
        settings, destinations = _bindings(self.operating, self.binding)
        require(settings['mode'] != 'off', 'REPORTING_OFF')
        now = self.clock()
        data = dict(project=self.binding['project'], scope=self.binding['scope_revision'],
            observed_at=now, timezone=effective_calendar(self.operating)['timezone'],
            candidate=describe([m['candidate'] for m in payload['members']]),
            summary='Combined final human review / Jira review passed batch', sections={
                'Evidence': describe(payload), 'Owner action': 'Review the exact candidates; merge remains an owner decision'},
            coverage={'gates': 'CURRENT_AT_PRESENTATION', 'accounting': 'UNAVAILABLE'})
        body = prepare_body('final-human-review', data)
        from ..review_batching import ReviewBatchStore
        budget = budget or self.delivery_budget or DeliveryBudget()
        with budget.checking():
            expected, _ = _read_projection_inputs(self.store, self.binding, now, effective_calendar(self.operating))
            budget.remaining()
            prepared = _prepare_report(self._id(payload), 'final-human-review', body, self.binding, destinations, now,
                extra={'review_batch': deepcopy(payload)}, senders=self.providers.values())
            budget.remaining()
        with self.store.transaction() as db:
            require(_projection_inputs(db, self.binding) == expected, 'REPORT_PROJECTION_STATE_CHANGED')
            terminal = ReviewBatchStore._reporting_failure_intents(db)
            if any(m['intent_id'] in terminal for m in payload['members']):
                prior = db.execute('SELECT state FROM review_presentation_outbox_v1 WHERE batch_id=?',
                                   (payload['batch_id'],)).fetchone()
                # An idempotent call for the original failed batch is harmless.
                # A stale replacement claim from an older runtime cannot take
                # ownership or create another report/platform allowance.
                require(prior is not None and prior[0] == 'FAILED', 'REPORT_REVIEW_INTENT_TERMINAL')
            existing = db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?', (self._id(payload),)).fetchone()
            if existing:
                require(loads(existing[0]).get('review_batch') == payload, 'REPORT_REVIEW_BATCH_MISMATCH')
            else:
                _retain_prepared(db, prepared)
        ReviewBatchStore(self.store).track_reporting(payload['batch_id'])

    def deliver_review_batch(self, payload):
        from ..review_batching import ReviewBatchStore
        budget = self.delivery_budget or DeliveryBudget()
        with self.store.connection() as db:
            phase = db.execute('SELECT phase FROM review_presentation_phase_v1 WHERE batch_id=?',
                               (payload['batch_id'],)).fetchone()
        if phase is not None and phase[0] == 'PREPARED':
            # Direct callers may hand off before fencing. Establish the same
            # durable charge as the controller, using fresh candidate evidence.
            require(callable(self.observe_candidate), 'REPORT_REVIEW_CHECK_REQUIRED')
            with budget.checking():
                calendar = effective_calendar(self._readmit())
                now = self.clock()
                pending = ReviewBatchStore(self.store)
                checked = pending.recheck(payload['batch_id'], now, calendar,
                    lambda candidate: self.observe_candidate(candidate, now))
                require(checked == payload, 'REPORT_REVIEW_BATCH_MISMATCH')
                pending.begin_delivery(payload['batch_id'], self.clock())
        self._retain_review_batch(payload, budget=budget)
        # The controller already rechecked this claim. The mandatory port
        # recheck below runs after destination verification, immediately before
        # reservation/POST, rather than adding another identical remote read
        # between those two checks. Direct port callers use the same guard.
        refused = []
        def before_send():
            try:
                self._check(payload, effective_calendar(self.operating), self.observe_candidate, budget=budget)
            except ValidationError as exc:
                refused.append(exc)
                raise
        drain(self.store, self.operating, self.binding, self.providers, report_ids={self._id(payload)}, budget=budget,
              before_send=before_send)
        if refused:
            self._defer(self._id(payload), str(refused[0]))
            raise refused[0]
        return self.observe_review_delivery(payload, budget=budget)

    def observe_review_delivery(self, payload, *, budget=None):
        # This port cannot send, even when terminal absence admits a retry.
        budget = budget or self.delivery_budget or DeliveryBudget()
        drain(self.store, self.operating, self.binding, self.providers,
              report_ids={self._id(payload)}, allow_send=False, budget=budget)
        status = reporting_status(self.store, self.operating, self.binding, report_ids={self._id(payload)})
        reports = [r for r in status['reports'] if r['report_id'] == self._id(payload)]
        if not reports:
            # Recover the reporting handoff before the batching cycle renders
            # raw-send owner alerts. This readback port still never sends;
            # resume owns fresh admission and the per-platform attempts.
            # PREPARED claims retain AWF-244's provably-unsent release path.
            with self.store.connection() as db:
                phase = db.execute('SELECT phase FROM review_presentation_phase_v1 WHERE batch_id=?',
                                   (payload['batch_id'],)).fetchone()
            if phase is None or phase[0] == 'SENDING':
                self._retain_review_batch(payload, budget=budget)
            return None
        report = reports[0]
        require(report.get('review_batch') == payload, 'REPORT_REVIEW_BATCH_MISMATCH')
        states = [d for d in status['deliveries'] if d['delivery_id'] in report['delivery_ids']]
        from ..review_batching import ReviewBatchStore
        pending = ReviewBatchStore(self.store)
        pending.track_reporting(payload['batch_id'])
        pending.finish_reporting_failure(payload['batch_id'], self.clock())
        if states and all(s['status'] == 'DELIVERED' for s in states):
            observed_at = max((h['observed_at'] for s in states for h in s['history'] if h.get('status') == 'DELIVERED'), key=timestamp)
            return dict(batch_id=payload['batch_id'], payload_sha256=payload['payload_sha256'],
                status='DELIVERED', observed_at=observed_at,
                provider_receipt_id=fingerprint('report-review-receipt', states))
        return None

    def resume(self, calendar, observe_candidate, *, batch_ids=None):
        """Controller-only retry: repeat lane C's pre-send check for this batch."""
        from ..review_batching import ReviewBatchStore
        pending = ReviewBatchStore(self.store)
        budget = self.delivery_budget or DeliveryBudget()
        pending.finish_restoration_readback(self.clock())
        # Restoration is a new presentation occurrence, never a reopened batch
        # or a reset of its previous per-platform delivery attempts.
        status = reporting_status(self.store, self.operating, self.binding)
        restorations = [r for r in status['reports'] if r.get('review_restoration') and any(
            d['report_id'] == r['report_id'] and d['status'] not in {'DELIVERED', 'FAILED'}
            for d in status['deliveries'])]
        payloads = pending.pending_batches(batch_ids=batch_ids)
        restoration_groups = {}
        for report in restorations:
            with self.store.connection() as db:
                key = pending.restoration_identity(db, report['report_id'])
            restoration_groups.setdefault(key, []).append(report)
        work = sorted([(key, 'restoration', group) for key, group in restoration_groups.items()] +
                      [(self._id(p), 'batch', p) for p in payloads])
        with self.store.transaction() as db:
            key = 'report-review-resume-cursor'
            prior = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
            cursor = prior[0] if prior else ''
            selected = ([v for v in work if v[0] > cursor] + [v for v in work if v[0] <= cursor])[:1]
            if selected:
                db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (key, selected[0][0]))
        restorations = [report for _, kind, group in selected if kind == 'restoration' for report in group]
        payloads = [v for _, kind, v in selected if kind == 'batch']
        if restorations:
            from ..review_batching import _window
            report_ids = {r['report_id'] for r in restorations}
            payload = restorations[0]['review_batch']
            # A later controller turn can admit the next opening. Crossing it
            # during this turn's provider verification must still refuse.
            admission_window = _window(self.clock(), calendar)
            def defer(exc, accepted_calendar):
                from ..review_batching import _window
                retry = (getattr(exc, 'retry_at', _window(self.clock(), accepted_calendar)['next_opening'])
                         if str(exc) == 'REPORT_REVIEW_WINDOW_EXHAUSTED' else None)
                for report_id in report_ids:
                    self._defer(report_id, str(exc), retry_at=retry)
            def before_send():
                try:
                    accepted_calendar = self._check(payload, calendar, observe_candidate, budget=budget,
                        restoration=True, restoration_report_id=restorations[0]['report_id'],
                        restoration_window=admission_window)
                except ValidationError as exc:
                    defer(exc, calendar)
                    raise
                def reserve(db, intent):
                    try:
                        pending.reserve_restoration(db, payload['batch_id'], intent['report_id'],
                                                    self.clock(), accepted_calendar, admission_window)
                    except ValidationError as exc:
                        # The delivery transaction rolls back; retain the refusal
                        # after it releases the writer lock (see below).
                        refusals.append((exc, accepted_calendar))
                        raise
                return reserve
            # The outbox invokes this guard only at reservation/POST. UNKNOWN
            # readback needs neither readiness nor an open presentation window.
            # Submit the whole occurrence to the ordinary concurrent drain;
            # per-report drains would serialize its destination-specific rows.
            refusals = []
            drain(self.store, self.operating, self.binding, self.providers,
                  report_ids=report_ids, budget=budget, before_send=before_send)
            for exc, accepted_calendar in refusals:
                defer(exc, accepted_calendar)
            pending.finish_restoration_readback(self.clock())

        for payload in payloads:
            report_id = self._id(payload)
            status = reporting_status(self.store, self.operating, self.binding, report_ids={report_id})
            states = [s for s in status['deliveries'] if s['report_id'] == report_id]
            if not states:
                # Recovery of a crash between claim/recheck and report intent.
                # Same immutable batch, never another presentation attempt.
                self.deliver_review_batch(payload)
                continue
            pending.track_reporting(payload['batch_id'])
            pending.finish_reporting_failure(payload['batch_id'], self.clock())
            if not any(s['status'] in {'PENDING', 'ABSENT', 'THROTTLED'} for s in states):
                continue
            try:
                self._check(payload, calendar, observe_candidate, budget=budget)
            except ValidationError as exc:
                # Budget deferral is expected scheduling, durably visible to
                # the owner rather than an unreported forever-CLAIMED record.
                self._defer(report_id, str(exc))
                if str(exc) == 'REPORT_DESTINATION_BUDGET':
                    return
                raise
            drain(self.store, self.operating, self.binding, self.providers, report_ids={report_id}, budget=budget,
                  before_send=lambda: self._check(payload, calendar, observe_candidate, budget=budget))
            self.observe_review_delivery(payload, budget=budget)
            try:
                budget.remaining()
            except ValidationError as exc:
                self._defer(report_id, str(exc))
