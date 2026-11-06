"""Reporting extension of the sole controller and outbox, never a service loop.

Context/observations and provider objects are trusted executable host ports.
One serialized controller cycle collects them before this atomic projection.
No usage write, Jira client or dispatch lives here. Admission resolves sender
references transiently through the shared credential filter.
"""
from copy import deepcopy
from datetime import timedelta
import threading
from time import monotonic

from .. import ValidationError
from ..canonical import canonical, fingerprint, loads, timestamp
from ..operating_calendar import effective_calendar, next_review_presentation
from .bodies import prepare_body, render_body
from .bounded import DeliveryBudget, MAX_ADVANCES
from .compose import compose_report, describe
from .delivery import DeliveryOutbox, delivery_intent, require
from .events import due_periods, owner_events, readiness_events, utc
from .settings import effective_reporting
from .safety import safe_report


def _bindings(operating, binding):
    settings = effective_reporting(operating, binding=binding)
    selected = {'teams', 'slack'} if settings['mode'] == 'both' else {settings['mode']} - {'off'}
    destinations = [b for b in settings['bindings'] if b['platform'] in selected]
    require(safe_report(binding) == binding and all(safe_report(d) == d for d in destinations),
            'REPORT_CREDENTIAL_WITHHELD')
    return settings, destinations


def _retain(db, report_id, kind, body, binding, destinations, now, *, extra=None, senders=(), threads=None):
    data = safe_report(body['data'], trusted_keys=True, senders=senders)
    if data != body['data']:
        body = prepare_body(kind, data)
    require(safe_report(binding, senders=senders) == binding and safe_report(extra, senders=senders) == extra,
            'REPORT_CREDENTIAL_WITHHELD')
    record = dict(report_id=report_id, kind=kind, binding=deepcopy(binding), body=body,
                  delivery_ids=[], **(extra or {}))
    for destination in destinations:
        intent = delivery_intent(report_id, destination, render_body(body, destination['platform']),
                                 thread_id=(threads or {}).get(destination['platform']))
        DeliveryOutbox.enqueue_in_transaction(db, intent)
        record['delivery_ids'].append(intent['delivery_id'])
    raw = canonical(record).decode()
    prior = db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?', (report_id,)).fetchone()
    if prior:
        require(prior[0] == raw, 'REPORT_IDENTITY_REUSED_WITH_DIFFERENT_BYTES')
    else:
        db.execute('INSERT INTO controller_reports VALUES(?,?,?,?)', (report_id, kind, now, raw))
    return record


def _body(kind, binding, now, calendar, context, period=None, action=None):
    supplied = deepcopy(context.get('records'))
    if supplied is not None:
        require(supplied.get('binding') == binding and supplied.get('created_at') == now,
                'REPORT_SOURCE_BINDING_MISMATCH')
        supplied.update(timezone=calendar['timezone'], period=period or {},
                        capacity=context.get('capacity'), accounting=context.get('accounting', {'mode': 'off'}))
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
    if kind == 'hourly':
        data['sections']['Capacity and review queue'] = describe(context.get('capacity'))
        data['sections']['Current work'] = describe(context.get('streams'))
        if context.get('streams') is not None:
            # Inventory details can be UNKNOWN while controller work is observed.
            # The displayed WIP must follow that observation, not ticket metrics.
            tickets = {t['key']: t for t in (supplied or {}).get('tickets', [])}
            data['current'] = []
            for stream in context['streams']:
                if not stream.get('ticket') or stream['state'] == 'COMPLETE':
                    continue
                ticket = deepcopy(tickets.get(stream['ticket'], {}))
                if not ticket or ticket.get('state') == 'UNKNOWN':
                    ticket['title'] = 'Ticket details unavailable'
                ticket.update(key=stream['ticket'], stage=stream['state'],
                    actor=stream['stream'] + ' / ' + stream['actor'],
                    activity=stream['activity'], next_gate=stream['verification_gate'])
                data['current'].append(ticket)
        data['sections']['Changes since the previous report'] = 'Gates: ' + describe(context.get('gates'))
    if kind == 'eod' and context.get('accounting_summary') is not None:
        data['sections']['Usage (tokens)'] = describe(context['accounting_summary'])
    if action is not None:
        record = action['record']['action']
        data.update(decision=record['decision'], decision_owner=record['owner'],
                    requested_response=record['due'], options=record['options'], resume=record['resume'],
                    delay_consequence=record['reason'], summary=action['kind'],
                    owner_action=record['decision'])
        data['sections']['Owner action'] = record['decision']
        data['sections']['Evidence'] = describe(action)
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


