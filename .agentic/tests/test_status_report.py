"""PR1 pure supplied-input projection acceptance tests."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError


def api():
    # Deliberately deferred: discovery succeeds on the featureless base.
    return importlib.import_module('agentic.status_report')


def task(key, predecessors, start, minutes):
    return dict(key=key, predecessors=predecessors, scheduled_start_minute=start,
                rounds=1, round_minutes=minutes, owner_touchpoint_minutes=0,
                owner_review_window=None)


def snapshot():
    scenario = {'source': 'fixture/plan.json', 'tasks': [
        task('AWF-2', ['AWF-1'], 20, 30), task('AWF-3', [], 0, 10),
        task('AWF-1', [], 0, 20)]}
    return {
        'inventory': {'status': 'KNOWN', 'source': 'fixture/inventory.json', 'tickets': [
            {'key': 'AWF-3', 'status': 'READY'}, {'key': 'AWF-2', 'status': 'IN_PROGRESS'},
            {'key': 'AWF-1', 'status': 'BACKLOG'}, {'key': 'AWF-4', 'status': 'DONE'},
            {'key': 'AWF-5', 'status': 'CANCELLED'}]},
        'assignments': {'status': 'KNOWN', 'records': [
            {'key': 'AWF-1', 'tier': 1, 'cap': 1, 'source': 'fixture/tier1'},
            {'key': 'AWF-2', 'tier': 2, 'cap': 4, 'source': 'fixture/tier2'},
            {'key': 'AWF-3', 'tier': 2, 'cap': 4, 'source': 'fixture/tier3'}]},
        'lane_evidence': {'baseline': {'status': 'KNOWN', 'recommendation': 2,
            'effective': 2, 'owner_override': None, 'source': 'fixture/lanes',
            'constraints': [constraint('resource:cpu', 2), constraint('observed_host_capacity', 2),
                            constraint('dependency_graph/file_overlap', 3)]},
            'rejected_override': None},
        'merge_train': {'status': 'KNOWN', 'enabled': True, 'cadence_minutes': 15,
                        'source': 'fixture/train', 'source_owner_review': 'normal_tier_final_gate'},
        'eta_inputs': {'scenario_format': 'AWF55_INTEGER_SCENARIOS_V1', 'effective_lanes': 2,
            'start_gap_minutes': 0, 'scenarios': {'p50': copy.deepcopy(scenario), 'p80': scenario},
            'forecast_anchor_at': '2026-10-20T13:00:00+01:00'},
        'release_binding': {'project_id': 'fixture', 'workflow_version': '1.9.4',
                            'jira_enabled': False, 'scope_selector': None}}


def constraint(name, value):
    return dict(name=name, value=value, actor=None, observed_at=None,
                source='fixture/constraints', reason='supplied capacity')


def binding():
    return dict(project_id='fixture', ticket_id='AWF-1', head='supplied-head',
                policy_sha256='a'*64, operating_hash=None)


class ProjectionTests(unittest.TestCase):
    def test_report_counts_outstanding_excludes_done_cancelled(self):
        data = snapshot()
        report = api().build_report(data)
        self.assertEqual(report['outstanding'], {'status': 'KNOWN', 'count': 3,
            'tickets': ['AWF-1', 'AWF-2', 'AWF-3'], 'source': 'fixture/inventory.json'})
        data['inventory']['tickets'][0]['status'] = 'DONE'
        self.assertEqual(api().build_report(data)['outstanding']['count'], 2)

    def test_priority_groups_by_tier_and_cap(self):
        self.assertEqual(api().build_report(snapshot())['priority'], {'status': 'KNOWN', 'groups': [
            {'tier': 1, 'cap': 1, 'tickets': ['AWF-1'], 'sources': ['fixture/tier1']},
            {'tier': 2, 'cap': 4, 'tickets': ['AWF-2', 'AWF-3'],
             'sources': ['fixture/tier2', 'fixture/tier3']}]})

    def test_lanes_recommendation_without_override(self):
        lanes = api().build_report(snapshot())['lanes']
        self.assertEqual(lanes, dict(status='KNOWN', recommendation=2, effective=2,
            owner_override=None, source='fixture/lanes', binding_constraint='observed_host_capacity',
            binding_constraints=['observed_host_capacity', 'resource:cpu'],
            constraint_details=sorted(snapshot()['lane_evidence']['baseline']['constraints'], key=lambda x:x['name'])))

    def test_lanes_owner_override_fixed_count_and_max_cap_records_who_when_and_binding_constraint(self):
        for mode, value, effective in [('fixed_count', 1, 1), ('max_cap', 1, 1), ('max_cap', 8, 2)]:
            data = snapshot()
            decision = dict(mode=mode, value=value, actor='owner', decided_at='2026-10-20T12:00:00Z', source='fixture/decision')
            data['lane_evidence']['baseline'].update(owner_override=decision, effective=effective)
            result = api().build_report(data)
            self.assertEqual(result['lanes']['effective'], effective)
            self.assertEqual(result['lanes']['owner_override'], decision)
            result['lanes']['owner_override']['actor'] = 'changed'
            self.assertEqual(decision['actor'], 'owner')
        data['lane_evidence']['baseline'].update(owner_override={**decision, 'mode':'fixed_count'}, effective=8)
        with self.assertRaises(ValidationError): api().build_report(data)

    def test_lane_ties_zero_rejections_and_inconsistent_evidence(self):
        data = snapshot()
        b = data['lane_evidence']['baseline']
        b['constraints'][2]['value'] = 2
        rejected = dict(mode='fixed_count', value=9, actor='owner', decided_at='2026-10-20T12:00:00Z', source='fixture/rejected', reason='too large')
        data['lane_evidence']['rejected_override'] = rejected
        result = api().build_report(data)
        self.assertEqual(result['lanes']['binding_constraints'], ['dependency_graph/file_overlap','observed_host_capacity','resource:cpu'])
        self.assertEqual(result['lane_observations'], {'rejected_override': rejected})
        result['lane_observations']['rejected_override']['value'] = 1
        self.assertEqual(rejected['value'], 9)
        for field in ['recommendation', 'effective']:
            bad = copy.deepcopy(data); bad['lane_evidence']['baseline'][field] = 1
            with self.assertRaises(ValidationError): api().build_report(bad)
        b.update(recommendation=0, effective=0)
        b['constraints'][0]['value'] = 0
        self.assertEqual(api().build_report(data)['lanes']['effective'], 0)

    def test_merge_train_on_with_cadence(self):
        for cadence in [15, 30]:
            data = snapshot(); data['merge_train']['cadence_minutes'] = cadence
            self.assertEqual(api().build_report(data)['merge_train'], data['merge_train'])

    def test_merge_train_off(self):
        for enabled, source in [(False,'awf_default'), (True,'adopter_default')]:
            data = snapshot(); data['merge_train'].update(enabled=enabled, source=source, cadence_minutes=None)
            self.assertEqual(api().build_report(data)['merge_train'], data['merge_train'])

    def test_recurring_schedule_alternatives_and_overnight_gap(self):
        for schedule, cadence in [({'kind':'INTERVAL','minutes':15},15),
            ({'kind':'UTC_DAILY_SLOTS','timezone':'UTC','slots':['10:00','15:00']},None),
            ({'kind':'UNKNOWN','reason':'missing recurring policy'},None)]:
            data = snapshot(); data['merge_train']['schedule'] = schedule
            result = api().build_report(data)['merge_train']
            self.assertEqual(result['schedule'], schedule); self.assertEqual(result['cadence_minutes'],cadence)
            self.assertIsNot(result['schedule'], schedule)
        for slots in [[], ['15:00','10:00'], ['10:00','10:00'], ['1:00'], ['24:00']]:
            data['merge_train']['schedule'] = dict(kind='UTC_DAILY_SLOTS',timezone='UTC',slots=slots)
            with self.assertRaises(ValidationError): api().build_report(data)
        data['merge_train']['schedule'] = {'kind':'INTERVAL','minutes':30}
        with self.assertRaises(ValidationError): api().build_report(data)

    def test_eta_deterministic_from_fixture_graph_with_p50_p80_and_basis(self):
        data = snapshot(); report = api().build_report(data)
        self.assertEqual((report['eta']['p50_minutes'],report['eta']['p80_minutes']), (50,50))
        self.assertEqual(report['eta']['basis'], data['eta_inputs'])
        self.assertEqual(report['eta']['confidence']['quantiles'], [0.5,0.8])
        data['eta_inputs']['scenarios']['p80']['tasks'][0]['round_minutes'] = 40
        self.assertEqual(api().build_report(data)['eta']['p80_minutes'],60)
        data['eta_inputs']['scenarios']['p50']['tasks'][0]['round_minutes'] = 40
        self.assertEqual(api().build_report(data)['eta']['p50_minutes'],60)
        self.assertEqual(report['eta']['p50_minutes'],50)

    def test_eta_invalid_normalized_graph_is_unknown_without_operational_replanning(self):
        mutations = [lambda t:t.update(predecessors=['missing']),
            lambda t:t.update(predecessors=['AWF-2']), lambda t:t.update(round_minutes=0),
            lambda t:t.update(rounds=9007199254740991), lambda t:t.update(scheduled_start_minute=0),
            lambda t:t.update(owner_review_window={'opens_at_minute':0,'closes_at_minute':1})]
        for mutate in mutations:
            data = snapshot(); mutate(data['eta_inputs']['scenarios']['p50']['tasks'][0])
            eta = api().build_report(data)['eta']
            self.assertEqual(set(eta), {'status','reason'}); self.assertEqual(eta['status'],'UNKNOWN')
        for change in ['occupancy','gap','duplicate','mismatch']:
            data = snapshot(); eta = data['eta_inputs']
            if change=='occupancy': eta['effective_lanes']=1
            if change=='gap': eta['start_gap_minutes']=1
            if change=='duplicate': eta['scenarios']['p50']['tasks'].append(copy.deepcopy(eta['scenarios']['p50']['tasks'][0]))
            if change=='mismatch': eta['scenarios']['p80']['tasks'].pop()
            self.assertEqual(api().build_report(data)['eta']['status'], 'UNKNOWN')

    def test_forecast_recorded_anchor_offsets_missing_invalid_overflow(self):
        data = snapshot(); eta = api().build_report(data)['eta']
        self.assertEqual(eta['forecast'], dict(status='KNOWN', anchor_at='2026-10-20T12:00:00.000000Z',
            p50_completion_at='2026-10-20T12:50:00.000000Z',p80_completion_at='2026-10-20T12:50:00.000000Z'))
        for anchor in [None, '9999-12-31T23:59:59Z']:
            data['eta_inputs']['forecast_anchor_at'] = anchor
            result = api().build_report(data)['eta']
            self.assertEqual(result['p50_minutes'],50)
            self.assertEqual(result['forecast'], {'status':'UNKNOWN','reason':'FORECAST_RANGE' if anchor else 'MISSING_FORECAST_ANCHOR'})
        del data['eta_inputs']['forecast_anchor_at']
        self.assertEqual(api().build_report(data)['eta']['forecast']['reason'],'MISSING_FORECAST_ANCHOR')
        for anchor in [True, 1, '', '2026-10-20T12:00:00', '2026-02-30T12:00:00Z']:
            data['eta_inputs']['forecast_anchor_at'] = anchor
            with self.assertRaises(ValidationError): api().build_report(data)

    def test_eta_reproducible_from_recorded_inputs_hash(self):
        data = snapshot(); original = copy.deepcopy(data)
        expected = hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        self.assertEqual(api().inputs_sha256(data),expected)
        self.assertEqual(api().build_report(data)['inputs_sha256'],expected)
        self.assertEqual(api().inputs_sha256(dict(reversed(list(data.items())))),expected)
        data['extra'] = None; self.assertNotEqual(api().inputs_sha256(data),expected)
        self.assertEqual(original,snapshot())
        data = original; data['inventory']['tickets'].reverse()
        self.assertNotEqual(api().inputs_sha256(data),expected)

    def test_missing_jira_or_plan_inputs_reported_unknown_with_reason(self):
        result = api().build_report({})
        for key, reason in [('outstanding','MISSING_INVENTORY'),('priority','MISSING_ASSIGNMENTS'),
            ('lanes','MISSING_PLAN'),('eta','MISSING_ETA_INPUTS'),('merge_train','MISSING_MERGE_TRAIN'),
            ('routing','MISSING_ROUTING'),('release_binding','MISSING_RELEASE_BINDING')]:
            self.assertEqual(result[key], {'status':'UNKNOWN','reason':reason})
        data = snapshot(); data.pop('eta_inputs')
        self.assertEqual(api().build_report(data)['outstanding']['count'],3)

    def test_release_binding_closed_reproducible_detached_and_sensitive(self):
        data = snapshot(); b = data['release_binding']
        selector = dict(allow_entire_project=False,selector_mode='epics',included_epics=['E-2','E-1'],
            labels_any=['λ'], components_any=[],additional_jql='',ownership_required=True)
        for enabled in [False,True]:
            b.update(jira_enabled=enabled,scope_selector=selector if enabled else None)
            result=api().build_report(data)['release_binding']
            self.assertEqual(result, {'status':'KNOWN','inputs':b,'sha256':api().inputs_sha256(b)})
            self.assertIsNot(result['inputs'],b)
        digest=result['sha256']
        for field, value in [('project_id','different'),('workflow_version','2'),('jira_enabled',False),('scope_selector',{**selector,'labels_any':['μ']})]:
            changed=copy.deepcopy(b); changed[field]=value
            if field=='jira_enabled': changed['scope_selector']=None
            data['release_binding']=changed
            self.assertNotEqual(api().build_report(data)['release_binding']['sha256'],digest)
        for bad in [{**b,'extra':1},{**b,'project_id':' '},{**b,'jira_enabled':1},{**b,'scope_selector':{}}]:
            data['release_binding']=bad
            with self.assertRaises(ValidationError): api().build_report(data)

    def test_routes_aliases_scopes_order_cohort_and_unknown_telemetry(self):
        data=snapshot(); b=binding()
        decl=lambda role,scope:dict(role=role,scope=scope,model='supplied-model',effort='high',source='OPERATING_CONFIG.yaml#/critic',enabled=False)
        proposal=dict(status='KNOWN',model='supplied-model',reasoning_effort='high',dispatch_performed=False)
        observed=dict(status='KNOWN',run_id='run',requested_model='m',requested_effort='high',
            observed_model='unobserved',observed_effort='unobserved',codex_cli_version='supplied',tokens='unknown',outcome='done',authentication='CALLER_ASSERTED')
        routing=dict(binding=b, declarations=[decl('writer',dict(kind='stream',id=None,stream='Z')),
            decl('reviewer',dict(kind='role',id=None,stream=None))],
            proposals=[dict(role='fix',source='fixture/proposal',binding=copy.deepcopy(b),value=proposal)],
            observations=[dict(role='worker',source='fixture/run',binding=copy.deepcopy(b),value=observed)])
        data['routing']=routing; result=api().build_report(data)['routing']
        self.assertEqual([x['role'] for x in result['declarations']],['critic','worker'])
        self.assertEqual(result['declarations'][1]['scope']['stream'],'Z')
        self.assertIsNone(result['declarations'][0]['pinned'])
        self.assertEqual(result['observations'][0]['value'],observed)
        self.assertEqual(result['proposals'][0]['role'],'worker')
        for field in b:
            r=copy.deepcopy(routing); r['proposals'][0]['binding'][field]='b'*64 if 'hash' in field or 'sha256' in field else 'different'
            data['routing']=r
            self.assertEqual(api().build_report(data)['routing']['proposals'][0]['value'], {'status':'UNKNOWN','reason':'ROUTING_BINDING_MISMATCH'})
        routing['proposals'][0]['binding']=None; data['routing']=routing
        self.assertEqual(api().build_report(data)['routing']['proposals'][0]['value']['reason'],'MISSING_ROUTING_BINDING')
        routing['proposals'][0]['value']={'status':'UNKNOWN','reason':'supplied unknown'}
        self.assertEqual(api().build_report(data)['routing']['proposals'][0]['value']['reason'],'supplied unknown')
        routing['declarations'].append(copy.deepcopy(routing['declarations'][0]))
        with self.assertRaises(ValidationError): api().build_report(data)

    def test_report_json_validates_against_schema_and_independent_text_parity(self):
        report=api().build_report(snapshot())
        import tempfile
        from agentic.contracts import Contracts
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            metrics=['outstanding','priority','lanes','merge_train','eta','release_binding','routing']
            schema={'$schema':'https://json-schema.org/draft/2020-12/schema',
                '$id':'urn:awf:1.2:status-report','type':'object','additionalProperties':False,
                'required':['inputs_sha256',*metrics],
                'properties':{'inputs_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'},
                    'lane_observations':{'type':'object'},
                    **{key:{'type':'object','required':['status'],
                        'properties':{'status':{'enum':['KNOWN','UNKNOWN']}}} for key in metrics}}}
            (folder/'status-report.schema.json').write_text(json.dumps(schema),encoding='utf-8')
            contracts=Contracts(folder)
            self.assertIs(contracts.validate('status-report',report),report)
            with self.assertRaises(ValidationError): contracts.validate('status-report',{**report,'unexpected':1})
        self.assertEqual(json.loads(api().render_json(report)),report)
        lines=api().render_text(report).splitlines()
        labelled={line.split(': ',1)[0]:json.loads(line.split(': ',1)[1]) for line in lines}
        self.assertEqual(labelled,report)
        with patch.object(api(),'render_json',side_effect=AssertionError('text must be independent')):
            self.assertEqual(api().render_text(report).splitlines(),lines)

    def test_legacy_lanes_and_all_routing_scope_alternatives(self):
        data=snapshot(); del data['lane_evidence']
        data['plan']=dict(recommendation=3,owner_override=None,binding_constraint='host',source='fixture/legacy-plan')
        self.assertEqual(api().build_report(data)['lanes'], dict(status='KNOWN',recommendation=3,
            owner_override=None,effective=3,binding_constraint='host',source='fixture/legacy-plan'))
        scopes=[dict(kind='role',id=None,stream=None),dict(kind='tier',id='2',stream=None),
            dict(kind='stream',id=None,stream='Z'),dict(kind='epic_stream',id='E-1',stream='Z'),
            dict(kind='agent',id='a',stream=None),dict(kind='ticket',id='T-1',stream=None)]
        for scope in scopes:
            data['routing']=dict(binding=None,proposals=[],observations=[],declarations=[dict(
                role='simple_worker',scope=scope,model='m',reasoning_effort='low',source='fixture/route')])
            self.assertEqual(api().build_report(data)['routing']['declarations'][0]['scope'],scope)
            bad=copy.deepcopy(data); bad['routing']['declarations'][0]['scope']['kind']='invalid'
            with self.assertRaises(ValidationError): api().build_report(bad)
        bad=copy.deepcopy(data); bad['routing']['declarations'][0]['effort']='high'
        with self.assertRaises(ValidationError): api().build_report(bad)

    def test_status_report_fixtures_are_portable_and_pure(self):
        import builtins
        data=snapshot(); original=copy.deepcopy(data)
        with patch.object(builtins,'open',side_effect=AssertionError('no acquisition')):
            report=api().build_report(data)
        self.assertEqual(data,original)
        report['eta']['basis']['effective_lanes']=99
        self.assertEqual(data,original)
        self.assertTrue(all(not x.startswith('/') and '\\' not in x for x in [data['inventory']['source'],data['eta_inputs']['scenarios']['p50']['source']]))


def admitted_fixture():
    from agentic.canonical import load
    config = load(ROOT / '.agentic/examples/PROJECT_CONFIG.yaml')
    release = dict(project_id=config['project']['id'],
        workflow_version=config['template']['expected_workflow_version'],
        jira_enabled=True, scope_selector=config['jira']['scope'])
    request = dict(record_id='ec4d418f-fbec-4fd2-bb89-e84fba56a19c',
        created_at='2026-10-25T09:00:00Z',
        binding=dict(project_id=config['project']['id'],
            repository_id=config['github']['repository_id'],
            repository=config['github']['repository'], cloud_id=config['jira']['cloud_id'],
            provider_project_id=config['jira']['provider_project_id'],
            project_key=config['jira']['project_key'], release_sha256=api().inputs_sha256(release)),
        github_identity=dict(actor_id=config['github']['expected_actor_id'],
            actor_login=config['github']['expected_actor_login'],
            auth_profile=config['github']['auth_profile'],
            repository_id=config['github']['repository_id'], repository=config['github']['repository'],
            readable=True, protocol='https', observed_at='2026-10-25T09:00:00Z'),
        jira_connections=[dict(connection_id='fixture-connection', cloud_id=config['jira']['cloud_id'],
            site=config['jira']['site'], account_id=config['jira']['controller_actor_id'],
            account_login='fixture-controller', observed_at='2026-10-25T09:00:00Z',
            projects=[dict(project_id=config['jira']['provider_project_id'],
                project_key=config['jira']['project_key'], browse=True)])])
    return config, request


class OptInReportTests(unittest.TestCase):
    def module(self):
        return importlib.import_module('agentic.status_report_store')

    def test_identity_binding_preflights_reject_unobserved_mismatch_and_disabled_jira(self):
        config, request = admitted_fixture()
        for change, reason in [('github', 'IDENTITY_UNOBSERVED'), ('jira', 'IDENTITY_UNOBSERVED'),
                               ('binding', 'REPORT_BINDING_MISMATCH'), ('actor', 'IDENTITY_MISMATCH'),
                               ('disabled', 'JIRA_DISABLED')]:
            cfg, data = copy.deepcopy(config), copy.deepcopy(request)
            if change == 'github': data['github_identity'] = None
            if change == 'jira': data['jira_connections'] = None
            if change == 'binding': data['binding']['release_sha256'] = 'f' * 64
            if change == 'actor': data['github_identity']['actor_id'] += 1
            if change == 'disabled': cfg['jira']['enabled'] = False
            with self.subTest(change=change):
                result = self.module().prepare_report(cfg, data)
                self.assertEqual(result['status'], 'NOT_RUN')
                self.assertIn(reason, result['reason'])
                self.assertNotIn('record', result)

    def test_admitted_unknowns_preserve_original_snapshot_and_replay(self):
        config, request = admitted_fixture()
        original = copy.deepcopy(request)
        result = self.module().prepare_report(config, request)
        self.assertEqual(result['status'], 'PREPARED')
        record = result['record']
        self.assertEqual(record['inputs']['request'], original)
        self.assertEqual(record['inputs']['configuration'], config)
        self.assertEqual(record['inputs_sha256'], api().inputs_sha256(record['inputs']))
        self.assertEqual(self.module().replay_report(record), result['report'])
        self.assertEqual(result['report']['outstanding']['status'], 'UNKNOWN')
        self.assertEqual(result['report']['eta']['status'], 'UNKNOWN')
        self.assertEqual(record['source_mode'], 'SUPPLIED_RECORDS_ONLY')
        self.assertEqual(request, original)
        record['inputs']['request']['binding']['project_id'] = 'changed'
        with self.assertRaises(ValidationError): self.module().replay_report(record)

    def test_replay_rechecks_identity_even_if_caller_rehashes_modified_inputs(self):
        config, request = admitted_fixture()
        record = self.module().prepare_report(config, request)['record']
        record['inputs']['request']['github_identity']['actor_id'] += 1
        record['inputs_sha256'] = api().inputs_sha256(record['inputs'])
        values = json.loads(record['values_json']); values['inputs_sha256'] = record['inputs_sha256']
        record['values_json'] = api().render_json(values)
        with self.assertRaisesRegex(ValidationError, 'IDENTITY_MISMATCH'):
            self.module().replay_report(record)

    def test_caller_inventory_and_train_assertions_need_independent_verifiers(self):
        config, request = admitted_fixture()
        request['inventory'] = snapshot()['inventory']
        request['merge_train'] = snapshot()['merge_train']
        result = self.module().prepare_report(config, request)
        self.assertEqual(result['report']['outstanding']['reason'], 'UNVERIFIED_INVENTORY')
        self.assertEqual(result['report']['merge_train']['reason'], 'UNVERIFIED_MERGE_TRAIN')
        accepted = self.module().prepare_report(config, request,
            verify_inventory=lambda value, binding: copy.deepcopy(value),
            verify_merge_train=lambda value, binding: copy.deepcopy(value))
        self.assertEqual(accepted['report']['outstanding']['count'], 3)
        self.assertEqual(accepted['report']['merge_train']['cadence_minutes'], 15)
        for verifier in [lambda *args: True, lambda value, binding: {**value, 'source':'foreign'}]:
            refused = self.module().prepare_report(config, request, verify_inventory=verifier)
            self.assertEqual(refused['report']['outstanding']['status'], 'UNKNOWN')

    def test_inventory_normalizes_only_exact_terminal_labels_and_verifier_failures_are_unknown(self):
        config, request = admitted_fixture()
        request['inventory'] = dict(status='KNOWN', source='fixture/provider', tickets=[
            dict(key='AWF-1', status=config['jira']['status_map']['done']),
            dict(key='AWF-2', status='Cancelled'), dict(key='AWF-3', status='done'),
            dict(key='AWF-4', status='READY')])
        result = self.module().prepare_report(config, request,
            verify_inventory=lambda value, binding: copy.deepcopy(value))
        self.assertEqual(result['report']['outstanding']['tickets'], ['AWF-3','AWF-4'])
        def unavailable(*args):
            raise RuntimeError('synthetic source unavailable')
        result = self.module().prepare_report(config, request, verify_inventory=unavailable)
        self.assertEqual(result['report']['outstanding']['status'], 'UNKNOWN')
        self.assertIn('VERIFIER_FAILED', result['report']['outstanding']['reason'])

    def test_models_per_agent_type_from_defaults_and_overrides_with_source(self):
        from agentic.operating import default_operating, validate_operating
        config, request = admitted_fixture()
        operating = default_operating()
        operating['streams']['A']['worker'] = dict(model='gpt-5.6-sol', reasoning_effort='high')
        checked = validate_operating(operating, config)
        result = self.module().prepare_report(config, request, operating=checked)
        routing = result['report']['routing']
        self.assertEqual({row['role'] for row in routing['declarations']},
                         {'controller','worker','critic','specialist','simple_worker'})
        stream = next(row for row in routing['declarations'] if row['scope']['stream'] == 'A'
                      and row['role'] == 'worker')
        self.assertEqual((stream['model'], stream['reasoning_effort']), ('gpt-5.6-sol','high'))
        self.assertIn('OPERATING_CONFIG.yaml', stream['source'])
        self.assertTrue(all(row['value']['status'] == 'UNKNOWN' for row in routing['proposals']))
        self.assertEqual(result['record']['inputs']['operating'], operating)

    def test_bad_assignment_plan_graph_records_are_unknown_without_recompute(self):
        config, request = admitted_fixture()
        request.update(assignments=[{'tier':1,'round_cap':1}], plan={'lane_count':99},
                       planning_graph={'p50':{},'p80':{}})
        with patch('agentic.planning.recompute_plan', side_effect=AssertionError('no replan')):
            result = self.module().prepare_report(config, request)
        for metric in ['priority', 'lanes', 'eta']:
            self.assertEqual(result['report'][metric]['status'], 'UNKNOWN')
            self.assertIn('INVALID_', result['report'][metric]['reason'])

    def test_state_record_immutable_retry_collision_delete_and_no_implicit_creation(self):
        import sqlite3
        import tempfile
        from agentic.store import Store
        config, request = admitted_fixture()
        prepared = self.module().prepare_report(config, request)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            with self.assertRaises(ValidationError): self.module().ReportStore(path, config['project']['id'])
            self.assertFalse(path.exists())
            state = Store(path, config['project']['id'])
            before = state.verify_chain()['sequence']
            reports = self.module().ReportStore(path, config['project']['id'])
            self.assertEqual(reports.append(prepared['record']), reports.append(prepared['record']))
            self.assertEqual(state.verify_chain()['sequence'], before + 1)
            from contextlib import closing
            with closing(sqlite3.connect(path)) as db:
                for sql in ['UPDATE events SET payload=payload', 'DELETE FROM events']:
                    with self.assertRaises(sqlite3.IntegrityError): db.execute(sql)
                for table in ['tickets','operations','reservations']:
                    self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)
            changed = copy.deepcopy(prepared['record']); changed['trigger'] = 'activation'
            with self.assertRaises(ValidationError): reports.append(changed)
            next_request = copy.deepcopy(request)
            next_request['record_id'] = '3061ccda-dafd-4fab-ab01-fbfcfb2688d4'
            next_record = self.module().prepare_report(config, next_request)['record']
            self.assertEqual(reports.append(next_record), before + 2)
            self.assertEqual(state.verify_chain()['sequence'], before + 2)
            with self.assertRaises(ValidationError): self.module().ReportStore(path, 'other-project')
            with self.assertRaises(ValidationError): self.module().ReportStore(path, config['project']['id'], worktree_roots=[Path(directory)])

    def test_verified_assignment_plan_and_graph_project_without_replanning(self):
        from agentic.canonical import fingerprint
        from agentic.intake_assignment import classify_ticket, _record, _seal
        from agentic.operating import default_operating, validate_operating
        from agentic.planning_graph import build_ticket_plan
        config, request = admitted_fixture()
        operating = validate_operating(default_operating(), config)
        ticket = dict(key='AWF-1', dependencies=[], write_paths=['docs/example.md'],
            duration_minutes=20, risk_tier=1, round_cap=1, owner_review_window=None,
            lane_class='feeder', owner_lane='A', blockers=[], idle_lane_reason=None,
            phase='work', status='ready', in_lane=False)
        graph = build_ticket_plan([ticket], lane_cap=1, start_gap_minutes=0,
                                  tier3_round_cap=3, generator_order=[])
        request['inventory'] = dict(status='KNOWN', source='fixture/inventory',
                                   tickets=[dict(key='AWF-1',status='READY')])
        classification = classify_ticket(config, dict(key='AWF-1', write_paths=['docs/example.md']))
        request['assignments'] = [_seal(_record('AWF-1', classification, source='fixture/intake',
            assigned_by='controller', now=request['created_at']))]
        capacity = dict(safe_lane_count=1, project_config_sha256=fingerprint('accepted-project-config', config),
                        operating_hash=operating.operating_hash)
        capacity['capacity_binding_sha256'] = fingerprint('planning_capacity_binding', capacity)
        plan = dict(schema_version=2, created_at=request['created_at'], lane_count=1,
            inputs=dict(capacity=capacity, jira_snapshot_sha256=fingerprint('planning_ticket_snapshot',
                request['inventory']['tickets'])),
            estimate=dict(capacity=copy.deepcopy(capacity), safe_lane_count=1, constraints={'observed_host_capacity':1}),
            owner_decision=dict(kind='accepted_default',value=1))
        plan['plan_sha256'] = fingerprint('planning_audit_record', plan)
        request['plan'] = plan
        request['planning_graph'] = dict(p50=graph, p80=copy.deepcopy(graph),
            rounds=[dict(key='AWF-1', rounds=1, round_minutes=15, owner_touchpoint_minutes=5)],
            forecast_anchor_at=request['created_at'])
        with patch('agentic.planning.recompute_plan', side_effect=AssertionError('no replan')):
            result = self.module().prepare_report(config, request, operating=operating,
                verify_inventory=lambda value, binding: copy.deepcopy(value))
        self.assertEqual(result['report']['priority']['groups'][0]['cap'], 1)
        self.assertEqual(result['report']['lanes']['effective'], 1)
        self.assertEqual(result['report']['eta']['p50_minutes'], 20)
        self.assertEqual(self.module().replay_report(result['record']), result['report'])
        graph80 = build_ticket_plan([{**ticket, 'duration_minutes':30}], lane_cap=1,
                                   start_gap_minutes=0, tier3_round_cap=3, generator_order=[])
        request['planning_graph'].update(p80=graph80, rounds={
            'p50':request['planning_graph']['rounds'],
            'p80':[dict(key='AWF-1', rounds=1, round_minutes=25, owner_touchpoint_minutes=5)]})
        quantiles = self.module().prepare_report(config, request, operating=operating,
            verify_inventory=lambda value, binding: copy.deepcopy(value))['report']['eta']
        self.assertEqual((quantiles['p50_minutes'], quantiles['p80_minutes']), (20,30))
        request['plan']['inputs']['capacity']['safe_lane_count'] = 999
        self.assertEqual(self.module().prepare_report(config, request, operating=operating)['report']['lanes']['status'], 'UNKNOWN')

    def test_run_report_validates_and_appends_only_after_identity_admission(self):
        import tempfile
        from agentic.store import Store
        from agentic.canonical import canonical
        config, request = admitted_fixture()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); project = folder / 'project'
            (project / '.agentic').mkdir(parents=True)
            (project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(config))
            source = folder / 'input.json'; source.write_bytes(canonical(request))
            state_path = folder / 'state.db'; state = Store(state_path, config['project']['id'])
            result = self.module().run_report(project, source, state_path)
            self.assertEqual(result['status'], 'RECORDED')
            self.assertEqual(state.verify_chain()['sequence'], 1)
            request['github_identity']['repository_id'] += 1
            source.write_bytes(canonical(request))
            self.assertEqual(self.module().run_report(project, source, state_path)['status'], 'NOT_RUN')
            self.assertEqual(state.verify_chain()['sequence'], 1)


if __name__=='__main__': unittest.main()
