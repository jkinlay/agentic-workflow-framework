"""Standard-library Slack Web API / Teams Graph outbound reporting transports.

No automatic HTTP retries or redirects. Credentials are operation arguments from
the existing secret-reference resolver, never stored on the transport. A missing
message is UNKNOWN: these APIs cannot prove terminal absence of a delayed POST.
The existing durable outbox alone owns attempt admission. Tests inject fake HTTP.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from time import sleep
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from .. import ValidationError
from ..canonical import fingerprint, timestamp
from .delivery import require, valid_message_id
from .safety import safe_report
from .deadline import RequestDeadline, DeadlineHTTPSHandler

MAX_RESPONSE = 2 * 1024 * 1024
# These responses reject the request; timeouts, conflicts and server errors
# are intentionally absent. Never infer non-delivery from arbitrary diagnostics.
REJECTED_HTTP = {400, 401, 403, 404, 405, 410, 413, 415, 422}
REJECTED_SLACK = {'channel_not_found', 'not_in_channel', 'invalid_auth', 'account_inactive',
                  'token_revoked', 'missing_scope', 'is_archived', 'msg_too_long', 'no_text',
                  'invalid_arguments', 'invalid_blocks', 'restricted_action'}


class TerminalRejection(ValidationError):
    def __init__(self):
        super().__init__('REPORT_PROVIDER_REJECTED')


class Throttled(ValidationError):
    """Safe numeric delay only; never carry provider diagnostics into state."""
    def __init__(self, headers=None):
        super().__init__('REPORT_PROVIDER_THROTTLED')
        value = next((v for k, v in (headers or {}).items() if k.lower() == 'retry-after'), None)
        # Both providers specify seconds. Missing/invalid values use 60 seconds;
        # never sleep here or clip a large valid delay to an earlier retry.
        self.seconds = 60
        if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
            self.seconds = max(1, int(value)) if len(value) <= 12 else 10**12
        self.deadline = None


def throttle_deadline(observed, seconds):
    now = timestamp(observed)
    maximum = datetime(9999, 12, 31, tzinfo=timezone.utc)
    delay = min(seconds, int((maximum - now).total_seconds()))
    return (now + timedelta(seconds=delay)).isoformat().replace('+00:00', 'Z')


def graph_next_link(url, resource):
    """Compare decoded segments, never decode encoded path separators away."""
    parsed, expected = urlsplit(url), urlsplit(resource)
    require(parsed.scheme == 'https' and parsed.netloc == 'graph.microsoft.com'
            and not parsed.fragment and not any(ord(c) < 32 for c in url)
            and tuple(unquote(p, errors='strict') for p in parsed.path.split('/'))
            == tuple(unquote(p, errors='strict') for p in expected.path.split('/')),
            'REPORT_PAGINATION_ORIGIN')
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_json(method, url, headers, payload, *, timeout=15):
    """One request, finite timeout/response. Never retain provider diagnostics."""
    require(type(timeout) in {int, float} and 0 < timeout <= 15, 'REPORT_HTTP_TIMEOUT')
    require(urlsplit(url).scheme == 'https' and urlsplit(url).hostname in {'slack.com', 'graph.microsoft.com'},
            'REPORT_HTTP_ORIGIN')
    try:
        with RequestDeadline(timeout) as deadline:
            data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            request = Request(url, data=data, method=method, headers=headers)
            # Direct TLS keeps origin and socket ownership explicit; ambient
            # proxy settings cannot bypass the request deadline.
            with build_opener(NoRedirect(), ProxyHandler({}), DeadlineHTTPSHandler(deadline)).open(
                    request, timeout=timeout) as response:
                require(200 <= response.status < 300, 'REPORT_HTTP_FAILURE')
                # Also watch an HTTPResponse supplied by a network test port.
                sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if sock is not None:
                    deadline.watch(sock)
                raw = response.read(MAX_RESPONSE + 1)
                deadline.remaining()
        require(len(raw) <= MAX_RESPONSE, 'REPORT_HTTP_RESPONSE_SIZE')
        value = json.loads(raw)
        require(isinstance(value, dict), 'REPORT_HTTP_RESPONSE_SHAPE')
        return value
    except HTTPError as error:
        if error.code == 429:
            raise Throttled(error.headers) from None
        if method == 'POST' and error.code in REJECTED_HTTP:
            raise TerminalRejection() from None
        raise ValidationError('REPORT_HTTP_UNAVAILABLE') from None
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


def graph_matches(actual, expected, marker):
    parser = _Text()
    parser.feed(actual)
    lines = [line.strip() for line in ''.join(parser.parts).splitlines() if line.strip()]
    return (bool(lines) and lines[-1] == marker
            and fingerprint('report-graph-content-1', list(content_identity(actual)))
            == fingerprint('report-graph-content-1', list(content_identity(expected))))


# Explicit aliases only: never discard arbitrary emoji or colon-delimited text.
# Unknown conversions fail closed instead of allowing different content to match.
SLACK_EMOJI = {
    '\U0001f389': 'tada', '\U0001f600': 'grinning', '\U0001f603': 'smiley',
    '\U0001f604': 'smile', '\U0001f601': 'grin', '\U0001f602': 'joy',
    '\U0001f605': 'sweat_smile', '\U0001f606': 'laughing', '\U0001f609': 'wink',
    '\U0001f60a': 'blush', '\U0001f60d': 'heart_eyes', '\U0001f622': 'cry',
    '\U0001f62d': 'sob', '\U0001f680': 'rocket', '\U0001f525': 'fire',
    '\U0001f44d': '+1', '\U0001f44e': '-1', '\U0001f44f': 'clap',
    '\U0001f440': 'eyes', '\U0001f4a1': 'bulb', '\U0001f4cc': 'pushpin',
    '\u2705': 'white_check_mark', '\u274c': 'x', '\u26a0': 'warning',
    '\u2764': 'heart', '\u2139': 'information_source', '\u23f3': 'hourglass_flowing_sand',
}


def slack_identity(value):
    # Slack's three documented escaping entities, once (not arbitrary HTML).
    value = value.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    for emoji, alias in SLACK_EMOJI.items():
        value = value.replace(emoji + '\ufe0f', ':' + alias + ':').replace(emoji, ':' + alias + ':')
    return hashlib.sha256(value.replace('\r\n', '\n').encode('utf-8')).hexdigest()


def teams_html(value):
    # Only our renderer's constrained bold and HTTPS Markdown links become HTML.
    # Escape all supplied HTML first; Graph text messages do not render Markdown.
    value = html.escape(value, quote=True)
    value = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'\[([^\]\n]+)\]\((https://[^\s()]+)\)', r'<a href="\2">\1</a>', value)
    return value.replace('\n', '<br>')


class BaseTransport:
    platform = None

    def __init__(self, *, context, sender, http=http_json, clock=None, pause=sleep):
        require(isinstance(context, str) and context and isinstance(sender, str) and sender, 'REPORT_TRANSPORT_BINDING')
        self.context, self.sender, self.http = context, sender, http
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        self.pause = pause
        self.cooldown_store = None
        self.cooldowns = {}

    def cooldown_key(self, url):
        # Slack: every destination/app in this workspace shares a method limit.
        # Graph: conservatively group ALL resources/methods within the tenant.
        # Neither credential rotation, sender, thread nor binding revision resets it.
        method = urlsplit(url).path.rsplit('/', 1)[-1] if self.platform == 'slack' else 'tenant'
        return 'report-cooldown:' + fingerprint('report-cooldown-1', [self.platform, self.context, method])

    def cooldown(self, url, error=None):
        key, observed = self.cooldown_key(url), self.clock()
        if self.cooldown_store is None:
            retained = self.cooldowns.get(key)
            if error is not None:
                deadline = throttle_deadline(observed, error.seconds)
                self.cooldowns[key] = max(retained or deadline, deadline, key=timestamp)
                error.deadline = self.cooldowns[key]
        else:
            with self.cooldown_store.transaction() as db:
                row = db.execute('SELECT retry_not_before FROM controller_report_cooldown WHERE scope=?', (key,)).fetchone()
                retained = row[0] if row else None
                if error is not None:
                    deadline = throttle_deadline(observed, error.seconds)
                    deadline = max(retained or deadline, deadline, key=timestamp)
                    db.execute('INSERT INTO controller_report_cooldown VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET retry_not_before=excluded.retry_not_before',
                               (key, deadline))
                    error.deadline = deadline
        if error is None and retained and timestamp(observed) < timestamp(retained):
            deferred = Throttled()
            deferred.deadline = retained
            raise deferred

    def call(self, method, url, secret, payload=None):
        if method == 'POST':
            require(safe_report(payload, trusted_keys=True, credentials=(secret,)) == payload,
                    'REPORT_CREDENTIAL_WITHHELD')
        try:
            require(isinstance(secret, str) and secret and not any(ord(c) < 32 for c in secret), 'REPORT_SECRET_INVALID')
            self.cooldown(url)
            from .bounded import admit_http
            timeout = admit_http(method)
            options = {'timeout': timeout} if self.http is http_json else {}
            raw = self.http(method, url, {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json; charset=utf-8'}, payload, **options)
            require(isinstance(raw, dict), 'REPORT_HTTP_RESPONSE_SHAPE')
            return raw
        except TerminalRejection:
            raise TerminalRejection() from None
        except Throttled as error:
            if error.deadline is None:
                self.cooldown(url, error)
            raise
        except HTTPError as error:
            if error.code == 429:
                throttle = Throttled(error.headers)
                self.cooldown(url, throttle)
                raise throttle from None
            if method == 'POST' and error.code in REJECTED_HTTP:
                raise TerminalRejection() from None
            raise ValidationError('REPORT_HTTP_UNAVAILABLE') from None
        except Exception:
            raise ValidationError('REPORT_HTTP_UNAVAILABLE') from None

    def wire_text(self, intent):
        # Inspect raw members before Teams HTML conversion can split a detected
        # credential across markup. Direct transport callers get the same guard.
        require(safe_report(intent, trusted_keys=True) == intent, 'REPORT_CREDENTIAL_WITHHELD')
        require('AWF delivery:' not in intent['payload'], 'REPORT_RESERVED_MARKER')
        value = intent['payload'] + '\n\n' + self.marker(intent)
        return teams_html(value) if self.platform == 'teams' else value

    @staticmethod
    def marker(intent):
        return 'AWF delivery: ' + intent['delivery_id'] + ' / ' + intent['payload_sha256']

    def throttle(self, intent, attempt_id, error):
        observed = self.clock()
        # Saturate at the last representable year, rather than retrying early
        # on an overflowing provider value. Only canonical timestamps persist.
        deadline = error.deadline or throttle_deadline(observed, error.seconds)
        return dict(status='THROTTLED', evidence_kind='PROVIDER_THROTTLE',
                    delivery_id=intent['delivery_id'], platform=self.platform,
                    destination=intent['binding']['destination'], payload_sha256=intent['payload_sha256'],
                    attempt_id=attempt_id, observed_at=observed, retry_not_before=deadline,
                    observation_id=fingerprint('report-throttle-1', [intent['delivery_id'], attempt_id, observed, deadline]))

    def evidence(self, intent, attempts, message_id):
        require(valid_message_id(self.platform, message_id), 'REPORT_PROVIDER_ID_INVALID')
        observed = self.clock()
        evidence = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', delivery_id=intent['delivery_id'],
                    platform=self.platform, destination=intent['binding']['destination'],
                    payload_sha256=intent['payload_sha256'], attempt_ids=list(attempts), message_id=message_id,
                    observed_at=observed, observation_id=fingerprint('report-readback-1', [intent['delivery_id'], message_id, observed]))
        require(safe_report(evidence, trusted_keys=True) == evidence, 'REPORT_CREDENTIAL_WITHHELD')
        return evidence

    def rejection(self, intent, attempt_id):
        observed = self.clock()
        return dict(status='REJECTED', terminal=True, evidence_kind='PROVIDER_REJECTION',
                    delivery_id=intent['delivery_id'], platform=self.platform,
                    destination=intent['binding']['destination'], payload_sha256=intent['payload_sha256'],
                    attempt_id=attempt_id, observed_at=observed,
                    observation_id=fingerprint('report-rejection-1', [intent['delivery_id'], attempt_id, observed]))

    def reconcile(self, intent, attempts, secret):
        # A finite settle window helps delayed visibility, never proves absence.
        # These listing APIs cannot exclude delayed POSTs or stale senders.
        for poll in range(3):
            if poll:
                self.pause(0.1)
            result = self._readback(intent, attempts, secret)
            if result.get('status') == 'VISIBLE':
                return result
        return dict(status='UNKNOWN')


class SlackTransport(BaseTransport):
    platform = 'slack'
    origin = 'https://slack.com/api/'

    def api(self, method, path, secret, payload=None, **query):
        url = self.origin + path + ('?' + urlencode(query) if query else '')
        result = self.call(method, url, secret, payload)
        if result.get('ok') is False and result.get('error') == 'ratelimited':
            error = Throttled()
            self.cooldown(url, error)
            raise error
        if method == 'POST' and result.get('ok') is False and result.get('error') in REJECTED_SLACK:
            raise TerminalRejection()
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
        try:
            payload = dict(channel=intent['binding']['destination'], text=text,
                     mrkdwn=True, parse='none', unfurl_links=False, unfurl_media=False, client_msg_id=attempt_id)
            if intent.get('thread_id'):
                payload['thread_ts'] = intent['thread_id']
            self.api('POST', 'chat.postMessage', secret, payload)
        except TerminalRejection:
            return self.rejection(intent, attempt_id)
        except Throttled as error:
            return self.throttle(intent, attempt_id, error)

    def _readback(self, intent, attempts, secret):
        cursor = None
        for _ in range(10):
            query = dict(channel=intent['binding']['destination'], limit=100)
            if intent.get('thread_id'):
                query['ts'] = intent['thread_id']
            if cursor:
                query['cursor'] = cursor
            result = self.api('GET', 'conversations.replies' if intent.get('thread_id') else 'conversations.history', secret, **query)
            for message in result.get('messages', []):
                if (isinstance(message.get('text'), str)
                        and message['text'].replace('\r\n', '\n').endswith('\n\n' + self.marker(intent))
                        and slack_identity(message['text']) == slack_identity(self.wire_text(intent))
                        and message.get('user') == self.sender
                        and message.get('channel', intent['binding']['destination']) == intent['binding']['destination']
                        and (message.get('thread_ts') == intent['thread_id'] if intent.get('thread_id') else
                             message.get('thread_ts') in {None, message.get('ts')})
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
        identity_valid = (me.get('id') == self.sender and any(o.get('id') == self.context for o in org.get('value', []))
                and actual_team.get('id') == binding['destination'].split('/')[0]
                and actual_channel.get('id') == binding['destination'].split('/')[1])
        if not identity_valid:
            return False
        resource = team + '/channels/' + channel + '/members'
        url = resource
        for _ in range(10):
            members = self.call('GET', url, secret)
            if any(m.get('userId') == self.sender for m in members.get('value', [])):
                return True
            url = members.get('@odata.nextLink')
            if not url:
                break
            url = graph_next_link(url, resource)
        return False

    def send(self, intent, attempt_id, secret):
        team, channel = self.route(intent['binding'])
        text = self.wire_text(intent)
        require(len(text.encode('utf-8')) <= 20000, 'REPORT_PAYLOAD_TOO_LARGE')
        try:
            path = team + '/channels/' + channel + '/messages'
            if intent.get('thread_id'):
                path += '/' + quote(intent['thread_id'], safe='') + '/replies'
            self.call('POST', path, secret,
                      dict(body=dict(contentType='html', content=text)))
        except TerminalRejection:
            return self.rejection(intent, attempt_id)
        except Throttled as error:
            return self.throttle(intent, attempt_id, error)

    def _readback(self, intent, attempts, secret):
        team, channel = self.route(intent['binding'])
        path = team + '/channels/' + channel + '/messages'
        if intent.get('thread_id'):
            path += '/' + quote(intent['thread_id'], safe='') + '/replies'
        url = path + '?$top=50'
        for _ in range(10):
            result = self.call('GET', url, secret)
            for message in result.get('value', []):
                body = message.get('body', {})
                if (graph_matches(body.get('content', ''), self.wire_text(intent), self.marker(intent))
                        and message.get('from', {}).get('user', {}).get('id') == self.sender
                        and not message.get('deletedDateTime') and isinstance(message.get('id'), str) and message['id']):
                    return self.evidence(intent, attempts, message['id'])
            url = result.get('@odata.nextLink')
            if not url:
                break
            # Provider pagination may never move credentials off this resource.
            url = graph_next_link(url, path)
        return dict(status='UNKNOWN')
