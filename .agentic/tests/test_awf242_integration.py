"""Mode integration regressions; all host observations below are fixtures."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import os
import uuid
import importlib.util
import io
import sqlite3
from contextlib import redirect_stdout, closing

import source_only  # noqa: F401
from agentic import ValidationError
from agentic.budgeting import fresh_settings
from agentic import operating as op
from agentic.lifecycle import transition
from agentic.model_routing import RoutingLedger, policy_from_config
from test_operating import governance
from test_model_routing import request, capabilities, outcome
from test_awf242_budgeting import FixtureHost
from agentic.budget_enrollment import EnrollmentRegistry
from agentic.budgeting import BudgetLedger, settings_hash
from agentic.store import Store as StateStore


class ModeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'host-state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])

    def snapshot(self):
        return op.validate_operating(self.value, self.gov)

    def test_fresh_defaults_are_explicit_and_legacy_is_not_reinterpreted(self):
        self.assertEqual(self.value['budgeting'], fresh_settings())
        old = deepcopy(self.value)
        old.pop('budgeting')
        self.assertNotIn('budgeting', op.validate_operating(old, self.gov).config)

    def test_off_routing_needs_no_usage_and_security_mismatch_still_quarantines(self):
        self.value['budgeting'] = fresh_settings()
        ledger = RoutingLedger(self.root / 'routing.sqlite')
        reserved = ledger.reserve('project', policy_from_config(self.gov),
            request(reservation_tokens=None, stream='A'), capabilities(), operating=self.snapshot(), governance=self.gov)
        self.assertFalse(reserved['budget_required'])
        self.assertFalse(reserved['requires_host_limit_enforcement'])
        observed = outcome(reserved)
        observed.pop('actual_tokens')
        self.assertEqual(ledger.settle(reserved['run_id'], observed)['status'], 'settled')
        second = ledger.reserve('project', policy_from_config(self.gov),
            request(reservation_tokens=None, stream='A'), capabilities(), operating=self.snapshot(), governance=self.gov)
        observed = outcome(second, actual_model='wrong')
        self.assertEqual(ledger.settle(second['run_id'], observed)['status'], 'quarantined')

    def test_lifecycle_off_omits_only_budget_guard(self):
        facts = dict(dispatch_permitted=True, lease_current=True, ownership_current=True)
        self.assertEqual(transition('READY', 'DISPATCH_ACCEPTED', facts, budgeting=fresh_settings()), 'DISPATCHED')
        with self.assertRaises(ValidationError):
            transition('READY', 'DISPATCH_ACCEPTED', {**facts, 'lease_current': False}, budgeting=fresh_settings())
        with self.assertRaises(ValidationError):
            transition('READY', 'DISPATCH_ACCEPTED', facts)

    def enrollment(self, run_id, ticket='EX-10'):
        host = FixtureHost()
        registry = EnrollmentRegistry(self.root / 'enrollment.sqlite', host)
        binding = dict(project_id=self.gov['project']['id'], ticket_id=ticket,
                       run_id=run_id, dispatch_id='dispatch-' + run_id, session_id='session-' + run_id, role='worker')
        host.enrollment = binding
        registry.enroll(binding)
        return registry, binding

    def test_canonical_facade_and_routing_share_atomic_lifetime_total(self):
        settings = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True,
                        max_tokens_per_ticket=150, max_tokens_per_project=150)
        self.value['budgeting'] = settings
        registry, binding = self.enrollment(str(uuid.uuid4()))
        facade = BudgetLedger(self.root / 'routing.sqlite', registry)
        facade.reserve(binding, settings, 100)
        facade.settle(binding, 100, 'observed-1')
        registry, second = self.enrollment(str(uuid.uuid4()), ticket='EX-20')
        with self.assertRaisesRegex(ValidationError, 'Lifetime project'):
            RoutingLedger(facade.path).reserve(binding['project_id'], policy_from_config(self.gov),
                request(ticket_id='EX-20', reservation_tokens=100, stream='A'), capabilities(),
                operating=self.snapshot(), governance=self.gov, budget_registry=registry, budget_binding=second)
        self.assertEqual(facade.summary(binding['project_id'])['known_tokens'], 100)
        with facade._routing()._transaction() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='budget_charges'").fetchone())

    def test_stage_one_history_is_bridged_once_without_losing_unknown_or_running_charges(self):
        registry, binding = self.enrollment(str(uuid.uuid4()))
        registry, running = self.enrollment(str(uuid.uuid4()))
        path = self.root / 'routing.sqlite'
        enabled = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute('''CREATE TABLE budget_charges(
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
                binding TEXT NOT NULL, settings TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
                status TEXT NOT NULL, actual_tokens INTEGER, observation_id TEXT UNIQUE,
                budget_overrun INTEGER NOT NULL DEFAULT 0)''')
            db.execute('INSERT INTO budget_charges VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (binding['run_id'], binding['project_id'], binding['ticket_id'],
                        json.dumps(binding), json.dumps(enabled), 80, 'UNKNOWN', None, 'legacy-observation', 0))
            db.execute('INSERT INTO budget_charges VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (running['run_id'], running['project_id'], running['ticket_id'],
                        json.dumps(running), json.dumps(enabled), 60, 'RESERVED', None, None, 0))
            original = db.execute('SELECT * FROM budget_charges').fetchall()
            db.commit()
        facade = BudgetLedger(path, registry)
        self.assertEqual(facade.summary(binding['project_id'])['unknown_runs'], 1)
        self.assertEqual(facade.summary(binding['project_id'])['unresolved_reserved_tokens'], 80)
        self.assertEqual(facade.summary(binding['project_id'])['reserved_tokens'], 60)
        facade.reserve(None, fresh_settings(), None)
        facade.settle(running, 30, 'late-after-off')
        self.assertEqual(facade.summary(binding['project_id'])['known_tokens'], 30)
        registry, second = self.enrollment(str(uuid.uuid4()))
        with self.assertRaisesRegex(ValidationError, 'UNKNOWN'):
            facade.reserve(second, enabled, 1)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT * FROM budget_charges').fetchall(), original)
            self.assertEqual(db.execute('SELECT count(*) FROM model_runs').fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM budget_charges')

    def test_accounting_only_ignores_caps_and_does_not_quarantine_overrun(self):
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
                                      max_tokens_per_ticket=1, max_tokens_per_project=1)
        registry, binding = self.enrollment(str(uuid.uuid4()))
        ledger = RoutingLedger(self.root / 'routing.sqlite')
        reserved = ledger.reserve(binding['project_id'], policy_from_config(self.gov),
            request(stream='A'), capabilities(), operating=self.snapshot(), governance=self.gov,
            budget_registry=registry, budget_binding=binding)
        self.assertFalse(reserved['requires_host_limit_enforcement'])
        self.assertEqual(reserved['run_cap_signal'], 'OFF')
        self.assertEqual(ledger.settle(reserved['run_id'], outcome(reserved, actual_tokens=5000,
            observation_id='accounting-overrun-usage'))['status'], 'settled')
        self.assertEqual(BudgetLedger(ledger.path, registry).summary(binding['project_id'])['known_tokens'], 5000)

    def test_accounting_without_estimate_is_unknown_until_observed_and_never_refused_for_quota(self):
        settings = dict(fresh_settings(), accounting_enabled=True)
        registry, binding = self.enrollment(str(uuid.uuid4()))
        facade = BudgetLedger(self.root / 'routing.sqlite', registry)
        facade.reserve(binding, settings, None)
        registry, second = self.enrollment(str(uuid.uuid4()))
        enabled = dict(settings, enforcement_enabled=True)
        with self.assertRaisesRegex(ValidationError, 'UNKNOWN'):
            facade.reserve(second, enabled, 10)
        facade.settle(binding, 100, 'measured')
        facade.reserve(second, enabled, 10)

    def test_budget_only_quarantine_does_not_block_off_but_security_still_does(self):
        registry, binding = self.enrollment(str(uuid.uuid4()))
        facade = BudgetLedger(self.root / 'routing.sqlite', registry)
        enabled = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        facade.reserve(binding, enabled, 10)
        facade.settle(binding, 100, 'overrun')
        ledger = RoutingLedger(facade.path)
        reserved = ledger.reserve(binding['project_id'], policy_from_config(self.gov), request(stream='A'),
                                  capabilities(), operating=self.snapshot(), governance=self.gov)
        self.assertFalse(reserved['budget_required'])
        ledger.settle(reserved['run_id'], outcome(reserved, actual_model='wrong'))
        with self.assertRaisesRegex(ValidationError, 'quarantined'):
            ledger.reserve(binding['project_id'], policy_from_config(self.gov), request(stream='A'),
                           capabilities(), operating=self.snapshot(), governance=self.gov)

    def test_off_preserves_bounded_recovery_and_human_retry_ceiling(self):
        self.gov['execution']['model_routing']['reconciliation'].update(
            enabled=True, authorized_operator_ids=['fixture-owner'], max_reconciled_incident_retries_per_ticket=0)
        policy = policy_from_config(self.gov)
        ledger = RoutingLedger(self.root / 'routing.sqlite')
        first = ledger.reserve('project', policy, request(stream='A', reservation_tokens=None),
                               capabilities(), operating=self.snapshot(), governance=self.gov)
        ledger.reconcile('project', policy, first['run_id'], dict(
            reconciliation_id='close-first', operator_id='fixture-owner', authorization_ref='fixture-authority',
            reason='Host stopped orphan', evidence_ref='fixture-stop', remediation_ref='fixture-remediation',
            host_stopped=True, remediation_verified=True))
        blocked = ledger.reserve('project', policy, request(stream='A', phase='renamed', reservation_tokens=None),
                                 capabilities(), operating=self.snapshot(), governance=self.gov)
        self.assertEqual(blocked['code'], 'RECONCILED_INCIDENT_RETRY_LIMIT')
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 1
        blocked = ledger.reserve('project', policy_from_config(self.gov), request(stream='A'),
                                 capabilities(), operating=self.snapshot(), governance=self.gov)
        self.assertEqual(blocked['code'], 'RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION')

    def test_untrusted_budget_edit_and_invalid_mixed_change_leave_all_bytes_unchanged(self):
        self.prepare_operating()
        before = (self.root / op.CONFIG).read_bytes()
        with self.assertRaisesRegex(ValidationError, 'PR'):
            op.set_operating(self.root, self.gov, 'enable', ['budgeting.accounting_enabled=true'])
        with self.assertRaises(ValidationError):
            op.set_operating(self.root, self.gov, 'invalid mixed', ['budgeting.accounting_enabled=true', 'streams.count=999'])
        self.assertEqual((self.root / op.CONFIG).read_bytes(), before)
    def test_state_store_off_has_no_reservation_and_new_modes_use_canonical_ledger(self):
        project = self.gov['project']['id']
        store = StateStore(self.root / 'state.sqlite', project)
        run_id = str(uuid.uuid4())
        self.assertEqual(store.reserve_budget(run_id, 'EX-10', None, None, None, None,
                                             budgeting=fresh_settings()), 'OFF')
        self.assertEqual(store.settle_budget(run_id, None, None, budgeting=fresh_settings()), 'OFF')
        registry, binding = self.enrollment(run_id)
        facade = BudgetLedger(self.root / 'routing.sqlite', registry)
        settings = dict(fresh_settings(), accounting_enabled=True)
        self.assertEqual(store.reserve_budget(run_id, 'EX-10', None, 100, None, None,
            budgeting=settings, budget_ledger=facade, budget_binding=binding), 'RESERVED')
        with store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM reservations').fetchone()[0], 0)
        store.settle_budget(run_id, 1000, None, budget_ledger=facade, budget_binding=binding, observation_id='observed')
        self.assertEqual(facade.summary(project)['known_tokens'], 1000)

    def prepare_operating(self):
        import shutil
        import subprocess
        subprocess.run([shutil.which('git'), 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root / op.GOVERNANCE).parent.mkdir(parents=True, exist_ok=True)
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        (self.root / op.CONFIG).write_bytes(op._json(self.value))
        op.initialize_operating(self.root, self.gov)
    def test_cli_off_proposal_without_ledger_is_not_a_dispatch_authorization(self):
        self.prepare_operating()
        spec = importlib.util.spec_from_file_location('awf242_route_cli', Path(__file__).resolve().parents[1] / 'scripts/route_model.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        req = self.root / 'request.json'
        caps = self.root / 'capabilities.json'
        req.write_text(json.dumps(request(stream='A', reservation_tokens=None)))
        caps.write_text(json.dumps(capabilities()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(['reserve', '--config', str(self.root / op.GOVERNANCE),
                             '--project-root', str(self.root), '--request', str(req), '--capabilities', str(caps)])
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['budget_required'])
        self.assertFalse(result['admitted'])
        self.assertFalse(result['dispatch_performed'])
