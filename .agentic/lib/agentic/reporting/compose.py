"""Compose all report bodies from admitted observations without controller I/O.

The verifier is a trusted executable collector, not a field in supplied data.
No verifier/stale inputs yield explicit unknowns, not invented live reporting.
Historical snapshots remain unchanged; retain this derived artifact alongside
their source identities in the sole controller's journal.
"""
from copy import deepcopy
from fractions import Fraction

from .. import ValidationError
from ..canonical import fingerprint, timestamp
from .analysis import (scope_metrics, elapsed_cohorts, milestone_forecast,
                       token_metrics, token_rollup, daily_metrics, estimated_time)
from .bodies import prepare_body
from .delivery import require
from .metrics import required_leaves, elapsed_seconds


def number(value):
    if isinstance(value, dict) and set(value) == {'numerator', 'denominator'}:
        value = Fraction(value['numerator'], value['denominator'])
    return format(float(value), '.3g')


def period_text(period, timezone):
    if not period or not period.get('start') or not period.get('end'):
        return 'Unknown: period unavailable'
    from .events import zone
    tz = zone(timezone)
    return ' to '.join(timestamp(period[k]).astimezone(tz).strftime('%Y-%m-%d %H:%M %Z') for k in ('start', 'end'))


def forecast_text(scenarios, timezone):
    if not scenarios:
        return 'UNAVAILABLE: milestone inputs unknown'
    from .events import zone
    lines = []
    for name in ('base', 'optimistic', 'conservative'):
        row = scenarios[name]
        eta = row.get('eta') or row.get('conditional_eta')
        date = timestamp(eta).astimezone(zone(timezone)).strftime('%Y-%m-%d %H:%M %Z') if eta else 'Unknown'
        lines.append(name.capitalize() + ': ' + date + ' (' + number(row['remaining_seconds'] / 3600) + ' h remaining)'
                     + (' CONDITIONAL; external waits excluded: ' + describe(row['external_waits']) if row['external_waits'] else ''))
    lines.append('Dependency path: ' + ' -> '.join(scenarios['base']['dependency_path']))
    lines.append(scenarios['base']['basis'] + '; scenario range, no percentiles')
    return '\n'.join(lines)


