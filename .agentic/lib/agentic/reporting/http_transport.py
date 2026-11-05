"""Standard-library Slack Web API / Teams Graph outbound reporting transports.

No automatic HTTP retries or redirects. Credentials are operation arguments from
the existing secret-reference resolver, never stored on the transport. A missing
message is UNKNOWN: these APIs cannot prove terminal absence of a delayed POST.
The existing durable outbox alone owns attempt admission. Tests inject fake HTTP.
"""
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .. import ValidationError
from ..canonical import fingerprint
from .delivery import require

MAX_RESPONSE = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_json(method, url, headers, payload):
    """One request, finite timeout/response. Never retain provider diagnostics."""
    require(urlsplit(url).scheme == 'https' and urlsplit(url).hostname in {'slack.com', 'graph.microsoft.com'},
            'REPORT_HTTP_ORIGIN')
    try:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(url, data=data, method=method, headers=headers)
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            require(200 <= response.status < 300, 'REPORT_HTTP_FAILURE')
            raw = response.read(MAX_RESPONSE + 1)
        require(len(raw) <= MAX_RESPONSE, 'REPORT_HTTP_RESPONSE_SIZE')
        value = json.loads(raw)
        require(isinstance(value, dict), 'REPORT_HTTP_RESPONSE_SHAPE')
        return value
    except Exception:
        raise ValidationError('REPORT_HTTP_UNAVAILABLE') from None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []

    def handle_data(self, value):
        self.parts.append(value)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href'))
        if tag in {'br', 'p', 'div'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'p', 'div'}:
            self.parts.append('\n')


def text_content(value):
    parser = _Text()
    parser.feed(value)
    return ' '.join(''.join(parser.parts).split())


def content_identity(value):
    parser = _Text()
    parser.feed(value)
    return (' '.join(''.join(parser.parts).split()), parser.links)


def teams_html(value):
    # Only our renderer's constrained bold and HTTPS Markdown links become HTML.
    # Escape all supplied HTML first; Graph text messages do not render Markdown.
    value = html.escape(value, quote=True)
    value = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'\[([^\]\n]+)\]\((https://[^\s()]+)\)', r'<a href="\2">\1</a>', value)
    return value.replace('\n', '<br>')


