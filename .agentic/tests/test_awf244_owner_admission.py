"""Calendar admission through disposable installed workflow subprocesses."""
from contextlib import closing
import json
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
