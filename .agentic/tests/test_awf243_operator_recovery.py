"""Operator exits through the real CLI and adapter; only HTTP/host inputs are fake."""
import copy
import contextlib
import io
import json
import os
import runpy
import unittest
import uuid
from unittest.mock import patch

import test_jira_mutation_scope as scope_fixture
import test_awf243_review_regressions as regressions

from agentic import ValidationError
from agentic.canonical import fingerprint, sha256
from agentic.continuous_controller import ContinuousControllerStore
from test_jira_mutation_scope import ROOT, KEY
from test_reference_controller_adapter import FakeRunner


TRANSPORTS = {}


class OperatorRecoveryTests(unittest.TestCase):
    adapter_fixture = regressions.RealJiraCliRegressions.adapter_fixture
    history = staticmethod(regressions.RealJiraCliRegressions.history)
    dump = regressions.PreNineCliRegressions.dump
    lifecycle = regressions.PreNineCliRegressions.lifecycle
    adopt = regressions.PreNineCliRegressions.adopt
    rows = regressions.PreNineCliRegressions.rows
    prepare_closeout = regressions.PreNineCliRegressions.prepare_closeout
    def merge(self):
        prior = self.now
        try:
            self.now = self.clock.value
            return regressions.PreNineCliRegressions.merge(self)
        finally:
            self.now = prior

    def fixture(self):
        regressions.RealJiraCliRegressions.fixture(self)
        key = str(uuid.uuid4())
        TRANSPORTS[key] = dict(_http_transport=self.http, _clock=self.clock, _runner=FakeRunner())
        self.addCleanup(TRANSPORTS.pop, key)
        self.shim = self.root / 'transport_adapter.py'
        self.shim.write_text('import runpy\nfrom test_awf243_operator_recovery import TRANSPORTS\n'
            'def build_adapters(config):\n'
            '    config.update(TRANSPORTS[' + repr(key) + '])\n'
            '    return runpy.run_path(' + repr(str(self.adapter)) + ')["build_adapters"](config)\n', encoding='utf-8')
        original = self.http.request
        self.labels, self.owner, self.bad_history, self.reject_done = ['awf-fixture'], None, None, False
        def request(method, url, **kwargs):
            if method == 'POST' and self.reject_done:
                self.posted.append(url)
                return 400, json.dumps({'errorMessages': ['Transition rejected'], 'errors': {}})
            code, body = original(method, url, **kwargs)
            if method == 'GET' and '/issue/' in url:
                value = json.loads(body)
                if 'fields' in value:
                    if 'labels' in value['fields']:
                        value['fields']['labels'] = self.labels
                    if self.owner and 'status' in value['fields']:
                        value['fields']['status'] = {'id': '10120'}
                        value['fields']['updated'] = '2026-10-09T08:00:02.195+0000'
                    if self.owner and 'expand=changelog' in url and 'changelog' in value:
                        page = value['changelog']
                        page['histories'].insert(0, self.history('100', '2026-10-09T08:00:01.195+0000',
                            self.owner, '10119', '10120'))
                        page['total'] += 1
                        page['maxResults'] += 1
                        if self.bad_history == 'truncated':
                            page['total'] += 1
                        elif self.bad_history == 'actor':
                            del page['histories'][0]['author']
                        elif self.bad_history == 'later':
                            page['histories'].insert(0, self.history('101', '2026-10-09T08:00:02.195+0000',
                                'controller', '10119', '10120'))
                            page['total'] += 1
                        elif self.bad_history == 'future':
                            page['histories'][0]['created'] = '2026-10-10T08:00:00.195+0000'
                        elif self.bad_history == 'missing':
                            del value['changelog']
                body = json.dumps(value)
            return code, body
        self.http.request = request
        return self

    def run_cli(self, arguments):
        arguments = list(arguments)
        if arguments[0] == 'jira-lifecycle':
            event = arguments[arguments.index('--event') + 1]
            arguments[arguments.index('--transition-id') + 1] = '31' if event == 'JIRA_RECONCILED' else '21'
        # No production seam is replaced: the reviewed adapter module injects only transports.
        config = self.dump('project.json', self.policy)
        adapter_config = self.dump('adapter.json', self.config)
        output = io.StringIO()
        with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'fixture-token'}), contextlib.redirect_stdout(output):
            result = self.cli.main(['--state', str(self.state), '--stream', getattr(self, 'stream', 'A'), '--worktree-root', str(ROOT),
                '--project-config', str(config), '--adapter-module', str(self.shim),
                '--adapter-sha256', sha256(self.shim.read_bytes()), '--adapter-config', str(adapter_config), *arguments])
        self.assertEqual(0, result)
        return json.loads(output.getvalue())

    def resolve(self, row):
        return self.run_cli(['jira-operation-resolve', '--issue', row['issue_id'], '--operation-id', row['operation_id'],
            '--expected-intent-sha256', row['intent_sha256'], '--expected-project-id', self.policy['project']['id'],
            '--expected-repository-id', str(self.policy['github']['repository_id']), '--operator', 'fixture-owner',
            '--now', self.now, '--disposition', 'ABANDON_NO_REPLAY', '--reason', 'Retire without replay',
            '--provenance', 'urn:awf:fixture:investigation'])

    def edit_config(self):
        self.policy['jira']['owner_closure_keywords'].append('acceptance')
        from agentic.lifecycle import definition
        from agentic.policy import policy_hash
        self.binding['policy_hash'] = policy_hash(self.policy, definition())
        path = self.dump('accepted.json', self.policy)
        self.config['observations']['accepted_project_config'].update(path=str(path), sha256=sha256(path.read_bytes()))

    def test_adopt_mixed_pending_and_held_has_named_no_replay_exits(self):
        self.fixture()
        self.assertEqual('SUCCEEDED', self.lifecycle()['record']['status'])
        store = ContinuousControllerStore(self.state, ['A'])
        healthy = self.rows()[0]
        with store.transaction() as db:
            db.execute("DELETE FROM controller_meta WHERE key LIKE 'jira-identity:%'")
            for index, status in enumerate(('PENDING', 'HELD'), 2):
                row = {key: value for key, value in healthy.items() if key != 'record'}
                record = json.loads(row['record_json'])
                row.update(operation_id=str(uuid.UUID(int=index)), issue_id='EX-' + str(index), status=status,
                    operation_json=None, readback_json=None)
                record['operation_id'], record['binding']['issue_id'] = row['operation_id'], row['issue_id']
                row['record_json'] = json.dumps(record)
                row['intent_sha256'] = fingerprint('controller-jira-operation',
                    {key: value for key, value in record.items() if key != 'created_at'})
                db.execute('INSERT INTO controller_jira_operation VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
        result = self.adopt()
        self.assertEqual([healthy['operation_id']], result['adopted_operations'])
        self.assertEqual(['EX-2', 'EX-3'], [row['issue_id'] for row in result['skipped_operations']])
        for skipped in result['skipped_operations']:
            self.assertIn('--expected-intent-sha256 ' + skipped['intent_sha256'], skipped['next_command'])
            self.assertIn('ABANDON_NO_REPLAY', skipped['next_command'])
            raw = next(row for row in store.jira_operations(skipped['issue_id']))
            self.assertEqual('ABANDON_NO_REPLAY', self.resolve(raw)['resolution']['disposition'])
        self.assertEqual(1, len(self.posted))
        self.assertFalse(self.lifecycle()['planned'])

    def test_resolution_survives_reviewed_policy_edit_and_real_identity_still_refuses(self):
        self.fixture()
        self.fail_readback = True
        self.assertEqual('UNKNOWN', self.lifecycle()['record']['status'])
        row = self.rows()[0]
        self.resolve(row)
        self.edit_config()
        self.assertEqual('ABANDON_NO_REPLAY', self.resolve(row)['resolution']['disposition'])
        self.assertFalse(self.lifecycle('BLOCK')['writes_stopped'])
        self.assertFalse(self.lifecycle()['planned'])
        self.policy['github']['repository_id'] += 1
        with self.assertRaisesRegex(ValidationError, 'project/repository'):
            self.lifecycle('BLOCK')
        self.assertEqual(1, len(self.posted))

    @regressions.skip_unless_source_repo()
    def test_genuine_f71_pending_identity_recheck_does_not_rollback_healthy_adoption(self):
        self.fixture()
        # Execute the historical producer to create its real pre-pin database.
        legacy = regressions.PreNineCliRegressions.historical(self, 'f71ee70f32737bfb02b9b09b1e2e420529e0cc63',
            '.agentic/lib/agentic/continuous_controller.py', module=True)
        store = legacy.ContinuousControllerStore(self.state, ['A'])
        original = self.http.request
        import sqlite3
        def request(method, url, **kwargs):
            incoming = url
            code, body = original(method, url.replace('/issue/EX-2?', '/issue/' + KEY + '?'), **kwargs)
            value = json.loads(body) if body else None
            if isinstance(value, dict) and value.get('key') == KEY and '/issue/EX-2?' in incoming:
                value['key'] = 'EX-2'
            if url.endswith('/myself'):
                with contextlib.closing(sqlite3.connect(self.state)) as db:
                    pending = db.execute("SELECT 1 FROM controller_jira_operation WHERE issue_id='EX-2'").fetchone()
                if pending:
                    value['accountId'] = 'other'
            return code, json.dumps(value) if value is not None else body
        self.http.request = request
        with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'fixture-token'}):
            calls = runpy.run_path(str(self.adapter))['build_adapters']({**self.config,
                '_http_transport': self.http, '_clock': self.clock, '_runner': FakeRunner()})
            def produce(issue):
                return legacy.production_jira_lifecycle(store, config=self.policy, contract={}, event='WORKER_STARTED',
                    facts={'run_registered': True, 'worktree_verified': True}, binding={**self.binding, 'issue_id': issue},
                    issue_type='LEAF', state='DISPATCHED', producer_id='controller', run_id=str(uuid.UUID(int=77)),
                    now=self.now, evidence=['urn:awf:fixture:historical'], transition_id='21',
                    **{key: calls[key] for key in ('observe_provider_identity', 'observe_issue_identity',
                        'observe_issue_policy', 'read_current_status', 'write_transition', 'read_transition')})
            self.assertEqual('SUCCEEDED', produce(KEY)['record']['status'])
            self.posted.clear()
            self.assertFalse(produce('EX-2')['planned'])
        store = ContinuousControllerStore(self.state, ['A'])
        healthy, pending = self.rows()[0], store.jira_operations('EX-2')[0]
        self.assertEqual('PENDING', pending['status'])
        result = self.adopt()
        self.assertEqual([healthy['operation_id']], result['adopted_operations'])
        self.assertEqual(['EX-2'], [row['issue_id'] for row in result['skipped_operations']])
        self.resolve(pending)
        self.assertEqual('HELD', store.jira_operations('EX-2')[0]['status'])
        self.assertEqual([], self.posted)

    def test_corrupt_adoption_names_ticket_and_operation(self):
        self.fixture()
        self.assertEqual('SUCCEEDED', self.lifecycle()['record']['status'])
        row = self.rows()[0]
        with ContinuousControllerStore(self.state, ['A']).transaction() as db:
            db.execute("DELETE FROM controller_meta WHERE key LIKE 'jira-identity:%'")
            db.execute("UPDATE controller_jira_operation SET readback_json='{}'")
        with self.assertRaisesRegex(ValidationError, KEY + ' operation ' + row['operation_id']):
            self.adopt()
        self.assertEqual(1, len(self.posted))

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    def test_adopted_done_counts_after_policy_edit(self):
        self.fixture()
        self.before_status, self.after_status = '10119', '10120'
        self.histories[0]['items'][0]['to'] = '10119'
        self.record = self.lifecycle('JIRA_RECONCILED')['record']
        store = ContinuousControllerStore(self.state, ['A'])
        with store.transaction() as db:
            db.execute("DELETE FROM controller_meta WHERE key LIKE 'jira-identity:%'")
        self.adopt()
        self.edit_config()
        # Closeout is issued under the newly accepted policy; historical write bytes stay intact.
        self.dump_transition()
        self.prepare_closeout()
        merged = self.merge()
        self.assertEqual('COUNTED', merged['jira_progress']['jira_state'], merged)
        self.assertEqual(1, len(self.posted))

    def dump_transition(self):
        from pathlib import Path
        Path(self.config['observations']['jira_transition_directory'], self.record['record_id'] + '.json').write_text(
            json.dumps({'record': self.record}), encoding='utf-8')

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    def test_owner_hold_observes_external_done_and_counts_without_post(self):
        self.fixture()
        self.labels = ['awf-fixture', 'release-1.9.6']
        self.assertIn('owner closes in Jira', self.lifecycle('JIRA_RECONCILED')['reason'])
        self.owner = 'fixture-owner'
        self.clock.value = '2026-10-09T08:00:03Z'
        result = self.lifecycle('JIRA_RECONCILED')
        self.assertFalse(result['planned'])
        self.assertEqual('fixture-owner', result['owner_closure']['actor'])
        self.assertEqual('2026-10-09T08:00:01.195Z', result['owner_closure']['transition_at'])
        self.record = result['record']
        self.prepare_closeout()
        merged = self.merge()
        self.assertEqual('COUNTED', merged['jira_progress']['jira_state'], merged)
        self.assertEqual([], self.posted)

    def test_owner_hold_never_trusts_status_or_controller_or_incomplete_history(self):
        for problem in ('actor', 'truncated', 'later', 'future', 'controller', 'missing'):
            with self.subTest(problem=problem):
                self.fixture()
                self.labels = ['awf-fixture', 'release-1.9.6']
                self.owner = 'controller' if problem == 'controller' else 'fixture-owner'
                self.bad_history = problem
                self.clock.value = '2026-10-09T08:00:03Z'
                result = self.lifecycle('JIRA_RECONCILED')
                self.assertFalse(result['planned'])
                self.assertIn('owner closes in Jira', result['reason'])
                if problem == 'controller':
                    self.assertIn('actor equals the controller actor', result['reason'])
                    self.assertIn("sharing the owner's Jira account", result['reason'])
                    self.assertIn("can't tell the two apart", result['reason'])
                self.assertNotIn('owner_closure', result)
                self.assertEqual([], self.posted)

    def test_missing_envelope_names_operation_and_no_replay_resolution(self):
        self.fixture()
        self.fail_readback = True
        self.assertEqual('UNKNOWN', self.lifecycle()['record']['status'])
        row = self.rows()[0]
        with ContinuousControllerStore(self.state, ['A']).transaction() as db:
            db.execute("DELETE FROM controller_meta WHERE key LIKE 'jira-identity:%'")
        self.assertEqual([KEY], [item['issue_id'] for item in self.adopt()['skipped_operations']])
        with self.assertRaises(ValidationError) as caught:
            self.lifecycle()
        message = str(caught.exception)
        self.assertIn(KEY + ' operation ' + row['operation_id'], message)
        self.assertIn('--expected-intent-sha256 ' + row['intent_sha256'], message)
        self.assertIn('ABANDON_NO_REPLAY', message)
        self.assertEqual(1, len(self.posted))

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    def test_count_rechecks_owner_transition_without_post(self):
        self.fixture()
        self.labels = ['awf-fixture', 'release-1.9.6']
        self.owner = 'fixture-owner'
        self.clock.value = '2026-10-09T08:00:03Z'
        self.record = self.lifecycle('JIRA_RECONCILED')['record']
        self.prepare_closeout()
        self.owner = 'controller'
        self.assertEqual('UNOBSERVED', self.merge()['jira_progress']['jira_state'])
        self.assertEqual([], self.posted)

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    def test_rejected_done_abandon_then_owner_closure_counts_without_replay(self):
        self.fixture()
        self.before_status, self.after_status = '10119', '10120'
        self.histories[0]['items'][0]['to'] = '10119'
        self.reject_done = True
        self.assertEqual('UNKNOWN', self.lifecycle('JIRA_RECONCILED')['record']['status'])
        self.resolve(self.rows()[0])
        self.owner, self.reject_done = 'fixture-owner', False
        self.clock.value = '2026-10-09T08:00:03Z'
        self.record = self.lifecycle('JIRA_RECONCILED')['record']
        self.prepare_closeout()
        merged = self.merge()
        self.assertEqual('COUNTED', merged['jira_progress']['jira_state'], merged)
        self.assertEqual('HELD', self.rows()[0]['status'])
        self.assertEqual(1, len(self.posted))


