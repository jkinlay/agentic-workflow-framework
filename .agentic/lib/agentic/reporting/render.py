"""Compact channel fallback, explicit unknowns and no silent truncation."""
import html
from urllib.parse import urlsplit

from .. import ValidationError
from .delivery import require
from .records import replay_snapshot


def render_channel(record, platform, *, max_bytes=None, detail_url=None, verify_detail=None):
    values = replay_snapshot(record)
    require(platform in {'teams', 'slack'}, 'REPORT_PLATFORM_INVALID')
    limit = {'slack': 12000, 'teams': 20000}[platform]
    if max_bytes is not None:
        require(type(max_bytes) is int and 0 < max_bytes <= limit, 'REPORT_RENDER_LIMIT_INVALID')
        limit = max_bytes
    def plain(value):
        # Suppress supplied markdown, links and channel-wide mentions. Trusted
        # detail URLs are emitted separately after access verification.
        text = str(value)
        for char in ('*', '_', '`', '[', ']', '(', ')', '@', '\\'):
            text = text.replace(char, ' ')
        return html.escape(text, quote=False)
    bold = '*' if platform == 'slack' else '**'
    def section(title, text):
        return bold + title + bold + '\n\n' + plain(text)
    candidate = values['candidate']
    metrics = values['metrics']
    overview = ('Unknown: ' + metrics['reason'] if metrics['status'] == 'UNKNOWN' else
        'Epics: {epics} | Implementation leaf tickets: {implementation_tickets} | Reconciled delivered: {delivered}'.format(**metrics))
    blocks = [section('AWF ' + values['report_type'] + ' report',
                     'Observed: ' + record['created_at'] + ' | Freshness: ' + values['freshness']),
        section('Summary', values['summary']), section('Scope and candidate',
            values['binding']['scope_revision'] + ' | PR ' + str(candidate['pr']) + '\nHead: ' + candidate['head']
            + '\nBase: ' + candidate['base']),
        section('Scope and progress', overview),
        section('Period', values['period']['start'] + ' to ' + values['period']['end']
                + (' | Catch-up' if values['period']['catch_up'] else '')),
        section('Owner action', values['owner_action']),
        section('Sources and freshness', '\n'.join(s['source'] + ' | ' + s['observed_at']
                + (' | STALE' if s['stale'] else '') for s in values['sources']) or 'Unknown: source observations absent'),
        section('Forecast and usage', 'Unknown: dependency/capacity forecast and enrolled-agent accounting unavailable.\n'
                + 'Baseline: ' + values['baseline_revision'] + ' | Forecast revision: ' + values['forecast_revision']),
        section('Authority', values['authority'])]
    if detail_url is not None:
        parsed = urlsplit(detail_url)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and not any(c in detail_url for c in '<>\r\n ()[]'), 'REPORT_DETAIL_URL_INVALID')
        require(callable(verify_detail), 'REPORT_DETAIL_LINK_UNVERIFIED')
        try:
            verified = verify_detail(detail_url, dict(values['binding']))
        except Exception:
            raise ValidationError('REPORT_DETAIL_LINK_UNVERIFIED') from None
        require(verified is True, 'REPORT_DETAIL_LINK_UNVERIFIED')
        blocks.append(('<' + detail_url + '|Detailed report>') if platform == 'slack'
                      else '[Detailed report](' + detail_url + ')')
    text = '\n\n'.join(blocks)
    require(len(text.encode('utf-8')) <= limit, 'REPORT_RENDER_TOO_LARGE')
    return text
