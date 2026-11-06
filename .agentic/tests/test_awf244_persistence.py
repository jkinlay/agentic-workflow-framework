"""Persisted calendar settings; trusted host observations are offline fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import source_only  # noqa: F401
from agentic import ValidationError, operating as op
from agentic.canonical import fingerprint, sha256
from agentic.operating_calendar import effective_calendar
from test_operating import governance


class FixtureHost:
    def __init__(self):
        self.calls = []
        self.identity = 'fixture-observation'
        self.corrupt = False

    def observe_owner_direction(self, project_id, prior, changes):
        self.calls.append(deepcopy(changes))
        return dict(observation_id=self.identity, actor_id='fixture-owner',
                    project_id=project_id, prior_settings_hash='0' * 64 if self.corrupt else prior,
                    changes=deepcopy(changes))


class CalendarPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.gov = governance()
        (self.root / op.GOVERNANCE).parent.mkdir(parents=True)
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.value.pop('operating_calendar', None)
        (self.root / op.CONFIG).write_bytes(op._json(self.value))
        self.host = FixtureHost()

    def registry(self):
        from agentic.operating_calendar import CalendarSettingsRegistry
        return CalendarSettingsRegistry(Path(self.temp.name) / 'host.sqlite', self.host,
                                        worktree_roots=(self.root,))

    def set_calendar(self, *assignments):
        return op.set_operating(self.root, self.gov, 'fixture owner direction', assignments,
                                calendar_registry=self.registry())

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
                     '.agentic/examples/OPERATING_CONFIG.yaml',
                     '.agentic/templates/source-config/OPERATING_CONFIG.yaml'):
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
        for key in before.keys() - {'source'}:
            self.assertEqual(before[key], restarted.config[key])
        self.assertEqual(self.gov, json.loads((self.root / op.GOVERNANCE).read_text()))
        self.assertFalse(restarted.config['budgeting']['accounting_enabled'])
        self.assertFalse(restarted.config['budgeting']['enforcement_enabled'])
        event = json.loads(next((self.root / op.STATE / 'changes').glob('*.json')).read_text())
        self.assertEqual(event['calendar_owner_observation']['actor_id'], 'fixture-owner')
        self.assertEqual(event['calendar_owner_observation']['prior_settings_hash'],
                         fingerprint('operating-calendar', effective_calendar(before)))

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
        self.assertEqual(self.host.calls, [])
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_instruction_text_missing_host_mismatch_and_replay_refuse(self):
        assignment = ['operating_calendar.eod.time=18:00']
        with self.assertRaises(ValidationError):
            op.set_operating(self.root, self.gov, 'I am the owner', assignment)
        self.host.corrupt = True
        with self.assertRaises(ValidationError):
            self.set_calendar(*assignment)
        self.assertEqual(self.value, op.read_operating(self.root, self.gov).config)
        self.host.corrupt = False
        self.set_calendar(*assignment)
        with self.assertRaises(ValidationError):
            self.set_calendar('operating_calendar.eod.time=19:00')
        self.assertEqual(effective_calendar(op.read_operating(self.root, self.gov).config)['eod']['time'], '18:00')

    def test_interrupted_update_uses_existing_pinned_recovery(self):
        original = op._new
        def interrupt(tree, name, raw):
            if name.startswith(op.STATE + '/changes/'):
                raise OSError('fixture interruption')
            return original(tree, name, raw)
        with patch.object(op, '_new', side_effect=interrupt), self.assertRaises(OSError):
            self.set_calendar('operating_calendar.review_batching.enabled=false')
        with self.assertRaises(ValidationError):
            op.read_operating(self.root, self.gov)
        pin = sha256((self.root / op.JOURNAL).read_bytes())
        recovered = op.recover_operating(self.root, self.gov, pin)
        self.assertFalse(effective_calendar(recovered.config)['review_batching']['enabled'])
        self.assertEqual(recovered, op.read_operating(self.root, self.gov))
        self.assertEqual(len(self.host.calls), 1)

    def test_calendar_cannot_be_epic_scoped(self):
        with self.assertRaises(ValidationError):
            op.set_operating(self.root, self.gov, 'fixture', ['operating_calendar.eod.time=18:00'],
                             epic_id='FIX-1', calendar_registry=self.registry())
        self.assertEqual(self.host.calls, [])

    def test_calendar_audit_bound_precedes_observation_consumption(self):
        route = self.value['streams']['A']['worker']
        assignments = ['operating_calendar.eod.time=18:00'] + [
            f'epic_overrides.FIX-{i}.streams.A.worker={route["model"]}/{route["reasoning_effort"]}'
            for i in range(1, 66)]
        with self.assertRaises(ValidationError):
            self.set_calendar(*assignments)
        self.assertEqual(self.host.calls, [])
        self.assertEqual(op.read_operating(self.root, self.gov).config, self.value)
        self.assertFalse((self.root / op.JOURNAL).exists())
        self.set_calendar('operating_calendar.eod.time=18:00')
        with self.registry().transaction('') as db:
            rows = db.execute('SELECT observation_id FROM calendar_owner_observations').fetchall()
        self.assertEqual([row[0] for row in rows], ['fixture-observation'])

    def test_pending_budget_proposal_does_not_consume_calendar_observation(self):
        from agentic.owner_admission import PendingOwnerConfirmation
        with patch('agentic.owner_admission.propose', return_value={'status': 'PENDING_OWNER_CONFIRMATION'}), \
                self.assertRaises(PendingOwnerConfirmation):
            op.set_operating(self.root, self.gov, 'fixture mixed direction',
                ['budgeting.max_tokens_per_ticket=5000000', 'operating_calendar.eod.time=18:00'],
                calendar_registry=self.registry())
        self.assertEqual(self.host.calls, [])
        self.assertEqual(op.read_operating(self.root, self.gov).config, self.value)
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_invalid_budget_observation_does_not_consume_calendar_observation(self):
        from agentic.budget_enrollment import EnrollmentRegistry
        budget_host = FixtureHost()
        budget_host.corrupt = True
        budget_registry = EnrollmentRegistry(Path(self.temp.name) / 'budget.sqlite', budget_host, (self.root,))
        with self.assertRaises(ValidationError):
            op.set_operating(self.root, self.gov, 'fixture mixed direction',
                ['budgeting.max_tokens_per_ticket=5000000', 'operating_calendar.eod.time=18:00'],
                budget_registry=budget_registry, calendar_registry=self.registry())
        self.assertEqual(self.host.calls, [])
        self.assertEqual(op.read_operating(self.root, self.gov).config, self.value)

    def test_mixed_admissions_share_one_recoverable_operating_event(self):
        from agentic.budget_enrollment import EnrollmentRegistry
        budget_registry = EnrollmentRegistry(Path(self.temp.name) / 'budget.sqlite', FixtureHost(), (self.root,))
        original = op._new
        def interrupt(tree, name, raw):
            if name.startswith(op.STATE + '/changes/'):
                raise OSError('fixture mixed interruption')
            return original(tree, name, raw)
        with patch.object(op, '_new', side_effect=interrupt), self.assertRaises(OSError):
            op.set_operating(self.root, self.gov, 'fixture mixed direction',
                ['budgeting.max_tokens_per_ticket=5000000', 'operating_calendar.eod.time=18:00'],
                budget_registry=budget_registry, calendar_registry=self.registry())
        pin = sha256((self.root / op.JOURNAL).read_bytes())
        recovered = op.recover_operating(self.root, self.gov, pin)
        self.assertEqual(recovered.config['budgeting']['max_tokens_per_ticket'], 5000000)
        self.assertFalse(recovered.config['budgeting']['accounting_enabled'])
        self.assertEqual(effective_calendar(recovered.config)['eod']['time'], '18:00')
        events = list((self.root / op.STATE / 'changes').glob('*.json'))
        self.assertEqual(len(events), 1)
        event = json.loads(events[0].read_text())
        self.assertIn('budget_owner_observation', event)
        self.assertIn('calendar_owner_observation', event)
        self.assertEqual(len(self.host.calls), 1)


if __name__ == '__main__':
    unittest.main()
