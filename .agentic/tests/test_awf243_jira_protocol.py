"""Offline wire-contract regressions through the shipped adapter operations."""
import base64
import json
import os
import runpy
import subprocess
import sys
import tempfile
import traceback
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from test_reference_controller_adapter import (
    ROOT, NOW, FakeHttp, FakeClock, FakeRunner, _base_config, _dispatch_payload,
)
from agentic import ValidationError


class JiraProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = _base_config(self.root)
        self.namespace = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        self.clock = FakeClock('2026-10-09T08:00:37Z')
        self.env = patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret', ACCOUNT_EMAIL='actor@example.invalid')
        self.env.start()
        self.addCleanup(self.env.stop)

    def build(self, http=None):
        self.http = http or FakeHttp()
        return self.namespace['build_adapters']({**self.config, '_http_transport': self.http, '_clock': self.clock})

    def traverse(self, adapters):
        pages, cursor = [], None
        for _ in range(20):
            page = adapters['fetch_scope_page']('project = EX', cursor)
            pages.append(page)
            cursor = page['next_cursor']
            if cursor is None:
                self.assertTrue(page['complete'])
                return pages
        self.fail('Traversal did not terminate within its bound')

    def test_explicit_auth_modes_bind_destination_and_anonymous_tenant(self):
        for mode in ('oauth', 'scoped_api_token', 'unscoped_api_token'):
            with self.subTest(mode=mode):
                self.config['jira'].update(credential_type=mode)
                if mode != 'oauth':
                    self.config['jira']['email_env'] = 'ACCOUNT_EMAIL'
                else:
                    self.config['jira'].pop('email_env', None)
                http = FakeHttp(credential_type=mode)
                result = self.build(http)['observe_issue_identity']('EX-42')
                self.assertEqual('EX-42', result['issue_key'])
                self.assertTrue(all(call[0] == 'GET' for call in http.calls))
                self.assertNotIn('Authorization', http.calls[0][2])
                self.assertNotIn('fixture-secret', repr(result))

    def test_auth_combinations_fail_closed_without_http(self):
        for changes in ({'credential_type': None}, {'credential_type': 'guess'},
                        {'credential_type': 'scoped_api_token'},
                        {'credential_type': 'unscoped_api_token', 'email_env': 'AWF_JIRA_TOKEN'},
                        {'credential_type': 'oauth', 'email_env': 'ACCOUNT_EMAIL'}):
            with self.subTest(changes=changes):
                self.config['jira'].update(changes)
                http = FakeHttp()
                with self.assertRaises(ValidationError):
                    self.build(http)['observe_provider_identity']()
                self.assertEqual([], http.calls)
                self.config['jira'].pop('email_env', None)
        self.config['jira'].update(credential_type='scoped_api_token', email_env='ACCOUNT_EMAIL')
        for missing in ('ACCOUNT_EMAIL', 'AWF_JIRA_TOKEN'):
            with self.subTest(missing=missing), patch.dict(os.environ):
                os.environ.pop(missing)
                http = FakeHttp(credential_type='scoped_api_token')
                with self.assertRaises(ValidationError):
                    self.build(http)['observe_provider_identity']()
                self.assertEqual([], http.calls)

    def test_oauth_shared_resource_identity_selects_jira_by_scopes(self):
        for scope in ('read:jira-work', 'read:issue:jira', 'read:board-scope:jira-software',
                      'read:issue.property:jira', 'send:notification:jira'):
            for reverse in (False, True):
                with self.subTest(scope=scope, reverse=reverse):
                    resources = [
                        {'id': 'cloud-1', 'url': 'https://jira.example.invalid',
                         'scopes': ['read:confluence-content.all']},
                        {'id': 'cloud-1', 'url': 'https://jira.example.invalid',
                         'scopes': [scope]},
                        {'id': 'other-cloud', 'url': 'https://other.example.invalid',
                         'scopes': ['read:jira-work']},
                    ]
                    if reverse:
                        resources.reverse()
                    http = self.resource_http(resources)
                    result = self.build(http)['observe_issue_identity']('EX-42')
                    self.assertEqual('EX-42', result['issue_key'])
                    self.assertEqual([
                        'https://jira.example.invalid/_edge/tenant_info',
                        'https://api.atlassian.com/oauth/token/accessible-resources',
                        *['https://api.atlassian.com/ex/jira/cloud-1' + path for path in (
                            '/rest/api/3/myself', '/rest/api/3/project/EX',
                            '/rest/api/3/issue/EX-42?fields=key')],
                    ], [call[1] for call in http.calls])
                    self.assertTrue(all(call[0] == 'GET' for call in http.calls))

    @staticmethod
    def resource_http(resources):
        class Resources(FakeHttp):
            def respond(inner, method, url, *, body):
                if url.endswith('/oauth/token/accessible-resources'):
                    return 200, json.dumps(resources)
                return super().respond(method, url, body=body)
        return Resources()

    def test_oauth_missing_non_jira_or_conflicting_resources_refuse_before_myself(self):
        jira = {'id': 'cloud-1', 'url': 'https://jira.example.invalid',
                'scopes': ['read:jira-work']}
        invalid = [{}, [], [jira, dict(jira)],
                   [{**jira, 'id': 'foreign'}], [{**jira, 'url': 'https://foreign.invalid'}],
                   [jira, {**jira, 'url': 'https://foreign.invalid'}],
                   [jira, {**jira, 'id': 'foreign'}]]
        for scopes in (None, [], 'read:jira-work', [None], ['offline_access'],
                       ['read:confluence-content.all'], ['read:issue:jira-pretend'],
                       ['prefix:read:jira-work']):
            invalid.append([{**jira, 'scopes': scopes}])
        invalid.append([{key: value for key, value in jira.items() if key != 'scopes'}])
        for resources in invalid:
            with self.subTest(resources=resources):
                http = self.resource_http(resources)
                with self.assertRaisesRegex(ValidationError, 'OAuth resource identity mismatch'):
                    self.build(http)['observe_issue_identity']('EX-42')
                self.assertEqual(2, len(http.calls))
                self.assertTrue(all(call[0] == 'GET' for call in http.calls))
                self.assertFalse(any('/myself' in call[1] or '/issue/' in call[1]
                                     for call in http.calls))
        # A JSON null is rejected by the transport's object/list boundary.
        http = self.resource_http(None)
        with self.assertRaisesRegex(ValidationError, 'Jira request failed'):
            self.build(http)['observe_issue_identity']('EX-42')
        self.assertEqual(2, len(http.calls))

    def isolated_search(self, *, omit_tzdata=False):
        # Expose only distributions declared by the shipped lock. -I -S also
        # prevents ambient site packages or a system timezone DB hiding gaps.
        from agentic.adoption_config import _locked_dependencies
        from test_bootstrap_configuration import installed_wheel
        dependencies, _ = _locked_dependencies(ROOT / '.agentic/requirements.lock')
        with tempfile.TemporaryDirectory(prefix='awf-zone-runtime-') as temporary:
            runtime = Path(temporary)
            for name, requirement in dependencies.items():
                if omit_tzdata and name == 'tzdata':
                    continue
                version, wheel = installed_wheel(requirement['name'])
                self.assertEqual(requirement['version'], version)
                with zipfile.ZipFile(BytesIO(wheel)) as archive:
                    archive.extractall(runtime)
            script = '''
import importlib.util, json, socket, sys, zoneinfo
sys.path[:0] = sys.argv[1:]
zoneinfo.reset_tzpath(())
zoneinfo.ZoneInfo.clear_cache()
from test_awf243_jira_protocol import JiraProtocolTests
from agentic import ValidationError
from unittest.mock import patch
case = JiraProtocolTests()
try:
    case.setUp()
    with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
        adapters = case.build()
        try:
            pages = case.traverse(adapters)
            result = {'complete': pages[-1]['complete'],
                      'ids': [item['id'] for page in pages for item in page['items']]}
        except ValidationError as error:
            result = {'error': str(error)}
    result.update(tzpath=list(zoneinfo.TZPATH),
                  tzdata_present=importlib.util.find_spec('tzdata') is not None,
                  search_requests=sum('/search/jql?' in call[1] for call in case.http.calls),
                  search_urls=[call[1] for call in case.http.calls if '/search/jql?' in call[1]])
    print(json.dumps(result))
finally:
    case.doCleanups()
'''
            process = subprocess.run(
                [sys.executable, '-B', '-I', '-S', '-c', script, str(runtime),
                 str(ROOT / '.agentic/tests'), str(ROOT / '.agentic/lib')],
                capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(0, process.returncode, process.stderr)
            return json.loads(process.stdout)

    def test_declared_runtime_search_without_system_timezone_database(self):
        result = self.isolated_search()
        self.assertEqual([], result['tzpath'])
        self.assertNotIn('error', result)
        self.assertTrue(result['tzdata_present'])
        self.assertTrue(result['complete'])
        self.assertEqual(['100', '101'], result['ids'])
        self.assertEqual(4, result['search_requests'])
        for url in result['search_urls']:
            self.assertIn('updated < "2026-10-09 09:00"', parse_qs(urlsplit(url).query)['jql'][0])

    def test_isolated_runtime_really_missing_timezone_data_refuses_search(self):
        result = self.isolated_search(omit_tzdata=True)
        self.assertEqual([], result['tzpath'])
        self.assertFalse(result['tzdata_present'])
        self.assertEqual('Jira authenticated user timezone is missing or unsupported', result['error'])
        self.assertEqual(0, result['search_requests'])

    def test_site_must_be_a_credential_free_https_origin(self):
        for site in ('http://jira.example.invalid', 'https://actor@jira.example.invalid',
                     'https://jira.example.invalid/path', 'https://jira.example.invalid/',
                     'https://jira.example.invalid?x', 'https://jira.example.invalid#x',
                     'https://jira.example.invalid\\evil', 'https://jira.example.invalid:bad',
                     'https://jira.example.invalid?', 'https://jira.example.invalid#'):
            with self.subTest(site=site):
                self.config['jira']['site'] = site
                with self.assertRaises(ValidationError):
                    self.build()
                self.assertEqual([], self.http.calls)

    def test_two_complete_traversals_use_opaque_tokens_and_minute_cutoff(self):
        adapters = self.build()
        pages = self.traverse(adapters)
        self.assertEqual(['100', '101'], [item['id'] for page in pages for item in page['items']])
        calls = [call for call in self.http.calls if '/search/jql?' in call[1]]
        self.assertEqual(4, len(calls))
        for call in calls:
            query = parse_qs(urlsplit(call[1]).query)
            self.assertIn('updated < "2026-10-09 09:00"', query['jql'][0])
            self.assertEqual(['issuetype,status,updated'], query['fields'])
        self.assertEqual(['opaque+/= next'], parse_qs(urlsplit(calls[1][1]).query)['nextPageToken'])
        self.clock.value = '2026-10-09T08:02:59Z'
        fresh = self.traverse(adapters)
        self.assertNotEqual(pages[0]['snapshot_id'], fresh[0]['snapshot_id'])
        self.assertEqual(self.clock.value, fresh[0]['observed_at'])

    def test_drift_in_membership_status_or_updated_never_completes(self):
        for field in ('id', 'status', 'updated'):
            with self.subTest(field=field):
                class Drift(FakeHttp):
                    def request(inner, method, url, **kwargs):
                        code, body = super().request(method, url, **kwargs)
                        if '/search/jql?' in url and sum('/search/jql?' in call[1] for call in inner.calls) > 2:
                            value = json.loads(body)
                            issue = value['issues'][0]
                            if field == 'id':
                                issue['id'] += '9'
                            elif field == 'status':
                                issue['fields']['status']['id'] += '9'
                            else:
                                issue['fields']['updated'] = '2026-10-09T07:59:31Z'
                            body = json.dumps(value)
                        return code, body
                with self.assertRaisesRegex(ValidationError, 'changed|drift'):
                    self.traverse(self.build(Drift()))

    def test_invalid_enhanced_pages_fail_closed(self):
        mutations = {
            'missing-type': lambda v: v['issues'][0]['fields'].pop('issuetype'),
            'missing-status': lambda v: v['issues'][0]['fields']['status'].pop('id'),
            'missing-updated': lambda v: v['issues'][0]['fields'].pop('updated'),
            'missing-isLast': lambda v: v.pop('isLast'),
            'missing-token': lambda v: v.pop('nextPageToken', None),
            'contradictory': lambda v: v.update(isLast=True, nextPageToken='x'),
            'repeated-token': lambda v: v.update(isLast=False, nextPageToken='opaque+/= next'),
            'duplicate-id': lambda v: v['issues'][0].update(id='100'),
        }
        for problem, mutate in mutations.items():
            with self.subTest(problem=problem):
                class BadPage(FakeHttp):
                    def request(inner, method, url, **kwargs):
                        code, body = super().request(method, url, **kwargs)
                        if '/search/jql?' in url:
                            value = json.loads(body)
                            mutate(value)
                            body = json.dumps(value)
                        return code, body
                with self.assertRaises(ValidationError):
                    self.traverse(self.build(BadPage()))

    def test_missing_timezone_or_ambiguous_minute_refuses_before_search(self):
        for problem in ('missing', 'unsupported', 'ambiguous'):
            with self.subTest(problem=problem):
                class Zone(FakeHttp):
                    def request(inner, method, url, **kwargs):
                        code, body = super().request(method, url, **kwargs)
                        if url.endswith('/myself'):
                            value = json.loads(body)
                            if problem == 'missing':
                                value.pop('timeZone')
                            elif problem == 'unsupported':
                                value['timeZone'] = 'Unknown/Zone'
                            body = json.dumps(value)
                        return code, body
                self.clock.value = '2026-10-25T01:30:00Z'
                with self.assertRaisesRegex(ValidationError, 'timezone'):
                    self.traverse(self.build(Zone()))
                self.assertFalse(any('/search/' in call[1] for call in self.http.calls))

    def test_transition_preflight_must_match_available_destination(self):
        for transitions in ([], [{'id': '21', 'to': {'id': '10120'}}],
                            [{'id': '999', 'to': {'id': '10119'}}]):
            with self.subTest(transitions=transitions):
                class BadTransition(FakeHttp):
                    def request(inner, method, url, **kwargs):
                        code, body = super().request(method, url, **kwargs)
                        if method == 'GET' and url.endswith('/transitions'):
                            body = json.dumps({'transitions': transitions})
                        return code, body
                adapters = self.build(BadTransition())
                record = {'binding': {'issue_id': 'EX-42'}, 'from_status_id': '10118',
                          'to_status_id': '10119', 'transition_id': '21',
                          'operation_id': '10000000-0000-0000-0000-000000000001',
                          'jira_provider': adapters['observe_provider_identity']()}
                with self.assertRaisesRegex(ValidationError, 'transition'):
                    adapters['write_transition'](record)
                self.assertFalse(any(call[0] == 'POST' for call in self.http.calls))
                self.assertEqual([], list((self.root / 'runs').glob('jira-attempt-*')))

    def test_three_status_shared_mapping_and_explicit_review_opt_out(self):
        from test_awf243_review_regressions import RealJiraCliRegressions
        from agentic.canonical import sha256
        from agentic.lifecycle import definition
        from agentic.policy import policy_hash
        case = RealJiraCliRegressions('test_pre_post_timeout_is_unattempted_and_retryable')
        self.addCleanup(case.doCleanups)
        case.fixture()
        self.assertEqual(case.policy['jira']['status_map']['in_progress'], case.policy['jira']['status_map']['in_review'])
        self.assertEqual('SUCCEEDED', case.lifecycle()['record']['status'])
        shared = case.lifecycle('PR_READY')
        self.assertFalse(shared['planned'])
        self.assertFalse(shared.get('writes_stopped', False))
        self.assertEqual(1, len(case.posted))
        case.fixture()
        case.policy['jira']['lifecycle_writes']['in_review'] = False
        accepted = case.config['observations']['accepted_project_config']
        Path(accepted['path']).write_text(json.dumps(case.policy), encoding='utf-8')
        accepted['sha256'] = sha256(Path(accepted['path']).read_bytes())
        case.binding['policy_hash'] = policy_hash(case.policy, definition())
        disabled = case.lifecycle('PR_READY')
        self.assertFalse(disabled['planned'])
        self.assertEqual('jira.lifecycle_writes.in_review is false', disabled['reason'])
        self.assertEqual([], case.posted)
        self.assertEqual([], case.rows())

    def test_injected_transport_exception_chain_and_persisted_records_are_sanitized(self):
        secrets = ['fixture-secret', 'actor@example.invalid',
                   base64.b64encode(b'actor@example.invalid:fixture-secret').decode()]
        class Leaky(FakeHttp):
            def request(inner, *args, **kwargs):
                raise RuntimeError(' '.join(secrets))
        self.config['jira'].update(credential_type='scoped_api_token', email_env='ACCOUNT_EMAIL')
        with self.assertRaises(ValidationError) as error:
            self.build(Leaky())['observe_issue_identity']('EX-42')
        rendered = ''.join(traceback.format_exception(error.exception))
        retained = ''.join(p.read_text() for p in (self.root / 'runs').glob('*.json'))
        for secret in secrets:
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, retained)

    def test_named_email_is_stripped_from_child_entry_points(self):
        self.config['jira'].update(credential_type='scoped_api_token', email_env='ACCOUNT_EMAIL')
        runner = FakeRunner()
        adapters = self.namespace['build_adapters']({**self.config, '_runner': runner, '_clock': self.clock})
        adapters['observe_inventory']()
        adapters['dispatch_ticket'](_dispatch_payload('email-scrubbing'))
        self.assertTrue(runner.calls)
        self.assertTrue(runner.launches)
        for env in [call[2] for call in runner.calls] + [call[2] for call in runner.launches]:
            self.assertNotIn('ACCOUNT_EMAIL', env)
            self.assertNotIn('AWF_JIRA_TOKEN', env)

    def test_redirects_are_refused_without_a_destination_request(self):
        # Exercise urllib's real redirect handling through the public observer;
        # only its low-level HTTPS handler is replaced. No network is opened.
        import io
        import urllib.request
        from email.message import Message
        requested = []
        class Redirect(urllib.request.HTTPSHandler):
            def https_open(inner, request):
                requested.append(request.full_url)
                headers = Message()
                if request.full_url.endswith('/_edge/tenant_info'):
                    self.assertIsNone(request.get_header('Authorization'))
                    raw, code = json.dumps({'cloudId': 'cloud-1'}).encode(), 200
                elif request.full_url.endswith('/accessible-resources'):
                    raw, code = json.dumps([{'id': 'cloud-1', 'url': 'https://jira.example.invalid', 'scopes': ['read:jira-work']}]).encode(), 200
                else:
                    self.assertTrue(request.get_header('Authorization'))
                    headers['Location'] = 'https://foreign.invalid/?fixture-secret'
                    raw, code = b'', 302
                response = urllib.response.addinfourl(io.BytesIO(raw), headers, request.full_url, code)
                response.msg = 'Found' if code == 302 else 'OK'
                return response
        opener = urllib.request.build_opener(self.namespace['_RefuseRedirects'](), Redirect())
        globals_ = self.namespace['_HttpTransport'].request.__globals__
        for mode in ('oauth', 'scoped_api_token'):
            with self.subTest(mode=mode):
                self.config['jira']['credential_type'] = mode
                if mode != 'oauth':
                    self.config['jira']['email_env'] = 'ACCOUNT_EMAIL'
                requested.clear()
                with patch.dict(globals_, urlopen=opener.open):
                    observer = self.namespace['build_adapters']({**self.config, '_clock': self.clock})['observe_issue_identity']
                    with self.assertRaises(ValidationError) as error:
                        observer('EX-42')
                self.assertEqual(['https://jira.example.invalid/_edge/tenant_info',
                                  *(['https://api.atlassian.com/oauth/token/accessible-resources'] if mode == 'oauth' else []),
                                  'https://api.atlassian.com/ex/jira/cloud-1/rest/api/3/myself'], requested)
                rendered = ''.join(traceback.format_exception(error.exception))
                self.assertNotIn('fixture-secret', rendered)
                self.assertNotIn('actor@example.invalid', rendered)
                self.assertNotIn(base64.b64encode(b'actor@example.invalid:fixture-secret').decode(), rendered)

    def test_time_and_request_limits_cover_both_traversals(self):
        class Slow(FakeHttp):
            def request(inner, method, url, **kwargs):
                result = super().request(method, url, **kwargs)
                if '/search/jql?' in url:
                    self.clock.ticks += 61
                return result
        with self.assertRaisesRegex(ValidationError, 'time bound'):
            self.traverse(self.build(Slow()))
        self.clock = FakeClock()
        class ManyPages(FakeHttp):
            def request(inner, method, url, **kwargs):
                if '/search/jql?' not in url:
                    return super().request(method, url, **kwargs)
                inner.calls.append((method, url, kwargs['headers'], kwargs['body']))
                count = sum('/search/jql?' in call[1] for call in inner.calls)
                return 200, json.dumps({'issues': [], 'isLast': False, 'nextPageToken': str(count)})
        # A fixed clock isolates the request bound from the independent time cap.
        self.clock.monotonic = lambda: 0
        adapters = self.build(ManyPages())
        cursor = None
        with self.assertRaisesRegex(ValidationError, 'request bound'):
            for _ in range(21):
                cursor = adapters['fetch_scope_page']('project = EX', cursor)['next_cursor']
        self.assertEqual(20, sum('/search/jql?' in call[1] for call in self.http.calls))


if __name__ == '__main__':
    unittest.main()