def drain(store, operating, binding, providers, *, report_ids=None, allow_send=True, budget=None, before_send=None):
    settings, destinations = _bindings(operating, binding)
    if settings['mode'] == 'off':
        return []
    budget = budget or DeliveryBudget()
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
                  AND json_extract(d.intent_json,'$.binding')=json(?)
                  AND json_extract(d.state_json,'$.status') NOT IN ('DELIVERED','FAILED')
                  AND (? OR r.kind<>'final-human-review')
                  AND (? IS NULL OR r.report_id IN (SELECT value FROM json_each(?))))
                SELECT * FROM (SELECT *,row_number() OVER (
                    PARTITION BY CASE WHEN fresh THEN kind ELSE 'history' END
                    ORDER BY CASE WHEN fresh THEN -sequence ELSE observations END,sequence) AS position
                    FROM actionable)
                WHERE position<=? ORDER BY fresh DESC,
                    CASE kind WHEN 'owner-action' THEN 0 WHEN 'hourly' THEN 1 ELSE 2 END,position""",
                (platform, canonical(destination).decode(), report_ids is not None, ids, ids,
                 MAX_ADVANCES)).fetchall()
        fresh = [r for r in rows if r['fresh']]
        history = sorted([r for r in rows if not r['fresh']],
                         key=lambda r: (r['observations'], r[3]))[:1]
        slots = MAX_ADVANCES - bool(history)
        alerts = [r for r in fresh if r[2] == 'owner-action']
        hourly = [r for r in fresh if r[2] == 'hourly']
        # Reserve progress for the newest hourly even during an alert burst.
        selected = alerts[:slots - bool(hourly)] + hourly[:1]
        selected_ids = {r[1] for r in selected}
        selected += [r for r in fresh if r[1] not in selected_ids][:slots - len(selected)]
        work = selected + history
        with budget.destination(platform):
            for row in work:
                record = loads(row[0])
                if record['binding'] != binding:
                    continue
                if not budget.admit(platform, reconciliation=not row['fresh']):
                    continue
                try:
                    state = outbox.advance(row[1], provider, allow_send=allow_send,
                                           before_send=before_send, clock=getattr(provider, 'clock', None))
                except Exception:
                    state = dict(status='UNKNOWN', reason='REPORT_PROVIDER_UNAVAILABLE')
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
        thread.join(max(0, budget.stage_deadline - monotonic()))
    if any(thread.is_alive() for thread in threads):
        budget.cancel()
        raise ValidationError('REPORT_DESTINATION_DEADLINE')
    if failures:
        raise ValidationError('REPORT_DESTINATION_STAGE_FAILED') from None
    return results


def reporting_cycle(store, *, operating, binding, now, providers=None, context=None, delivery_budget=None):
    settings, destinations = _bindings(operating, binding)
    if settings['mode'] == 'off' and not settings['bindings']:
        return dict(state='OFF', generated=0, reports=[], deliveries=[], coverage={'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})
    timestamp(now)
    key = 'reporting:' + fingerprint('reporting-scope', binding)
    if settings['mode'] == 'off':
        with store.connection() as db:
            existing = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
        if existing is None:
            return dict(state='OFF', generated=0, reports=[], deliveries=[], coverage={'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})
        with store.transaction() as db:
            row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
            state = loads(row[0]) if row else dict(owner={})
            require('cursor' not in state or timestamp(state['cursor']) <= timestamp(now), 'REPORT_CLOCK_REGRESSION')
            state.update(cursor=now, suspended=True)
            db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, canonical(state).decode()))
        return dict(state='OFF', generated=0, reports=[], deliveries=[], coverage={'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})
    calendar, context = effective_calendar(operating), safe_report(context or {}, senders=(providers or {}).values())
    generated = []
    with store.transaction() as db:
        row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
        state = loads(row[0]) if row else dict(cursor=utc(timestamp(now) - timedelta(microseconds=1)), owner={})
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
                             dict(context, previous_daily_end=state.get('daily_end')), period)
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
                    'CATCH-UP: missed hourly periods ending ' + ', '.join(p['end'] for p in hours)
                    + '\nSpan: ' + hours[0]['start'] + ' to ' + hours[-1]['end']
                    + '\nOlder End of Day dates (retained, no separate post): '
                    + (', '.join(p['local_end'][:10] for p in daily[:-2]) or 'None'))
                body = prepare_body('hourly', data, verify_link=context.get('verify_link'))
            send = (period['kind'] == 'hourly' and (summary is None or period == summary)
                    or period['kind'] == 'eod' and period in daily[-2:])
            if not send:
                extra['delivery_policy'] = 'LISTED_IN_CATCH_UP'
            generated.append(_retain(db, report_id, period['kind'], body, binding,
                                     destinations if send else [], now, extra=extra,
                                     senders=(providers or {}).values()))
            if period['kind'] == 'eod':
                state['daily_end'] = period['end']
        if context.get('owner_actions') is not None:
            events, state['owner'] = owner_events(fingerprint('reporting-scope', binding), context['owner_actions'], state['owner'])
            for action in events:
                generated.append(_retain(db, action['id'], 'owner-action',
                    _body('owner-action', binding, now, calendar, context, action=action), binding, destinations, now,
                    senders=(providers or {}).values()))
        for notice in context.get('review_corrections', []):
            destination = notice['binding']
            if destination not in destinations:
                continue  # Never retarget a correction after channel rotation.
            body = prepare_body('owner-action', dict(project=binding['project'], observed_at=now,
                timezone=calendar['timezone'], scope=binding['scope_revision'],
                candidate=describe(notice['invalidated']), summary='Correction: prior readiness withdrawn',
                sections={'Evidence': describe(notice),
                          'Owner action': 'Do not rely on prior readiness; await current candidate review'}))
            generated.append(_retain(db, notice['id'], 'owner-action', body, binding, [destination], now,
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
                generated.append(_retain(db, notice['id'], 'owner-action', body, binding, destinations, now,
                                         senders=(providers or {}).values()))
        state['cursor'] = end
        db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, canonical(state).decode()))
    deliveries = drain(store, operating, binding, providers or {}, budget=delivery_budget)
    return dict(state='ENABLED', generated=len(generated), reports=generated, deliveries=deliveries,
                coverage=deepcopy(context.get('coverage', {'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'})))


class ReviewReportingPort:
    """Lane C's exact batch seam; only channel evidence can close a batch.

    Exhausted/partial destinations stay visible in reporting diagnostics. They
    never issue NOT_DELIVERED and thus never multiply lane C's retry allowance.
    """
    def __init__(self, store, operating, binding, providers, clock, *, observe_candidate=None, delivery_budget=None):
        self.store, self.operating, self.binding = store, operating, binding
        self.providers, self.clock = providers, clock
        self.observe_candidate = observe_candidate
        self.delivery_budget = delivery_budget

    def _id(self, payload):
        return 'review-' + payload['batch_id']

    def _check(self, payload, calendar, observe_candidate):
        from ..review_batching import ReviewBatchStore
        require(all(str(m['candidate']['repository_id']) == self.binding['repository'] for m in payload['members']),
                'REPORT_SCOPE_BINDING_MISMATCH')
        now, checked = self.clock(), []
        def observe(candidate):
            value = observe_candidate(candidate, now)
            checked.append(value)
            return value
        checked_payload = ReviewBatchStore(self.store).recheck(payload['batch_id'], now, calendar, observe)
        require(checked_payload == payload, 'REPORT_REVIEW_BATCH_MISMATCH')
        send_at = self.clock()
        require(not next_review_presentation(send_at, calendar)['held'] and
                all(timestamp(v['expires_at']) > timestamp(send_at) for v in checked),
                'REPORT_REVIEW_CHECK_EXPIRED')

    def deliver_review_batch(self, payload):
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
        with self.store.transaction() as db:
            _retain(db, self._id(payload), 'final-human-review', body, self.binding, destinations, now,
                    extra={'review_batch': deepcopy(payload)}, senders=self.providers.values())
        if self.observe_candidate is not None:
            self._check(payload, effective_calendar(self.operating), self.observe_candidate)
        drain(self.store, self.operating, self.binding, self.providers, report_ids={self._id(payload)}, budget=self.delivery_budget,
              before_send=(lambda: self._check(payload, effective_calendar(self.operating), self.observe_candidate))
              if self.observe_candidate is not None else None)
        return self.observe_review_delivery(payload)

    def observe_review_delivery(self, payload):
        # This port cannot send, even when terminal absence admits a retry.
        drain(self.store, self.operating, self.binding, self.providers,
              report_ids={self._id(payload)}, allow_send=False, budget=self.delivery_budget)
        status = reporting_status(self.store, self.operating, self.binding, report_ids={self._id(payload)})
        reports = [r for r in status['reports'] if r['report_id'] == self._id(payload)]
        if not reports:
            return None
        report = reports[0]
        require(report.get('review_batch') == payload, 'REPORT_REVIEW_BATCH_MISMATCH')
        states = [d for d in status['deliveries'] if d['delivery_id'] in report['delivery_ids']]
        if states and all(s['status'] == 'DELIVERED' for s in states):
            observed_at = max((h['observed_at'] for s in states for h in s['history'] if h.get('status') == 'DELIVERED'), key=timestamp)
            return dict(batch_id=payload['batch_id'], payload_sha256=payload['payload_sha256'],
                status='DELIVERED', observed_at=observed_at,
                provider_receipt_id=fingerprint('report-review-receipt', states))
        return None

    def resume(self, calendar, observe_candidate):
        """Controller-only retry: repeat lane C's pre-send check for this batch."""
        from ..review_batching import ReviewBatchStore
        pending = ReviewBatchStore(self.store)
        with self.store.connection() as db:
            payloads = [pending._payload(row) for row in db.execute(
                "SELECT * FROM review_presentation_outbox_v1 WHERE state='CLAIMED' ORDER BY batch_id LIMIT ?",
                (MAX_ADVANCES,))]
        for payload in payloads:
            report_id = self._id(payload)
            status = reporting_status(self.store, self.operating, self.binding, report_ids={report_id})
            states = [s for s in status['deliveries'] if s['report_id'] == report_id]
            if not any(s['status'] in {'PENDING', 'ABSENT', 'THROTTLED'} for s in states):
                continue
            self._check(payload, calendar, observe_candidate)
            drain(self.store, self.operating, self.binding, self.providers, report_ids={report_id}, budget=self.delivery_budget,
                  before_send=lambda: self._check(payload, calendar, observe_candidate))
