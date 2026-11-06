"""Versioned supplied report bodies, ordered layouts and bounded presentation.

Every missing section is explicit Unknown. None must be supplied as an observed
fact. Links require a host-owned verifier. This renderer never creates hosting,
approval, a current observation, or authority from report text.
"""
from copy import deepcopy
import html
from urllib.parse import urlsplit

from .. import ValidationError
from ..canonical import canonical, fingerprint, timestamp
from .delivery import require

LAYOUTS = {
    'hourly': ('Summary', 'Current work', 'PRs and Jira tickets', 'Waiting and blockers', 'Next tickets',
               'Capacity and review queue', 'Changes since the previous report', 'Next action', 'Owner action', 'Sources and freshness'),
    'eod': ('Summary', 'PRs merged and Jira delivery', 'Remaining work', 'Critical paths', 'Time and estimate comparison',
            'Usage (tokens)', 'Epic and project forecast', 'Blockers and risks', 'Next action', 'Owner action', 'Sources and freshness'),
    'jira-review': ('Summary', 'Scope and progress', 'PRs and Jira tickets', 'Critical path', 'Epic outlook',
                    'Time and review outcome', 'Blockers', 'Next action', 'Owner action', 'Sources and freshness'),
    'owner-action': ('Decision / readiness', 'Owner action', 'Impact and independent work', 'Evidence',
                     'Summary', 'PRs and Jira tickets', 'Next action', 'Sources and freshness'),
    'final-human-review': ('Decision / readiness', 'Owner action', 'Impact and independent work', 'Evidence',
                           'Summary', 'Scope and progress', 'PRs and Jira tickets', 'Critical path', 'Epic outlook',
                           'Validation and review completion', 'Findings and accepted limitations', 'Time and review outcome',
                           'Blockers', 'Next action', 'Sources and freshness'),
}
TITLES = {'hourly': 'Hourly progress', 'eod': 'End of Day report', 'jira-review': 'AWF Jira review report',
          'owner-action': 'Owner action required', 'final-human-review': 'Ready for final human review / AWF Jira review passed'}


def plain(value):
    text = str(value)
    for char in ('*', '_', '`', '[', ']', '(', ')', '@', '\\'):
        text = text.replace(char, ' ')
    return html.escape(text, quote=False)


def verified_url(url, verify):
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
            and not any(c in url for c in '<>\r\n ()[]|') and callable(verify), 'REPORT_LINK_UNVERIFIED')
    try:
        accepted = verify(url)
    except Exception:
        raise ValidationError('REPORT_LINK_UNVERIFIED') from None
    require(accepted is True, 'REPORT_LINK_UNVERIFIED')
    return url


def prepare_body(kind, data, *, verify_link=None):
    require(kind in LAYOUTS and isinstance(data, dict), 'REPORT_BODY_TYPE')
    require({'project', 'observed_at', 'timezone', 'scope', 'candidate', 'summary', 'sections'} <= data.keys(), 'REPORT_BODY_REQUIRED')
    timestamp(data['observed_at'])
    values = deepcopy(data)
    require(isinstance(values['sections'], dict), 'REPORT_BODY_SECTION')
    # Accept legacy supplied metric keys while new bodies use a label outside
    # the credential grammar. The detector itself retains every existing rule.
    if 'Token usage' in values['sections']:
        require('Usage (tokens)' not in values['sections'], 'REPORT_BODY_SECTION')
        values['sections']['Usage (tokens)'] = values['sections'].pop('Token usage')
    require(set(values['sections']) <= set(LAYOUTS[kind]), 'REPORT_BODY_SECTION')
    for pr in values.get('prs', []):
        verified_url(pr['url'], verify_link)
        for ticket in pr.get('tickets', []):
            verified_url(ticket['url'], verify_link)
    for group in ('current', 'next', 'epics'):
        for item in values.get(group, []):
            if item.get('url'):
                verified_url(item['url'], verify_link)
    if values.get('detail_url'):
        verified_url(values['detail_url'], verify_link)
    payload = dict(format='awf-report-body-1', kind=kind, data=values)
    return dict(**payload, sha256=fingerprint('report-body-1', payload))


