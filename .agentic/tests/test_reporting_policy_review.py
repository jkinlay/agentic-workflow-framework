"""Auxiliary review reproductions with real disposable policy refs and fake HTTP."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic import operating as op
from agentic.canonical import canonical
from agentic.protected_settings import require_budget_policy


class PolicyReviewTests(unittest.TestCase):
    def fixture(self):
        from test_reporting_settings_pr import ReportingSettingsPRTests
        case = ReportingSettingsPRTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def controller(self):
        from test_reporting_e2_regressions import ReportingE2Regressions
        case = ReportingE2Regressions()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_public_factory_refuses_unmerged_plain_settings_before_secret_or_post(self):
        from agentic.reporting.platform import build_reporting_providers
        from test_reporting_fixtures import ReportingNetwork
        case = self.fixture()
        proposed = case.enabled()
        proposed['reporting'] = dict(mode='slack', bindings=[case.bindings()[1]])
        proposed['reporting']['bindings'][0]['destination'] = 'CPLANTED'
        case.git('checkout', '-B', 'codex/planted')
        case.write(proposed)
        case.git('add', op.CONFIG)
        case.git('commit', '-qm', 'Unmerged reporting proposal')
        binding = {k: proposed['reporting']['bindings'][0][k] for k in ('project', 'repository', 'scope_revision')}
        network = ReportingNetwork()
        with patch('agentic.reporting.platform.environment_secret', side_effect=AssertionError('secret forbidden')):
            with self.assertRaisesRegex(ValidationError, 'REPORT_ACCEPTED_POLICY_REQUIRED'):
                build_reporting_providers({'slack': dict(context='workspace', sender='sender')},
                                          proposed, binding, http=network)
        self.assertEqual('off', op.read_operating(case.root, case.gov).config['reporting']['mode'])
        self.assertEqual([], network.posts)
        proposed_snapshot = replace(op.validate_operating(proposed, case.gov),
            _provenance_json=canonical({'path': str(case.root / op.CONFIG)}))
        self.assertEqual({}, build_reporting_providers({'slack': dict(context='workspace', sender='sender')},
            proposed_snapshot, binding, repository_root=case.root, governance=case.gov, http=network))
        self.assertEqual([], network.posts)

    def test_controller_refuses_forged_policy_repository(self):
        import test_continuous_controller as controller
        from settings_pr_fixtures import accepted_fixture_snapshot
        case = self.controller()
        case.settings['mode'] = 'off'
        real = controller.real_production_controller_cycle
        def planted(*args, **kwargs):
            snapshot = kwargs['planning_operating_snapshot']
            self.assertEqual('off', op.read_operating(kwargs['repository_root'], kwargs['planning_config']).config['reporting']['mode'])
            value = snapshot.config
            value['reporting']['mode'] = 'both'
            decoy = Path(case.helper.temp.name) / 'decoy'
            accepted_fixture_snapshot(case, decoy, value, kwargs['planning_config'])
            kwargs['planning_operating_snapshot'] = replace(snapshot,
                _provenance_json=canonical({**snapshot.provenance, 'path': str(decoy / op.CONFIG)}))
            return real(*args, **kwargs)
        with patch.object(controller, 'real_production_controller_cycle', planted):
            with self.assertRaisesRegex(ValidationError, 'provenance disagrees with the accepted repository root'):
                case.cycle('2026-11-10T10:00:00Z')
        self.assertEqual([], case.network.posts)

    def test_adapter_refuses_forged_policy_repository(self):
        from settings_pr_fixtures import accepted_fixture_snapshot
        case = self.controller()
        snapshot = case.policy_snapshot()
        root = Path(snapshot.provenance['path']).parent
        gov = op.load_yaml((root / op.GOVERNANCE).read_bytes())
        decoy = root.parent / 'decoy'
        accepted_fixture_snapshot(case, decoy, snapshot.config, gov)
        forged = replace(snapshot, _provenance_json=canonical({**snapshot.provenance, 'path': str(decoy / op.CONFIG)}))
        with self.assertRaisesRegex(ValidationError, 'provenance disagrees with the accepted repository root'):
            case.adapters['configure_reporting'](forged, case.binding, repository_root=root)
        from agentic.reporting.platform import build_reporting_providers
        with self.assertRaisesRegex(ValidationError, 'provenance disagrees with the accepted repository root'):
            build_reporting_providers({}, forged, case.binding, repository_root=root, governance=gov)
        self.assertEqual([], case.network.posts)

    def test_unprotected_schema_error_also_withholds_rejected_values(self):
        case = self.fixture()
        sentinel = 'ghp_' + 'Q' * 36
        value = deepcopy(case.value)
        value['streams']['count'] = sentinel
        proposed = case.root / 'proposed.yaml'
        proposed.write_bytes(op._json(value))
        for args in (['validate', '--file', str(proposed)], ['show']):
            if args == ['show']:
                case.publish(value)
                case.write(case.value)
            out = io.StringIO()
            with redirect_stdout(out):
                code = op.main(['--root', str(case.root), *args, '--json'])
            self.assertEqual(2, code)
            self.assertNotIn(sentinel, out.getvalue())

    def test_root_shape_no_echo_all_seven_fields_validate_and_default_show(self):
        case = self.fixture()
        sentinel = 'ghp_' + 'Q' * 36
        with ExitStack() as stack:
            for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                           'urllib.request.urlopen', 'agentic.reporting.platform.environment_secret',
                           'agentic.reporting.platform.build_reporting_providers',
                           'agentic.reporting.http_transport.SlackTransport',
                           'agentic.reporting.http_transport.TeamsTransport'):
                stack.enter_context(patch(target, side_effect=AssertionError('I/O forbidden')))
            for field in case.bindings()[0]:
                with self.subTest(field=field):
                    bad = case.enabled()
                    bad['reporting']['bindings'][0][field] = sentinel
                    proposed = case.root / 'proposed.yaml'
                    proposed.write_bytes(op._json([bad]))
                    for surface in ('validate', 'show'):
                        if surface == 'validate':
                            args = ['validate', '--file', str(proposed)]
                        else:
                            case.publish([bad])
                            case.write(case.value)
                            args = ['show']
                        out = io.StringIO()
                        with redirect_stdout(out):
                            code = op.main(['--root', str(case.root), *args, '--json'])
                        self.assertEqual(2, code)
                        self.assertNotIn(sentinel, out.getvalue())
                    case.publish(case.value)

    def changed_during_verification(self, change):
        import test_continuous_controller as controller
        from settings_pr_fixtures import publish_fixture_settings
        case = self.controller()
        case.helper.listed = [deepcopy(case.helper.current)]
        case.settings['mode'] = 'slack'
        case.settings['bindings'] = [b for b in case.settings['bindings'] if b['platform'] == 'slack']
        real = controller.real_production_controller_cycle
        original_http = type(case.network).__call__
        observed = {}
        def capture(*args, **kwargs):
            observed.update(root=Path(kwargs['repository_root']), gov=kwargs['planning_config'])
            return real(*args, **kwargs)
        def http(network, method, url, headers, payload):
            if network is case.network and 'conversations.info' in url and not observed.get('changed'):
                value = op.read_operating(observed['root'], observed['gov']).config
                if change == 'off':
                    value['reporting']['mode'] = 'off'
                else:
                    value['reporting']['bindings'][0].update(destination='CROTATED', revision='2', secret_ref='env:ROTATED')
                publish_fixture_settings(case, observed['root'], observed['gov'], value)
                observed['changed'] = True
            return original_http(network, method, url, headers, payload)
        with patch.object(controller, 'real_production_controller_cycle', capture), patch.object(type(case.network), '__call__', http):
            case.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(observed.get('changed'))
        self.assertEqual([], case.network.posts)

    def test_retained_review_port_readmits_off_immediately_before_post(self):
        self.changed_during_verification('off')

    def test_retained_review_port_readmits_exact_binding_immediately_before_post(self):
        self.changed_during_verification('binding')

    def test_public_factory_sender_refuses_scope_rotation_during_verification(self):
        from agentic.reporting.platform import build_reporting_providers
        from agentic.reporting.delivery import DeliveryOutbox, delivery_intent, install_delivery_schema
        from test_reporting_delivery import Database
        from test_reporting_fixtures import ReportingNetwork
        case = self.fixture()
        value = case.enabled()
        destination = dict(case.bindings()[1], destination='CAPPROVED')
        value['reporting'] = dict(mode='slack', bindings=[destination])
        case.publish(value)
        snapshot = op.read_operating(case.root, case.gov)
        binding = {k: destination[k] for k in ('project', 'repository', 'scope_revision')}
        network = ReportingNetwork()
        original = type(network).__call__
        changed = []
        def http(net, method, url, headers, payload):
            if net is network and 'conversations.info' in url and not changed:
                rotated = deepcopy(value)
                rotated['reporting']['bindings'][0]['scope_revision'] = 'next-scope'
                case.publish(rotated)
                changed.append(True)
            return original(net, method, url, headers, payload)
        with patch('agentic.reporting.platform.environment_secret', return_value='synthetic-report-secret'):
            providers = build_reporting_providers({'slack': dict(context='workspace', sender='sender')},
                snapshot, binding, repository_root=case.root, governance=case.gov, http=network)
            database = Database(str(case.root / 'delivery.sqlite'))
            with database.transaction() as db:
                install_delivery_schema(db)
            outbox = DeliveryOutbox(database)
            intent = delivery_intent('retained-scope', destination, 'Synthetic report')
            outbox.enqueue(intent)
            with patch.object(type(network), '__call__', http):
                state = outbox.advance(intent['delivery_id'], providers['slack'])
        self.assertTrue(changed)
        self.assertEqual([], network.posts)
        self.assertEqual([], state['attempts'])

    def test_final_clock_check_follows_policy_reload(self):
        from agentic.review_batching import ReviewBatchStore
        from agentic.reporting.runtime import ReviewReportingPort
        from agentic.operating_calendar import effective_calendar
        case = self.controller()
        now = '2026-11-10T16:59:59Z'
        case.clock.value = now
        batches = ReviewBatchStore(case.helper.store)
        batches.enqueue(case.helper.current, now, {})
        payload = batches.claim(now, {}, lambda candidate: case.helper.observe_candidate(candidate, now))
        operating = {'reporting': deepcopy(case.settings)}
        def reload_policy():
            case.clock.value = '2026-11-10T17:00:01Z'
            return operating
        port = ReviewReportingPort(case.helper.store, operating, case.binding, {},
            lambda: case.clock.value, load_operating=reload_policy)
        with self.assertRaisesRegex(ValidationError, 'REPORT_REVIEW_CHECK_EXPIRED'):
            port._check(payload, effective_calendar(operating), case.helper.observe_candidate)

    def test_final_calendar_binding_uses_reloaded_policy(self):
        from agentic.review_batching import ReviewBatchStore
        from agentic.reporting.runtime import ReviewReportingPort
        from agentic.operating_calendar import effective_calendar
        case = self.controller()
        now = '2026-11-10T10:00:00Z'
        case.clock.value = now
        batches = ReviewBatchStore(case.helper.store)
        batches.enqueue(case.helper.current, now, {})
        payload = batches.claim(now, {}, lambda candidate: case.helper.observe_candidate(candidate, now))
        operating = {'reporting': deepcopy(case.settings)}
        current = deepcopy(operating)
        current['operating_calendar'] = effective_calendar(operating)
        current['operating_calendar']['review_batching']['presentation_time'] = '08:00'
        port = ReviewReportingPort(case.helper.store, operating, case.binding, {},
            lambda: case.clock.value, load_operating=lambda: current)
        with self.assertRaisesRegex(ValidationError, 'REPORT_REVIEW_CHECK_EXPIRED'):
            port._check(payload, effective_calendar(operating), case.helper.observe_candidate)

    def test_accepted_absent_operating_file_admits_off(self):
        case = self.fixture()
        case.git('rm', '-f', op.CONFIG)
        case.git('commit', '-qm', 'Accepted absent operating file')
        case.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        case.write(case.value)
        snapshot = op.read_operating(case.root, case.gov)
        require_budget_policy(snapshot)
        self.assertEqual('off', snapshot.config['reporting']['mode'])
        self.assertFalse(snapshot.provenance['protected_settings']['unverified'])

    def test_deleted_working_file_cannot_replace_accepted_reporting_with_defaults(self):
        from agentic.reporting.platform import build_reporting_providers
        from agentic.budgeting import accepted_budget_snapshot
        case = self.fixture()
        value = case.enabled()
        destination = dict(case.bindings()[1], destination='CAPPROVED')
        value['reporting'] = dict(mode='slack', bindings=[destination])
        case.publish(value)
        proposed = replace(op.validate_operating(case.value, case.gov),
            _provenance_json=canonical({'path': str(case.root / op.CONFIG)}))
        (case.root / op.CONFIG).unlink()
        snapshot = accepted_budget_snapshot(case.root, case.gov, proposed)
        self.assertEqual(value['reporting'], snapshot.config['reporting'])
        binding = {k: destination[k] for k in ('project', 'repository', 'scope_revision')}
        with patch('agentic.reporting.platform.environment_secret', side_effect=AssertionError('secret forbidden')):
            providers = build_reporting_providers({'slack': dict(context='workspace', sender='sender')},
                proposed, binding, repository_root=case.root, governance=case.gov)
        self.assertEqual({'slack'}, set(providers))
        self.assertEqual(destination, providers['slack'].binding)

    def test_prepared_policy_rechecks_default_identity_and_reloads_on_change(self):
        from agentic.reporting.policy import ReportingPolicy
        import agentic.reporting.policy as policy
        case = self.fixture()
        snapshot = op.read_operating(case.root, case.gov)
        with patch.object(policy, 'accepted_budget_snapshot', wraps=policy.accepted_budget_snapshot) as derive:
            current = ReportingPolicy(case.root, case.gov, snapshot)
            self.assertEqual('off', current()['reporting']['mode'])
            self.assertEqual('off', current()['reporting']['mode'])
            repeated = ReportingPolicy(case.root, case.gov, snapshot)
            self.assertEqual(current.snapshot.config, repeated.snapshot.config)
            shared = ReportingPolicy(case.root, case.gov, current.snapshot)
            self.assertEqual(current.snapshot.config, shared.snapshot.config)
            self.assertEqual(1, derive.call_count)
            copied = replace(current.snapshot)
            ReportingPolicy(case.root, case.gov, copied)
            self.assertEqual(2, derive.call_count)
            case.publish(case.enabled())
            self.assertEqual('both', current()['reporting']['mode'])
            self.assertEqual(3, derive.call_count)
            case.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
            case.git('remote', 'add', 'origin', str(case.root.parent / 'missing-policy-remote.git'))
            with self.assertRaises(ValidationError):
                current()
            self.assertEqual(4, derive.call_count)

    def test_review_stage_reuses_admitted_policy_but_observes_new_calendar(self):
        from agentic.continuous_controller import ContinuousControllerStore, production_review_batching_cycle
        from agentic.reporting.policy import ReportingPolicy
        from agentic.review_batching import ReviewBatchStore
        from test_awf244_controller_wiring import candidate
        import agentic.protected_settings as protected
        case = self.fixture()
        snapshot = ReportingPolicy(case.root, case.gov, op.read_operating(case.root, case.gov)).snapshot
        store = ContinuousControllerStore(case.root / 'review.sqlite', ['A'])
        now = '2026-11-10T07:30:00Z'
        ReviewBatchStore(store).enqueue(candidate(), now, {})
        def observe():
            return production_review_batching_cycle(store,
                calendar={'review_batching': {'presentation_time': '09:00'}},
                repository_root=case.root, config=case.gov, operating_snapshot=snapshot,
                binding=dict(project_id=case.gov['project']['id'], repository_id='101', scope_sha256='a'*64),
                clock=lambda: now)
        with patch.object(protected, 'effective_snapshot', wraps=protected.effective_snapshot) as derive:
            self.assertFalse(observe()['status']['intents'][0]['held'])
            self.assertEqual(0, derive.call_count)
            changed = deepcopy(case.value)
            changed['operating_calendar']['review_batching']['presentation_time'] = '08:00'
            case.publish(changed)
            self.assertTrue(observe()['status']['intents'][0]['held'])
            self.assertEqual(1, derive.call_count)


class InjectedPeriodicPolicyTests(unittest.TestCase):
    controller = PolicyReviewTests.controller
    def injected(self, change=None):
        import test_continuous_controller as controller
        from settings_pr_fixtures import publish_fixture_settings
        from agentic.reporting.platform import SlackReportingAdapter
        from agentic.reporting.http_transport import SlackTransport
        case = self.controller()
        case.settings['mode'] = 'slack'
        case.settings['bindings'] = [b for b in case.settings['bindings'] if b['platform'] == 'slack']
        sender = SlackReportingAdapter(SlackTransport(context='workspace', sender='sender',
            http=case.network, clock=case.clock.now), case.settings['bindings'][0],
            resolve_secret=lambda ref: 'synthetic-report-secret', mode='slack')
        self.assertIsNone(sender.load_policy)
        case.adapters.pop('configure_reporting', None)
        case.adapters['reporting_providers'] = {'slack': sender}
        real = controller.real_production_controller_cycle
        original = type(case.network).__call__
        observed = {}
        def capture(*args, **kwargs):
            observed.update(root=Path(kwargs['repository_root']), gov=kwargs['planning_config'])
            return real(*args, **kwargs)
        def http(net, method, url, headers, payload):
            if net is case.network and 'conversations.info' in url and not observed.get('verified'):
                observed['verified'] = True
                if change:
                    value = op.read_operating(observed['root'], observed['gov']).config
                    change(value)
                    publish_fixture_settings(case, observed['root'], observed['gov'], value)
            return original(net, method, url, headers, payload)
        with patch.object(controller, 'real_production_controller_cycle', capture), patch.object(type(case.network), '__call__', http):
            case.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(observed.get('verified'))
        states = case.status()['deliveries']
        self.assertTrue(states)
        if change:
            self.assertEqual([], case.network.posts)
            self.assertTrue(all(d['attempts'] == [] and d['status'] != 'DELIVERED' for d in states))
        else:
            self.assertEqual(1, len(case.network.posts))
            self.assertTrue(all(len(d['attempts']) == 1 and d['status'] == 'DELIVERED' for d in states))
            self.assertGreater(case.network.history_reads, 0)

    def test_injected_periodic_sender_readmits_off_after_destination_verification(self):
        self.injected(lambda v: v.update(reporting=dict(mode='off', bindings=[])))

    def test_injected_periodic_sender_readmits_binding_rotation_after_destination_verification(self):
        self.injected(lambda v: v['reporting']['bindings'][0].update(
            destination='CROTATED', revision='2', secret_ref='env:ROTATED'))

    def test_injected_periodic_sender_unchanged_policy_delivers_once(self):
        self.injected()

    def test_injected_periodic_sender_readmits_each_binding_field_and_mode_removal(self):
        for field in ('project', 'repository', 'scope_revision', 'platform', 'destination', 'revision', 'secret_ref', 'mode'):
            with self.subTest(field=field):
                def change(value):
                    if field == 'mode':
                        value.pop('reporting')
                    else:
                        value['reporting']['bindings'][0][field] = {
                            'repository': '202', 'platform': 'teams', 'secret_ref': 'env:ROTATED'
                        }.get(field, 'rotated')
                self.injected(change)

    def test_controller_accepted_off_without_bindings_suspends_reporting_across_restart(self):
        from agentic.continuous_controller import ContinuousControllerStore
        from agentic.canonical import fingerprint, loads
        case = self.controller()
        case.cycle('2026-11-07T16:59:59Z')
        case.cycle('2026-11-07T17:00:00Z')
        before = deepcopy(case.status())
        enabled = deepcopy(case.settings)
        case.settings = dict(mode='off', bindings=[])
        result = case.cycle('2026-11-07T17:30:00Z')
        self.assertEqual(0, result['reporting']['generated'])
        key = 'reporting:' + fingerprint('reporting-scope', case.binding)
        with case.helper.store.connection() as db:
            state = loads(db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()[0])
        self.assertTrue(state['suspended'])
        self.assertEqual('2026-11-07T17:30:00Z', state['cursor'])
        case.helper.store = ContinuousControllerStore(case.helper.store.path, ['A', 'B', 'C'], cadence_seconds=3600)
        case.settings = enabled
        result = case.cycle('2026-11-07T20:00:00Z')
        self.assertEqual(1, result['reporting']['generated'])
        report = result['reporting']['reports'][0]
        self.assertEqual('hourly', report['kind'])
        self.assertEqual('2026-11-07T20:00:00Z', report['period']['end'])
        self.assertFalse(report['period']['catch_up'])
        after = case.status()
        self.assertEqual(before['reports'], after['reports'][:-1])
        self.assertEqual(before['deliveries'], after['deliveries'][:-2])
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1 for d in after['deliveries'][-2:]))
