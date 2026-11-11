"""Stage-three integration regressions; trusted ports are synthetic, never live."""
from copy import deepcopy
import json
import os
import io
from contextlib import closing, redirect_stdout
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic.reporting import runtime
from agentic import operating as op
from agentic.canonical import fingerprint, sha256
from agentic.continuous_controller import ContinuousControllerStore
from test_operating import governance


def binding(platform='slack', project='p', repository='101', scope='s'):
    return dict(project=project, repository=repository, scope_revision=scope,
                platform=platform, destination='approved-' + platform, revision='1', secret_ref='env:REPORT_TOKEN')


class ReportingSettingsTests(unittest.TestCase):
    from reporting_pr_fixtures import ReportingPRFixture as admission_fixture
    setUpClass = classmethod(admission_fixture.setUpClass.__func__)
    setUp = admission_fixture.setUp
    save_governance = admission_fixture.save_governance
    events = admission_fixture.events
    cli = admission_fixture.cli
    unchanged = admission_fixture.unchanged
    git = admission_fixture.git
    write = admission_fixture.write
    publish = admission_fixture.publish
    refresh = admission_fixture.refresh
    apply_reporting = admission_fixture.apply_reporting
    bindings = admission_fixture.bindings

    def destinations(self):
        return [binding(p, self.gov['project']['id'], str(self.gov['github']['repository_id']))
                for p in ('teams', 'slack')]

    def set(self, mode, **kwargs):
        snapshot = self.apply_reporting(mode, self.destinations())
        self.count = getattr(self, 'count', 0) + 1
        return snapshot

    def test_modes_persist_show_and_enable_no_other_feature(self):
        from agentic.reporting.settings import effective_reporting
        self.assertEqual(effective_reporting({}), dict(mode='off', bindings=[]))
        initial = op.read_operating(self.root, self.gov).config
        for mode in ('teams', 'slack', 'both', 'off'):
            result = self.set(mode)
            self.assertEqual(op.read_operating(self.root, self.gov), result)
            self.assertEqual(result.config['reporting']['mode'], mode)
            self.assertEqual(result.config.get('budgeting'), initial.get('budgeting'))
            self.assertNotIn('chat', result.config)
            self.assertIn('Reporting (effective settings)', op.render_operating(result, self.gov, state='CONFIGURED'))
        self.assertEqual(self.count, 4)

    def test_inert_default_preserves_planning_only_governance_without_github(self):
        gov = deepcopy(self.gov)
        gov.pop('github')
        value = op.default_operating(gov['execution']['model_routing'])
        from agentic.reporting.settings import effective_reporting
        self.assertEqual(effective_reporting(op.validate_operating(value, gov).config),
                         dict(mode='off', bindings=[]))
        value['reporting'] = effective_reporting({})
        self.assertEqual(op.validate_operating(value, gov).config['reporting']['mode'], 'off')
        value['reporting'] = dict(mode='slack', bindings=[binding()])
        with self.assertRaisesRegex(ValidationError, 'REPORT_PROJECT_REPOSITORY_BINDING_REQUIRED'):
            op.validate_operating(value, gov)

    def test_missing_foreign_and_mixed_invalid_refuse_before_host(self):
        for assignments in (['reporting.mode=both'],
            ['reporting.mode=slack', 'reporting.bindings=' + json.dumps([binding(project='foreign')])],
            ['reporting.mode=slack', 'reporting.bindings=' + json.dumps([binding()]), 'streams.count=99']):
            before = (self.root / op.CONFIG).read_bytes()
            with self.assertRaises(ValidationError):
                op.set_operating(self.root, self.gov, 'Enable', assignments)
            self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(getattr(self, 'count', 0), 0)
        with self.assertRaisesRegex(ValidationError, 'PR'):
            op.set_operating(self.root, self.gov, 'OFF', ['reporting.mode=off'])

    def test_cli_shows_effective_settings_delivery_and_coverage_without_migration(self):
        from agentic.cli import main
        from agentic.reporting.runtime import reporting_cycle
        snapshot = self.set('both')
        state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(state_dir.cleanup)
        controller = ContinuousControllerStore(Path(state_dir.name) / 'controller.sqlite3', ['A'])
        accepted = dict(project=self.gov['project']['id'], repository=str(self.gov['github']['repository_id']), scope_revision='s')
        reporting_cycle(controller, operating=snapshot.config, binding=accepted, now='2026-11-07T17:00:00Z')
        path = self.root / 'binding.json'
        path.write_text(json.dumps(accepted))
        output = io.StringIO()
        before = controller.path.read_bytes()
        with redirect_stdout(output), patch.object(ContinuousControllerStore, '__init__', side_effect=AssertionError('migration forbidden')):
            result = main(['--root', str(self.root), 'reporting-status', '--state', str(controller.path), '--binding', str(path)])
        self.assertEqual(result, 0)
        shown = json.loads(output.getvalue())
        self.assertEqual(shown['settings']['mode'], 'both')
        self.assertEqual({s['status'] for s in shown['deliveries']}, {'PENDING'})
        self.assertEqual(shown['coverage']['accounting'], 'UNAVAILABLE')
        self.assertEqual(controller.path.read_bytes(), before)


