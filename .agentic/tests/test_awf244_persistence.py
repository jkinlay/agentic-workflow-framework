"""Calendar persistence through offline, real default-branch PR fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError, operating as op
from agentic.canonical import fingerprint, sha256
from agentic.operating_calendar import effective_calendar
from settings_pr_fixtures import publish_fixture_settings
from test_operating import governance


class CalendarPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.value.pop('operating_calendar', None)
        publish_fixture_settings(self, self.root, self.gov, self.value)

    def set_calendar(self, *assignments):
        # Validate the proposed file, then simulate its observed default merge.
        value = json.loads((self.root / op.CONFIG).read_bytes())
        for assignment in assignments:
            path, text = assignment.split('=', 1)
            path = op._path(path)
            op._put(value, path, op._selection(path, text))
        op.validate_operating(value, self.gov)
        publish_fixture_settings(self, self.root, self.gov, value)
        return op.read_operating(self.root, self.gov)

    def test_legacy_read_show_and_bootstrap_preserve_bytes_and_hash(self):
        path = self.root / op.CONFIG
        path.write_bytes(b'\r\n' + path.read_bytes().replace(b'\n', b'\r\n') + b'\r\n')
        raw = (self.root / op.CONFIG).read_bytes()
        expected = fingerprint('operating', self.value)
        first = op.read_operating(self.root, self.gov)
        self.assertEqual(first.operating_hash, expected)
        self.assertNotIn('operating_calendar', first.config)
        table = op.render_operating(first, self.gov, state='UNVERIFIED')
        for text in ('Europe/London', '07:00', '17:00', 'continuous'):
            self.assertIn(text, table)
        op.initialize_operating(self.root, self.gov)
        self.assertEqual(raw, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(expected, op.read_operating(self.root, self.gov).operating_hash)

    def test_unrelated_update_does_not_materialize_calendar_defaults(self):
        updated = op.set_operating(self.root, self.gov, 'fixture two streams', ['streams.count=2'])
        self.assertNotIn('operating_calendar', updated.config)
        self.assertNotIn('operating_calendar', json.loads((self.root / op.CONFIG).read_text()))
        self.assertEqual(updated.config['budgeting'], self.value['budgeting'])

    def test_fresh_generated_defaults_and_partial_schema(self):
        from agentic.contracts import Contracts
        root = Path(__file__).resolve().parents[2]
        expected = effective_calendar({})
        for name in ('OPERATING_CONFIG.yaml', '.agentic/defaults/OPERATING_CONFIG.yaml',
                     '.agentic/examples/OPERATING_CONFIG.yaml'):
            with self.subTest(name=name):
                self.assertEqual(json.loads((root / name).read_text())['operating_calendar'], expected)
        value = deepcopy(self.value)
        value['operating_calendar'] = {'eod': {'time': '18:00'}}
        Contracts(op.SCHEMAS).validate('operating-config', value)
        self.assertEqual(op.validate_operating(value, self.gov).config, value)
        for bad in (None, {'eod': {'time': None}}, {'adapters': True},
                    {'review_batching': {'weekdays': [True]}}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Contracts(op.SCHEMAS).validate('operating-config', {**value, 'operating_calendar': bad})

    @source_only.skip_unless_source_repo(
        "Source configuration forms are not shipped in installed runtimes",
        '.agentic/templates/source-config/OPERATING_CONFIG.yaml')
    def test_fresh_source_template_calendar_defaults(self):
        root = Path(__file__).resolve().parents[2]
        name = '.agentic/templates/source-config/OPERATING_CONFIG.yaml'
        self.assertEqual(json.loads((root / name).read_text())['operating_calendar'], effective_calendar({}))

    def test_restart_persistence_and_unrelated_settings_preserved(self):
        before = deepcopy(self.value)
        updated = self.set_calendar('operating_calendar.timezone=America/New_York',
            'operating_calendar.review_batching.presentation_time=08:15',
            'operating_calendar.review_batching.weekdays=[1,3,5]',
            'operating_calendar.eod.time=18:30', 'operating_calendar.eod.weekdays=[1,2,3,4,5,6,7]')
        restarted = op.read_operating(self.root, self.gov)
        self.assertEqual(updated, restarted)
        calendar = effective_calendar(restarted.config)
        self.assertEqual(calendar['timezone'], 'America/New_York')
        self.assertEqual(calendar['review_batching']['presentation_time'], '08:15')
        for key in before.keys():
            self.assertEqual(before[key], restarted.config[key])
        self.assertEqual(self.gov, json.loads((self.root / op.GOVERNANCE).read_text()))
        self.assertFalse(restarted.config['budgeting']['accounting_enabled'])
        self.assertFalse(restarted.config['budgeting']['enforcement_enabled'])
        self.assertEqual(40, len(restarted.provenance['protected_settings']['commit']))
        self.assertFalse((self.root / op.STATE).exists())

    def test_mixed_refusals_are_atomic_and_do_not_consume_observations(self):
        raw = (self.root / op.CONFIG).read_bytes()
        invalid = ('operating_calendar.eod.time=25:00', 'operating_calendar.timezone=Invalid/Zone',
            'operating_calendar.review_batching.weekdays=[true]',
            'operating_calendar.review_batching.cutoff_time=06:00',
            'operating_calendar.reporting=true', 'streams.A.worker=unknown/high',
            'operating_calendar.work_mode=paused', 'operating_calendar.eod.weekdays=[]')
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.set_calendar('operating_calendar.eod.time=18:00' if 'eod.time=' not in bad
                                  else 'operating_calendar.timezone=UTC', bad)
            self.assertEqual(raw, (self.root / op.CONFIG).read_bytes())
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_interrupted_update_uses_existing_pinned_recovery(self):
        self.set_calendar('operating_calendar.review_batching.enabled=false')
        original = op._new
        def interrupt(tree, name, raw):
            if name.startswith(op.STATE + '/changes/'):
                raise OSError('fixture interruption')
            return original(tree, name, raw)
        with patch.object(op, '_new', side_effect=interrupt), self.assertRaises(OSError):
            op.set_operating(self.root, self.gov, 'two streams', ['streams.count=2'])
        with self.assertRaises(ValidationError):
            op.read_operating(self.root, self.gov)
        pin = sha256((self.root / op.JOURNAL).read_bytes())
        recovered = op.recover_operating(self.root, self.gov, pin)
        self.assertFalse(effective_calendar(recovered.config)['review_batching']['enabled'])
        self.assertEqual(2, recovered.count)
        self.assertEqual(recovered, op.read_operating(self.root, self.gov))

    def test_calendar_cannot_be_epic_scoped(self):
        with self.assertRaises(ValidationError):
            op.set_operating(self.root, self.gov, 'fixture', ['operating_calendar.eod.time=18:00'], epic_id='FIX-1')
        self.assertFalse((self.root / op.STATE).exists())

    def test_calendar_audit_bound_precedes_observation_consumption(self):
        route = self.value['streams']['A']['worker']
        assignments = ['operating_calendar.eod.time=18:00'] + [
            f'epic_overrides.FIX-{i}.streams.A.worker={route["model"]}/{route["reasoning_effort"]}'
            for i in range(1, 66)]
        with self.assertRaises(ValidationError):
            op.set_operating(self.root, self.gov, 'fixture oversized mixed request', assignments)
        self.assertEqual(op.read_operating(self.root, self.gov).config, self.value)
        self.assertFalse((self.root / op.JOURNAL).exists())


if __name__ == '__main__':
    unittest.main()
