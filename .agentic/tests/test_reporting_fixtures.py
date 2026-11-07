"""Network-only provider fixtures; synthetic identities in documented API shapes."""
from copy import deepcopy
import io
import json
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit

TEAM_ID = 'fbe2bf47-16c8-47cf-b4a5-4b9b187c508b'
CHANNEL_ID = '19:4a95f7d8db4c4e7fae857bcebe0623e6@thread.tacv2'
TEAMS_DESTINATION = TEAM_ID + '/' + CHANNEL_ID


def isolate_reporting_admission(case):
    """Publish the probe's validated settings in a disposable default ref."""
    from unittest.mock import patch
    import test_awf244_controller_wiring as calendar_fixture
    import settings_pr_fixtures
    publish = settings_pr_fixtures.accepted_fixture_snapshot
    prepared = {}
    def snapshot(owner, root, value, governance):
        checked = calendar_fixture.validate_operating(value, governance)
        # Reusing unchanged fixture input must not manufacture a new policy
        # commit in a timed controller call. Independently observe its default
        # ref; actual runtime admission remains unpatched on every cycle.
        import subprocess
        import shutil
        from pathlib import Path
        from agentic.canonical import canonical
        from agentic.child_process import isolated_git_env
        key = (Path(root), canonical(checked.config), canonical(governance))
        prior = prepared.get(key)
        def default_commit():
            observed = subprocess.run([shutil.which('git'), '-C', str(root), 'rev-parse',
                '--verify', 'refs/remotes/origin/HEAD'], env=isolated_git_env(),
                capture_output=True, check=False)
            return observed.stdout if observed.returncode == 0 else None
        current = default_commit() if prior is not None else None
        if prior is not None and current is not None and prior[0] == current:
            return prior[1]
        result = publish(owner, root, checked.config, governance)
        prepared[key] = (default_commit(), result)
        return result
    boundary = patch('settings_pr_fixtures.accepted_fixture_snapshot', snapshot)
    boundary.start()
    case.addCleanup(boundary.stop)
    # The calendar harness publishes its final policy in its own repository.
    # Its preliminary planning snapshot is synthetic input, not a policy read.
    # Avoid reading a second, unused source clone inside runtime timing probes;
    # production still independently admits the subsequently published policy.
    from test_planning import operating_snapshot
    planning = patch('test_continuous_controller_planning.fixture_operating_snapshot',
                     lambda governance, count, **kwargs: operating_snapshot(governance, count))
    planning.start()
    case.addCleanup(planning.stop)


def refresh_reporting_repository(case, root):
    import shutil
    import subprocess
    from agentic.child_process import isolated_git_env
    head, tree = subprocess.check_output([shutil.which('git'), '-C', str(root),
        'rev-parse', 'HEAD', 'HEAD^{tree}'], env=isolated_git_env()).decode().splitlines()
    case.repository.update(repository_root=root, repository_head_sha=head, repository_tree_sha=tree)


def prepare_reporting_repository(case, root):
    """Give a source clone this probe's real accepted three-lane policy."""
    from test_planning import config, operating_snapshot
    from settings_pr_fixtures import publish_fixture_settings
    governance = config(3)
    governance['project']['id'] = case.binding['project']
    governance['github']['repository_id'] = case.binding['repository']
    value = operating_snapshot(governance, 3).config
    value['reporting'] = deepcopy(case.settings)
    publish_fixture_settings(case, root, governance, value)


def reporting_policy_snapshot(case):
    from test_reporting_e2_regressions import ReportingE2Regressions
    return ReportingE2Regressions.policy_snapshot(case)


def slack_text(value):
    # Slack stores Unicode emoji as colon notation, even with parse=none.
    return value.replace('\U0001f389', ':tada:')


def slack_message(payload, index=0):
    message = dict(type='message', text=slack_text(payload['text']), user='sender',
                   ts=str(1793902283 + index) + '.424109')
    if payload.get('thread_ts'):
        message['thread_ts'] = payload['thread_ts']
    return message


def graph_message(payload, index=0):
    return dict(id=str(1793902283424 + index), messageType='message',
                body=deepcopy(payload['body']), **{'from': {'user': {'id': 'sender'}}})


class JsonResponse:
    """urllib response port, including Graph's actual 201 status and body."""
    def __init__(self, value, status=200):
        self.raw = json.dumps(value).encode('utf-8')
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.raw[:size]