class ReportingRuntimeTests(unittest.TestCase):
    def setUp(self):
        secrets = patch.dict(os.environ, REPORT_TOKEN='synthetic-report-token')
        secrets.start()
        self.addCleanup(secrets.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'controller.sqlite3'
        self.store = ContinuousControllerStore(self.path, ['A'])
        self.binding = dict(project='p', repository='101', scope_revision='s')
        self.root = dict(reporting=dict(mode='both', bindings=[binding('teams'), binding()]))
        self.sent = []
        self.visible_platforms = {'slack'}
        owner = self
        class Provider:
            def send(self, intent, attempt):
                owner.sent.append((intent, attempt))
            def reconcile(self, intent, attempts):
                return dict(status='VISIBLE' if intent['binding']['platform'] in owner.visible_platforms else 'UNKNOWN',
                    evidence_kind='CHANNEL_READBACK', delivery_id=intent['delivery_id'],
                    destination=intent['binding']['destination'], platform=intent['binding']['platform'],
                    payload_sha256=intent['payload_sha256'], attempt_ids=attempts,
                    observation_id='read-' + intent['delivery_id'], observed_at='2026-11-07T19:00:00Z',
                    message_id='1355517523.000005' if intent['binding']['platform'] == 'slack' else '1616990032035')
        self.providers = {p: Provider() for p in ('teams', 'slack')}

    def cycle(self, now, **kwargs):
        from agentic.reporting.runtime import reporting_cycle
        return reporting_cycle(self.store, operating=self.root, binding=self.binding, now=now,
                               providers=self.providers, defer_delivery=False, load_operating=lambda: self.root, **kwargs)

    def test_weekend_idle_eod_continuity_restart_and_destination_independence(self):
        self.cycle('2026-11-07T16:59:59Z')
        self.cycle('2026-11-07T17:00:00Z')
        self.store = ContinuousControllerStore(self.path, ['A'])
        self.cycle('2026-11-07T18:00:00Z')
        from agentic.reporting.runtime import reporting_status
        result = reporting_status(self.store, self.root, self.binding)
        self.assertEqual(len(result['reports']), 3)
        self.assertEqual([r['kind'] for r in result['reports']].count('eod'), 1)
        self.assertEqual(len(self.sent), 6)
        self.assertEqual({r['status'] for r in result['deliveries'] if r['platform'] == 'slack'}, {'DELIVERED'})
        self.assertEqual({r['status'] for r in result['deliveries'] if r['platform'] == 'teams'}, {'UNKNOWN'})
        self.assertIn('UNAVAILABLE', str(result['coverage']))
        self.cycle('2026-11-07T18:00:00Z')
        self.assertEqual(len(self.sent), 6)

    def test_off_and_foreign_bindings_never_invoke_provider(self):
        self.root['reporting']['mode'] = 'off'
        self.cycle('2026-11-07T17:00:00Z')
        self.assertEqual(self.sent, [])
        self.root['reporting']['mode'] = 'both'
        self.root['reporting']['bindings'][0]['scope_revision'] = 'foreign'
        with self.assertRaises(ValidationError):
            self.cycle('2026-11-07T18:00:00Z')
        self.assertEqual(self.sent, [])

    def test_atomic_generation_failure_and_labelled_catchup(self):
        self.cycle('2026-11-07T16:00:01Z')
        from agentic.reporting import runtime
        real = runtime.render_body
        with patch.object(runtime, 'render_body', side_effect=ValidationError('synthetic render failure')):
            with self.assertRaises(ValidationError):
                self.cycle('2026-11-07T18:30:00Z')
        result = self.cycle('2026-11-07T18:30:00Z')
        self.assertEqual(result['generated'], 3)
        self.assertTrue(all(r['body']['data']['catch_up'] for r in result['reports']))
        self.assertTrue(all('CATCH-UP' in i['payload'] for i, _ in self.sent))

    def test_immediate_owner_events_deduplicate_and_unavailable_never_resolves(self):
        action = dict(id='access', decision='Grant access', owner='owner', due='now',
                      reason='blocked', options=['grant', 'park'], resume='access')
        context = dict(owner_actions=[action])
        self.cycle('2026-11-07T18:30:00Z', context=context)
        self.assertEqual(len(self.sent), 2)
        self.cycle('2026-11-07T18:31:00Z', context=context)
        self.cycle('2026-11-07T18:32:00Z')
        self.assertEqual(len(self.sent), 2)
        # Establish the delivered-request precondition on both platforms;
        # an ambiguous original must reconcile before its resolution posts.
        self.visible_platforms.add('teams')
        self.cycle('2026-11-07T18:32:30Z', context=context)
        from agentic.reporting.runtime import reporting_status
        self.assertEqual({d['status'] for d in reporting_status(self.store, self.root, self.binding)['deliveries']}, {'DELIVERED'})
        self.assertEqual(len(self.sent), 2)
        self.cycle('2026-11-07T18:33:00Z', context=dict(owner_actions=[]))
        self.assertEqual(len(self.sent), 4)

    def test_first_boundary_and_configured_weekend_eod_are_due(self):
        self.root['operating_calendar'] = dict(eod=dict(time='19:15'))
        first = self.cycle('2026-11-07T18:00:00Z')
        self.assertEqual(first['generated'], 1)
        result = self.cycle('2026-11-07T20:00:00Z')
        self.assertEqual(result['generated'], 3)
        self.assertEqual([r['kind'] for r in result['reports']].count('eod'), 1)

    def test_disabled_periods_are_not_backfilled_as_downtime(self):
        self.cycle('2026-11-07T16:59:59Z')
        self.cycle('2026-11-07T17:00:00Z')
        self.root['reporting']['mode'] = 'off'
        self.cycle('2026-11-07T17:30:00Z')
        self.root['reporting']['mode'] = 'both'
        result = self.cycle('2026-11-07T20:00:00Z')
        self.assertEqual(result['generated'], 1)
        self.assertEqual(result['reports'][0]['body']['data']['period'].count('20:00'), 2)

    def cleared_off(self, absent=False):
        from agentic.canonical import loads
        self.visible_platforms.add('teams')
        self.cycle('2026-11-07T16:59:59Z')
        self.cycle('2026-11-07T17:00:00Z')
        from agentic.reporting.runtime import reporting_status
        before = deepcopy(reporting_status(self.store, self.root, self.binding))
        enabled = deepcopy(self.root)
        if absent:
            self.root.pop('reporting')
        else:
            self.root['reporting'] = dict(mode='off', bindings=[])
        sent = len(self.sent)
        result = self.cycle('2026-11-07T17:30:00Z')
        self.assertEqual(0, result['generated'])
        self.assertEqual(sent, len(self.sent))
        key = 'reporting:' + fingerprint('reporting-scope', self.binding)
        with self.store.connection() as db:
            state = loads(db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()[0])
        self.assertTrue(state['suspended'])
        self.assertEqual('2026-11-07T17:30:00Z', state['cursor'])
        with self.assertRaisesRegex(ValidationError, 'REPORT_CLOCK_REGRESSION'):
            self.cycle('2026-11-07T17:29:00Z')
        self.store = ContinuousControllerStore(self.path, ['A'])
        self.root = enabled
        result = self.cycle('2026-11-07T20:00:00Z')
        self.assertEqual(1, result['generated'])
        self.assertEqual('hourly', result['reports'][0]['kind'])
        self.assertEqual('2026-11-07T20:00:00Z', result['reports'][0]['period']['end'])
        self.assertFalse(result['reports'][0]['period']['catch_up'])
        after = reporting_status(self.store, self.root, self.binding)
        self.assertEqual(before['reports'], after['reports'][:-1])
        self.assertEqual(before['deliveries'], after['deliveries'][:-2])
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1 for d in after['deliveries'][-2:]))

    def test_cleared_bindings_off_period_is_not_backfilled_after_restart(self):
        self.cleared_off()

    def test_absent_reporting_block_off_period_is_not_backfilled_after_restart(self):
        self.cleared_off(absent=True)

    def test_readiness_correction_is_immediate_but_final_notice_is_batched(self):
        c = dict(project='p', repository='101', scope_revision='s', pr=1, head='a'*40,
                 base='b'*40, gate='g', stage='READY_FOR_OWNER_AUTHORIZATION',
                 critic=True, specialists=True, final_gate=True)
        result = self.cycle('2026-11-07T18:30:00Z', context=dict(readiness_candidates=[c]))
        self.assertEqual(result['generated'], 0)
        result = self.cycle('2026-11-07T18:31:00Z', context=dict(readiness_candidates=[dict(c, head='c'*40, final_gate=False)]))
        self.assertEqual(result['generated'], 1)
        self.assertEqual(result['reports'][0]['kind'], 'owner-action')
        self.assertIn('READINESS_INVALIDATED', str(result['reports'][0]))

    def test_cli_readback_uses_no_controller_migration_or_provider(self):
        from agentic.reporting.observations import inspect_reporting
        self.cycle('2026-11-07T16:59:59Z')
        self.cycle('2026-11-07T17:00:00Z')
        before = self.path.read_bytes()
        status = inspect_reporting(self.path, self.root, self.binding)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(len(status['reports']), 2)
        self.assertEqual(status['settings']['mode'], 'both')
        self.assertEqual({s['status'] for s in status['deliveries']}, {'DELIVERED', 'UNKNOWN'})
        missing = self.path.with_name('missing.sqlite3')
        with self.assertRaises(ValidationError):
            inspect_reporting(missing, self.root, self.binding)
        self.assertFalse(missing.exists())

    def test_review_receipt_requires_both_destinations_and_readback_never_sends(self):
        from agentic.reporting.runtime import ReviewReportingPort
        from test_awf244_controller_wiring import candidate
        from agentic.review_batching import ReviewBatchStore
        now = '2026-11-10T10:00:00Z'
        batch_store = ReviewBatchStore(self.store)
        c = candidate()
        batch_store.enqueue(c, now, {})
        def observe(value):
            return dict(candidate=value, observed_at=now, expires_at='2026-11-10T10:01:00Z',
                open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        payload = batch_store.claim(now, {}, observe)
        batch_store.recheck(payload['batch_id'], now, {}, observe)
        batch_store.begin_delivery(payload['batch_id'], now)
        # All-readback evidence timestamps must be current for this batch.
        for provider in self.providers.values():
            original = provider.reconcile
            provider.reconcile = lambda intent, attempts, f=original: dict(f(intent, attempts), observed_at=now)
        port = ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: now,
                                   load_operating=lambda: self.root)
        self.assertIsNone(port.deliver_review_batch(payload))
        self.assertEqual(len(self.sent), 2)
        self.assertIsNone(port.observe_review_delivery(payload))
        self.assertEqual(len(self.sent), 2)
        teams = self.providers['teams'].reconcile
        self.providers['teams'].reconcile = lambda i, a: dict(teams(i, a), status='VISIBLE')
        receipt = port.observe_review_delivery(payload)
        self.assertEqual(receipt['status'], 'DELIVERED')
        self.assertEqual(receipt['batch_id'], payload['batch_id'])
        self.assertEqual(len(self.sent), 2)

    def test_review_retry_rechecks_gate_and_never_multiplies_attempts(self):
        from agentic.reporting.runtime import ReviewReportingPort, reporting_status
        from test_awf244_controller_wiring import candidate
        from agentic.review_batching import ReviewBatchStore
        now = '2026-11-10T10:00:00Z'
        self.root['reporting']['mode'] = 'slack'
        batches = ReviewBatchStore(self.store)
        c = candidate()
        batches.enqueue(c, now, {})
        def observation(value, current):
            return dict(candidate=value, observed_at=current, expires_at='2026-11-10T10:05:00Z',
                open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        payload = batches.claim(now, {}, lambda c: observation(c, now))
        batches.recheck(payload['batch_id'], now, {}, lambda c: observation(c, now))
        batches.begin_delivery(payload['batch_id'], now)
        original = self.providers['slack'].reconcile
        self.providers['slack'].reconcile = lambda i, a: dict(original(i, a), status='ABSENT', terminal=True, observed_at=now)
        port = ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: now,
                                   load_operating=lambda: self.root)
        self.assertIsNone(port.deliver_review_batch(payload))
        self.assertEqual(len(self.sent), 1)
        now = '2026-11-10T10:01:00Z'
        self.assertIsNone(port.observe_review_delivery(payload))
        self.assertEqual(len(self.sent), 1)
        with self.assertRaises(ValidationError):
            port.resume({}, lambda c, n: dict(observation(c, n), final_gate_valid=False))
        self.assertEqual(len(self.sent), 1)
        port.resume({}, observation)
        self.assertEqual(len(self.sent), 2)
        port.resume({}, observation)
        self.assertEqual(len(self.sent), 2)
        state = reporting_status(self.store, self.root, self.binding)['deliveries'][0]
        self.assertEqual(state['status'], 'FAILED')
        self.assertEqual(len(state['attempts']), 2)
        self.assertIsNone(port.observe_review_delivery(payload))

    def test_reporting_port_checks_the_last_clock_after_current_gate_reads(self):
        from agentic.reporting.runtime import ReviewReportingPort
        from agentic.review_batching import ReviewBatchStore
        from test_awf244_controller_wiring import candidate
        now = '2026-11-10T16:59:59Z'
        c = candidate()
        batches = ReviewBatchStore(self.store)
        batches.enqueue(c, now, {})
        def observe(value, observed):
            nonlocal now
            result = dict(candidate=value, observed_at=observed, expires_at='2026-11-10T17:00:59Z',
                open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
            now = '2026-11-10T17:00:00Z'
            return result
        payload = batches.claim(now, {}, lambda value: dict(candidate=value, observed_at=now,
            expires_at='2026-11-10T17:00:59Z', open=True, merged=False,
            readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True))
        port = ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: now,
                                   observe_candidate=observe, load_operating=lambda: self.root)
        with self.assertRaises(ValidationError):
            port.deliver_review_batch(payload)
        self.assertEqual(self.sent, [])

    def test_read_only_missing_parent_creates_nothing(self):
        from agentic.reporting.observations import inspect_reporting
        parent = self.path.parent / 'absent-parent'
        with self.assertRaises(ValidationError):
            inspect_reporting(parent / 'state.sqlite3', self.root, self.binding)
        self.assertFalse(parent.exists())