class BaseTransport:
    platform = None

    def __init__(self, *, context, sender, http=http_json, clock=None):
        require(isinstance(context, str) and context and isinstance(sender, str) and sender, 'REPORT_TRANSPORT_BINDING')
        self.context, self.sender, self.http = context, sender, http
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))

    def call(self, method, url, secret, payload=None):
        try:
            require(isinstance(secret, str) and secret and not any(ord(c) < 32 for c in secret), 'REPORT_SECRET_INVALID')
            raw = self.http(method, url, {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json; charset=utf-8'}, payload)
            require(isinstance(raw, dict), 'REPORT_HTTP_RESPONSE_SHAPE')
            return raw
        except Exception:
            raise ValidationError('REPORT_HTTP_UNAVAILABLE') from None

    def wire_text(self, intent):
        marker = '\n\nAWF delivery: ' + intent['delivery_id'] + ' / ' + intent['payload_sha256']
        value = intent['payload'] + marker
        return teams_html(value) if self.platform == 'teams' else value

    def evidence(self, intent, attempts, message_id):
        observed = self.clock()
        return dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', delivery_id=intent['delivery_id'],
                    platform=self.platform, destination=intent['binding']['destination'],
                    payload_sha256=intent['payload_sha256'], attempt_ids=list(attempts), message_id=message_id,
                    observed_at=observed, observation_id=fingerprint('report-readback-1', [intent['delivery_id'], message_id, observed]))


class SlackTransport(BaseTransport):
    platform = 'slack'
    origin = 'https://slack.com/api/'

    def api(self, method, path, secret, payload=None, **query):
        url = self.origin + path + ('?' + urlencode(query) if query else '')
        result = self.call(method, url, secret, payload)
        require(result.get('ok') is True, 'REPORT_SLACK_UNAVAILABLE')
        return result

    def verify_destination(self, binding, secret):
        require(binding['platform'] == self.platform and re.fullmatch('[CG][A-Z0-9]+', binding['destination']), 'REPORT_SLACK_DESTINATION')
        auth = self.api('GET', 'auth.test', secret)
        channel = self.api('GET', 'conversations.info', secret, channel=binding['destination']).get('channel', {})
        return (auth.get('team_id') == self.context and auth.get('user_id') == self.sender
                and channel.get('id') == binding['destination'] and channel.get('is_member') is True
                and not channel.get('is_archived', False))

    def send(self, intent, attempt_id, secret):
        text = self.wire_text(intent)
        require(len(text.encode('utf-8')) <= 12000, 'REPORT_PAYLOAD_TOO_LARGE')
        self.api('POST', 'chat.postMessage', secret, dict(channel=intent['binding']['destination'], text=text,
                 mrkdwn=True, parse='none', unfurl_links=False, unfurl_media=False, client_msg_id=attempt_id))

    def reconcile(self, intent, attempts, secret):
        cursor = None
        for _ in range(10):
            query = dict(channel=intent['binding']['destination'], limit=100)
            if cursor:
                query['cursor'] = cursor
            result = self.api('GET', 'conversations.history', secret, **query)
            for message in result.get('messages', []):
                if (message.get('text') == self.wire_text(intent) and message.get('user') == self.sender
                        and isinstance(message.get('ts'), str) and message['ts']):
                    return self.evidence(intent, attempts, message['ts'])
            cursor = result.get('response_metadata', {}).get('next_cursor')
            if not cursor:
                break
        return dict(status='UNKNOWN')


class TeamsTransport(BaseTransport):
    """Graph delegated sender; ordinary application-only migration send is not used.

    context is the approved tenant ID; /me and channel membership are verified
    per operation. Destination is exact team-ID/channel-ID, never a display name.
    Token acquisition/tenant issuer validation belongs to the trusted resolver.
    """
    platform = 'teams'
    origin = 'https://graph.microsoft.com/v1.0'

    def route(self, binding):
        require(binding['platform'] == self.platform, 'REPORT_PLATFORM_MISMATCH')
        parts = binding['destination'].split('/')
        require(len(parts) == 2 and all(parts) and all(p not in {'.', '..'} for p in parts), 'REPORT_TEAMS_DESTINATION')
        return self.origin + '/teams/' + quote(parts[0], safe=''), quote(parts[1], safe='')

    def verify_destination(self, binding, secret):
        team, channel = self.route(binding)
        me = self.call('GET', self.origin + '/me?$select=id', secret)
        org = self.call('GET', self.origin + '/organization?$select=id', secret)
        actual_team = self.call('GET', team, secret)
        actual_channel = self.call('GET', team + '/channels/' + channel, secret)
        members = self.call('GET', team + '/channels/' + channel + '/members', secret)
        return (me.get('id') == self.sender and any(o.get('id') == self.context for o in org.get('value', []))
                and actual_team.get('id') == binding['destination'].split('/')[0]
                and actual_channel.get('id') == binding['destination'].split('/')[1]
                and any(m.get('userId') == self.sender for m in members.get('value', [])))

    def send(self, intent, attempt_id, secret):
        team, channel = self.route(intent['binding'])
        text = self.wire_text(intent)
        require(len(text.encode('utf-8')) <= 20000, 'REPORT_PAYLOAD_TOO_LARGE')
        self.call('POST', team + '/channels/' + channel + '/messages', secret,
                  dict(body=dict(contentType='html', content=text)))

    def reconcile(self, intent, attempts, secret):
        team, channel = self.route(intent['binding'])
        path = team + '/channels/' + channel + '/messages'
        url = path + '?$top=50'
        for _ in range(10):
            result = self.call('GET', url, secret)
            for message in result.get('value', []):
                body = message.get('body', {})
                if (content_identity(body.get('content', '')) == content_identity(self.wire_text(intent))
                        and message.get('from', {}).get('user', {}).get('id') == self.sender
                        and not message.get('deletedDateTime') and isinstance(message.get('id'), str) and message['id']):
                    return self.evidence(intent, attempts, message['id'])
            url = result.get('@odata.nextLink')
            if not url:
                break
            # Provider pagination may never move credentials off this resource.
            parsed = urlsplit(url)
            require(parsed.scheme == 'https' and parsed.netloc == 'graph.microsoft.com'
                    and parsed.path == urlsplit(path).path, 'REPORT_PAGINATION_ORIGIN')
        return dict(status='UNKNOWN')
