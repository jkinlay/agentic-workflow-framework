"""Deterministic metrics over admitted observations; never a usage ledger."""
from collections import Counter, defaultdict
from fractions import Fraction

from ..canonical import timestamp
from .delivery import require


def ratio(value):
    value = Fraction(value)
    return dict(numerator=value.numerator, denominator=value.denominator)


def median(values):
    values = sorted(values)
    n = len(values)
    return Fraction(values[n//2]) if n % 2 else Fraction(values[n//2-1] + values[n//2], 2)


def delivered(ticket):
    prs = ticket.get('required_prs')
    complete = (ticket['state'] == 'DONE' and bool(prs) and bool(ticket.get('reconciled_at'))
                and all(pr.get('merged_at') for pr in prs))
    if complete:
        require(max(timestamp(pr['merged_at']) for pr in prs) <= timestamp(ticket['reconciled_at']),
                'REPORT_ACTUAL_TIME_ORDER')
    return complete


def elapsed_seconds(ticket):
    prs = ticket.get('required_prs')
    if not delivered(ticket) or not ticket.get('first_started_at'):
        return None
    if any(not pr.get('merged_at') for pr in prs):
        return None
    start = timestamp(ticket['first_started_at'])
    merged = max(timestamp(pr['merged_at']) for pr in prs)
    reconciled = timestamp(ticket['reconciled_at'])
    require(start <= merged <= reconciled, 'REPORT_ACTUAL_TIME_ORDER')
    def seconds(delta):
        return Fraction(delta.days * 86400 + delta.seconds) + Fraction(delta.microseconds, 1000000)
    return seconds(merged-start), seconds(reconciled-merged)


def project_metrics(tickets):
    """Count parents once via leaf children; conditional work stays separate.

    Each leaf has one Epic owner (or Unassigned); shared dependencies therefore
    enter the scope once. This is a counting rule, not inferred Jira ownership.
    """
    parents = {t['parent'] for t in tickets if t.get('parent')}
    leaves = [t for t in tickets if t['kind'] == 'implementation' and t['key'] not in parents]
    required = [t for t in leaves if not t.get('conditional', False)]
    epics = {t['key'] for t in tickets if t['kind'] == 'epic'}
    matrix, groups, states = defaultdict(Counter), defaultdict(list), Counter()
    cohorts = defaultdict(list)
    cohort_completed = Counter()
    delivered_count = 0
    defects = []
    for t in required:
        matrix[t.get('priority') or 'Unknown'][t.get('size') or 'Unestimated'] += 1
        groups[t.get('epic') or 'Unassigned'].append(t)
        states[t['state']] += 1
        actual = elapsed_seconds(t)
        delivered_count += bool(delivered(t))
        # Cohort coverage includes Done records with incomplete merge/time data.
        if t['state'] == 'DONE':
            cohort_completed[t['cohort']] += 1
        if actual is not None:
            cohorts[t['cohort']].append(actual)
        start, end = t.get('planned_start'), t.get('planned_end')
        if not start or not end:
            defects.append(dict(key=t['key'], reason='PLANNED_DATES_MISSING'))
        elif end < start:
            defects.append(dict(key=t['key'], reason='PLANNED_DATES_INVERTED'))
    elapsed = {}
    for cohort in sorted({t['cohort'] for t in required}):
        samples = cohorts[cohort]
        durations = sorted(a[0] for a in samples)
        n = len(durations)
        lower, upper = durations[:n//2], durations[(n+1)//2:]
        elapsed[cohort] = dict(n=n, coverage=dict(numerator=n, denominator=cohort_completed[cohort]),
            mean_seconds=ratio(Fraction(sum(durations), n)) if n else None,
            median_seconds=ratio(median(durations)) if n else None,
            iqr_seconds=ratio(median(upper)-median(lower)) if len(lower) else None,
            reconciliation_delay_mean_seconds=ratio(Fraction(sum(a[1] for a in samples), n)) if n else None,
            basis='first WORKER_STARTED to final required PR merge; elapsed includes waits')
    return dict(status='KNOWN', epics=len(epics), implementation_tickets=len(required),
        conditional_tickets=len(leaves)-len(required), delivered=delivered_count,
        priority_size={p: dict(sorted(sizes.items())) for p, sizes in sorted(matrix.items())},
        per_epic={e: dict(implementation_tickets=len(groups[e]),
                         delivered=sum(bool(delivered(t)) for t in groups[e]))
                  for e in sorted(epics | set(groups))},
        states=dict(sorted(states.items())), elapsed=elapsed, planning_defects=defects,
        accounting=dict(status='UNAVAILABLE', reason='NO_ADMITTED_ENROLLMENT_ACCOUNTING_SNAPSHOT'),
        forecast=dict(status='UNAVAILABLE', reason='NO_ADMITTED_DEPENDENCY_CAPACITY_SCENARIOS'))
