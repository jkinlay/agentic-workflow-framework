"""Reporting whole-domain policy includes OFF, deletion and every binding field."""
from copy import deepcopy
import json
import unittest
from reporting_pr_fixtures import ReportingPRFixture
from agentic import operating as op
from agentic.reporting.settings import effective_reporting
import test_reporting_settings_pr as policy

class ReportingBoundaryTests(ReportingPRFixture,unittest.TestCase):
    def test_hand_edits_cannot_enable_reporting_or_bind_destinations(self):
        for mode in ('both','off'):
            value=json.loads(self.before);value['reporting']=dict(mode=mode,bindings=self.bindings())
            self.forge_config_event(value)
            self.assertEqual('off',effective_reporting(op.read_operating(self.root,self.gov).config)['mode'])
        self.assertFalse(self.calls.exists())

    test_every_confirmed_reporting_value_and_removal_is_protected=policy.ReportingSettingsPRTests.test_working_and_pr_edits_in_both_directions_and_every_binding_field
    enabled=policy.ReportingSettingsPRTests.enabled
    test_defaults_load_without_git_gh_or_owner_profile=policy.ReportingSettingsPRTests.test_absent_empty_and_explicit_off_normalize_without_git

    def test_one_confirmation_displays_all_external_changes_without_secrets(self):
        self.apply_reporting('both',**{'budgeting.accounting_enabled':True,'operating_calendar.eod.time':'18:00'})
        self.write(self.value)
        result=self.cli('show','--json')
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)
        shown=json.loads(result.stdout)
        self.assertEqual('both',shown['configuration']['reporting']['mode'])
        self.assertEqual({'reporting','budgeting','operating_calendar'},{d['path'] for d in shown['protected_settings']['differences']})
        self.assertIn('PR',shown['protected_settings']['next_action'])
