"""Git-independent 1.9.6 upgrade acceptance for raw batch materialization."""
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from agentic import VERSION
from agentic.canonical import load_yaml
from agentic.installer import CONFIG, INSTALLED, PROVENANCE, install, verify_installed
from source_only import skip_unless_source_repo
from upgrade_fixtures import materialize, verify_materialized


@skip_unless_source_repo('requires committed historical fixtures, without requiring Git metadata',
                         '.agentic/tests/fixtures/upgrades/versions.json')
class Upgrade196Tests(unittest.TestCase):
    def assert_retained(self, before, after, path=()):
        if isinstance(before, dict):
            for key, value in before.items():
                if path == ('template',) and key == 'expected_workflow_version':
                    continue
                self.assertIn(key, after, path)
                self.assert_retained(value, after[key], path + (key,))
        else:
            self.assertEqual(before, after, path)

    @staticmethod
    def accounting(path):
        connection = sqlite3.connect(path)
        try:
            return connection.execute('SELECT run_id, reserved_tokens, reserved_cost, '
                                      'actual_tokens, actual_cost FROM model_runs ORDER BY run_id').fetchall()
        finally:
            connection.close()

    def test_194_actual_upgrade_preserves_owner_state_through_195_and_196(self):
        with tempfile.TemporaryDirectory(prefix='awf-196-acceptance-') as temporary:
            destination = Path(temporary).resolve()
            before = materialize('1.9.4', destination)
            verify_materialized('1.9.4', destination)
            # Historical provenance carries the canonical UUID; receipt fixtures may use a marker.
            old_install_id = load_yaml((destination / PROVENANCE).read_bytes())['installation']['install_id']
            ledger = destination / '.agentic-state/routing/ledger.sqlite'
            self.assertTrue(ledger.is_file())
            accounting = self.accounting(ledger)
            self.assertTrue(accounting)
            pin = hashlib.sha256((ROOT / 'MANIFEST.json').read_bytes()).hexdigest()
            result = install(ROOT, destination, pin, mode='upgrade', configure=True, discover=False)
            steps = result['upgrade']['steps']
            self.assertEqual([('1.9.4', '1.9.5'), ('1.9.5', '1.9.6')],
                             [(step['from'], step['to']) for step in steps[:2]])
            self.assertEqual(VERSION, steps[-1]['to'])
            version_step = steps[1]
            self.assertEqual([], version_step['new_required_settings'])
            diff = version_step['configuration_diff']
            self.assertIn('-    "expected_workflow_version": "1.9.5"', diff)
            self.assertIn('+    "expected_workflow_version": "1.9.6"', diff)
            self.assertEqual(1, diff.count('\n-'))
            self.assertEqual(2, diff.count('\n+'))
            self.assertEqual(pin, verify_installed(destination))
            receipt = json.loads((destination / INSTALLED).read_bytes())
            self.assertEqual(VERSION, receipt['template_version'])
            self.assertEqual(old_install_id, receipt['install_id'])
            provenance = load_yaml((destination / PROVENANCE).read_bytes())
            self.assertEqual(VERSION, provenance['template']['version'])
            self.assertEqual(old_install_id, provenance['installation']['install_id'])
            self.assert_retained(load_yaml(before['configuration']),
                                 load_yaml((destination / CONFIG).read_bytes()))
            self.assertIsNotNone(before['operating'])
            self.assertEqual(before['operating'], (destination / 'OPERATING_CONFIG.yaml').read_bytes())
            for relative, raw in before['owner_files'].items():
                if relative not in {CONFIG, INSTALLED, PROVENANCE, '.gitignore'}:
                    self.assertEqual(raw, (destination / relative).read_bytes(), relative)
            self.assertEqual(accounting, self.accounting(ledger))
            note = '.agentic-state/legacy-note.txt'
            self.assertEqual(before['state'][note], (destination / note).read_bytes())
            archived = destination / f'.agentic-state/archive/upgrade-to-{VERSION}/files/legacy-note.txt'
            self.assertEqual(before['state'][note], archived.read_bytes())
            self.assertEqual(0, archived.stat().st_mode & stat.S_IWRITE)


if __name__ == '__main__':
    unittest.main()
