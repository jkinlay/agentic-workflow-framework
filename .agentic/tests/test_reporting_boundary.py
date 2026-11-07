"""Reporting inherits the final clone-bound owner transaction, through the CLI."""
from copy import deepcopy
import json
import shutil
import unittest

import test_reporting_owner_admission as reporting
import test_awf244_boundary as calendar
import test_awf242_owner_admission as owner
from agentic import operating as op


class ReportingBoundaryTests(unittest.TestCase):
    setUpClass = classmethod(owner.OwnerAdmissionTests.setUpClass.__func__)
    setUp = owner.OwnerAdmissionTests.setUp
    git = owner.OwnerAdmissionTests.git
    cli = owner.OwnerAdmissionTests.cli
    script = owner.OwnerAdmissionTests.script
    pending = owner.OwnerAdmissionTests.pending
    confirm = owner.OwnerAdmissionTests.confirm
    recover = owner.OwnerAdmissionTests.recover
    refresh = owner.OwnerAdmissionTests.refresh
    apply = owner.OwnerAdmissionTests.apply
    events = owner.OwnerAdmissionTests.events
    unchanged = owner.OwnerAdmissionTests.unchanged
    refused = owner.OwnerAdmissionTests.refused
    save_governance = owner.OwnerAdmissionTests.save_governance
    forge_config_event = owner.OwnerAdmissionTests.forge_config_event
    assert_load_refused = calendar.CalendarBoundaryTests.assert_load_refused
    bindings = reporting.ReportingOwnerAdmissionTests.bindings
    assignments = reporting.ReportingOwnerAdmissionTests.assignments

    def test_hand_edits_cannot_enable_reporting_or_bind_destinations(self):
        initial = json.loads(self.before)
        for mode in ('both', 'off'):
            with self.subTest(mode=mode):
                value = deepcopy(initial)
                value['reporting'] = dict(mode=mode, bindings=self.bindings())
                self.forge_config_event(value)
                self.assert_load_refused()
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.fixture.base / 'protected').exists())

    def test_every_confirmed_reporting_value_and_removal_is_protected(self):
        self.apply(*self.assignments())
        confirmed = json.loads(self.before)
        for field in ('mode', 'destination', 'revision', 'secret_ref', 'bindings', 'defaults', 'remove'):
            with self.subTest(field=field):
                value = deepcopy(confirmed)
                if field == 'remove':
                    value.pop('reporting')
                elif field == 'defaults':
                    value['reporting'] = dict(mode='off', bindings=[])
                elif field == 'mode':
                    value['reporting']['mode'] = 'off'
                elif field == 'bindings':
                    value['reporting']['bindings'].reverse()
                else:
                    value['reporting']['bindings'][0][field] = ('env:OTHER_SECRET' if field == 'secret_ref' else 'replacement')
                self.forge_config_event(value)
                self.assert_load_refused()

    def test_defaults_load_without_git_gh_or_owner_profile(self):
        shutil.rmtree(self.root / '.git')
        for reporting_value in (None, {}, dict(mode='off', bindings=[])):
            with self.subTest(reporting=reporting_value):
                value = json.loads(self.before)
                if reporting_value is None:
                    value.pop('reporting', None)
                else:
                    value['reporting'] = reporting_value
                self.forge_config_event(value)
                result = self.cli('show', '--json', owner_error='AssertionError("owner profile checked without Git")')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual('ACCEPTED', json.loads(result.stdout)['status'])
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.fixture.base / 'protected').exists())

    def test_one_confirmation_displays_all_external_changes_without_secrets(self):
        self.env['REPORT_TEST_SECRET'] = 'synthetic-never-resolve-this-value'
        self.apply(*self.assignments(), 'budgeting.accounting_enabled=true', 'operating_calendar.eod.time=18:00')
        value = json.loads(self.before)
        value['budgeting']['accounting_enabled'] = False
        value['operating_calendar']['eod']['time'] = '19:00'
        value['reporting']['bindings'][0]['destination'] = 'replacement'
        self.forge_config_event(value)
        self.refresh()
        result = self.cli('set', '--instruction', 'repair all domains', '--set', 'reporting.mode=both', '--json')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        changes = {c['path']: c for c in report['confirmation_changes']}
        self.assertEqual(dict(path='budgeting.accounting_enabled', before=True, after=False), changes['budgeting.accounting_enabled'])
        self.assertIn('operating_calendar.eod', changes)
        self.assertEqual('replacement', changes['reporting.bindings']['after'][0]['destination'])
        self.assertIn('reporting', report['protected_values'])
        result = self.confirm(report['digest'])
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for phrase in ('reporting.bindings', 'budgeting.accounting_enabled', 'operating_calendar.eod', 'replacement'):
            self.assertIn(phrase, result.stdout)
        self.assertNotIn(self.env['REPORT_TEST_SECRET'], result.stdout)
        database = next((self.fixture.base / 'protected').rglob('owner-admission.sqlite3'))
        self.assertNotIn(self.env['REPORT_TEST_SECRET'].encode(), database.read_bytes())
        self.assertNotIn(self.env['REPORT_TEST_SECRET'].encode(), b''.join(self.events()))
        self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json').stdout)['status'])

    def mixed_assignments(self):
        bindings = self.bindings()
        bindings[0]['revision'] = str(len(self.events()))
        return (*self.assignments(bindings=bindings), 'budgeting.max_tokens_per_ticket=' + str(123 + len(self.events())),
                'operating_calendar.eod.time=' + str(18 + len(self.events()) % 5) + ':00')

    def test_all_domains_before_wal_crashes_allow_fresh_confirmation(self):
        for phase in ('before_consume', 'after_consume_write', 'after_consume', 'before_register', 'after_register', 'before_journal'):
            with self.subTest(phase=phase):
                assignments = self.mixed_assignments()
                digest = self.pending(*assignments)
                result = self.confirm(digest, crash=phase)
                self.assertEqual(72 if phase == 'before_register' else 71, result.returncode, result.stdout + result.stderr)
                self.unchanged()
                self.apply(*assignments)
                self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json').stdout)['status'])
                self.assertEqual(2, self.confirm(digest).returncode)

    def test_all_domains_after_wal_crashes_recover_once_without_gh(self):
        for phase in ('journal_published', 'after_journal', 'config_published', 'after_config', 'after_audit',
                      'before_readback', 'after_readback', 'before_complete', 'after_complete', 'after_event', 'after_unlink'):
            with self.subTest(phase=phase):
                digest = self.pending(*self.mixed_assignments())
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

    def test_reporting_confirmation_is_clone_bound(self):
        self.apply(*self.assignments())
        clone = self.fixture.base / 'copy'
        shutil.copytree(self.root, clone)
        result = self.cli('show', '--json', root=clone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('OWNER_CONFIRMATION_REQUIRED', json.loads(result.stdout)['status'])
        result = self.cli('set', '--instruction', 'confirm copied reporting', '--set', 'reporting.mode=both', '--json', root=clone)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        result = self.confirm(json.loads(result.stdout)['digest'], root=clone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for root in (self.root, clone):
            self.assertEqual('ACCEPTED', json.loads(self.cli('show', '--json', root=root).stdout)['status'])

    def test_later_confirmation_supersedes_mixed_reporting_journal(self):
        result = self.confirm(self.pending(*self.mixed_assignments()), crash='after_journal')
        self.assertEqual(71, result.returncode, result.stdout + result.stderr)
        journal = json.loads((self.root / op.JOURNAL).read_bytes())
        (self.root / op.JOURNAL).unlink()
        self.apply('reporting.mode=off')
        accepted, events = self.before, self.events()
        result = self.recover(journal)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('supersedes', result.stdout)
        self.assertEqual(accepted, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(events, self.events())
