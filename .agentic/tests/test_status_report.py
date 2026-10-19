"""AWF-55t: doc 46 red-first contract, Tier 3 / FULL for this test slice.

The in-process seams below are proposed test interfaces, not a CLI ruling.
Imports deliberately occur inside tests: discovery must work before AWF-55 ships.
No adapter, network, installed environment, or repository-local state is needed.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))


def normalized_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def snapshot():
    """Explicitly bound synthetic inputs; no assertion of live source trust.

    Terminal semantics / disabled-Jira authority and AWF-50 versus AWF-115
    remain owner questions. This fixture declares both its authority and states.
    """
    inventory = [
        {'id': 'TASK-1', 'status': 'Ready'},
        {'id': 'TASK-2', 'status': 'In Progress'},
        {'id': 'TASK-3', 'status': 'In Review'},
        {'id': 'TASK-4', 'status': 'Done'},
        {'id': 'TASK-5', 'status': 'Cancelled'},
    ]
    tiers = [
        {'ticket': 'TASK-1', 'tier': 1, 'cap': 1, 'source': 'tiers/task-1.json'},
        {'ticket': 'TASK-2', 'tier': 2, 'cap': 3, 'source': 'tiers/task-2.json'},
        {'ticket': 'TASK-3', 'tier': 3, 'cap': 3, 'source': 'tiers/task-3.json'},
    ]
    return {
        'inventory': {'status': 'VERIFIED', 'source': 'inventory/tickets.json',
                      'authority': 'synthetic-bound-inventory',
                      'terminal_states': ['Done', 'Cancelled'],
                      'records': inventory, 'sha256': normalized_hash(inventory)},
        'tiers': {'status': 'VERIFIED', 'source': 'tiers/index.json',
                  'records': tiers, 'sha256': normalized_hash(tiers)},
        'plan': {'status': 'VERIFIED', 'source': 'planning/plan.json',
                 'recommendation': 2, 'owner_override': None,
                 'constraints': {'dependency_width': 2, 'host_workers': 3,
                                 'governance_cap': 6}},
        # Degenerate quantiles remove any need to choose an estimator or
        # confidence thresholds (open question 5). Two serial tasks each take
        # two rounds of 10 minutes plus a 5-minute owner touchpoint: 50 total.
        'eta_inputs': {
            'status': 'VERIFIED', 'source': 'planning/eta.json',
            'graph': [{'ticket': 'TASK-1', 'requires': []},
                      {'ticket': 'TASK-2', 'requires': ['TASK-1']}],
            'rounds': {'TASK-1': 2, 'TASK-2': 2},
            'round_minutes': {'p50': 10, 'p80': 10},
            'owner_touchpoint_minutes': {'p50': 5, 'p80': 5},
            'owner_touchpoints_per_ticket': 1, 'lanes': 2,
        },
    }


def report_builder():
    return importlib.import_module('agentic.status_report').build_report


def store_type():
    return importlib.import_module('agentic.status_report_store').StatusReportStore


class StatusReportTests(unittest.TestCase):
    def test_report_counts_outstanding_excludes_done_cancelled(self):
        inputs = snapshot()
        report = report_builder()(inputs)
        self.assertEqual(report['outstanding'], {
            'status': 'KNOWN', 'count': 3,
            'tickets': ['TASK-1', 'TASK-2', 'TASK-3'],
            'source': 'inventory/tickets.json',
        })
        # A hard-coded total must not satisfy the counting contract.
        inputs['inventory']['records'][0]['status'] = 'Done'
        inputs['inventory']['sha256'] = normalized_hash(inputs['inventory']['records'])
        changed = report_builder()(inputs)['outstanding']
        self.assertEqual(changed['count'], 2)
        self.assertEqual(changed['tickets'], ['TASK-2', 'TASK-3'])

    def test_priority_groups_by_tier_and_cap(self):
        inputs = snapshot()
        inputs['inventory']['records'].append({'id': 'TASK-6', 'status': 'Ready'})
        inputs['tiers']['records'].extend([
            {'ticket': 'TASK-6', 'tier': 2, 'cap': 4, 'source': 'tiers/task-6.json'},
            {'ticket': 'TASK-4', 'tier': 3, 'cap': 3, 'source': 'tiers/task-4.json'},
        ])
        for member in ('inventory', 'tiers'):
            inputs[member]['sha256'] = normalized_hash(inputs[member]['records'])
        priority = report_builder()(inputs)['priority']
        self.assertEqual(priority, {'status': 'KNOWN', 'groups': [
            {'tier': 1, 'cap': 1, 'tickets': ['TASK-1'], 'sources': ['tiers/task-1.json']},
            {'tier': 2, 'cap': 3, 'tickets': ['TASK-2'], 'sources': ['tiers/task-2.json']},
            {'tier': 2, 'cap': 4, 'tickets': ['TASK-6'], 'sources': ['tiers/task-6.json']},
            {'tier': 3, 'cap': 3, 'tickets': ['TASK-3'], 'sources': ['tiers/task-3.json']},
        ]})

    def test_lanes_recommendation_without_override(self):
        lanes = report_builder()(snapshot())['lanes']
        self.assertEqual(lanes, {
            'status': 'KNOWN', 'recommendation': 2, 'owner_override': None,
            'effective': 2, 'binding_constraint': 'dependency_width',
            'source': 'planning/plan.json',
        })
        # Owner override presentation is open question 6; no override is tested.

    def test_eta_deterministic_from_fixture_graph_with_p50_p80_and_basis(self):
        inputs = snapshot()
        first = report_builder()(inputs)['eta']
        second = report_builder()(copy.deepcopy(inputs))['eta']
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'KNOWN')
        self.assertEqual((first['p50_minutes'], first['p80_minutes']), (50, 50))
        self.assertEqual(first['basis'], inputs['eta_inputs'])
        # Threshold labels remain open: assert recorded evidence, not HIGH/LOW.
        self.assertEqual(first['confidence'], {
            'quantiles': [0.5, 0.8], 'source': 'planning/eta.json',
        })
        inputs['eta_inputs']['rounds']['TASK-2'] = 3
        longer = report_builder()(inputs)['eta']
        self.assertEqual((longer['p50_minutes'], longer['p80_minutes']), (60, 60))

    def test_eta_reproducible_from_recorded_inputs_hash(self):
        inputs = snapshot()
        first = report_builder()(inputs)
        # Key insertion order must not change normalized input identity.
        reordered = json.loads(json.dumps(inputs, sort_keys=True))
        second = report_builder()(reordered)
        expected = normalized_hash(inputs)
        self.assertEqual(first['inputs_sha256'], expected)
        self.assertEqual(second['inputs_sha256'], expected)
        self.assertEqual(first['eta'], second['eta'])
        inputs['eta_inputs']['lanes'] = 1
        self.assertNotEqual(report_builder()(inputs)['inputs_sha256'], expected)

    def test_missing_jira_or_plan_inputs_reported_unknown_with_reason(self):
        build = report_builder()
        for unavailable, reason in (
                (None, 'MISSING_SOURCE'),
                ({'status': 'INVALID', 'reason': 'SOURCE_DIGEST_MISMATCH'},
                 'SOURCE_DIGEST_MISMATCH')):
            for source, affected in (
                    ('inventory', ('outstanding', 'priority', 'eta')),
                    ('plan', ('lanes', 'eta'))):
                with self.subTest(reason=reason, source=source):
                    inputs = snapshot()
                    inputs[source] = copy.deepcopy(unavailable)
                    report = build(inputs)
                    for metric in affected:
                        self.assertEqual(report[metric], {
                            'status': 'UNKNOWN', 'reason': reason,
                        })
                    unaffected = 'lanes' if source == 'inventory' else 'outstanding'
                    self.assertEqual(report[unaffected]['status'], 'KNOWN')
        # The fixture has no alternate authority: disabled-Jira fallback policy
        # is open question 4 and must not be guessed here.

    def test_state_record_immutable(self):
        Store = store_type()
        with tempfile.TemporaryDirectory(prefix='awf-report-') as temporary:
            database = Path(temporary) / 'state.db'
            original = {'inputs_sha256': normalized_hash(snapshot()), 'sequence': 1}
            with Store(database) as store:
                first = store.append('report-1', original)
                self.assertEqual(store.append('report-1', copy.deepcopy(original)), first)
                self.assertEqual(store.get('report-1'), original)
                with self.assertRaises(ValueError):
                    store.append('report-1', {'sequence': 999})
                with self.assertRaises(ValueError):
                    store.replace('report-1', {'sequence': 999})
                with self.assertRaises(ValueError):
                    store.delete('report-1')
                store.append('report-2', {'sequence': 2})
                self.assertEqual(store.list_ids(), ['report-1', 'report-2'])
                self.assertEqual(store.get('report-1'), original)
            with Store(database) as reopened:
                self.assertEqual(reopened.get('report-1'), original)
                self.assertEqual(reopened.list_ids(), ['report-1', 'report-2'])
        # Retention/archival/authorized purge remains open question 7. No purge
        # authority is granted to report generation by these assertions.

    def test_report_is_read_only(self):
        module = importlib.import_module('agentic.status_report')
        inputs = snapshot()
        before = copy.deepcopy(inputs)
        # Separate read-only source adapters expose forbidden mutation spies.
        sources = {}
        for name in ('inventory', 'tiers', 'plan', 'eta_inputs'):
            adapter = Mock(spec=['read', 'write', 'update', 'delete', 'transition'])
            adapter.read.return_value = inputs[name]
            sources[name] = adapter
        config = Mock(spec=['read', 'write', 'update', 'delete'])
        config.read.return_value = {'jira': {'enabled': False}}
        sources['config'] = config
        store = Mock(spec=['append', 'replace', 'delete'])
        with tempfile.TemporaryDirectory(prefix='awf-report-') as temporary:
            root = Path(temporary)
            marker = root / 'project' / 'config.json'
            marker.parent.mkdir()
            marker.write_text('{"read_only":true}\n', encoding='utf-8')
            original_files = {p.relative_to(root).as_posix(): p.read_bytes()
                              for p in root.rglob('*') if p.is_file()}
            report = module.create_report(sources=sources, store=store,
                                          report_id='report-1', project_root=root / 'project')
            self.assertEqual(report, module.build_report(before))
            self.assertEqual(inputs, before)
            self.assertEqual({p.relative_to(root).as_posix(): p.read_bytes()
                              for p in root.rglob('*') if p.is_file()}, original_files)
        for adapter in sources.values():
            adapter.read.assert_called_once_with()
            adapter.write.assert_not_called()
            adapter.update.assert_not_called()
            adapter.delete.assert_not_called()
        for name in ('inventory', 'tiers', 'plan', 'eta_inputs'):
            sources[name].transition.assert_not_called()
        store.append.assert_called_once_with('report-1', report)
        store.replace.assert_not_called()
        store.delete.assert_not_called()

    def test_status_report_fixtures_are_portable(self):
        inputs = snapshot()
        members = [inputs[name]['source'] for name in inputs]
        members.extend(record['source'] for record in inputs['tiers']['records'])
        with tempfile.TemporaryDirectory(prefix='awf-report-') as temporary:
            root = Path(temporary)
            for member in members:
                self.assertFalse(PurePosixPath(member).is_absolute())
                self.assertEqual(PureWindowsPath(member).drive, '')
                self.assertNotIn('\\', member)
                self.assertNotIn('..', PurePosixPath(member).parts)
                path = root.joinpath(*PurePosixPath(member).parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}\n', encoding='utf-8')
                self.assertEqual(path.relative_to(root).as_posix(), member)
            report = report_builder()(inputs)
            self.assertEqual(report['outstanding']['count'], 3)
            self.assertEqual(report['lanes']['effective'], 2)
            self.assertEqual(report['inputs_sha256'], normalized_hash(inputs))
            self.assertEqual(report['eta']['p80_minutes'], 50)
        # CLI shape/activation (open question 1) and installed-runtime execution
        # are host work; these assertions themselves are identical on both OSes.


class StatusReportRoundTwoTests(unittest.TestCase):
    """Doc 46 additions; existing round-one tests remain unchanged."""

    def test_lanes_owner_override_fixed_count_and_max_cap_records_who_when_and_binding_constraint(self):
        build = report_builder()
        # Stay below the safe recommendation: presentation above it is Q6.
        for mode in ('fixed_count', 'max_cap'):
            with self.subTest(mode=mode):
                inputs = snapshot()
                decision = {
                    'mode': mode, 'value': 1, 'actor': 'fixture-owner',
                    'decided_at': '2026-10-01T12:00:00Z',
                    'source': 'decisions/lanes.json',
                }
                inputs['plan']['owner_override'] = decision
                self.assertEqual(build(inputs)['lanes'], {
                    'status': 'KNOWN', 'recommendation': 2,
                    'owner_override': decision, 'effective': 1,
                    'binding_constraint': 'owner_override',
                    'source': 'planning/plan.json',
                })
        # A cap is a ceiling, not a request to increase the recommendation.
        inputs = snapshot()
        inputs['plan']['owner_override'] = {**decision, 'value': 3}
        lanes = build(inputs)['lanes']
        self.assertEqual(lanes['recommendation'], 2)
        self.assertEqual(lanes['owner_override'], inputs['plan']['owner_override'])
        self.assertEqual(lanes['effective'], 2)
        self.assertEqual(lanes['binding_constraint'], 'dependency_width')

    def test_merge_train_on_with_cadence(self):
        build = report_builder()
        # Q5 leaves the default cadence open; reproduce an explicit decision.
        for minutes in (15, 30):
            with self.subTest(minutes=minutes):
                inputs = snapshot()
                inputs['merge_train'] = {
                    'status': 'VERIFIED', 'source': 'decisions/merge-train.json',
                    'enabled': True, 'cadence_minutes': minutes,
                    'source_owner_review': 'normal_tier_final_gate',
                }
                self.assertEqual(build(inputs)['merge_train'], {
                    'status': 'KNOWN', 'source': 'decisions/merge-train.json',
                    'enabled': True, 'cadence_minutes': minutes,
                    'source_owner_review': 'normal_tier_final_gate',
                })

    def test_merge_train_off(self):
        build = report_builder()
        # AWF-54 Q2=B and Q4=B are settled; no cadence default is invented.
        for repository_kind, enabled in (('awf', False), ('adopter', True)):
            with self.subTest(repository_kind=repository_kind):
                inputs = snapshot()
                inputs['merge_train'] = {
                    'status': 'VERIFIED', 'source': 'policy/merge-train.json',
                    'repository_kind': repository_kind, 'owner_override': None,
                    'cadence_minutes': None,
                }
                self.assertEqual(build(inputs)['merge_train'], {
                    'status': 'KNOWN', 'source': 'policy/merge-train.json',
                    'enabled': enabled, 'cadence_minutes': None,
                    'source_owner_review': 'normal_tier_final_gate',
                })

    def test_report_json_validates_against_schema(self):
        module = importlib.import_module('agentic.status_report')
        from agentic import ValidationError
        from agentic.contracts import Contracts

        inputs = snapshot()
        inputs['merge_train'] = {
            'status': 'VERIFIED', 'source': 'decisions/merge-train.json',
            'enabled': True, 'cadence_minutes': 15,
            'source_owner_review': 'normal_tier_final_gate',
        }
        report = module.build_report(inputs)
        contracts = Contracts(ROOT / '.agentic/schemas')
        self.assertIn('status-report', contracts.schemas, 'Missing status-report schema')
        contracts.validate('status-report', report)
        decoded = json.loads(module.render_json(report))
        self.assertEqual(decoded, report)
        contracts.validate('status-report', decoded)
        # Proposed in-process text seam: one labelled JSON value per metric.
        # CLI spelling and route meaning (T5) await owner rulings.
        text = module.render_text(report)
        for key in ('outstanding', 'priority', 'lanes', 'merge_train', 'eta',
                    'inputs_sha256'):
            lines = [line for line in text.splitlines() if line.startswith(key + ': ')]
            self.assertEqual(len(lines), 1, key)
            self.assertEqual(json.loads(lines[0].split(': ', 1)[1]), decoded[key])
        for key in (None, 'lanes', 'merge_train', 'eta'):
            with self.subTest(closed_object=key):
                invalid = copy.deepcopy(decoded)
                target = invalid if key is None else invalid[key]
                target['unexpected_status_report_member'] = True
                with self.assertRaises(ValidationError):
                    contracts.validate('status-report', invalid)

    def test_on_demand_matches_activation_format(self):
        module = importlib.import_module('agentic.status_report')
        # Proposed hooks only; activation/alias CLI spelling remains Q1.
        inputs = snapshot()
        before = copy.deepcopy(inputs)
        activation = module.activation_report(
            copy.deepcopy(inputs), observed_at='2026-10-01T12:00:00Z')
        on_demand = module.on_demand_report(
            copy.deepcopy(inputs), observed_at='2026-10-01T12:01:00Z')
        self.assertEqual(activation['trigger'], 'activation')
        self.assertEqual(on_demand['trigger'], 'on_demand')
        self.assertEqual(activation['observed_at'], '2026-10-01T12:00:00Z')
        self.assertEqual(on_demand['observed_at'], '2026-10-01T12:01:00Z')
        stable = lambda report: {key: value for key, value in report.items()
                                 if key not in ('trigger', 'observed_at')}
        self.assertEqual(stable(activation), stable(on_demand))
        self.assertEqual(activation['inputs_sha256'], normalized_hash(inputs))
        self.assertEqual(activation['outstanding']['count'], 3)
        self.assertEqual(activation['lanes']['effective'], 2)
        self.assertEqual(inputs, before)
        from agentic.contracts import Contracts
        contracts = Contracts(ROOT / '.agentic/schemas')
        for report in (activation, on_demand):
            contracts.validate('status-report', report)
            self.assertEqual(json.loads(module.render_json(report)), report)
        # Identical metadata isolates rendering parity from the trigger/time.
        normalized = {**on_demand, 'trigger': activation['trigger'],
                      'observed_at': activation['observed_at']}
        self.assertEqual(module.render_text(activation), module.render_text(normalized))


if __name__ == '__main__':
    unittest.main()
