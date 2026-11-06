"""Calendar shares the clone-bound owner boundary and transaction recovery."""
from copy import deepcopy
import json
import shutil
import unittest

import test_awf242_owner_admission as fixture
from agentic import operating as op


class CalendarBoundaryTests(unittest.TestCase):
    setUpClass = classmethod(fixture.OwnerAdmissionTests.setUpClass.__func__)
    setUp = fixture.OwnerAdmissionTests.setUp
    git = fixture.OwnerAdmissionTests.git
    cli = fixture.OwnerAdmissionTests.cli
    script = fixture.OwnerAdmissionTests.script
    pending = fixture.OwnerAdmissionTests.pending
    confirm = fixture.OwnerAdmissionTests.confirm
    recover = fixture.OwnerAdmissionTests.recover
    refresh = fixture.OwnerAdmissionTests.refresh
    apply = fixture.OwnerAdmissionTests.apply
    events = fixture.OwnerAdmissionTests.events
    unchanged = fixture.OwnerAdmissionTests.unchanged
    refused = fixture.OwnerAdmissionTests.refused
    save_governance = fixture.OwnerAdmissionTests.save_governance
    forge_config_event = fixture.OwnerAdmissionTests.forge_config_event

    def assert_load_refused(self):
        result = self.cli('show', '--json')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(result.stdout)['status'])
        result = self.script('', code=(
            'from agentic import operating as op\n'
            f'root=Path({str(self.root)!r})\n'
            'op.read_operating(root,op.load_yaml((root/op.GOVERNANCE).read_bytes()))\n'))
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('OWNER_SETTINGS_REFUSED', result.stderr)

    def test_hand_edited_calendar_cannot_take_effect(self):
        value = json.loads(self.before)
        value['operating_calendar'] = {'eod': {'time': '18:00'}}
        self.forge_config_event(value)
        self.assert_load_refused()
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.fixture.base / 'protected').exists())

    def test_confirmed_calendar_edits_and_removal_refuse(self):
        self.apply('operating_calendar.eod.time=18:00')
        confirmed = json.loads(self.before)
        for change in ('edit', 'defaults', 'remove'):
            with self.subTest(change=change):
                value = deepcopy(confirmed)
                if change == 'remove':
                    value.pop('operating_calendar')
                elif change == 'defaults':
                    value['operating_calendar'] = op.effective_calendar({})
                else:
                    value['operating_calendar']['eod']['time'] = '19:00'
                self.forge_config_event(value)
                self.assert_load_refused()

    def test_default_calendar_without_git_or_owner_profile_loads(self):
        shutil.rmtree(self.root / '.git')
        for calendar in (None, {}, op.effective_calendar({})):
            with self.subTest(calendar=calendar):
                value = json.loads(self.before)
                if calendar is None:
                    value.pop('operating_calendar', None)
                else:
                    value['operating_calendar'] = calendar
                self.forge_config_event(value)
                result = self.cli('show', '--json', owner_error='AssertionError("owner profile consulted without Git")')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual('ACCEPTED', json.loads(result.stdout)['status'])
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.fixture.base / 'protected').exists())

    def test_mixed_repair_displays_external_edits_in_both_domains(self):
        self.apply('budgeting.accounting_enabled=true', 'operating_calendar.eod.time=18:00')
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = False
        value['operating_calendar']['eod']['time'] = '19:00'
        self.forge_config_event(value)
        self.refresh()
        result = self.cli('set', '--instruction', 'repair both', '--set', 'operating_calendar.work_mode=continuous', '--json')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        changes = {c['path']: c for c in report['confirmation_changes']}
        self.assertEqual(dict(path='budgeting.accounting_enabled', before=True, after=False), changes['budgeting.accounting_enabled'])
        self.assertIn('operating_calendar.eod', changes)
        result = self.confirm(report['digest'])
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for phrase in ('budgeting.accounting_enabled', 'operating_calendar.eod', '19:00'):
            self.assertIn(phrase, result.stdout)
        self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json').stdout)['status'])

    def test_mixed_crashes_before_wal_allow_reproposal(self):
        for phase in ('before_consume', 'after_consume_write', 'after_consume', 'before_register', 'after_register', 'before_journal'):
            with self.subTest(phase=phase):
                assignments = ('budgeting.max_tokens_per_ticket=' + str(123 + len(self.events())),
                               'operating_calendar.eod.time=' + str(18 + len(self.events()) % 5) + ':00')
                digest = self.pending(*assignments)
                result = self.confirm(digest, crash=phase)
                self.assertEqual(72 if phase == 'before_register' else 71, result.returncode, result.stdout + result.stderr)
                self.unchanged()
                self.apply(*assignments)
                self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json').stdout)['status'])
                self.assertEqual(2, self.confirm(digest).returncode)

    def test_mixed_crashes_after_wal_recover_exactly_once(self):
        for phase in ('journal_published', 'after_journal', 'config_published', 'after_config', 'after_audit',
                      'before_readback', 'after_readback', 'before_complete', 'after_complete', 'after_event', 'after_unlink'):
            with self.subTest(phase=phase):
                assignments = ('budgeting.max_tokens_per_ticket=' + str(123 + len(self.events())),
                               'operating_calendar.eod.time=' + str(18 + len(self.events()) % 5) + ':00')
                digest = self.pending(*assignments)
                result = self.confirm(digest, crash=phase)
                self.assertEqual(71, result.returncode, result.stdout + result.stderr)
                calls = self.calls.read_bytes()
                if phase != 'after_unlink':
                    journal = json.loads((self.root / op.JOURNAL).read_bytes())
                    result = self.recover(journal)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    count = len(self.events())
                    result = self.recover(journal)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertEqual(count, len(self.events()))
                self.assertEqual(calls, self.calls.read_bytes())
                self.refresh()
                self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json').stdout)['status'])
                self.refused(digest, 'PROPOSAL_ALREADY_CONSUMED')

    def test_calendar_confirmation_is_clone_bound(self):
        self.apply('operating_calendar.eod.time=18:00')
        clone = self.fixture.base / 'copy'
        shutil.copytree(self.root, clone)
        result = self.cli('show', '--json', root=clone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(result.stdout)['status'])
        result = self.cli('set', '--instruction', 'confirm copied calendar',
                          '--set', 'operating_calendar.eod.time=18:00', '--json', root=clone)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        digest = json.loads(result.stdout)['digest']
        result = self.confirm(digest, root=clone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for root in (self.root, clone):
            result = self.cli('show', '--json', root=root)
            self.assertEqual('ACCEPTED', json.loads(result.stdout)['status'])

    def test_later_calendar_confirmation_supersedes_registered_mixed_journal(self):
        result = self.confirm(self.pending('budgeting.max_tokens_per_ticket=123',
                                          'operating_calendar.eod.time=18:00'), crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        journal = json.loads((self.root / op.JOURNAL).read_bytes())
        (self.root / op.JOURNAL).unlink()
        self.apply('operating_calendar.eod.time=19:00')
        accepted, events = self.before, self.events()
        result = self.recover(journal)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('supersedes', result.stdout)
        self.assertEqual(accepted, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(events, self.events())
