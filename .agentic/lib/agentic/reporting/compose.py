"""Compose all report bodies from admitted observations without controller I/O.

The verifier is a trusted executable collector, not a field in supplied data.
No verifier/stale inputs yield explicit unknowns, not invented live reporting.
Historical snapshots remain unchanged; retain this derived artifact alongside
their source identities in the sole controller's journal.
"""
from copy import deepcopy

from .. import ValidationError
from ..canonical import fingerprint, timestamp
from .analysis import (scope_metrics, elapsed_cohorts, milestone_forecast,
                       token_metrics, token_rollup, daily_metrics, estimated_time)
from .bodies import prepare_body
from .delivery import require
from .metrics import required_leaves


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
                sections={}, freshness=freshness, period=describe(values.get('period')),
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
    metrics = scope_metrics(tickets)
    implementation = required_leaves(tickets)
    completed = [t['key'] for t in implementation if t.get('state') == 'DONE']
    accounting = values.get('accounting', {})
    usage = token_metrics(accounting.get('mode', 'off'), accounting.get('observations', []), accounting.get('enrolled', []),
                         completed=completed, complete_tickets=accounting.get('complete_tickets', []),
                         forecasts=accounting.get('forecasts'))
    elapsed = elapsed_cohorts(implementation)
    if implementation and all(t.get('state') in {'BACKLOG', 'READY'} and not t.get('first_started_at') for t in implementation):
        data['summary'] += ' Implementation has not started.'
    forecast = dict(status='UNAVAILABLE', reason='DEPENDENCY_CAPACITY_INPUTS_UNKNOWN')
    f = values.get('forecast')
    if f is not None:
        forecast = milestone_forecast(f['nodes'], f['milestones'], f['capacity'], observed,
                                      reservations=f.get('reservations'), previous=f.get('previous'))
    estimates = estimated_time(implementation)
    data['detail_metrics'] = dict(scope=metrics, elapsed=elapsed, estimated_time=estimates, tokens=usage, forecast=forecast)
    data['source_sha256'] = fingerprint('report-composition-1', values)
    data['stage'] = values.get('stage', 'Unknown; planning acceptance, review, owner approval, merge and Done are separate')
    sections['Scope and progress'] = ('Epics: ' + str(metrics['epics']) + ' | Implementation tickets: '
        + str(metrics['implementation_tickets']) + ' | Delivered: ' + str(metrics['delivered'])
        + '\nStates: ' + describe(metrics['states']) + '\nPriority by size: ' + describe(metrics['priority_size'])
        + '\nDispositions: ' + describe(metrics['dispositions']))
    sections['Remaining work'] = 'Required leaf tickets remaining: ' + str(metrics['remaining']) + '\n' + describe(metrics['states'])
    sections['Remaining work'] += '\nDispositions: ' + describe(metrics['dispositions'])
    if metrics['planning_defects']:
        defect_summary = describe(metrics['planning_defects'])
        sections['Remaining work'] += '\nPlanning defects: ' + defect_summary
    sections['Time and estimate comparison'] = describe(elapsed) + '\nOriginal estimates (not actuals): ' + describe(estimates or None)
    sections['Time and review outcome'] = sections['Time and estimate comparison'] + '\nReview: ' + describe(values.get('review'))
    sections['Time and review outcome'] += '\nOwner approval: ' + describe(values.get('owner_approval'))
    sections['Usage (tokens)'] = describe(usage)
    sections['Critical path'] = describe(forecast.get('milestones', forecast))
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
        return dict(t, title=t.get('title', 'Unknown'), stage=t.get('state', 'Unknown'))
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
            epic['eta'] = describe(forecast.get('milestones', {}).get(ticket['key']))
            epic['tokens'] = describe(rollup['epics'].get(ticket['key'])) if rollup else 'Unknown: accounting OFF or incomplete'
            data['epics'].append(epic)
    data['overhead'] = describe(rollup.get('shared')) if rollup else 'Unknown: attributed shared usage unavailable'
    if kind == 'eod':
        data['delivered'] = str(metrics['delivered']) + ' reconciled in scope (period delivery Unknown)'
        if values.get('period', {}).get('end') and values.get('timezone'):
            daily = daily_metrics(values.get('prs'), tickets, values['period']['end'],
                                  values.get('previous_daily_end'), values['timezone'], observed_at=observed)
            data['detail_metrics']['daily'] = daily
            data['today'] = describe(daily['today']['period'])
            data['since_previous'] = describe(daily['since_previous']['period'])
            count = daily['today']['reconciled_ticket_count']
            data['delivered'] = (str(count) + ' reconciled today' if count is not None
                                 else 'UNAVAILABLE: Jira reconciliation coverage incomplete')
            if daily['today']['merged_prs'] is not None:
                data['prs'] = daily['today']['merged_prs']
            data['sections']['PRs merged and Jira delivery'] = ('Since previous daily report: '
                + describe(daily['since_previous']) + '\nOverlapping views; never add together')
    for field in ('decision', 'decision_owner', 'requested_response', 'options', 'recommendation', 'delay_consequence',
                  'independent_work', 'resume', 'next_gate', 'detail_url'):
        if field in values:
            data[field] = values[field]
    return prepare_body(kind, data, verify_link=verify_link)