def render_body(body, platform, *, max_bytes=None):
    require(platform in {'slack', 'teams'}, 'REPORT_PLATFORM_INVALID')
    payload = {k: body[k] for k in ('format', 'kind', 'data')}
    require(body.get('format') == 'awf-report-body-1' and body.get('sha256') == fingerprint('report-body-1', payload),
            'REPORT_BODY_HASH')
    kind, data = body['kind'], body['data']
    require(kind in LAYOUTS, 'REPORT_BODY_TYPE')
    limit = {'slack': 12000, 'teams': 20000}[platform]
    if max_bytes is not None:
        require(type(max_bytes) is int and 0 < max_bytes <= limit, 'REPORT_RENDER_LIMIT_INVALID')
        limit = max_bytes
    bold = '*' if platform == 'slack' else '**'
    def link(url, title):
        return '<' + url + '|' + plain(title) + '>' if platform == 'slack' else '[' + plain(title) + '](' + url + ')'
    def item_title(item):
        return (link(item['url'], item['key']) if item.get('url') else plain(item['key'])) + ' — ' + plain(item.get('title', 'Unknown title'))
    def ticket_block(item, group):
        lines = [item_title(item), 'Planned start: ' + plain(item.get('planned_start') or 'Unknown: planning defect')
                 + ' | Planned end: ' + plain(item.get('planned_end') or 'Unknown: planning defect')]
        fields = (('Owner', 'owner'), ('Remaining', 'remaining'), ('ETA / milestone', 'eta'), ('Tokens', 'tokens'),
                  ('Dependencies, assumptions and forecast change', 'assumptions')) if group == 'epics' else (
                  ('Stage', 'stage'), ('Actor/stream', 'actor'), ('Elapsed', 'elapsed'), ('Next gate', 'next_gate'),
                  ('Activity / eligibility and dependencies', 'activity'))
        lines += [label + ': ' + plain(item.get(key, 'Unknown')) for label, key in fields]
        if item.get('conditional'):
            lines.append('CONDITIONAL candidate')
        return '\n'.join(lines)
    prs = []
    for pr in data.get('prs', []):
        tickets = ' — '.join(link(t['url'], t['key']) for t in pr.get('tickets', [])) or 'Jira association unverified'
        prs.append(link(pr['url'], 'PR #' + str(pr['number'])) + ' — ' + tickets
                   + '\nStatus: ' + plain(pr.get('status', 'Unknown')) + ' | Reviewed head: ' + plain(pr.get('head', 'Unknown'))
                   + '\nMerged: ' + plain(pr.get('merged_at', 'Unknown')) + ' | Merge commit: ' + plain(pr.get('merge_commit', 'Unknown')))
    sections = dict(data['sections'])
    # Render retained legacy bodies without rewriting their bytes or digest.
    if 'Token usage' in sections and 'Usage (tokens)' not in sections:
        sections['Usage (tokens)'] = sections.pop('Token usage')
    sections['Summary'] = data['summary']
    header = bold + plain(data['project']) + ' — ' + TITLES[kind] + bold
    header += '\n\nObserved: ' + plain(data['observed_at']) + ' | Timezone: ' + plain(data['timezone'])
    header += '\nScope: ' + plain(data['scope']) + ' | Candidate: ' + plain(data['candidate'])
    header += '\nStage: ' + plain(data.get('stage', 'Unknown; approval, merge and reconciled Done are separate'))
    if kind in {'hourly', 'eod'}:
        header += '\nPeriod: ' + plain(data.get('period', 'Unknown')) + ' | Data: ' + plain(data.get('freshness', 'Unknown'))
        if data.get('catch_up'):
            header += '\nCATCH-UP — original period, current observation; no historical freshness implied'
    if kind == 'eod':
        header += '\nToday: ' + plain(data.get('today', 'Unknown'))
        header += '\nSince previous daily report: ' + plain(data.get('since_previous', 'Unknown'))
        header += '\nOverlapping reconciliation views; never add together'
    blocks = [header]
    for title in LAYOUTS[kind]:
        text = plain(sections.get(title, 'Unknown: ' + title + ' observations unavailable'))
        if kind in {'owner-action', 'final-human-review'}:
            if title == 'Decision / readiness':
                text += '\nDecision: ' + plain(data.get('decision', 'Unknown'))
                text += '\nCandidate: ' + plain(data['candidate']) + ' | Stage: ' + plain(data.get('stage', 'Unknown'))
                text += '\nNext gate: ' + plain(data.get('next_gate', 'Unknown'))
            if title == 'Owner action':
                text += '\nDecision owner: ' + plain(data.get('decision_owner', 'Unknown'))
                text += '\nRequested response: ' + plain(data.get('requested_response', 'Unknown'))
                text += '\nOptions: ' + plain('; '.join(data.get('options', [])) or 'Unknown')
                text += '\nRecommendation: ' + plain(data.get('recommendation', 'Unknown'))
            if title == 'Impact and independent work':
                text += '\nConsequence of delay: ' + plain(data.get('delay_consequence', 'Unknown'))
                text += '\nWork that can continue: ' + plain(data.get('independent_work', 'Unknown'))
                text += '\nResume condition: ' + plain(data.get('resume', 'Unknown'))
        if title == 'PRs and Jira tickets' and 'prs' in data:
            text = '\n\n'.join(prs) or 'None'
        if title == 'PRs merged and Jira delivery' and 'prs' in data:
            merged = [block for pr, block in zip(data['prs'], prs) if pr.get('merged_at')]
            text += '\n\nDistinct merged PRs: ' + str(len({pr['url'] for pr in data['prs'] if pr.get('merged_at')}))
            text += '\n\n' + ('\n\n'.join(merged) or 'None')
            text += '\nTickets delivered: ' + plain(data.get('delivered', 'Unknown; PR count is not ticket count'))
        for label, group in (('Current work', 'current'), ('Next tickets', 'next'), ('Epic outlook', 'epics'), ('Epic and project forecast', 'epics')):
            if title == label and group in data:
                text = '\n\n'.join(ticket_block(t, group) for t in data[group]) or 'None'
                if group == 'epics':
                    text += '\n\nCommon overhead and scope reconciliation: ' + plain(data.get('overhead', 'Unknown'))
        if title == 'Sources and freshness':
            text += '\nBaseline: ' + plain(data.get('baseline_revision', 'Unknown')) + ' | Forecast version: ' + plain(data.get('forecast_revision', 'Unknown'))
            if data.get('detail_url'):
                text = link(data['detail_url'], 'Detailed report') + '\n\n' + text
        blocks.append(bold + title + bold + '\n\n' + text)
    blocks.append('REPORT ONLY — no merge, release, dispatch, chat or accounting authority')
    text = '\n\n'.join(blocks).replace('AWF delivery:', 'AWF delivery (quoted):')
    require(len(text.encode('utf-8')) <= limit, 'REPORT_RENDER_TOO_LARGE')
    return text


def render_detail(body):
    """Complete versioned data artifact for approved hosting; never shortened."""
    payload = {k: body[k] for k in ('format', 'kind', 'data')}
    require(body['sha256'] == fingerprint('report-body-1', payload), 'REPORT_BODY_HASH')
    return canonical(body).decode('utf-8')