class ReportingNetwork:
    def __init__(self):
        self.posts = []
        self.messages = {'slack': [], 'teams': []}
        self.block = threading.Event()
        self.entered = threading.Event()
        self.slow = False
        self.slow_readback = False
        self.history_reads = 0
        self.remote_pending = []
        self.membership_paged = False
        self.messages_paged = False
        self.throttled = set()
        self.read_throttled = set()
        self.timeout_post = lambda platform, payload: False
        self.slack_page_size = 100
        self.retry_after = '60'
        self.calls = []
        self.message_filter = lambda platform, messages: messages
        self.next_link = lambda path: path + '?$skiptoken=page2'

    def open(self, request, timeout):
        """Replace only urllib's network opener; exercise http_json itself."""
        payload = json.loads(request.data) if request.data else None
        value = self(request.get_method(), request.full_url, request.headers, payload)
        return JsonResponse(value, 201 if request.get_method() == 'POST' and
                            'graph.microsoft.com' in request.full_url else 200)

    def __call__(self, method, url, headers, payload):
        self.calls.append((method, url))
        platform = 'slack' if 'slack.com' in url else 'teams'
        path = unquote(urlsplit(url).path)
        second = '$skiptoken=' in url
        if method == 'GET' and any(part in url for part in self.read_throttled):
            headers = {} if self.retry_after is None else {'Retry-After': self.retry_after}
            raise HTTPError(url, 429, 'Too Many Requests', headers, io.BytesIO(b'{}'))
        if method == 'POST':
            self.posts.append((platform, deepcopy(payload)))
            if platform in self.throttled:
                headers = {} if self.retry_after is None else {'Retry-After': self.retry_after}
                raise HTTPError(url, 429, 'Too Many Requests', headers, io.BytesIO(b'{}'))
            if self.timeout_post(platform, payload):
                raise TimeoutError('synthetic undelivered POST timeout')
            if platform == 'teams' and self.slow:
                self.entered.set()
                if not self.block.wait(5):
                    self.remote_pending.append(deepcopy(payload))
                    raise TimeoutError('synthetic ambiguous POST timeout')
            self.messages[platform].append(deepcopy(payload))
            index = len(self.messages[platform]) - 1
            if platform == 'slack':
                message = slack_message(payload, index)
                return dict(ok=True, channel=payload['channel'], ts=message['ts'], message=message)
            return graph_message(payload, index)
        if self.block.is_set() and self.remote_pending:
            self.messages['teams'].extend(self.remote_pending)
            self.remote_pending.clear()
        if 'auth.test' in url:
            return dict(ok=True, team_id='workspace', user_id='sender')
        if 'conversations.info' in url:
            return dict(ok=True, channel=dict(id=parse_qs(urlsplit(url).query)['channel'][0], is_member=True))
        if 'conversations.history' in url or 'conversations.replies' in url:
            self.history_reads += 1
            if self.slow_readback:
                self.entered.set()
                if not self.block.wait(5):
                    raise TimeoutError('synthetic delayed readback timeout')
            messages = [slack_message(m, i) for i, m in enumerate(self.messages['slack'])]
            query = parse_qs(urlsplit(url).query)
            if 'conversations.replies' in url:
                thread = query['ts'][0]
                messages = [m for m in messages if m.get('thread_ts', m['ts']) == thread]
            else:
                messages = [m for m in reversed(messages) if 'thread_ts' not in m]
            offset = int(query.get('cursor', ['0'])[0])
            size = min(int(query.get('limit', ['100'])[0]), self.slack_page_size)
            page = messages[offset:offset + size]
            cursor = str(offset + size) if offset + size < len(messages) else ''
            return dict(ok=True, messages=self.message_filter('slack', page),
                        response_metadata=dict(next_cursor=cursor), has_more=bool(cursor))
        if '/me?' in url:
            return dict(id='sender')
        if '/organization?' in url:
            return dict(value=[dict(id='tenant')])
        if path.endswith('/members'):
            if self.membership_paged and not second:
                return dict(value=[dict(userId='other-%d' % i) for i in range(100)],
                            **{'@odata.nextLink': self.next_link('https://graph.microsoft.com' + path)})
            return dict(value=[dict(userId='sender')])
        if '/messages' in path:
            if self.messages_paged and not second:
                return dict(value=[], **{'@odata.nextLink': self.next_link('https://graph.microsoft.com' + path)})
            messages = [graph_message(m, i) for i, m in enumerate(self.messages['teams'])]
            return dict(value=self.message_filter('teams', messages))
        return dict(id=CHANNEL_ID if '/channels/' in path else TEAM_ID)
