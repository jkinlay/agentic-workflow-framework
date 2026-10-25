"""Whole-catalog parity for the opt-in status-report contracts (T16)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import generate_contracts


class StatusReportContractTests(unittest.TestCase):
    def test_generator_check_clean_for_status_report_schema(self):
        generated = generate_contracts.catalog()
        self.assertTrue({'status-report', 'status-report-input'} <= generated.keys())
        actual = {path.name.removesuffix('.schema.json'): json.loads(path.read_text(encoding='utf-8'))
                  for path in (ROOT / '.agentic/schemas').glob('*.schema.json')}
        self.assertEqual(actual, generated)
        for selected in [True, False]:
            catalog = generate_contracts.catalog(include_ticket_plan=selected)
            for name in ['status-report', 'status-report-input']:
                self.assertEqual(catalog[name], generated[name])
        checked = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/generate_contracts.py'), '--check'],
                                 capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_closed_report_schema_accepts_existing_projection_and_rejects_extras(self):
        sys.path.insert(0, str(ROOT / '.agentic/lib'))
        sys.path.insert(0, str(ROOT / '.agentic/tests'))
        from agentic import ValidationError
        from agentic.contracts import Contracts
        from agentic.status_report import build_report
        from test_status_report import snapshot
        contracts = Contracts(ROOT / '.agentic/schemas')
        report = build_report(snapshot())
        contracts.validate('status-report', report)
        for value in [{**report, 'extra':1}, {**report, 'outstanding':{**report['outstanding'], 'extra':1}}]:
            with self.assertRaises(ValidationError): contracts.validate('status-report', value)


if __name__ == '__main__': unittest.main()
