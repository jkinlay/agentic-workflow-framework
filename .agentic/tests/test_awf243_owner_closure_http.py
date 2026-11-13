"""Owner-held closeout through the CLI and a stateful loopback Jira server."""
import copy
import json
import threading
import unittest
from source_only import COMMITTED_GIT_REASON, committed_git_tuple
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import test_awf243_operator_recovery as operator_fixture
import test_awf243_review_regressions as regressions
from test_jira_mutation_scope import KEY


class _JiraServer:
    """Synthetic observed wire shapes; GET never changes issue state."""
    def __init__(self):
        self.done = False
        self.problem = None
        self.calls = []
        self.errors = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                outer.calls.append(('GET', self.path))
                try:
                    value = outer.respond(self.path, self.headers)
                    raw = json.dumps(value).encode('utf-8')
                    self.send_response(200)
                except Exception as exc:
                    outer.errors.append(exc)
                    raw = b'{}'
                    self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                outer.calls.append(('POST', self.path))
                self.send_response(405)
                self.send_header('Content-Length', '0')
                self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, url, *, headers, body, timeout, max_bytes):
        # Replace only transport: all requests go to this literal loopback
        # address, with the original provider path and synthetic credentials.
        parsed = urlsplit(url)
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=timeout)
        try:
            connection.request(method, parsed.path + ('?' + parsed.query if parsed.query else ''),
                               body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise AssertionError('Fake Jira response exceeds the adapter bound')
            return response.status, raw.decode('utf-8')
        finally:
            connection.close()

    def issue(self):
        status = '10120' if self.done else '10119'
        histories = [dict(id='16776', created='2026-10-09T08:59:58.721+0100',
            author={'accountId': 'fixture-owner'}, items=[{'field': 'IssueParentAssociation',
                'fieldtype': 'jira', 'from': None, 'to': '12055'}])]
        if self.done:
            histories.insert(0, dict(id='16952', created='2026-10-09T08:00:01.195+0000',
                author={'accountId': 'fixture-owner'}, items=[
                    {'field': 'resolution', 'fieldId': 'resolution', 'from': None, 'to': '10000'},
                    {'field': 'status', 'fieldId': 'status', 'from': '10119', 'to': '10120'}]))
            histories.insert(0, dict(id='17284', created='2026-10-09T08:00:02.195+0000',
                author={'accountId': 'fixture-owner'}, items=[
                    {'field': 'labels', 'fieldId': 'labels', 'from': None, 'to': None}]))
        value = {'id': '100', 'key': KEY, 'fields': {
            'status': {'id': status, 'name': 'Done' if self.done else 'In Progress',
                       'statusCategory': {'key': 'done' if self.done else 'indeterminate'}},
            'updated': '2026-10-09T08:00:02.195+0000' if self.done else '2026-10-09T07:59:59.195+0000'},
            'changelog': {'startAt': 0, 'maxResults': len(histories),
                          'total': len(histories), 'histories': histories}}
        if self.done and self.problem == 'truncated':
            value['changelog']['total'] += 1
        elif self.done and self.problem == 'controller':
            histories[1]['author']['accountId'] = 'controller'
        elif self.done and self.problem == 'missing-actor':
            histories[1].pop('author')
        return value

    def respond(self, target, headers):
        parsed = urlsplit(target)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == '/_edge/tenant_info':
            assert headers.get('Authorization') is None
            return {'cloudId': 'cloud-1'}
        assert headers.get('Authorization') == 'Bearer fixture-token'
        if path == '/oauth/token/accessible-resources':
            return [{'id': 'cloud-1', 'url': 'https://jira.example.invalid', 'scopes': ['read:jira-work']}]
        prefix = '/ex/jira/cloud-1/rest/api/3/'
        assert path.startswith(prefix), target
        route = path[len(prefix):]
        if route == 'myself':
            return {'accountId': 'controller', 'timeZone': 'Europe/London'}
        if route == 'project/EX':
            return {'id': 'project-1', 'key': 'EX'}
        if route == 'issue/' + KEY:
            if query.get('fields') == ['key']:
                return {'id': '100', 'key': KEY}
            if query.get('fields') == ['project,parent,labels,components,issuetype,summary']:
                return {'key': KEY, 'fields': {'project': {'id': 'project-1', 'key': 'EX'},
                    'labels': ['awf-fixture', 'release-1.9.6'], 'components': [],
                    'issuetype': {'name': 'Task'}, 'summary': 'Release acceptance'}}
            value = self.issue()
            if query.get('expand') != ['changelog']:
                value.pop('changelog')
            return value
        if route == 'search/jql':
            assert query['fields'] == ['issuetype,status,updated']
            assert 'updated < "2026-10-09 09:02"' in query['jql'][0]
            token = query.get('nextPageToken', [None])[0]
            assert token in (None, 'opaque+/= next')
            fields = copy.deepcopy(self.issue()['fields'])
            fields['issuetype'] = {'name': 'Task'}
            if token:
                fields['status'] = {'id': '10118', 'name': 'To Do', 'statusCategory': {'key': 'new'}}
            return {'issues': [{'id': '101' if token else '100', 'fields': fields}],
                    'isLast': token is not None, **({} if token else {'nextPageToken': 'opaque+/= next'})}
        raise AssertionError('Unexpected fake Jira route: ' + target)


class OwnerClosureHttpTests(unittest.TestCase):
    def fixture(self):
        case = operator_fixture.OperatorRecoveryTests()
        self.addCleanup(case.doCleanups)
        case.fixture()
        server = _JiraServer()
        self.addCleanup(server.close)
        case.http.request = server.request
        return case, server

    def assert_read_only(self, server):
        self.assertEqual([], server.errors)
        self.assertTrue(server.calls)
        self.assertTrue(all(method == 'GET' for method, _ in server.calls), server.calls)

    def close_by_owner(self, case, server):
        held = case.lifecycle('JIRA_RECONCILED')
        self.assertFalse(held['planned'])
        self.assertIn('owner closes in Jira', held['reason'])
        server.done = True  # An external owner action, never a GET side effect.
        case.clock.value = '2026-10-09T08:00:03Z'
        result = case.lifecycle('JIRA_RECONCILED')
        self.assertFalse(result['planned'])
        self.assertEqual('fixture-owner', result['owner_closure']['actor'])
        self.assertEqual('16952', result['owner_closure']['history_id'])
        self.assertEqual('2026-10-09T08:00:01.195Z', result['owner_closure']['transition_at'])
        case.record = result['record']
        case.prepare_closeout()
        case.clock.value = '2026-10-09T08:02:03Z'

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    @unittest.skipIf(committed_git_tuple(regressions.scope_fixture.ROOT) is None, COMMITTED_GIT_REASON)
    def test_owner_done_counts_after_history_recheck_and_two_search_traversals(self):
        case, server = self.fixture()
        self.close_by_owner(case, server)
        history_reads = sum('expand=changelog' in path for _, path in server.calls)
        merged = case.merge()
        self.assertEqual('COUNTED', merged['jira_progress']['jira_state'], merged)
        self.assertEqual(1, merged['jira_progress']['closed'])
        self.assertEqual(1, merged['jira_progress']['remaining_open'])
        self.assertGreater(sum('expand=changelog' in path for _, path in server.calls), history_reads)
        searches = [path for _, path in server.calls if '/search/jql?' in path]
        self.assertEqual(4, len(searches))
        self.assertEqual([False, True, False, True], ['nextPageToken=' in path for path in searches])
        self.assert_read_only(server)

    def test_incomplete_or_controller_history_never_releases_owner_hold(self):
        for problem in ('truncated', 'controller', 'missing-actor'):
            with self.subTest(problem=problem):
                case, server = self.fixture()
                server.done, server.problem = True, problem
                case.clock.value = '2026-10-09T08:00:03Z'
                result = case.lifecycle('JIRA_RECONCILED')
                self.assertFalse(result['planned'])
                self.assertIn('owner closes in Jira', result['reason'])
                self.assertNotIn('owner_closure', result)
                self.assert_read_only(server)

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    @unittest.skipIf(committed_git_tuple(regressions.scope_fixture.ROOT) is None, COMMITTED_GIT_REASON)
    def test_count_refuses_when_owner_history_changes_after_reconciliation(self):
        case, server = self.fixture()
        self.close_by_owner(case, server)
        server.problem = 'controller'
        merged = case.merge()
        self.assertEqual('UNOBSERVED', merged['jira_progress']['jira_state'], merged)
        self.assertEqual('UNOBSERVED', merged['jira_progress']['closed'])
        self.assertEqual('UNOBSERVED', merged['jira_progress']['remaining_open'])
        self.assert_read_only(server)


if __name__ == '__main__':
    unittest.main()