class OwnerClosureIdentityTests(unittest.TestCase):
    """R1: owner-only history must bind every later write and count admission."""
    identities = ('project', 'repository', 'cloud', 'site', 'provider_project', 'controller_actor')

    def held(self):
        case = OperatorRecoveryTests().fixture()
        self.addCleanup(case.doCleanups)
        case.labels = ['awf-fixture', 'release-1.9.6']
        case.owner = 'fixture-owner'
        case.clock.value = '2026-10-09T08:00:03Z'
        case.record = case.lifecycle('JIRA_RECONCILED')['record']
        self.assertEqual([], case.rows())
        self.assertEqual([], case.posted)
        return case

    def drift(self, case, kind):
        original_site = case.config['jira']['site']
        original_gateway = 'https://api.atlassian.com/ex/jira/' + case.config['jira']['cloud_id']
        if kind == 'project':
            case.policy['project']['id'] = '10000000-0000-0000-0000-000000000099'
            case.binding['project_id'] = case.config['github']['project_id'] = case.policy['project']['id']
        elif kind == 'repository':
            case.policy['github']['repository_id'] += 1
            case.binding['repository_id'] = case.config['github']['repository_id'] = case.policy['github']['repository_id']
        else:
            field, value = {'cloud': ('cloud_id', 'cloud-two'),
                'site': ('site', 'https://second.example.invalid'),
                'provider_project': ('provider_project_id', 'project-two'),
                'controller_actor': ('controller_actor_id', 'controller-two')}[kind]
            case.policy['jira'][field] = case.config['jira'][field] = value
        # Change only HTTP inputs, accepted config and CLI arguments, never production seams.
        original = case.http.request
        def request(method, url, **kwargs):
            # The shared wire fixture validates its original destinations. Route
            # the changed tenant through that fixture, then return the changed
            # observed identity, including OAuth's separate resource binding.
            site = case.policy['jira']['site']
            gateway = 'https://api.atlassian.com/ex/jira/' + case.policy['jira']['cloud_id']
            if url.startswith(site + '/'):
                fixture_url = original_site + url[len(site):]
            elif url.startswith(gateway + '/'):
                fixture_url = original_gateway + url[len(gateway):]
            else:
                self.assertEqual('https://api.atlassian.com/oauth/token/accessible-resources', url)
                fixture_url = url
            code, body = original(method, fixture_url, **kwargs)
            if method == 'GET':
                value = json.loads(body)
                if url.endswith('/oauth/token/accessible-resources'):
                    value = [{'id': case.policy['jira']['cloud_id'], 'url': site, 'scopes': ['read:jira-work']}]
                elif url.endswith('/myself'):
                    value['accountId'] = case.policy['jira']['controller_actor_id']
                elif url.endswith('/_edge/tenant_info'):
                    value = {'cloudId': case.policy['jira']['cloud_id']}
                elif '/project/EX' in url:
                    value['id'] = case.policy['jira']['provider_project_id']
                elif '/issue/' in url and 'project' in value.get('fields', {}):
                    value['fields']['project']['id'] = case.policy['jira']['provider_project_id']
                body = json.dumps(value)
            return code, body
        case.http.request = request
        original_cli = case.run_cli
        def run_cli(arguments):
            arguments = list(arguments)
            if '--producer-id' in arguments:
                arguments[arguments.index('--producer-id') + 1] = case.policy['jira']['controller_actor_id']
            if arguments[0] == 'merge-observed':
                from pathlib import Path
                path = Path(arguments[arguments.index('--jira-progress') + 1])
                value = json.loads(path.read_text())
                value['jira_binding'].update(cloud_id=case.policy['jira']['cloud_id'],
                    project_id=case.policy['jira']['provider_project_id'],
                    actor_id=case.policy['jira']['controller_actor_id'])
                path.write_text(json.dumps(value), encoding='utf-8')
            return original_cli(arguments)
        case.run_cli = run_cli
        case.edit_config()
        # Auth/tenant failures must not hide identity drift from the history
        # guard: prove the real adapter observes every changed provider field.
        with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'fixture-token'}):
            calls = runpy.run_path(str(case.adapter))['build_adapters']({**case.config,
                '_http_transport': case.http, '_clock': case.clock, '_runner': FakeRunner()})
            self.assertEqual({
                'cloud_id': case.policy['jira']['cloud_id'], 'site': case.policy['jira']['site'],
                'project_id': case.policy['jira']['provider_project_id'],
                'project_key': case.policy['jira']['project_key'],
                'controller_actor_id': case.policy['jira']['controller_actor_id'],
            }, calls['observe_provider_identity']())
        self.assertEqual([], case.posted)

    def refused_lifecycle(self, case, event):
        with self.assertRaisesRegex(ValidationError,
                'Owner closure history belongs to a different project/repository/issue binding or provider/controller actor'):
            case.lifecycle(event)
        self.assertEqual([], case.posted)

    def test_r1_owner_only_history_refuses_another_closure_after_identity_drift(self):
        for kind in self.identities:
            with self.subTest(identity=kind):
                case = self.held()
                self.drift(case, kind)
                self.refused_lifecycle(case, 'JIRA_RECONCILED')

    def test_r1_owner_only_history_refuses_reopened_worker_write_after_identity_drift(self):
        for kind in self.identities:
            with self.subTest(identity=kind):
                case = self.held()
                self.drift(case, kind)
                case.owner = None
                # Reopened to Ready with an observed external status transition.
                case.histories.append(case.history('101', '2026-10-09T08:00:02.195+0000',
                    'fixture-owner', '10120', '10118'))
                self.refused_lifecycle(case, 'WORKER_STARTED')

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    def test_r1_owner_only_history_refuses_count_after_identity_drift(self):
        for kind in self.identities:
            with self.subTest(identity=kind):
                case = self.held()
                self.drift(case, kind)
                # Reproduce the second accepted closure, then try a fresh closeout.
                # A fixed controller refuses observation and retains the first record.
                try:
                    result = case.lifecycle('JIRA_RECONCILED')
                except ValidationError as exc:
                    self.assertIn('Owner closure history belongs to a different', str(exc))
                else:
                    case.record = result['record']
                case.prepare_closeout()
                result = case.merge()['jira_progress']
                self.assertEqual('UNOBSERVED', result['jira_state'], result)
                self.assertIn('Owner closure history belongs to a different project/repository/issue binding or provider/controller actor', result['reason'])
                self.assertEqual([], case.posted)

    @regressions.skip_unless_source_repo(
        "Git closeout fixture requires the AWF source repository's committed HEAD and LICENSE", "LICENSE")
    def test_r1_reviewed_policy_edit_and_repeat_owner_observations_remain_idempotent(self):
        case = self.held()
        case.edit_config()
        for _ in range(3):
            case.record = case.lifecycle('JIRA_RECONCILED')['record']
        case.prepare_closeout()
        for _ in range(3):
            self.assertEqual('COUNTED', case.merge()['jira_progress']['jira_state'])
        self.assertEqual([], case.rows())
        self.assertEqual([], case.posted)


