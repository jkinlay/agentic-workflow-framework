"""Codec and exact-name dispatch tests; no production report schemas."""
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.agentic/lib'))
from agentic import ValidationError
from agentic.canonical import canonical, validate_value
from agentic.contracts import Contracts


def api(): return importlib.import_module('agentic.status_report')
def fractions(): return {'eta':{'confidence':{'quantiles':[0.5,0.8]}}}


class BoundaryTests(unittest.TestCase):
    def test_only_output_fraction_pointers_and_integer_only_inputs(self):
        value=fractions()
        self.assertEqual(api().loads_report(api().canonical_report(value)),value)
        for bad in [value, {'eta_inputs':{'x':0.5}}, {'eta_inputs':{'x':1.0}}]:
            with self.assertRaises(ValidationError): api().validate_report_input(bad)
        for bad in [{'x':0.5},{'eta':{'basis':0.5}}, {'x':1.0},
            {'eta':{'confidence':{'quantiles':[True,0.8]}}},
            {'eta':{'confidence':{'quantiles':[0.5,0.8,1]}}},
            {'eta':{'confidence':{'quantiles':[0.8,0.5]}}},
            {'eta':{'confidence':{'quantiles':[0,1]}}},
            {'eta':{'confidence':{'quantiles':[0.5]}}}]:
            with self.subTest(bad=bad),self.assertRaises(ValidationError): api().validate_report_value(bad)
        for bad in [value, {'cost':1.0}]:
            with self.assertRaises(ValidationError): canonical(bad)

    def test_decoder_rejects_duplicates_bom_unicode_scalar_nonfinite_overflow(self):
        invalid=[b'{"a":1,"\\u0061":2}', b'\xef\xbb\xbf{}', b'{"a":"\xff"}',
            '{"x":"\\ud800"}', '\ud800', '[]', '1','true','null','"s"',
            '{"x":NaN}','{"x":Infinity}','{"x":-Infinity}','{"x":1e400}',
            '{"x":1.0}','{"x":9007199254740992}', '{']
        for value in invalid:
            with self.subTest(value=repr(value)),self.assertRaises(ValidationError): api().loads_report(value)
        for value in [float('nan'),float('inf'),float('-inf'),{1:'x'},{'x':'\udfff'}]:
            with self.assertRaises(ValidationError): api().validate_report_value(value)

    def test_integer_depth_and_utf8_inclusive_size_boundaries(self):
        bound=2**53-1
        self.assertEqual(api().loads_report('{"n":%d,"m":%d}'%(bound,-bound)),{'n':bound,'m':-bound})
        for n in [bound+1,-bound-1]:
            with self.assertRaises(ValidationError): api().validate_report_value({'n':n})
        value=0
        for _ in range(63): value=[value]
        api().validate_report_value({'x':value})
        with self.assertRaises(ValidationError): api().validate_report_value({'x':[value]})
        limit=8388608
        exact=b'{"x":"'+b'a'*(limit-8)+b'"}'
        self.assertEqual(len(exact),limit)
        self.assertEqual(len(api().loads_report(exact)['x']),limit-8)
        with self.assertRaises(ValidationError): api().loads_report(exact+b' ')
        unicode_text='{"x":"λ"}'
        self.assertEqual(api().loads_report(unicode_text),{'x':'λ'})

    def test_canonical_bytes_codepoints_order_and_presence(self):
        value={'z':'λ','a':[2,1]}
        self.assertEqual(api().canonical_report(value),b'{"a":[2,1],"z":"\xce\xbb"}')
        self.assertEqual(api().canonical_report(value),api().canonical_report({'a':[2,1],'z':'λ'}))
        self.assertNotEqual(api().inputs_sha256({'x':'é'}),api().inputs_sha256({'x':'e\u0301'}))

    def test_dispatch_exact_registered_names_precedence_schema_identity_formats_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for name in ['status-report','status-report-input','status-report-extra','other']:
                schema={'$schema':'https://json-schema.org/draft/2020-12/schema',
                    '$id':'urn:awf:1.2:'+name,'type':'object'}
                (folder/(name+'.schema.json')).write_text(json.dumps(schema),encoding='utf-8')
            contracts=Contracts(folder); value=fractions()
            self.assertIs(contracts.validate('status-report',value),value)
            for name in ['status-report-input','status-report-extra','other','missing']:
                with self.subTest(name=name),self.assertRaisesRegex(ValidationError,'integer'):
                    contracts.validate(name,value)
            del contracts.schemas['status-report']
            with self.assertRaisesRegex(ValidationError,'integer'): contracts.validate('status-report',value)
            with self.assertRaisesRegex(ValidationError,'Unknown contract'): contracts.validate('status-report',{})
            contracts=Contracts(folder)
            contracts.schemas['status-report'].update(required=['required'])
            with self.assertRaisesRegex(ValidationError,'required'): contracts.validate('status-report',value)
            contracts.schemas['status-report']={'type':'object','properties':{'when':{'format':'date-time'}},
                'required':['a%02d'%i for i in range(20)]}
            with self.assertRaises(ValidationError) as raised: contracts.validate('status-report',{'when':'invalid'})
            self.assertEqual(str(raised.exception).count('; '),11)
            self.assertIn('a00',str(raised.exception)); self.assertNotIn('a12',str(raised.exception))
            contracts.schemas['status-report']['required']=[]
            with self.assertRaisesRegex(ValidationError,'date-time'): contracts.validate('status-report',{'when':'invalid'})
            bad={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:awf:1.2:bad','default':0.5}
            (folder/'bad.schema.json').write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaises(ValidationError): Contracts(folder)

    def test_both_fresh_import_orders(self):
        for names in [('contracts','status_report'),('status_report','contracts')]:
            code='import sys; sys.path.insert(0,sys.argv[1]); '+ '; '.join('import agentic.'+n for n in names)
            result=subprocess.run([sys.executable,'-B','-I','-c',code,str(ROOT/'.agentic/lib')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_every_catalog_contract_remains_integer_strict(self):
        contracts=Contracts(ROOT/'.agentic/schemas')
        for name in contracts.schemas:
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError,'integer'):
                contracts.validate(name, {'unrelated_permissive_slot':0.5})

    def test_boolean_task_numbers_are_invalid_normalized_input(self):
        from test_status_report import snapshot
        for field in ['scheduled_start_minute','rounds','round_minutes','owner_touchpoint_minutes']:
            data=snapshot()
            data['eta_inputs']['scenarios']['p50']['tasks'][0][field]=True
            with self.subTest(field=field),self.assertRaises(ValidationError): api().build_report(data)
        for field in ['opens_at_minute','closes_at_minute']:
            data=snapshot()
            window={'opens_at_minute':20,'closes_at_minute':50}
            window[field]=True
            data['eta_inputs']['scenarios']['p50']['tasks'][0]['owner_review_window']=window
            with self.subTest(field=field),self.assertRaises(ValidationError): api().build_report(data)

    def test_ordinary_status_exits_and_output_without_repository_fixture(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from agentic.cli import main
        from agentic.providers import github_status
        for state, ordinary, required in [('ACTIVE',0,0),('CONFIGURED',0,4),(None,2,4)]:
            report={'line':'AWF 1.9.4: '+str(state),'project_state':state,'next_action':None}
            for require_active, code in [(False,ordinary),(True,required)]:
                output=io.StringIO()
                args=['--root',str(ROOT),'status']+(['--require-active'] if require_active else [])
                with patch.object(github_status,'project_status',return_value=report),redirect_stdout(output):
                    self.assertEqual(main(args),code)
                self.assertEqual(output.getvalue(),github_status.render_status(report)+'\n')
            output=io.StringIO()
            with patch.object(github_status,'project_status',return_value=report),redirect_stdout(output):
                self.assertEqual(main(['--root',str(ROOT),'status','--json']),ordinary)
            self.assertEqual(json.loads(output.getvalue()),report)

    def test_report_diagnostics_preserve_status_stdout_exits_and_opt_in(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch
        from agentic.child_process import render_bounded_diagnostic
        from agentic.cli import main
        from agentic.providers import github_status
        from agentic import status_report_store
        from test_status_report import admitted_fixture, snapshot

        config, request = admitted_fixture()
        request['github_identity'] = None
        unknown = api().build_report({})
        self.assertEqual(unknown['eta'], dict(status='UNKNOWN', reason='MISSING_ETA_INPUTS'))
        failure = 'synthetic report failure'
        oversized = 'synthetic report failure\n\x1b' + '\u03bb' * 5000
        run_report = status_report_store.run_report
        cases = ['missing_both', 'missing_input', 'missing_store', 'identity',
                 'exception', 'oversized_exception', 'recorded', 'recorded_unknown', 'no_report']
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            project = folder / 'project'
            (project / '.agentic').mkdir(parents=True)
            (project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(config))
            source = folder / 'input.json'
            source.write_bytes(canonical(request))
            store = folder / 'state.db'
            for state, ordinary, required in [('ACTIVE', 0, 0), ('CONFIGURED', 0, 4), (None, 2, 4)]:
                status = dict(line='AWF 1.9.4: ' + str(state), project_state=state, next_action=None)
                for require_active, code in [(False, ordinary), (True, required)]:
                    for as_json in [False, True]:
                        for case in cases:
                            with self.subTest(state=state, required=require_active, json=as_json, case=case):
                                args = ['--root', str(project), 'status']
                                if require_active: args.append('--require-active')
                                if as_json: args.append('--json')
                                if case != 'no_report': args.append('--report')
                                if case not in {'missing_both', 'missing_input', 'no_report'}:
                                    args.extend(['--report-input', str(source)])
                                if case not in {'missing_both', 'missing_store', 'no_report'}:
                                    args.extend(['--report-store', str(store)])
                                result = None
                                side_effect = None
                                if case.startswith('missing_'):
                                    result = dict(status='NOT_RUN', reason=
                                        'REPORT_ARGUMENTS_REQUIRED: supply --report-input and --report-store')
                                elif case == 'identity':
                                    result = dict(status='NOT_RUN', reason='IDENTITY_UNOBSERVED')
                                    side_effect = run_report
                                elif case in {'exception', 'oversized_exception'}:
                                    message = oversized if case == 'oversized_exception' else failure
                                    side_effect = RuntimeError(message)
                                    result = dict(status='FAILED', reason=render_bounded_diagnostic(
                                        message, code='STATUS_REPORT_FAILED'))
                                elif case in {'recorded', 'recorded_unknown'}:
                                    result = dict(status='RECORDED', report=
                                        unknown if case == 'recorded_unknown' else api().build_report(snapshot()))
                                stdout, stderr = io.StringIO(), io.StringIO()
                                with patch.object(github_status, 'project_status', return_value=status), \
                                        patch.object(status_report_store, 'run_report',
                                            return_value=result, side_effect=side_effect) as run, \
                                        patch.object(status_report_store, 'ReportStore') as reports, \
                                        redirect_stdout(stdout), redirect_stderr(stderr):
                                    self.assertEqual(main(args), code)
                                expected = status if result is None else {**status, 'status_report': result}
                                if as_json:
                                    self.assertEqual(json.loads(stdout.getvalue()), expected)
                                else:
                                    text = github_status.render_status(expected) + '\n'
                                    if result is not None:
                                        text += 'Status report: ' + result['status'] + '\n'
                                        text += (api().render_text(result['report']) if 'report' in result
                                                 else result['reason']) + '\n'
                                    self.assertEqual(stdout.getvalue(), text)
                                if case.startswith('missing_') or case == 'no_report':
                                    run.assert_not_called()
                                else:
                                    run.assert_called_once_with(project, source, store)
                                reports.assert_not_called()
                                self.assertFalse(store.exists())
                                if result is not None and result['status'] in {'NOT_RUN', 'FAILED'}:
                                    diagnostic = (result['reason'] if result['status'] == 'FAILED'
                                        else render_bounded_diagnostic(result['reason'], code='STATUS_REPORT_NOT_RUN'))
                                    self.assertEqual(stderr.getvalue(), diagnostic + '\n')
                                    self.assertEqual(len(stderr.getvalue().splitlines()), 1)
                                    self.assertLessEqual(len(stderr.getvalue().rstrip('\n').encode('utf-8')), 4096)
                                    if case == 'oversized_exception':
                                        self.assertIn('<truncated>', stderr.getvalue())
                                        self.assertNotIn('\x1b', stderr.getvalue())
                                else:
                                    self.assertEqual(stderr.getvalue(), '')

    def test_module_has_no_operational_imports(self):
        module=api()
        import ast
        tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8-sig'))
        imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        self.assertFalse(set(imports)&{'model_routing','planning','store','controller','installer','publication','host_preflight','policy','merge_train'})
        for field in ['effective_lanes','start_gap_minutes']:
            from test_status_report import snapshot
            data=snapshot(); data['eta_inputs'][field]=True
            with self.assertRaises(ValidationError): module.build_report(data)


class OptInUnknownRoutingTests(unittest.TestCase):
    def test_coded_unknown_routing_remains_unknown_in_projection(self):
        unknown = dict(status='UNKNOWN', reason='INVALID_ROUTING: incomplete configuration')
        self.assertEqual(api().build_report({'routing':unknown})['routing'], unknown)


class RevisionBoundaryTests(unittest.TestCase):
    def test_forecast_utc_unrepresentable_anchors_raise_validation_error(self):
        from test_status_report import snapshot
        for anchor in ['0001-01-01T00:00:00+01:00',
                       '9999-12-31T23:59:59-01:00']:
            for invalid_graph in [False, True]:
                data = snapshot()
                data['eta_inputs']['forecast_anchor_at'] = anchor
                if invalid_graph:
                    data['eta_inputs']['scenarios']['p50']['tasks'][0]['predecessors'] = ['missing']
                original = copy.deepcopy(data)
                with self.subTest(anchor=anchor, invalid_graph=invalid_graph):
                    with self.assertRaisesRegex(ValidationError, 'forecast anchor'):
                        api().build_report(data)
                    self.assertEqual(data, original)

    def test_forecast_representable_utc_boundaries_and_completion_overflow(self):
        from test_status_report import snapshot
        cases = [
            ('0001-01-01T01:00:00+01:00', '0001-01-01T00:00:00.000000Z',
             '0001-01-01T00:50:00.000000Z'),
            ('9999-12-31T22:00:00-01:00', '9999-12-31T23:00:00.000000Z',
             '9999-12-31T23:50:00.000000Z'),
            ('9999-12-31T22:59:59-01:00', '9999-12-31T23:59:59.000000Z', None)]
        for anchor, utc, completion in cases:
            data = snapshot()
            data['eta_inputs']['forecast_anchor_at'] = anchor
            with self.subTest(anchor=anchor):
                eta = api().build_report(data)['eta']
                self.assertEqual((eta['p50_minutes'], eta['p80_minutes']), (50, 50))
                self.assertEqual(eta['basis'], data['eta_inputs'])
                expected = ({'status': 'UNKNOWN', 'reason': 'FORECAST_RANGE'}
                            if completion is None else dict(status='KNOWN', anchor_at=utc,
                            p50_completion_at=completion, p80_completion_at=completion))
                self.assertEqual(eta['forecast'], expected)

    def test_projection_operational_fail_on_call_matrix(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from test_status_report import snapshot, binding

        # Real existing attributes only: a renamed/missing seam must fail setup.
        seams = {
            'planning': ('planning.build_backlog_graph', 'planning.plan_lanes',
                         'planning.recompute_plan', 'planning_graph.build_ticket_plan',
                         'planning_graph.validate_ticket_plan'),
            'routing_selection': ('model_routing.select_route', 'model_routing._select_route'),
            'ledger': ('model_routing.RoutingLedger.__init__',
                       'model_routing.RoutingLedger.reserve', 'model_routing.RoutingLedger.settle',
                       'store.Store.reserve_budget', 'store.Store.settle_budget'),
            'controller': ('continuous_controller.ContinuousControllerStore.__init__',
                           'continuous_controller.production_controller_cycle'),
            'resource_probing': ('host_preflight.preflight', 'host_preflight.run',
                                 'planning.produce_planning_resource_observation'),
            'recovery': ('store.Store.recover_inflight',
                         'continuous_controller.ContinuousControllerStore.recover_dispatches',
                         'publication.PublicationRecordStore.recover'),
            'reconciliation': ('model_routing.RoutingLedger.reconcile',
                               'store.Store.reconcile_operation',
                               'continuous_controller.production_batch_jira_reconciliation'),
            'registration': ('store.Store.register_request',
                             'continuous_controller.ContinuousControllerStore.register_overlap_allowance'),
            'publication': ('owner_publication.prepare_owner_publication',
                            'owner_publication.resume_owner_publication',
                            'publication.PublicationRecordStore.write')}
        targets = []
        for category, paths in seams.items():
            for path in paths:
                parts = path.split('.')
                owner = importlib.import_module('agentic.' + parts[0])
                for part in parts[1:-1]:
                    owner = getattr(owner, part)
                self.assertTrue(callable(getattr(owner, parts[-1])), path)
                targets.append((category, path, owner, parts[-1]))

        for case in ['valid', 'partial', 'missing', 'semantic_graph', 'numeric_type',
                     'anchor_underflow', 'anchor_overflow', 'lane_evidence', 'routing']:
            data = snapshot()
            cohort = binding()
            data['routing'] = dict(binding=cohort, declarations=[dict(role='writer',
                scope=dict(kind='role', id=None, stream=None), model='supplied-model',
                reasoning_effort='high', source='fixture/route')], proposals=[dict(
                role='worker', source='fixture/proposal', binding=copy.deepcopy(cohort),
                value=dict(status='KNOWN', model='supplied-model', reasoning_effort='high',
                           dispatch_performed=False))], observations=[])
            if case == 'partial':
                data.pop('inventory'); data.pop('assignments')
            elif case == 'missing':
                data = {}
            elif case == 'semantic_graph':
                data['eta_inputs']['scenarios']['p50']['tasks'][0]['predecessors'] = ['missing']
            elif case == 'numeric_type':
                data['eta_inputs']['effective_lanes'] = True
            elif case.startswith('anchor_'):
                data['eta_inputs']['forecast_anchor_at'] = (
                    '0001-01-01T00:00:00+01:00' if case == 'anchor_underflow'
                    else '9999-12-31T23:59:59-01:00')
            elif case == 'lane_evidence':
                data['lane_evidence']['baseline']['effective'] = 9
            elif case == 'routing':
                data['routing']['declarations'][0]['scope']['kind'] = 'invalid'
            original = copy.deepcopy(data)
            with self.subTest(case=case), ExitStack() as stack:
                guards = [(category, path, stack.enter_context(patch.object(owner, name,
                    side_effect=AssertionError('Operational call: ' + path))))
                    for category, path, owner, name in targets]
                if case in {'numeric_type', 'anchor_underflow', 'anchor_overflow',
                            'lane_evidence', 'routing'}:
                    with self.assertRaises(ValidationError):
                        api().build_report(data)
                else:
                    report = api().build_report(data)
                    if case == 'missing':
                        self.assertEqual(report['eta'], {'status': 'UNKNOWN', 'reason': 'MISSING_ETA_INPUTS'})
                    elif case == 'semantic_graph':
                        self.assertEqual(report['eta']['status'], 'UNKNOWN')
                        self.assertTrue(report['eta']['reason'].startswith('INVALID_ETA_GRAPH:'))
                        self.assertEqual(report['outstanding']['count'], 3)
                    else:
                        self.assertEqual(report['eta']['status'], 'KNOWN')
                        self.assertEqual(report['eta']['p50_minutes'], 50)
                    self.assertEqual(json.loads(api().render_json(report)), report)
                    self.assertEqual({line.split(': ', 1)[0]: json.loads(line.split(': ', 1)[1])
                                      for line in api().render_text(report).splitlines()}, report)
                self.assertEqual(data, original)
                for category, path, guard in guards:
                    with self.subTest(category=category, seam=path):
                        guard.assert_not_called()


class GroupCountContractTests(unittest.TestCase):
    def test_generated_contract_requires_bounded_integer_count_in_each_group(self):
        from test_status_report import snapshot
        report = api().build_report(snapshot())
        # Supply the required values independently of production projection.
        for group, count in zip(report['priority']['groups'], [1, 2]):
            group['count'] = count
        contracts = Contracts(ROOT / '.agentic/schemas')
        contracts.validate('status-report', report)
        for index in [0, 1]:
            for invalid in ['missing', True, -1, 0.5, 2**53, '2', None]:
                bad = copy.deepcopy(report)
                group = bad['priority']['groups'][index]
                if invalid == 'missing': group.pop('count')
                else: group['count'] = invalid
                with self.subTest(index=index, invalid=invalid), self.assertRaises(ValidationError):
                    contracts.validate('status-report', bad)

    def test_counts_equal_membership_exclude_terminal_assignments_and_render_in_both_forms(self):
        from test_status_report import snapshot
        data = snapshot()
        data['assignments']['records'].extend([
            dict(key='AWF-4', tier=1, cap=1, source='fixture/done'),
            dict(key='AWF-5', tier=3, cap=3, source='fixture/cancelled')])
        for terminal in [False, True]:
            with self.subTest(terminal=terminal):
                if terminal: data['inventory']['tickets'][0]['status'] = 'DONE'
                report = api().build_report(data)
                groups = report['priority']['groups']
                self.assertEqual([group['count'] for group in groups], [1, 1 if terminal else 2])
                self.assertEqual(sum(group['count'] for group in groups), report['outstanding']['count'])
                for group in groups:
                    self.assertEqual(group['count'], len(group['tickets']))
                    self.assertTrue(set(group['tickets']).isdisjoint({'AWF-4', 'AWF-5'}))
                    self.assertTrue(set(group['sources']).isdisjoint({'fixture/done', 'fixture/cancelled'}))
                self.assertEqual(json.loads(api().render_json(report))['priority'], report['priority'])
                text = {line.split(': ', 1)[0]:json.loads(line.split(': ', 1)[1])
                        for line in api().render_text(report).splitlines()}
                self.assertEqual(text['priority'], report['priority'])
        for ticket in data['inventory']['tickets']: ticket['status'] = 'DONE'
        self.assertEqual(api().build_report(data)['priority'], dict(status='KNOWN', groups=[]))


if __name__=='__main__': unittest.main()
