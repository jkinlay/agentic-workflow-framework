"""Reporting settings use merged/default policy, retaining CLI isolation checks."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from reporting_pr_fixtures import ReportingPRFixture
from agentic import operating as op

class ReportingOwnerAdmissionTests(ReportingPRFixture,unittest.TestCase):
    def test_modes_stay_pending_then_persist_across_fresh_process(self):
        initial=json.loads(self.before)
        for mode in ('teams','slack','both','off'):
            self.apply_reporting(mode)
            result=self.cli('show','--json')
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            shown=json.loads(result.stdout)
            config=shown['configuration']
            self.assertEqual(dict(mode=mode,bindings=self.bindings()),config['reporting'])
            self.assertEqual(initial['budgeting'],config['budgeting'])
            self.assertEqual(initial.get('operating_calendar'),config.get('operating_calendar'))
            self.assertEqual(initial['streams'],config['streams'])
            self.assertEqual(self.gov,json.loads(self.govpath.read_bytes()))
            self.assertNotIn('chat',config);self.assertFalse(shown['execution_authority'])
            self.assertIn('Reporting (effective settings)',shown['table'])
            self.assertEqual(self.git('rev-parse','refs/remotes/origin/main'),shown['protected_settings']['commit'])

    def test_off_unchanged_and_rebinding_require_admission(self):
        for choices in (('reporting.mode=off',),self.assignments(),self.assignments(bindings=list(reversed(self.bindings())))):
            result=self.cli('set','--instruction','Reporting change',*[v for a in choices for v in ('--set',a)],'--json')
            self.assertEqual(2,result.returncode,result.stdout+result.stderr)
            self.assertIn('PR',result.stdout);self.unchanged()

    def test_wrong_owner_and_foreign_bindings_refuse_before_secret_resolution(self):
        invalid=[[],[self.bindings()[0]],[self.bindings()[0]]*2]
        for field in ('project','repository','secret_ref'):
            b=self.bindings();b[0][field]='literal-secret' if field=='secret_ref' else 'foreign';invalid.append(b)
        for bindings in invalid:
            result=self.cli('set','--instruction','Invalid reporting binding',*[v for a in self.assignments(bindings=bindings) for v in ('--set',a)],'--json')
            self.assertEqual(2,result.returncode,result.stdout+result.stderr);self.unchanged()
            self.assertFalse(self.calls.exists())

    def test_mixed_reporting_budget_calendar_consumes_one_confirmation(self):
        self.apply_reporting('both',**{'budgeting.accounting_enabled':True,'budgeting.enforcement_enabled':True,'operating_calendar.eod.time':'18:00'})
        config=json.loads(self.cli('show','--json').stdout)['configuration']
        self.assertEqual('both',config['reporting']['mode'])
        self.assertTrue(config['budgeting']['accounting_enabled']);self.assertTrue(config['budgeting']['enforcement_enabled'])
        self.assertEqual('18:00',config['operating_calendar']['eod']['time'])

    def test_invalid_mixed_changes_propose_and_consume_nothing(self):
        for invalid in ('budgeting.max_tokens_per_ticket=0','operating_calendar.eod.time=25:00','streams.count=1001'):
            result=self.cli('set','--instruction','Invalid mixed reporting',*[v for a in (*self.assignments(),invalid) for v in ('--set',a)],'--json')
            self.assertEqual(2,result.returncode,result.stdout+result.stderr);self.unchanged()

    def test_historical_reporting_observation_still_validates(self):
        # Produce an ordinary local audit event; historical fields confer no authority.
        op.set_operating(self.root,self.gov,'Two streams',['streams.count=2'])
        event=json.loads(self.events()[-1])
        event['reporting_owner_observation']=dict(observation_id='historical-owner-direction',actor_id='historical-owner',
            project_id=self.gov['project']['id'],prior_settings_hash=op.fingerprint('reporting-settings',dict(mode='off',bindings=[])),
            changes=dict(mode='both',bindings=self.bindings()))
        self.assertEqual([],op._schema('operating-change',event))
        self.assertNotIn('owner_admission',event)

    def test_binding_refusal_never_calls_secret_resolver(self):
        from agentic import ValidationError
        with patch('agentic.reporting.platform.environment_secret',side_effect=AssertionError('secret read')):
            for field in ('project','repository'):
                b=self.bindings();b[0][field]='foreign'
                with self.assertRaisesRegex(ValidationError,'REPORT_SCOPE_BINDING_MISMATCH'):
                    op.set_operating(self.root,self.gov,'Invalid destination',self.assignments(bindings=b))
                self.unchanged()