class ControllerReportingTests(unittest.TestCase):
    def setUp(self):
        from test_reporting_fixtures import isolate_reporting_admission
        isolate_reporting_admission(self)

    def test_real_cycle_wires_sources_reports_batches_and_continued_dispatch(self):
        from test_awf244_controller_wiring import ControllerBatchingTests
        from test_continuous_controller import INVENTORY_BINDING, ticket
        helper = ControllerBatchingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        original_validate = op.validate_operating
        destinations = [binding(p, INVENTORY_BINDING['project_id'], INVENTORY_BINDING['repository_id'],
                                INVENTORY_BINDING['scope_sha256']) for p in ('teams', 'slack')]
        def validate(config, gov):
            config = deepcopy(config)
            config['reporting'] = dict(mode='both', bindings=destinations)
            return original_validate(config, gov)
        sends = []
        class Provider:
            def send(self, intent, attempt):
                sends.append(intent)
            def reconcile(self, intent, attempts):
                return dict(status='UNKNOWN')
        def observe(request):
            return dict(binding=request['binding'], observed_at=request['observed_at'],
                records=None, owner_actions=[dict(id='a', decision='Access', owner='owner', due='now',
                    reason='blocked', options=['grant'], resume='access')], authentication={'evidence_id': 'source'})
        def verify(auth, request):
            return dict(status='VERIFIED', evidence_id='source', snapshot={k: v for k, v in observe(request).items() if k != 'authentication'})
        calls = dict(reporting_providers={p: Provider() for p in ('teams', 'slack')},
                     observe_reporting_sources=observe, verify_reporting_sources=verify)
        with patch('test_awf244_controller_wiring.validate_operating', validate):
            first = helper.cycle('2026-11-10T10:00:01Z', [ticket('EX-1', 1)], **calls)
            second = helper.cycle('2026-11-10T11:00:00Z', **calls)
        self.assertEqual(len(first['dispatch_receipts']), 1)
        self.assertEqual(first['reporting']['generated'], 1)
        self.assertEqual(second['reporting']['generated'], 1)
        self.assertEqual(helper.delivered, [])  # reporting owns the review port
        self.assertEqual(len([s for s in sends if 'Ready for final human review' in s['payload']]), 2)
        self.assertEqual(len(sends), 6)
        self.assertFalse(second['execution_authority'])

    def test_reference_receipt_explicitly_is_local(self):
        import runpy
        from test_reference_controller_adapter import ReferenceControllerAdapterTests
        # Reuse its inert configuration and fake transports, never its tests.
        helper = ReferenceControllerAdapterTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        from test_reference_controller_adapter import FakeClock
        adapters = runpy.run_path(str(helper.adapter))['build_adapters']({**helper.config, '_clock': FakeClock()})
        receipt = adapters['deliver_status'](dict(delivery_id='test', observed_at='2026-11-05T10:00:00Z'))
        self.assertEqual(receipt['evidence_kind'], 'LOCAL_RECEIPT')
        self.assertFalse(receipt['channel_delivery'])