def time_text(cohorts):
    # Keep the machine-readable exact seconds in detail_metrics.
    def hours(value):
        if isinstance(value, list):
            return [hours(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key.endswith('_seconds') and (item is None or isinstance(item, dict) and 'numerator' in item):
                result[key[:-8] + '_hours'] = ('UNAVAILABLE' if item is None else
                    number(Fraction(item['numerator'], item['denominator']) / 3600))
            else:
                result[key] = hours(item)
        return result
    return describe(hours(cohorts))


def describe(value):
    """Readable short labelled records, not JSON or a wide channel table."""
    if value is None:
        return 'Unknown'
    if isinstance(value, dict):
        if set(value) == {'numerator', 'denominator'}:
            return str(value['numerator']) + '/' + str(value['denominator'])
        return '; '.join(str(k).replace('_', ' ') + ': ' + describe(v) for k, v in value.items()) or 'None'
    if isinstance(value, list):
        return '; '.join(describe(v) for v in value) or 'None'
    return str(value)


def compose_report(kind, supplied, accepted_binding, *, verify_sources=None, verify_link=None):
    """Superset of the version-2 snapshot inputs, with optional admitted fields.

    Extra source fields: project_name/timezone, prs, current_keys/next_keys,
    capacity, review_queue, conflicts, changes, blockers, next_action,
    owner_queue, review, owner_approval, forecast (nodes/milestones/capacity/
    reservations), accounting (mode/observations/enrolled/complete_tickets/
    forecasts), token_components, previous_daily_end, and immediate-alert fields.
    Their bytes are covered by the independently executed source verifier.
    """
    values = deepcopy(supplied)
    require(values.get('binding') == accepted_binding and isinstance(accepted_binding, dict)
            and set(accepted_binding) == {'project', 'repository', 'scope_revision'}, 'REPORT_SCOPE_BINDING_MISMATCH')
    observed = values['created_at']
    timestamp(observed)
    tickets = values.get('tickets', [])
    keys = [t['key'] for t in tickets]
    require(len(set(keys)) == len(keys) and set(keys) == set(values.get('included', []))
            and not set(keys) & set(values.get('excluded', [])), 'REPORT_TICKET_SCOPE_MISMATCH')
    admitted = False
    if callable(verify_sources):
        try:
            verified = verify_sources(deepcopy(values), deepcopy(accepted_binding))
        except Exception:
            raise ValidationError('REPORT_SOURCE_VERIFIER_FAILED') from None
        require(verified == values, 'REPORT_SOURCE_VERIFIER_MISMATCH')
        admitted = True
    sources = values.get('sources', [])
    for source in sources:
        require(timestamp(source['observed_at']) <= timestamp(observed) and type(source.get('stale')) is bool,
                'REPORT_SOURCE_INVALID')
    freshness = ('UNVERIFIED' if not admitted else 'MISSING' if not any(s.get('field') == 'tickets' for s in sources)
                 else 'STALE' if any(s['stale'] for s in sources) else 'FRESH')
    data = dict(project=values.get('project_name', accepted_binding['project']), observed_at=observed,
                timezone=values.get('timezone', 'Unknown'), scope=accepted_binding['scope_revision'],
                candidate=describe(values.get('candidate')), summary=values.get('summary', 'Unknown summary'),
                sections={}, freshness=freshness, period=period_text(values.get('period'), values.get('timezone', 'UTC')),
                catch_up=values.get('period', {}).get('catch_up', False),
                baseline_revision=values.get('baseline_revision', 'Unknown'),
                forecast_revision=values.get('forecast_revision', 'Unknown'), detail_metrics={})
    sections = data['sections']
    sections['Sources and freshness'] = freshness + '\n' + describe(sources)
    if freshness != 'FRESH':
        data['summary'] = 'Unknown: source observations ' + freshness
        return prepare_body(kind, data, verify_link=verify_link)
    if values.get('prs') is None:
        # Ticket observations remain usable; PR absence cannot establish zero.
        data['freshness'] = 'PARTIAL'
        sections['Sources and freshness'] = 'PARTIAL: PR observations unavailable\n' + describe(sources)
    # Bound nested ticket actuals before *any* completion, elapsed or usage
    # projection. Keep supplied evidence intact for history/source hashing.
    tickets = deepcopy(tickets)
    unavailable = []
    for ticket in tickets:
        actuals = [(ticket, 'first_started_at'), (ticket, 'reconciled_at')]
        actuals += [(pr, 'merged_at') for pr in ticket.get('required_prs', [])]
        for record, field in actuals:
            if not record.get(field):
                continue
            reason = None
            try:
                actual = timestamp(record[field])
            except (ValidationError, ValueError, TypeError):
                reason = 'REPORT_TICKET_ACTUAL_TIMESTAMP_INVALID'
            else:
                if actual > timestamp(observed):
                    reason = 'FUTURE_TICKET_ACTUAL'
            if reason:
                unavailable.append(dict(key=ticket['key'], field=field, reason=reason))
                record[field] = None
                if ticket.get('state') == 'DONE' and (field != 'first_started_at' or reason == 'FUTURE_TICKET_ACTUAL'):
                    ticket['state'] = 'UNKNOWN'
                    ticket['completion_unavailable'] = True
        # A bad completion ordering invalidates completion; a bad start only
        # invalidates elapsed time. Neither can poison another ticket's facts.
        try:
            elapsed_seconds(dict(ticket, state=ticket.get('state', 'UNKNOWN')))
        except ValidationError:
            field = 'reconciled_at'
            prs = ticket.get('required_prs', [])
            if prs and all(p.get('merged_at') for p in prs):
                final = max(timestamp(p['merged_at']) for p in prs)
                if ticket.get('reconciled_at') and timestamp(ticket['reconciled_at']) >= final:
                    field = 'first_started_at'
            ticket[field] = None
            if field == 'reconciled_at':
                ticket.update(state='UNKNOWN', completion_unavailable=True)
            unavailable.append(dict(key=ticket['key'], field=field, reason='REPORT_ACTUAL_TIME_ORDER'))
    if unavailable:
        data['freshness'] = 'PARTIAL'
        sections['Sources and freshness'] += '\nUNAVAILABLE actuals: ' + describe(unavailable)
    metrics = scope_metrics(tickets)
    implementation = required_leaves(tickets)
    completed = [t['key'] for t in implementation if t.get('state') == 'DONE']
    accounting = values.get('accounting', {})
    usage = token_metrics(accounting.get('mode', 'off'), accounting.get('observations', []), accounting.get('enrolled', []),
                         completed=completed, complete_tickets=accounting.get('complete_tickets', []),
                         forecasts=accounting.get('forecasts'))
    elapsed = elapsed_cohorts(implementation)
    if not values.get('implementation_observed') and implementation and all(t.get('state') in {'BACKLOG', 'READY'} and not t.get('first_started_at') for t in implementation):
        data['summary'] += ' Implementation has not started.'
    forecast = dict(status='UNAVAILABLE', reason='DEPENDENCY_CAPACITY_INPUTS_UNKNOWN')
    f = values.get('forecast')
    if f is not None:
        try:
            forecast = milestone_forecast(f['nodes'], f['milestones'], f['capacity'], observed,
                                          reservations=f.get('reservations'), previous=f.get('previous'))
        except ValidationError as exc:
            forecast = dict(status='UNAVAILABLE', reason=str(exc))
            data['freshness'] = 'PARTIAL'
            sections['Sources and freshness'] += '\nUNAVAILABLE forecast: ' + str(exc)
    estimates = estimated_time(implementation)
    data['detail_metrics'] = dict(scope=metrics, elapsed=elapsed, estimated_time=estimates, tokens=usage, forecast=forecast)
    if unavailable:
        data['detail_metrics']['unavailable_actuals'] = unavailable
    data['source_sha256'] = fingerprint('report-composition-1', values)
    data['stage'] = values.get('stage', 'Unknown; planning acceptance, review, owner approval, merge and Done are separate')
    sections['Scope and progress'] = ('Epics: ' + str(metrics['epics']) + ' | Implementation tickets: '
        + str(metrics['implementation_tickets']) + ' | Delivered: ' + str(metrics['delivered']) + '/' + str(metrics['implementation_tickets'])
        + ' | Blocked: ' + str(metrics['states'].get('BLOCKED', 0))
        + '\nStates: ' + describe(metrics['states']) + '\nPriority by size: ' + describe(metrics['priority_size'])
        + '\nDispositions: ' + describe(metrics['dispositions']))
    sections['Remaining work'] = 'Required leaf tickets remaining: ' + describe(metrics['remaining']) + '\n' + describe(metrics['states'])
    if metrics['unclassified']:
        sections['Remaining work'] += '\nKnown remaining: ' + str(metrics['known_remaining']) + ' | Unclassified: ' + str(metrics['unclassified'])
    sections['Remaining work'] += '\nDispositions: ' + describe(metrics['dispositions'])
    if metrics['planning_defects']:
        defect_summary = describe(metrics['planning_defects'])
        sections['Remaining work'] += '\nPlanning defects: ' + defect_summary
    sections['Time and estimate comparison'] = time_text(elapsed) + '\nOriginal estimates (not actuals): ' + describe(estimates or None)
    sections['Time and review outcome'] = sections['Time and estimate comparison'] + '\nReview: ' + describe(values.get('review'))
    sections['Time and review outcome'] += '\nOwner approval: ' + describe(values.get('owner_approval'))
    sections['Usage (tokens)'] = describe(usage)
    sections['Critical path'] = ('\n\n'.join(name + '\n' + forecast_text(rows, data['timezone'])
        for name, rows in forecast['milestones'].items()) if 'milestones' in forecast else describe(forecast))
    sections['Critical paths'] = sections['Critical path']
    sections['Capacity and review queue'] = ('Observed capacity: ' + describe(values.get('capacity'))
        + '\nReview queue: ' + describe(values.get('review_queue')) + '\nConflicts: ' + describe(values.get('conflicts')))
    for title in ('Waiting and blockers', 'Blockers', 'Blockers and risks'):
        sections[title] = describe(values.get('blockers'))
    sections['Changes since the previous report'] = describe(values.get('changes'))
    sections['Next action'] = describe(values.get('next_action'))
    sections['Owner action'] = describe(values.get('owner_action')) + '\nOwner queue: ' + describe(values.get('owner_queue'))
    if metrics['planning_defects']:
        sections['Owner action'] += '\nCorrect accepted planning defects: ' + describe(metrics['planning_defects'])
    sections['Validation and review completion'] = describe(values.get('review'))
    sections['Findings and accepted limitations'] = describe(values.get('findings'))
    sections['Evidence'] = describe(values.get('review')) + '\nSources: ' + describe(sources)
    sections['Decision / readiness'] = describe(values.get('decision'))
    # Keep only this report's declared labels; preparation refuses unknown keys.
    from .bodies import LAYOUTS
    data['sections'] = {k: v for k, v in sections.items() if k in LAYOUTS[kind]}
    if values.get('prs') is not None:
        data['prs'] = values['prs']
    by_key = {t['key']: t for t in tickets}
    def entry(t):
        row = dict(t, title=t.get('title', 'Unknown'), stage=t.get('state', 'Unknown'))
        if t.get('first_started_at'):
            row['elapsed'] = number((timestamp(observed) - timestamp(t['first_started_at'])).total_seconds() / 3600) + ' h since first start'
        return row
    for source_key, target in (('current_keys', 'current'), ('next_keys', 'next')):
        if source_key in values:
            require(all(k in by_key for k in values[source_key]), 'REPORT_DISPLAY_FOREIGN_TICKET')
            data[target] = [entry(by_key[k]) for k in values[source_key]]
    if 'current' not in data:
        data['current'] = [entry(t) for t in implementation if t.get('state') in {'WORKING', 'REVIEWING'}]
    data['epics'] = []
    rollup = None
    if usage.get('consumed') is not None or usage.get('tickets'):
        rollup = token_rollup(usage, {t['key']: t.get('epic') for t in tickets}, values.get('token_components'))
        data['detail_metrics']['token_rollup'] = rollup
    for ticket in tickets:
        if ticket.get('kind') == 'epic':
            epic = entry(ticket)
            epic['remaining'] = describe(metrics['per_epic'][ticket['key']]['remaining'])
            epic['eta'] = forecast_text(forecast.get('milestones', {}).get(ticket['key']), data['timezone'])
            epic['tokens'] = describe(rollup['epics'].get(ticket['key'])) if rollup else 'Unknown: accounting OFF or incomplete'
            data['epics'].append(epic)
    data['overhead'] = describe(rollup.get('shared')) if rollup else 'Unknown: attributed shared usage unavailable'
    if kind == 'eod':
        data['delivered'] = str(metrics['delivered']) + ' reconciled in scope (period delivery Unknown)'
        if values.get('period', {}).get('end') and values.get('timezone'):
            daily = daily_metrics(values.get('prs'), tickets, values['period']['end'],
                                  values.get('previous_daily_end'), values['timezone'], observed_at=observed)
            data['detail_metrics']['daily'] = daily
            data['today'] = period_text(daily['today']['period'], data['timezone'])
            data['since_previous'] = period_text(daily['since_previous']['period'], data['timezone'])
            count = daily['today']['reconciled_ticket_count']
            data['delivered'] = (str(count) + ' reconciled today' if count is not None
                                 else 'UNAVAILABLE: Jira reconciliation coverage incomplete')
            if daily['today']['merged_prs'] is not None:
                data['prs'] = daily['today']['merged_prs']
            data['sections']['PRs merged and Jira delivery'] = ('Since previous daily report: '
                + describe({k: daily['since_previous'].get(k) for k in ('merged_pr_count', 'reconciled_ticket_count')})
                + '\nMerged pending reconciliation: ' + describe(daily['merged_pending_reconciliation'])
                + '\nOverlapping views; never add together')
    for field in ('decision', 'decision_owner', 'requested_response', 'options', 'recommendation', 'delay_consequence',
                  'independent_work', 'resume', 'next_gate', 'detail_url'):
        if field in values:
            data[field] = values[field]
    return prepare_body(kind, data, verify_link=verify_link)
