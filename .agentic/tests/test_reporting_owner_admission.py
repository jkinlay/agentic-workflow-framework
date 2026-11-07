"""Reporting uses the installed CLI's single owner-confirmation transaction."""
from contextlib import closing
import json
import sqlite3
import unittest

import test_awf242_owner_admission as fixture
from agentic import operating as op


class ReportingOwnerAdmissionTests(unittest.TestCase):
    setUpClass = classmethod(fixture.OwnerAdmissionTests.setUpClass.__func__)
    setUp = fixture.OwnerAdmissionTests.setUp
    save_governance = fixture.OwnerAdmissionTests.save_governance
    events = fixture.OwnerAdmissionTests.events
    cli = fixture.OwnerAdmissionTests.cli
    pending = fixture.OwnerAdmissionTests.pending
    confirm = fixture.OwnerAdmissionTests.confirm
    unchanged = fixture.OwnerAdmissionTests.unchanged
    refused = fixture.OwnerAdmissionTests.refused
    recover = fixture.OwnerAdmissionTests.recover
    git = fixture.OwnerAdmissionTests.git

    def bindings(self):
        return [dict(project=self.gov['project']['id'], repository=str(self.gov['github']['repository_id']),
                     scope_revision='accepted-scope', platform=platform, destination='approved-' + platform,
                     revision='1', secret_ref='env:REPORT_TEST_SECRET') for platform in ('teams', 'slack')]

    def assignments(self, mode='both', bindings=None):
        return ('reporting.mode=' + mode, 'reporting.bindings=' + json.dumps(
            self.bindings() if bindings is None else bindings))

    def counts(self):
        database = next((self.fixture.base / 'protected').rglob('owner-admission.sqlite3'))
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual({'owner_proposals', 'owner_confirmations', 'owner_transactions', 'owner_applied'}, {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")})
            return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                         for table in ('owner_proposals', 'owner_confirmations'))

    def refresh(self):
        self.before, self.audit_before = (self.root / op.CONFIG).read_bytes(), self.events()

    def test_modes_stay_pending_then_persist_across_fresh_process(self):
        initial = json.loads(self.before)
        for index, mode in enumerate(('teams', 'slack', 'both', 'off'), 1):
            with self.subTest(mode=mode):
                digest = self.pending(*self.assignments(mode))
                self.assertEqual((index, index - 1), self.counts())
                self.assertEqual(0, self.confirm(digest).returncode)
                shown = self.cli('show', '--json')
                self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
                result = json.loads(shown.stdout)
                config = result['configuration']
                self.assertEqual(dict(mode=mode, bindings=self.bindings()), config['reporting'])
                self.assertEqual(initial['budgeting'], config['budgeting'])
                self.assertEqual(initial.get('operating_calendar'), config.get('operating_calendar'))
                self.assertEqual(initial['streams'], config['streams'])
                self.assertEqual(self.gov, json.loads(self.govpath.read_bytes()))
                self.assertNotIn('chat', config)
                self.assertFalse(result['execution_authority'])
                self.assertIn('Reporting (effective settings)', result['table'])
                self.assertEqual((index, index), self.counts())
                self.assertEqual(['owner-admission.sqlite3'],
                                 [p.name for p in (self.fixture.base / 'protected').rglob('*.sqlite3')])
                self.refresh()
                self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')

    def test_off_unchanged_and_rebinding_require_admission(self):
        digest = self.pending('reporting.mode=off')
        self.assertEqual(0, self.confirm(digest).returncode)
        self.refresh()
        digest = self.pending(*self.assignments())
        self.assertEqual(0, self.confirm(digest).returncode)
        self.refresh()
        replacement = self.bindings()
        replacement[0].update(destination='replacement-teams', revision='2')
        digest = self.pending('reporting.bindings=' + json.dumps(replacement))
        self.assertEqual((3, 2), self.counts())
        self.assertEqual(0, self.confirm(digest).returncode)
        self.assertEqual(replacement, json.loads(self.cli('show', '--json').stdout)['configuration']['reporting']['bindings'])
        self.refresh()
        self.pending('reporting.mode=off')
        self.assertEqual((4, 3), self.counts())

    def test_wrong_owner_and_foreign_bindings_refuse_before_secret_resolution(self):
        digest = self.pending(*self.assignments())
        self.identity.write_text(json.dumps(dict(id=9, login='owner', type='User')))
        self.refused(digest, 'OWNER_IDENTITY_REFUSED')
        calls = self.calls.read_bytes()
        invalid = [[], [self.bindings()[0]]]
        for field in ('project', 'repository'):
            foreign = self.bindings()
            foreign[0][field] = 'foreign'
            invalid.append(foreign)
        invalid.append([self.bindings()[0], self.bindings()[0]])
        bad_secret = self.bindings()
        bad_secret[0]['secret_ref'] = 'literal-secret'
        invalid.append(bad_secret)
        # This environment entry must never be resolved or echoed by set/confirm.
        self.env['REPORT_TEST_SECRET'] = 'synthetic-must-stay-unread'
        for bindings in invalid:
            with self.subTest(bindings=bindings):
                result = self.cli('set', '--instruction', 'Invalid reporting binding',
                                  *[v for a in self.assignments(bindings=bindings) for v in ('--set', a)], '--json')
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertNotIn(self.env['REPORT_TEST_SECRET'], result.stdout + result.stderr)
                self.assertEqual((1, 0), self.counts())
                self.assertEqual(calls, self.calls.read_bytes())
                self.unchanged()

    def test_mixed_reporting_budget_calendar_consumes_one_confirmation(self):
        digest = self.pending(*self.assignments(), 'budgeting.accounting_enabled=true',
                              'budgeting.enforcement_enabled=true', 'operating_calendar.eod.time=18:00')
        self.assertEqual((1, 0), self.counts())
        result = self.confirm(digest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        config = json.loads(self.cli('show', '--json').stdout)['configuration']
        self.assertEqual('both', config['reporting']['mode'])
        self.assertTrue(config['budgeting']['accounting_enabled'])
        self.assertTrue(config['budgeting']['enforcement_enabled'])
        self.assertEqual('18:00', config['operating_calendar']['eod']['time'])
        self.assertEqual((1, 1), self.counts())
        new_events = [json.loads(raw) for raw in self.events() if raw not in self.audit_before]
        self.assertEqual(1, len(new_events))
        self.assertEqual(digest, new_events[0]['owner_admission']['digest'])
        self.assertNotIn('reporting_owner_observation', new_events[0])

    def test_invalid_mixed_changes_propose_and_consume_nothing(self):
        digest = self.pending(*self.assignments())
        for invalid in ('budgeting.max_tokens_per_ticket=0', 'operating_calendar.eod.time=25:00', 'streams.count=1001'):
            with self.subTest(invalid=invalid):
                result = self.cli('set', '--instruction', 'Invalid mixed reporting',
                    *[v for a in (*self.assignments(), invalid) for v in ('--set', a)], '--json')
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.unchanged()
                self.assertEqual((1, 0), self.counts())
        self.assertEqual(0, self.confirm(digest).returncode)

    def test_confirmed_reporting_crash_recovers_once(self):
        digest = self.pending(*self.assignments())
        self.assertEqual(71, self.confirm(digest, crash='after_journal').returncode)
        journal = json.loads((self.root / op.JOURNAL).read_bytes())
        calls = self.calls.read_bytes()
        self.assertEqual(0, self.recover(journal).returncode)
        self.assertEqual(calls, self.calls.read_bytes())
        self.assertEqual(json.loads(journal['after']), json.loads(self.cli('show', '--json').stdout)['configuration'])
        self.assertEqual((1, 1), self.counts())

    def test_historical_reporting_observation_still_validates(self):
        # Legacy evidence remains schema-readable without turning it into a
        # newly authenticated confirmation or rewriting retained audit bytes.
        event = json.loads(self.audit_before[-1])
        event['reporting_owner_observation'] = dict(observation_id='historical-owner-direction',
            actor_id='historical-owner', project_id=self.gov['project']['id'],
            prior_settings_hash=op.fingerprint('reporting-settings', dict(mode='off', bindings=[])),
            changes=dict(mode='both', bindings=self.bindings()))
        self.assertEqual([], op._schema('operating-change', event))
        raw = op._json(dict(format='awf-operating-transaction-1', before=self.before.decode(),
                           after=self.before.decode(), event=event))
        self.assertEqual(event, json.loads(raw)['event'])
        self.assertNotIn('owner_admission', event)

    def test_binding_refusal_never_calls_secret_resolver(self):
        # Execute workflow.py in a fresh process with a failing module-level
        # resolver; no test hook or resolver selection exists in production.
        import subprocess
        import sys
        entry = self.root / '.agentic/scripts/workflow.py'
        for field in ('project', 'repository'):
            foreign = self.bindings()
            foreign[0][field] = 'foreign'
            args = [str(entry), '--root', str(self.root), 'operating', 'set', '--instruction',
                    'Invalid destination', *[v for a in self.assignments(bindings=foreign) for v in ('--set', a)], '--json']
            wrapper = (f'import sys,runpy\nsys.path.insert(0,{str(self.root / ".agentic/lib")!r})\n'
                       'import agentic.reporting.platform as platform\n'
                       'def forbidden(ref): raise AssertionError("SECRET_RESOLVER_MUST_NOT_RUN")\n'
                       'platform.environment_secret=forbidden\n'
                       f'sys.argv={args!r}\nrunpy.run_path({str(entry)!r},run_name="__main__")\n')
            result = subprocess.run([sys.executable, '-B', '-I', '-c', wrapper], env=self.env,
                cwd=self.fixture.base, capture_output=True, text=True, timeout=45)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn('REPORT_SCOPE_BINDING_MISMATCH', result.stdout)
            self.assertNotIn('SECRET_RESOLVER_MUST_NOT_RUN', result.stdout + result.stderr)
            self.unchanged()
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.fixture.base / 'protected').exists())


if __name__ == '__main__':
    unittest.main()