class AccountingObservationTests(unittest.TestCase):
    def test_off_unknown_and_only_explicit_attribution_with_read_only_bytes(self):
        from agentic.reporting.observations import attributed_accounting
        from agentic.budgeting import fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        from agentic.budgeting import BudgetLedger
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'routing.sqlite3'
            off = dict(budgeting=fresh_settings())
            self.assertIsNone(attributed_accounting(path, off, 'p')['consumed'])
            self.assertFalse(path.exists())
            settings = dict(fresh_settings(), accounting_enabled=True)
            class Host:
                def observe_enrollment(self, b):
                    return deepcopy(b)
            registry = EnrollmentRegistry(Path(raw) / 'enrollment.sqlite3', Host())
            ledger = BudgetLedger(path, registry)
            b = dict(project_id='p', ticket_id='EX-1', run_id='run', dispatch_id='dispatch',
                     session_id='session', role='critic')
            registry.enroll(b)
            ledger.reserve(b, settings, 100)
            ledger.settle(b, 20, 'usage')
            # Fake provider rows include controller/independent and a legacy run.
            with closing(sqlite3.connect(path)) as db:
                original = db.execute('SELECT * FROM model_runs').fetchone()
                columns = [row[1] for row in db.execute('PRAGMA table_info(model_runs)')]
                for run, req in [('controller', {'_budgeting': {**settings, 'attributable': False}}),
                                 ('independent-ultra', {}), ('unknown', {'_budgeting': {**settings, 'attributable': True},
                                    '_budget_binding': dict(b, run_id='unknown', session_id='other')})]:
                    values = dict(zip(columns, original))
                    values.update(run_id=run, request=json.dumps(req), agent_id='other',
                                  actual_tokens=None if run == 'unknown' else 999)
                    db.execute('INSERT INTO model_runs VALUES(' + ','.join('?' for _ in columns) + ')', list(values.values()))
                db.commit()
            before = path.read_bytes()
            status = attributed_accounting(path, dict(budgeting=settings), 'p')
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(status['known_tokens'], 20)
            self.assertIsNone(status['consumed'])
            self.assertEqual(status['unknown_runs'], ['unknown'])
            self.assertEqual(status['coverage'], dict(observed=1, enrolled=2))
            self.assertIsNone(attributed_accounting(path, off, 'p')['known_tokens'])

    def test_sources_require_independent_matching_current_bytes(self):
        from agentic.reporting.observations import collect_sources
        b = dict(project='p', repository='101', scope_revision='s')
        now = '2026-11-07T10:00:00Z'
        value = dict(binding=b, observed_at=now, records=None, owner_actions=[], authentication={'evidence_id': 'e'})
        for bad in (dict(value, observed_at='2026-11-07T09:00:00Z'), dict(value, binding=dict(b, project='foreign'))):
            with self.assertRaises(ValidationError):
                collect_sources(b, now, lambda request: bad, lambda auth, request: {})
        with self.assertRaises(ValidationError):
            collect_sources(b, now, lambda request: value, lambda auth, request: {})


