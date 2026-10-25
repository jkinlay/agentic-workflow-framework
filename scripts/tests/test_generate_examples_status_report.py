"""F01: report contracts must retain strict installed unfilled-form coverage."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic import ValidationError


class StatusReportExampleTests(unittest.TestCase):
    def test_f01_installed_form_catalog_includes_both_report_forms(self):
        forms = {name: load(ROOT / '.agentic/templates' / (name + '.yaml'))
                 for name in ['status-report', 'status-report-input']}
        spec = importlib.util.spec_from_file_location('awf55_self_test', ROOT / '.agentic/scripts/self_test.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        contracts = Contracts(ROOT / '.agentic/schemas')
        expected = module.unfilled_form_schema_names(contracts.schemas,
            {'operating-config','operating-change','operating-epics','operating-recommendation'})
        self.assertTrue({'status-report','status-report-input'} <= expected)
        for name in ['status-report', 'status-report-input']:
            form = forms[name]
            self.assertEqual((form['status'], form['template_for']), ('UNFILLED', name))
            module.complete_form(form['record'], contracts.schemas[name], contracts.schemas)
            with self.assertRaises(ValidationError): contracts.validate(name, form)
        paths = list((ROOT / '.agentic/templates').glob('*.yaml')) + [
            path for path in (ROOT / '.agentic/templates').glob('*.json')
            if path.name not in {'awf-main-ruleset.json','heavy-validation-capacity.json',
                                 'heavy-validation-plan.json','heavy-validation-review.json'}]
        covered = set()
        for path in paths:
            value = load(path)
            self.assertEqual(value['status'], 'UNFILLED')
            name = value['template_for']
            self.assertNotIn(name, covered); covered.add(name)
            module.complete_form(value['record'], contracts.schemas[name], contracts.schemas)
            with self.assertRaises(ValidationError): contracts.validate(name, value)
        self.assertEqual(covered, expected)

    def test_generated_report_example_validates_and_replays_unknown_snapshot(self):
        from agentic.status_report import build_report, loads_report
        report = loads_report((ROOT / '.agentic/examples/status-report.json').read_bytes())
        Contracts(ROOT / '.agentic/schemas').validate('status-report', report)
        self.assertEqual(report, build_report({}))


if __name__ == '__main__': unittest.main()
