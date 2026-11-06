"""Calendar admission through disposable installed workflow subprocesses."""
from contextlib import closing
import json
from copy import deepcopy
import sqlite3
import unittest

import test_awf242_owner_admission as fixture
from agentic import operating as op


class CalendarOwnerAdmissionTests(unittest.TestCase):
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

    def proposal(self, *extra, **kwargs):
        return self.pending('operating_calendar.eod.time=18:00', *extra, **kwargs)

    def counts(self):
        database = next((self.fixture.base / 'protected').rglob('owner-admission.sqlite3'))
        with closing(sqlite3.connect(database)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual({'owner_proposals', 'owner_confirmations'}, tables)
            return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                         for table in ('owner_proposals', 'owner_confirmations'))

    def test_calendar_pending_has_no_budget_or_operating_side_effects(self):
        self.proposal()
        self.assertFalse(self.calls.exists())
        self.assertEqual((1, 0), self.counts())
        self.assertEqual(['owner-admission.sqlite3'],
                         [p.name for p in (self.fixture.base / 'protected').rglob('*.sqlite3')])

    def test_calendar_confirmation_persists_fresh_process_and_refuses_replay(self):
        digest = self.proposal()
        result = self.confirm(digest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        shown = self.cli('show', '--json')
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        config = json.loads(shown.stdout)['configuration']
        self.assertEqual('18:00', config['operating_calendar']['eod']['time'])
        self.assertEqual(json.loads(self.before)['budgeting'], config['budgeting'])
        self.assertEqual((1, 1), self.counts())
        self.assertEqual(['owner-admission.sqlite3'],
                         [p.name for p in (self.fixture.base / 'protected').rglob('*.sqlite3')])
        audit = json.loads(self.events()[-1]) if len(self.events()) == 1 else next(
            json.loads(raw) for raw in self.events() if 'owner_admission' in json.loads(raw))
        self.assertEqual(digest, audit['owner_admission']['digest'])
        self.assertNotIn('budget_owner_observation', audit)
        self.assertNotIn('calendar_owner_observation', audit)
        self.before, self.audit_before = (self.root / op.CONFIG).read_bytes(), self.events()
        self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')

    def test_unconfirmed_calendar_journal_changes_nothing(self):
        journal = fixture.OwnerAdmissionTests.forged_journal(self)
        before = json.loads(self.before)
        after = deepcopy(before)
        after['operating_calendar'] = op.effective_calendar(before)
        after['operating_calendar']['eod']['time'] = '18:00'
        journal['after'] = op._json(after).decode()
        journal['event'].update(
            after_hash=op.fingerprint('operating', after),
            changes=[dict(path='operating_calendar', before=None, after=after['operating_calendar'])])
        raw = op._json(journal)
        (self.root / op.JOURNAL).write_bytes(raw)
        result = self.cli('recover', '--expected-journal-sha256', op.sha256(raw), '--json')
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_RECOVERY_REFUSED', result.stdout)
        self.assertEqual(self.before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(self.audit_before, self.events())
        self.assertEqual(raw, (self.root / op.JOURNAL).read_bytes())
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.fixture.base / 'protected').exists())

    def test_confirmed_calendar_crash_recovers_once_without_budget_state(self):
        for index, phase in enumerate(('after_journal', 'after_config', 'after_event')):
            with self.subTest(phase=phase):
                digest = self.pending('operating_calendar.eod.time=' + str(18 + index) + ':00')
                result = self.confirm(digest, crash=phase)
                self.assertEqual(71, result.returncode, result.stdout + result.stderr)
                journal = json.loads((self.root / op.JOURNAL).read_bytes())
                calls = self.calls.read_bytes()
                result = self.recover(journal)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(calls, self.calls.read_bytes())
                shown = self.cli('show', '--json')
                self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
                self.assertEqual(json.loads(journal['after']), json.loads(shown.stdout)['configuration'])
                self.assertEqual((index + 1, index + 1), self.counts())
                self.assertEqual(['owner-admission.sqlite3'],
                                 [p.name for p in (self.fixture.base / 'protected').rglob('*.sqlite3')])
                self.before, self.audit_before = (self.root / op.CONFIG).read_bytes(), self.events()
                self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')
                self.assertEqual(0, self.recover(journal).returncode)
                self.unchanged()

    def test_empty_owners_refuse_calendar(self):
        self.gov['merge_gate']['trusted_owner_ids'] = []
        self.save_governance()
        self.refused(self.proposal(), 'OWNER_GATE_NOT_CONFIGURED')
        self.assertEqual((1, 0), self.counts())

    def test_wrong_owner_refuses_calendar(self):
        digest = self.proposal()
        self.identity.write_text(json.dumps(dict(id=9, login='owner', type='User')))
        self.refused(digest, 'OWNER_IDENTITY_REFUSED')
        self.assertEqual((1, 0), self.counts())

    def test_expired_calendar_refuses(self):
        self.refused(self.proposal(clock='2000-01-01T00:00:00Z'), 'PROPOSAL_EXPIRED')
        self.assertEqual((1, 0), self.counts())

    def test_changed_governance_refuses_calendar(self):
        digest = self.proposal()
        self.gov['project']['name'] += ' changed'
        self.save_governance()
        self.refused(digest, 'PROPOSAL_BINDING_MISMATCH')
        self.assertEqual((1, 0), self.counts())

    def test_mixed_budget_calendar_consumes_one_confirmation(self):
        digest = self.proposal('budgeting.accounting_enabled=true', 'budgeting.enforcement_enabled=true')
        result = self.confirm(digest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        config = json.loads(self.cli('show', '--json').stdout)['configuration']
        self.assertTrue(config['budgeting']['accounting_enabled'])
        self.assertTrue(config['budgeting']['enforcement_enabled'])
        self.assertEqual('18:00', config['operating_calendar']['eod']['time'])
        self.assertEqual((1, 1), self.counts())

    def test_off_and_unchanged_protected_assignments_need_admission(self):
        for assignment in ('operating_calendar.review_batching.enabled=false',
                           'operating_calendar.work_mode=continuous',
                           'budgeting.accounting_enabled=false'):
            with self.subTest(assignment=assignment):
                digest = self.pending(assignment)
                self.assertEqual(0, self.counts()[1])
                self.assertEqual(64, len(digest))

    def test_invalid_mixed_changes_propose_and_consume_nothing(self):
        digest = self.proposal()
        for invalid in ('operating_calendar.review_batching.presentation_time=19:00',
                        'budgeting.max_tokens_per_ticket=0', 'streams.count=1001'):
            with self.subTest(invalid=invalid):
                result = self.cli('set', '--instruction', 'Invalid mixed change',
                                  '--set', 'operating_calendar.review_batching.cutoff_time=18:00',
                                  '--set', invalid, '--json')
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.unchanged()
                self.assertEqual((1, 0), self.counts())
        self.assertEqual(0, self.confirm(digest).returncode)


if __name__ == '__main__':
    unittest.main()