class MergeBatchFenceTests(unittest.TestCase):
    def test_claimed_intent_without_outbox_still_fences_new_head(self):
        from test_awf244_review_batching import BatchingTests, candidate
        case = BatchingTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        identity = case.enqueue()
        with case.controller.transaction() as db:
            db.execute("UPDATE review_presentation_intent_v1 SET state='CLAIMED',attempts=1 WHERE intent_id=?",
                       (identity,))
        case.enqueue(dict(candidate(), head_sha='e' * 40))
        self.assertIsNone(case.store.claim(case.now, {}, case.observe))
        with case.controller.connection() as db:
            row = db.execute('SELECT state,attempts FROM review_presentation_intent_v1 WHERE intent_id=?',
                             (identity,)).fetchone()
        self.assertEqual(('CLAIMED', 1), tuple(row))


class ReportingLibraryPolicyTests(unittest.TestCase):
    setUp = ReportingRuntimeTests.setUp

    def prepare(self):
        for now in ('2026-11-07T16:59:59Z', '2026-11-07T17:00:00Z'):
            runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding,
                now=now, providers=self.providers, defer_delivery=True)

    def test_default_cycle_defers_injected_sender_without_policy_loader(self):
        for now in ('2026-11-07T16:59:59Z', '2026-11-07T17:00:00Z'):
            result = runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding,
                now=now, providers=self.providers)
        self.assertGreater(result['generated'], 0)
        self.assertEqual([], self.sent)
        self.assertEqual([], result['deliveries'])

    def test_direct_drain_without_final_policy_check_never_reserves_or_sends(self):
        self.prepare()
        runtime.drain(self.store, self.root, self.binding, self.providers)
        self.assert_unsent()

    def test_review_port_without_policy_loader_refuses_readmission(self):
        port = runtime.ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: '2026-11-07T17:00:00Z')
        with self.assertRaisesRegex(ValidationError, 'REPORT_POLICY_CHECK_REQUIRED'):
            port._readmit()

    def test_explicit_cycle_delivery_rechecks_off_policy(self):
        self.prepare()
        current = deepcopy(self.root)
        calls = []
        def load():
            calls.append(True)
            return current
        current['reporting'] = dict(mode='off', bindings=[])
        runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding,
            now='2026-11-07T18:00:00Z', providers=self.providers, defer_delivery=False,
            load_operating=load)
        self.assertTrue(calls)
        self.assert_unsent()

    def test_provider_policy_reload_follows_destination_verification(self):
        self.prepare()
        current = deepcopy(self.root)
        verified = []
        def send_reserved(intent, attempt, reserve):
            verified.append(intent['binding']['platform'])
            current['reporting'] = dict(mode='off', bindings=[])
            reserve()
            self.fail('OFF policy reached POST')
        for provider in self.providers.values():
            provider.send_reserved = send_reserved
            provider.load_policy = lambda: current
        runtime.drain(self.store, self.root, self.binding, self.providers)
        self.assertEqual({'teams', 'slack'}, set(verified))
        self.assert_unsent()

    def test_explicit_cycle_with_unchanged_policy_delivers_once(self):
        for now in ('2026-11-07T16:59:59Z', '2026-11-07T17:00:00Z', '2026-11-07T17:00:00Z'):
            runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding,
                now=now, providers=self.providers, defer_delivery=False, load_operating=lambda: self.root)
        states = runtime.reporting_status(self.store, self.root, self.binding)['deliveries']
        self.assertEqual(4, len(self.sent))
        self.assertTrue(all(len(d['attempts']) == 1 for d in states))
        self.assertEqual({'DELIVERED', 'UNKNOWN'}, {d['status'] for d in states})

    def assert_unsent(self):
        self.assertEqual([], self.sent)
        states = runtime.reporting_status(self.store, self.root, self.binding)['deliveries']
        self.assertTrue(states)
        self.assertTrue(all(len(d['attempts']) == 0 and d['status'] != 'DELIVERED' for d in states))


if __name__ == '__main__':
    unittest.main()
