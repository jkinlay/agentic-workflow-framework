"""Calendar PR regressions use real temporary Git repositories, offline."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError, operating as op
from agentic.canonical import fingerprint
from agentic.operating_calendar import effective_calendar, next_review_presentation
from agentic.protected_settings import audit_matches, unprotected_hash
import test_awf242_settings_pr as budget_fixture


class CalendarPRTests(budget_fixture.SettingsPRTests):
    def calendar_value(self, enabled):
        value = deepcopy(self.value)
        value['operating_calendar']['review_batching']['enabled'] = enabled
        return value

    def held(self):
        snapshot = op.read_operating(self.root, self.gov)
        return next_review_presentation('2026-11-07T10:00:00Z', effective_calendar(snapshot.config))['held']

    def test_calendar_working_and_pr_edits_do_not_change_batching_in_either_direction(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.publish(self.calendar_value(enabled))
                self.write(self.calendar_value(not enabled))
                self.assertEqual(enabled, self.held())
                self.git('checkout', '-B', 'codex/calendar-' + str(enabled))
                self.git('add', op.CONFIG)
                self.git('commit', '-qm', 'Proposed calendar')
                self.assertEqual(enabled, self.held())
                commit = self.git('rev-parse', 'HEAD')
                self.git('update-ref', 'refs/remotes/origin/main', commit)
                self.assertEqual(not enabled, self.held())
                self.assertEqual(commit, op.read_operating(self.root, self.gov).provenance['protected_settings']['commit'])

    def test_calendar_set_refuses_pr_without_any_write(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValidationError, 'PR'):
            op.set_operating(self.root, self.gov, 'disable batching', ['operating_calendar.review_batching.enabled=false'])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_calendar_malformed_default_refuses_instead_of_releasing_hold(self):
        for invalid in (None, [], {'review_batching': {'enabled': 'false'}}, {'timezone': 'Bad/Zone'}):
            with self.subTest(invalid=invalid):
                value = deepcopy(self.value)
                value['operating_calendar'] = invalid
                self.publish(value)
                self.write(self.calendar_value(False))
                with self.assertRaisesRegex(ValidationError, 'default.branch'):
                    self.held()

    def test_calendar_absent_default_retains_legacy_defaults(self):
        value = deepcopy(self.value)
        value.pop('operating_calendar')
        self.publish(value)
        self.write(self.calendar_value(False))
        snapshot = op.read_operating(self.root, self.gov)
        self.assertNotIn('operating_calendar', snapshot.config)
        self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))
        self.assertTrue(self.held())

    def test_calendar_show_and_proposed_validation(self):
        accepted = self.calendar_value(False)
        self.publish(accepted)
        self.write(self.value)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, op.main(['--root', str(self.root), 'show', '--json']))
        report = json.loads(output.getvalue())
        self.assertFalse(report['effective_operating_calendar']['review_batching']['enabled'])
        self.assertTrue(any(x['path'] == 'operating_calendar' for x in report['protected_settings']['differences']))
        proposed = self.root / 'proposed.yaml'
        proposed.write_bytes(op._json(accepted))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, op.main(['--root', str(self.root), 'validate', '--file', str(proposed)]))
        accepted['operating_calendar']['review_batching']['cutoff_time'] = '06:00'
        proposed.write_bytes(op._json(accepted))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(2, op.main(['--root', str(self.root), 'validate', '--file', str(proposed)]))

    def test_pre_calendar_protection_and_195_audits_still_verify(self):
        legacy = deepcopy(self.value)
        legacy.pop('budgeting')
        legacy.pop('operating_calendar')
        old_raw = {'after_hash': fingerprint('operating', legacy)}
        self.assertTrue(audit_matches(self.value, old_raw))
        old_projection = {'after_hash': fingerprint('operating', self.value),
            'unprotected_after_hash': fingerprint('operating-unprotected', {k: v for k, v in self.value.items() if k != 'budgeting'})}
        self.assertTrue(audit_matches(self.value, old_projection))
        edited = self.calendar_value(False)
        self.assertEqual(unprotected_hash(self.value), unprotected_hash(edited))
        self.assertTrue(audit_matches(edited, old_projection))
        edited['streams']['count'] = 1
        self.assertFalse(audit_matches(edited, old_projection))

    def test_historical_audit_records_bridge_next_unprotected_change(self):
        for projection in (False, True):
            with self.subTest(projection=projection):
                # Each history starts in a separate real repository.
                from settings_pr_fixtures import publish_fixture_settings
                root = self.root / ('projection' if projection else 'legacy195')
                value = deepcopy(self.value)
                if not projection:
                    value.pop('budgeting')
                    value.pop('operating_calendar')
                publish_fixture_settings(self, root, self.gov, value)
                op.initialize_operating(root, self.gov)
                record = next((root / op.STATE / 'changes').glob('*.json'))
                event = json.loads(record.read_bytes())
                if projection:
                    event['unprotected_after_hash'] = fingerprint('operating-unprotected',
                        {k: v for k, v in value.items() if k != 'budgeting'})
                else:
                    event.pop('unprotected_before_hash', None)
                    event.pop('unprotected_after_hash', None)
                record.write_bytes(op._json(event))
                # A default-branch policy update must preserve old local audit.
                accepted = deepcopy(value)
                accepted['operating_calendar'] = effective_calendar({})
                accepted['operating_calendar']['eod']['time'] = '18:00'
                publish_fixture_settings(self, root, self.gov, accepted)
                working = deepcopy(self.value)
                working['operating_calendar']['review_batching']['enabled'] = False
                (root / op.CONFIG).write_bytes(op._json(working))
                self.assertEqual('18:00', effective_calendar(op.read_operating(root, self.gov).config)['eod']['time'])
                changed = op.set_operating(root, self.gov, 'two streams', ['streams.count=2'])
                self.assertEqual(2, changed.count)
                self.assertEqual('18:00', effective_calendar(changed.config)['eod']['time'])
                self.assertEqual(changed, op.read_operating(root, self.gov))

    def test_nonrepository_absent_empty_and_full_calendars_retain_default_behavior(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='awf-calendar-defaults-') as raw:
            root = Path(raw)
            (root / op.GOVERNANCE).parent.mkdir()
            (root / op.GOVERNANCE).write_bytes(op._json(self.gov))
            for calendar in (None, {}, effective_calendar({})):
                with self.subTest(calendar=calendar):
                    value = deepcopy(self.value)
                    if calendar is None:
                        value.pop('operating_calendar')
                    else:
                        value['operating_calendar'] = calendar
                    (root / op.CONFIG).write_bytes(op._json(value))
                    with patch('agentic.protected_settings._git', side_effect=AssertionError('Git called')), \
                            patch('agentic.git_isolation.native_git_executable', side_effect=AssertionError('Git admitted')):
                        snapshot = op.read_operating(root, self.gov)
                    self.assertEqual(value, snapshot.config)
                    self.assertEqual(effective_calendar({}), effective_calendar(snapshot.config))


if __name__ == '__main__':
    unittest.main()