class BatchOperatorDiagnosticsTests(unittest.TestCase):
    def fixture(self):
        case = regressions.BatchRecoveryRegressions()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_abandoned_batch_names_the_ticket_and_operation_without_any_callback(self):
        from agentic.continuous_controller import resolve_jira_operation
        case = self.fixture()
        def lost(record):
            case.spy.write_transition(record)
            raise TimeoutError('response lost after POST')
        self.assertFalse(case.batch(write_transition=lost)['complete'])
        row, config = case.row(), case.args['config']
        resolve_jira_operation(case.fixture.store, config, issue=row['issue_id'], operation_id=row['operation_id'],
            expected_intent_sha256=row['intent_sha256'], expected_project_id=config['project']['id'],
            expected_repository_id=config['github']['repository_id'], operator='fixture-owner', now=case.args['now'],
            disposition='ABANDON_NO_REPLAY', reason='Retire without replay', provenance='urn:awf:fixture:investigation')
        calls = list(case.spy.calls)
        with self.assertRaisesRegex(ValidationError, row['issue_id'] + ' operation ' + row['operation_id']):
            case.batch()
        self.assertEqual(calls, case.spy.calls)

    def test_pinless_held_batch_can_be_skipped_and_abandoned_by_cli(self):
        case = self.fixture()
        config = case.args['config']
        config['jira']['lifecycle_writes']['done'] = False
        self.assertEqual(['AWF-1'], case.batch()['held'])
        row = case.row()
        with case.fixture.store.transaction() as db:
            db.execute('DELETE FROM controller_meta WHERE key=?', ('jira-identity:' + row['operation_id'],))
        driver = OperatorRecoveryTests()
        driver.fixture()
        self.addCleanup(driver.doCleanups)
        driver.state, driver.policy, driver.stream, driver.now = case.fixture.store.path, config, 'B', case.args['now']
        result = driver.adopt()
        self.assertEqual([('AWF-1', 'HELD')], [(item['issue_id'], item['status']) for item in result['skipped_operations']])
        self.assertEqual('ABANDON_NO_REPLAY', driver.resolve(row)['resolution']['disposition'])
        self.assertEqual([], case.spy.calls)
