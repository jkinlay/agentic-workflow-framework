"""Read-only planning/usage projections over supplied, admitted observations.

No Jira client, accounting switch, ledger, capacity reservation or scheduler.
Scenarios are deterministic assumptions, never statistical percentiles.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import math

from .. import ValidationError
from ..canonical import fingerprint, timestamp
from .delivery import require
from .events import utc
from .metrics import ratio, median

SCENARIOS = ('optimistic', 'base', 'conservative')


def planning_defects(tickets):
    by_key = {t['key']: t for t in tickets}
    require(len(by_key) == len(tickets), 'REPORT_DUPLICATE_TICKET')
    defects = []
    def defect(t, reason):
        defects.append(dict(key=t['key'], reason=reason, owner_action='Correct accepted planning data'))
    for t in tickets:
        if t.get('planning_observed') is False:
            continue
        start, end = t.get('planned_start'), t.get('planned_end')
        if not start or not end:
            defect(t, 'PLANNED_DATES_MISSING')
            continue
        try:
            require(date.fromisoformat(start).isoformat() == start and date.fromisoformat(end).isoformat() == end,
                    'REPORT_PLANNED_DATE_INVALID')
        except (ValidationError, ValueError, TypeError):
            defect(t, 'PLANNED_DATE_INVALID')
            continue
        if end < start:
            defect(t, 'PLANNED_DATES_INVERTED')
        if t.get('due') and end != t['due']:
            defect(t, 'DUE_MISMATCH')
        if t.get('baseline_start') and start != t['baseline_start'] or t.get('baseline_end') and end != t['baseline_end']:
            defect(t, 'BASELINE_DRIFT')
        for dep in t.get('dependencies', []):
            predecessor = by_key.get(dep)
            if predecessor is None:
                defect(t, 'PREDECESSOR_UNOBSERVED')
            elif predecessor.get('planned_end'):
                if start < predecessor['planned_end']:
                    defect(t, 'PREDECESSOR_ORDER')
                elif start == predecessor['planned_end']:
                    # Day-only fields cannot establish same-day order.
                    if t.get('start_offset_seconds') is None or predecessor.get('end_offset_seconds') is None:
                        defect(t, 'INTRADAY_ORDER_UNKNOWN')
                    elif t['start_offset_seconds'] < predecessor['end_offset_seconds']:
                        defect(t, 'PREDECESSOR_ORDER')
        if t.get('kind') == 'epic':
            children = [c for c in tickets if c.get('epic') == t['key'] and not c.get('conditional', False)]
            if children and all(c.get('planned_start') and c.get('planned_end') for c in children):
                if start != min(c['planned_start'] for c in children) or end != max(c['planned_end'] for c in children):
                    defect(t, 'EPIC_WINDOW')
            elif children:
                defect(t, 'EPIC_CHILD_DATES_UNKNOWN')
    return defects


def milestone_forecast(nodes, milestones, capacity, as_of, *, reservations=None, previous=None):
    """Resource-constrained list schedule over the union of remaining ancestors.

    Each node is unique, dependencies name nodes, resource names an observed
    capacity pool, and conflicts optionally name exclusive files/resources.
    remaining_seconds includes only retained WIP plus expected review/rework;
    model/source evidence for those estimates is the caller's responsibility.
    Reservations are observed unavailable seconds per resource slot. External
    waits have no guessed duration: affected milestone dates stay conditional.
    """
    origin = timestamp(as_of)
    require(isinstance(capacity, dict) and capacity and all(type(n) is int and n > 0 for n in capacity.values()),
            'REPORT_CAPACITY_UNAVAILABLE')
    graph = {n['key']: n for n in nodes}
    require(len(graph) == len(nodes), 'REPORT_GRAPH_DUPLICATE')
    for node in nodes:
        require(node.get('resource') in capacity and isinstance(node.get('dependencies'), list), 'REPORT_GRAPH_RESOURCE')
        require(len(set(node['dependencies'])) == len(node['dependencies']), 'REPORT_GRAPH_DEPENDENCY_DUPLICATE')
        durations = node.get('remaining_seconds')
        require(isinstance(durations, dict) and set(durations) == set(SCENARIOS)
                and all(type(v) is int and v >= 0 for v in durations.values())
                and durations['optimistic'] <= durations['base'] <= durations['conservative'], 'REPORT_REMAINING_UNKNOWN')
        require(all(dep in graph for dep in node['dependencies']), 'REPORT_DEPENDENCY_UNOBSERVED')
    ancestry = {}
    def ancestors(key, visiting=None):
        require(key in graph, 'REPORT_MILESTONE_UNKNOWN')
        visiting = set() if visiting is None else visiting
        require(key not in visiting, 'REPORT_DEPENDENCY_CYCLE')
        if key in ancestry:
            return ancestry[key]
        result = {key}
        for dep in graph[key]['dependencies']:
            result |= ancestors(dep, visiting | {key})
        ancestry[key] = result
        return result
    sets = {name: set().union(*(ancestors(k) for k in targets)) for name, targets in milestones.items()}
    required = set().union(*sets.values()) if sets else set()
    results = {name: {} for name in milestones}
    schedules = {}
    for scenario in SCENARIOS:
        slots = {}
        for pool, count in capacity.items():
            reserved = list((reservations or {}).get(pool, [0] * count))
            require(len(reserved) == count and all(type(v) is int and v >= 0 for v in reserved), 'REPORT_RESERVATION_INVALID')
            slots[pool] = reserved
        finish, paths, conflict_free, remaining = {}, {}, {}, set(required)
        schedule = []
        while remaining:
            eligible = [graph[k] for k in sorted(remaining) if all(d in finish for d in graph[k]['dependencies'])]
            require(eligible, 'REPORT_DEPENDENCY_CYCLE')
            candidates = []
            for node in eligible:
                resource = node['resource']
                slot = min(range(len(slots[resource])), key=lambda i: (slots[resource][i], i))
                start = max([slots[resource][slot]] + [finish[d] for d in node['dependencies']]
                            + [conflict_free.get(c, 0) for c in node.get('conflicts', [])])
                candidates.append((start, node['key'], slot))
            start, key, slot = min(candidates)
            node = graph[key]
            finish[key] = start + node['remaining_seconds'][scenario]
            schedule.append(dict(key=key, start_seconds=start, finish_seconds=finish[key],
                                 resource=node['resource'], slot=slot, owner=node.get('owner'),
                                 next_gate=node.get('next_gate'), conflicts=node.get('conflicts', [])))
            predecessor = max(node['dependencies'], key=lambda d: finish[d], default=None)
            paths[key] = (paths[predecessor] if predecessor else []) + [key]
            slots[node['resource']][slot] = finish[key]
            for conflict in node.get('conflicts', []):
                conflict_free[conflict] = finish[key]
            remaining.remove(key)
        schedules[scenario] = schedule
        for name, keys in sets.items():
            last = max(keys, key=lambda k: (finish[k], k), default=None)
            duration = finish[last] if last else 0
            waits = sorted({graph[k]['external_wait'] for k in keys if graph[k].get('external_wait')})
            results[name][scenario] = dict(status='CONDITIONAL' if waits else 'SCENARIO',
                remaining_seconds=duration, eta=None if waits else utc(origin + timedelta(seconds=duration)),
                conditional_eta=utc(origin + timedelta(seconds=duration)) if waits else None,
                external_waits=waits, dependency_path=paths[last] if last else [],
                basis='Observed capacity, supplied remaining work; external waits excluded')
    return dict(status='SCENARIOS', as_of=as_of, milestones=results, capacity=dict(capacity), schedule=schedules,
                previous=previous, model='deterministic resource-constrained list schedule; no percentiles',
                input_sha256=fingerprint('report-forecast-1', [nodes, milestones, capacity, as_of, reservations]))


def token_metrics(mode, observations, enrolled, *, completed=(), complete_tickets=(), forecasts=None):
    """Use attributed provider input+output totals; subsets are never added.

    Enrollment and observations must come from the trusted accounting adapter.
    Unknown charges lower coverage and make the project total unavailable.
    Known subtotal stays explicitly labelled. IDs deduplicate retries of reads,
    not actual retry/review/failure runs (which have distinct observation IDs).
    """
    if mode != 'on':
        return dict(status='UNAVAILABLE', reason='ACCOUNTING_OFF_OR_UNKNOWN')
    unique, excluded = {}, 0
    for row in observations:
        if row.get('agent') not in enrolled or row.get('role') in {'controller', 'independent', 'main-chat'}:
            excluded += 1
            continue
        require(isinstance(row.get('id'), str) and row['id'], 'REPORT_USAGE_ID_REQUIRED')
        require(row['id'] not in unique or unique[row['id']] == row, 'REPORT_USAGE_ID_CONFLICT')
        unique[row['id']] = row
    tickets, models, roles, outcomes, overhead, unknown = Counter(), Counter(), Counter(), Counter(), 0, []
    input_tokens, output_tokens, splits_known = 0, 0, True
    for identity, row in unique.items():
        amounts = [row.get('input'), row.get('output')]
        split = all(type(n) is int and n >= 0 for n in amounts)
        if split:
            total = sum(amounts)
            input_tokens += amounts[0]
            output_tokens += amounts[1]
        elif type(row.get('total')) is int and row['total'] >= 0:
            total = row['total']
            splits_known = False
        else:
            unknown.append(identity)
            continue
        models[row.get('model') or 'Unknown'] += total
        roles[row.get('role') or 'Unknown'] += total
        outcomes[row.get('outcome') or 'Unknown'] += total
        if row.get('ticket'):
            tickets[row['ticket']] += total
        else:
            overhead += total
    known = sum(tickets.values()) + overhead
    complete = set(completed) & set(complete_tickets)
    complete -= {r.get('ticket') for r in unique.values() if r['id'] in unknown}
    # Completeness with no observed record is not measured zero.
    complete &= set(tickets)
    remaining = None
    if forecasts is not None:
        # Each retained work/review/overhead component has one stable ID and a
        # measured comparable-history basis. Duplicate components refuse.
        seen, totals = set(), [0, 0, 0]
        for f in forecasts:
            require(f.get('id') and f['id'] not in seen and f.get('basis_observation_ids')
                    and set(f['basis_observation_ids']) <= set(unique)
                    and not set(f['basis_observation_ids']) & set(unknown), 'REPORT_TOKEN_FORECAST_UNSUPPORTED')
            seen.add(f['id'])
            values = [f.get(s) for s in SCENARIOS]
            require(all(type(v) is int and v >= 0 for v in values) and values == sorted(values), 'REPORT_TOKEN_FORECAST_INVALID')
            totals = [a+b for a, b in zip(totals, values)]
        remaining = dict(zip(SCENARIOS, totals))
    consumed = known if unique and not unknown else None
    return dict(status='KNOWN' if consumed is not None else 'PARTIAL', consumed=consumed, known_subtotal=known,
                unknown_observations=sorted(unknown), coverage=dict(known=len(unique)-len(unknown), total=len(unique)),
                completed_n=len(complete), completed_coverage=dict(numerator=len(complete), denominator=len(set(completed))),
                completed_mean=ratio(Fraction(sum(tickets[k] for k in complete), len(complete))) if complete else None,
                tickets=dict(tickets), overhead=overhead, model_mix=dict(models), excluded=excluded,
                input_tokens=input_tokens if consumed is not None and splits_known else None,
                output_tokens=output_tokens if consumed is not None and splits_known else None,
                by_role=dict(roles), by_outcome=dict(outcomes),
                forecast_components=deepcopy(forecasts),
                remaining=remaining, expected_total={s: consumed + remaining[s] for s in SCENARIOS}
                if consumed is not None and remaining is not None else None)


def elapsed_comparison(tickets):
    """Original estimates stay distinct; only calendar seconds are comparable."""
    from .metrics import elapsed_seconds
    tickets = [t for t in tickets if t.get('state') == 'DONE' or t.get('completion_unavailable')]
    samples, delays, comparisons, components = [], [], [], defaultdict(list)
    for ticket in tickets:
        actual = elapsed_seconds(ticket)
        if actual is None:
            continue
        samples.append(actual[0])
        delays.append(actual[1])
        estimate = ticket.get('original_estimate', {})
        if estimate.get('unit') == 'calendar_seconds' and type(estimate.get('value')) is int:
            comparisons.append(dict(key=ticket['key'], original=estimate['value'], actual=ratio(actual[0]),
                                    variance=ratio(actual[0]-estimate['value'])))
        for name in ('execution', 'review', 'external_wait', 'owner_wait'):
            value = ticket.get('components_seconds', {}).get(name)
            if type(value) is int and value >= 0:
                components[name].append(value)
    ordered = sorted(samples)
    lower, upper = ordered[:len(ordered)//2], ordered[(len(ordered)+1)//2:]
    return dict(n=len(samples), mean_seconds=ratio(sum(samples)/len(samples)) if samples else None,
                median_seconds=ratio(median(samples)) if samples else None,
                iqr_seconds=ratio(median(upper)-median(lower)) if lower else None,
                coverage=dict(numerator=len(samples), denominator=len(tickets)), comparisons=comparisons,
                reconciliation_delay=dict(status='KNOWN' if delays else 'UNAVAILABLE', n=len(delays),
                    coverage=dict(numerator=len(delays), denominator=len(tickets)),
                    mean_seconds=ratio(sum(delays)/len(delays)) if delays else None,
                    median_seconds=ratio(median(delays)) if delays else None,
                    basis='Final required PR merge to closeout/Jira reconciliation'),
                comparison_unavailable=len(samples)-len(comparisons),
                components={k: dict(n=len(v), mean_seconds=ratio(Fraction(sum(v), len(v)))) for k, v in components.items()},
                basis='Elapsed includes nights and waits; engineer-days are not converted')


def estimated_time(tickets):
    """Keep original estimates grouped by exact unit, never turn them into actuals."""
    groups = defaultdict(list)
    for ticket in tickets:
        estimate = ticket.get('original_estimate', {})
        if estimate.get('unit') and type(estimate.get('value')) is int and estimate['value'] >= 0:
            groups[estimate['unit']].append(dict(key=ticket['key'], original=estimate['value'],
                                               revisions=ticket.get('estimate_revisions', [])))
    return {unit: dict(n=len(rows), estimated_median=ratio(median([r['original'] for r in rows])),
                       tickets=rows, basis='Estimate, not observed elapsed time') for unit, rows in sorted(groups.items())}


def scope_metrics(tickets):
    """Current required leaf denominator; dispositions retained separately.

    Parent/subtask counting is explicit. A shared dependency has one key and one
    primary Epic owner; extra relationships never increase the scope total.
    """
    from .metrics import delivered, required_leaves
    keys = [t['key'] for t in tickets]
    require(len(keys) == len(set(keys)), 'REPORT_DUPLICATE_TICKET')
    by_key = {t['key']: t for t in tickets}
    for ticket in tickets:
        require(ticket['kind'] in {'epic', 'implementation'}, 'REPORT_TICKET_CLASSIFICATION')
        if ticket.get('epic'):
            require(ticket['epic'] in by_key and by_key[ticket['epic']]['kind'] == 'epic', 'REPORT_EPIC_SCOPE_MISMATCH')
        seen, current = set(), ticket
        while current.get('parent'):
            require(current['key'] not in seen and current['parent'] in by_key, 'REPORT_PARENT_SCOPE_OR_CYCLE')
            seen.add(current['key'])
            current = by_key[current['parent']]
    parents = {t['parent'] for t in tickets if t.get('parent')}
    leaves = [t for t in tickets if t['kind'] == 'implementation' and t['key'] not in parents]
    required = required_leaves(tickets)
    def matrix(rows):
        counts = defaultdict(Counter)
        for row in rows:
            counts[row.get('priority') or 'Unknown'][row.get('size') or 'Unestimated'] += 1
        return {p: dict(sorted(c.items())) for p, c in sorted(counts.items())}
    epics = [t for t in tickets if t['kind'] == 'epic']
    groups = defaultdict(list)
    for ticket in required:
        groups[ticket.get('epic') or 'Unassigned'].append(ticket)
    def is_delivered(t):
        return delivered(dict(t, state=t.get('state', 'UNKNOWN')))
    delivered_n = sum(bool(is_delivered(t)) for t in required)
    remaining_groups = {e: [t for t in rows if not is_delivered(t)] for e, rows in groups.items()}
    unclassified = sum(t.get('state', 'UNKNOWN') == 'UNKNOWN' and not t.get('completion_unavailable') for t in required)
    return dict(epics=len(epics), implementation_tickets=len(required), delivered=delivered_n,
        remaining=None if unclassified else len(required)-delivered_n, unclassified=unclassified,
        known_remaining=sum(not is_delivered(t) and t.get('state', 'UNKNOWN') != 'UNKNOWN' for t in required),
        priority_size=matrix(required), epic_priority_size=matrix(epics),
        states=dict(Counter(t.get('state', 'UNKNOWN') for t in required)),
        dispositions=dict(cancelled=sum(t.get('state') == 'CANCELLED' for t in leaves),
                          deferred=sum(t.get('state') in {'DEFERRED', 'OUT_OF_SCOPE'} for t in leaves),
                          conditional=sum(bool(t.get('conditional')) for t in leaves)),
        per_epic={e: dict(count=len(groups[e]), priority_size=matrix(groups[e]),
                         states=dict(Counter(t.get('state', 'UNKNOWN') for t in groups[e])),
                         remaining=dict(count=len(remaining_groups.get(e, [])),
                             priority_size=matrix(remaining_groups.get(e, [])),
                             states=dict(Counter(t.get('state', 'UNKNOWN') for t in remaining_groups.get(e, [])))))
                  for e in sorted({t['key'] for t in epics} | set(groups))},
        planned_dates=[dict(key=t['key'], kind=t['kind'], start=t.get('planned_start'), end=t.get('planned_end')) for t in tickets],
        planning_defects=planning_defects(tickets),
        counting_rule='Required implementation leaves; shared keys once; dispositions not delivered')


def elapsed_cohorts(tickets):
    """Never pool incompatible declared completion cohorts for a mean/median."""
    groups = defaultdict(list)
    for ticket in tickets:
        groups[ticket.get('cohort') or 'Unknown'].append(ticket)
    return {name: (elapsed_comparison(rows) if name != 'Unknown' else
                   dict(status='UNAVAILABLE', reason='COMPARABLE_COHORT_UNDECLARED', records=len(rows)))
            for name, rows in sorted(groups.items())}


def observed_token_forecast(work, history):
    """Measured min/mean/max scenarios scaled by remaining WIP fraction.

    History rows are completely attributed comparable completed-ticket token
    totals from token_metrics, not caps. Reviews/rework/common overhead enter as
    explicit unique remaining components with matching measured cohorts. The
    caller must not also include them in a work component's cohort estimate.
    """
    cohorts, seen = defaultdict(list), set()
    for row in history:
        require(row.get('id') not in seen and row.get('id') and row.get('cohort')
                and type(row.get('tokens')) is int and row['tokens'] >= 0, 'REPORT_TOKEN_HISTORY_INVALID')
        seen.add(row['id'])
        cohorts[row['cohort']].append(row)
    result, identities, totals = [], set(), [0, 0, 0]
    for component in work:
        require(component.get('id') and component['id'] not in identities, 'REPORT_TOKEN_COMPONENT_DUPLICATE')
        identities.add(component['id'])
        numerator, denominator = component.get('remaining_numerator'), component.get('remaining_denominator')
        require(type(numerator) is int and type(denominator) is int and 0 <= numerator <= denominator and denominator > 0,
                'REPORT_TOKEN_REMAINING_FRACTION')
        rows = cohorts[component['cohort']]
        if not rows:
            return dict(status='UNAVAILABLE', reason='NO_COMPARABLE_ATTRIBUTED_HISTORY', component=component['id'])
        values = [r['tokens'] for r in rows]
        fraction = Fraction(numerator, denominator)
        scenarios = [math.ceil(fraction * value) for value in (min(values), Fraction(sum(values), len(values)), max(values))]
        totals = [a+b for a, b in zip(totals, scenarios)]
        result.append(dict(id=component['id'], sample_n=len(rows), basis_observation_ids=[r['id'] for r in rows],
                           **dict(zip(SCENARIOS, scenarios))))
    return dict(status='SCENARIOS', remaining=dict(zip(SCENARIOS, totals)), components=result,
                basis='Measured comparable min/mean/max, remaining WIP fraction; no percentiles')


def token_rollup(usage, ticket_epics, components):
    """Reconcile primary Epic attribution plus a separate shared row once.

    components are the measured forecast's unique rows, enriched with accepted
    primary Epic ownership. Unknown scope usage propagates; known Epic subtotals
    are never asserted to be complete while the scope has unknown charges.
    """
    if 'forecast_components' in usage:
        retained = usage['forecast_components']
        if components is None:
            components = deepcopy(retained)
        else:
            require(retained is not None and isinstance(components, list), 'REPORT_TOKEN_COMPONENT_MISMATCH')
            expected = {c['id']: c for c in retained}
            require(len(components) == len(expected) and {c.get('id') for c in components} == set(expected),
                    'REPORT_TOKEN_COMPONENT_MISMATCH')
            for component in components:
                original = expected[component['id']]
                require(all(component.get(k) == v for k, v in original.items())
                        and set(component) <= set(original) | {'epic'}, 'REPORT_TOKEN_COMPONENT_MISMATCH')
    epics, shared, seen = {}, dict(consumed=usage.get('overhead'), remaining={s: 0 for s in SCENARIOS}), set()
    def row(epic):
        if not epic:
            return shared
        return epics.setdefault(epic, dict(consumed=0, remaining={s: 0 for s in SCENARIOS}))
    for ticket, amount in usage.get('tickets', {}).items():
        group = row(ticket_epics.get(ticket))
        require(type(amount) is int and amount >= 0, 'REPORT_TOKEN_ROLLUP_INVALID')
        group['consumed'] = (group['consumed'] or 0) + amount
    for component in components or []:
        require(component.get('id') and component['id'] not in seen, 'REPORT_TOKEN_COMPONENT_DUPLICATE')
        seen.add(component['id'])
        values = [component.get(s) for s in SCENARIOS]
        require(all(type(v) is int and v >= 0 for v in values) and values == sorted(values), 'REPORT_TOKEN_FORECAST_INVALID')
        group = row(component.get('epic'))
        for scenario in SCENARIOS:
            group['remaining'][scenario] += component[scenario]
    remaining = {s: shared['remaining'][s] + sum(e['remaining'][s] for e in epics.values()) for s in SCENARIOS}
    if 'remaining' in usage:
        require(usage['remaining'] == (remaining if components is not None else None), 'REPORT_TOKEN_TOTAL_MISMATCH')
    consumed = usage.get('consumed')
    if consumed is not None:
        require(shared['consumed'] is not None and consumed == shared['consumed'] + sum(e['consumed'] for e in epics.values()),
                'REPORT_TOKEN_TOTAL_MISMATCH')
    for group in [shared] + list(epics.values()):
        group['coverage'] = 'COMPLETE' if consumed is not None else 'KNOWN_SUBTOTAL_ONLY'
        group['expected_total'] = ({s: group['consumed'] + group['remaining'][s] for s in SCENARIOS}
                                   if group['consumed'] is not None else None)
        if consumed is None:
            group['known_subtotal'] = group['consumed']
            group['consumed'] = None
            group['expected_total'] = None
        if components is None:
            group['remaining'] = None
            group['expected_total'] = None
    return dict(epics=epics, shared=shared, allocation='One primary Epic per ticket; unallocated/shared kept separately',
                scope=dict(consumed=consumed, remaining=remaining if components is not None else None,
                           expected_total={s: consumed+remaining[s] for s in SCENARIOS}
                           if consumed is not None and components is not None else None))


def reconciliation_coverage(tickets):
    from .metrics import delivered, required_leaves
    implementation = required_leaves(tickets)
    known_states = {'BACKLOG', 'READY', 'WORKING', 'REVIEWING', 'MERGED', 'OWNER_WAITING',
                    'BLOCKED', 'PAUSED', 'PAUSED_INPUT', 'REVIEW_PASSED', 'OWNER_APPROVED',
                    'IN_PROGRESS', 'DISPATCHED', 'PR_DRAFT', 'READY_FOR_CRITIC', 'CHANGES_REQUESTED',
                    'AMENDING', 'REVIEW_CAP_REACHED', 'SPECIALIST_REVIEW', 'FINAL_REVIEW',
                    'READY_FOR_OWNER_AUTHORIZATION', 'OWNER_AUTHORIZED', 'MERGING', 'MERGE_UNKNOWN',
                    'MERGED_PENDING_JIRA', 'MERGED_PENDING_OWNER_CLOSURE', 'FAILED', 'REOPENED'}
    observed = sum(t.get('state') in known_states or delivered(t) for t in implementation)
    return dict(status='KNOWN' if observed == len(implementation) else 'UNAVAILABLE',
                observed=observed, total=len(implementation))


def daily_metrics(prs, tickets, end, previous_daily_end, timezone_name, *, observed_at=None):
    """Independent overlapping daily views and project-to-date elapsed cohorts.

    PR rows are observed merge records; ticket delivery separately requires final
    required merges and reconciliation. A merged PR never supplies ticket Done.
    """
    from .events import daily_views
    from .metrics import delivered, required_leaves
    views = daily_views(end, previous_daily_end, timezone_name)
    # Match the declared scope denominator for counts, coverage and every
    # elapsed view. Filter before splitting periods so parents stay excluded
    # even when their children completed outside the selected interval.
    tickets = required_leaves(tickets)
    unique = {}
    require(prs is None or isinstance(prs, list), 'REPORT_PR_INPUT_INVALID')
    for pr in prs or []:
        require(pr.get('id') and (pr['id'] not in unique or unique[pr['id']] == pr), 'REPORT_PR_ID_CONFLICT')
        if pr.get('merged_at'):
            try:
                merged = timestamp(pr['merged_at'])
            except (ValidationError, ValueError, TypeError):
                raise ValidationError('REPORT_MERGE_TIMESTAMP_INVALID') from None
            # Observation time bounds validity. Historical period boundaries
            # filter the view below; later valid observations remain available.
            require(merged <= timestamp(observed_at or end), 'REPORT_FUTURE_MERGE')
        unique[pr['id']] = pr
    coverage = reconciliation_coverage(tickets)
    available = coverage['status'] == 'KNOWN'
    result = dict(additive=False, label=views['label'], project_to_date=elapsed_cohorts(tickets),
                  reconciliation_coverage=coverage)
    for name in ('today', 'since_previous'):
        if views[name] is None:
            result[name] = dict(period=None, status='UNAVAILABLE', reason='NO_PREVIOUS_DAILY_REPORT')
            continue
        start, finish = timestamp(views[name]['start']), timestamp(views[name]['end'])
        merged = [pr for pr in unique.values() if pr.get('merged_at') and start < timestamp(pr['merged_at']) <= finish]
        completed = [t for t in tickets if delivered(t) and start < timestamp(t['reconciled_at']) <= finish]
        result[name] = dict(period=views[name], merged_pr_count=len(merged) if prs is not None else None,
                            merged_prs=merged if prs is not None else None,
                            pr_status='KNOWN' if prs is not None else 'UNAVAILABLE',
                            reconciled_ticket_count=len(completed) if available else None,
                            reconciliation_coverage=coverage,
                            reconciled_tickets=[t['key'] for t in completed],
                            elapsed=elapsed_cohorts(completed))
    result['merged_pending_reconciliation'] = [t['key'] for t in tickets if t.get('state') in
        {'MERGED', 'OWNER_WAITING', 'MERGED_PENDING_JIRA', 'MERGED_PENDING_OWNER_CLOSURE'}
                                              and not delivered(t)]
    return result
