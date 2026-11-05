"""Preservation evidence is read-only, exact and cannot waive target identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import compare, inventory, write_report, plan


class PreservationReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        (self.root / '.agentic').mkdir(parents=True)
        self.config = self.root / '.agentic/PROJECT_CONFIG.yaml'
        self.config.write_text(json.dumps({'github': {'repository': 'example-org/example-repo',
            'repository_id': 101, 'base_branch': 'trunk'}}), encoding='utf-8')
        (self.root / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')

    def test_inventory_is_nonwriting_and_contains_modes_and_history(self):
        state = self.root / '.agentic-state/operating/changes/record.json'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'synthetic history')
        before = {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                  for p in self.root.rglob('*') if p.is_file()}
        report = inventory(self.root)
        self.assertEqual('SYNTHETIC_OWNER_CUSTOMIZATION', report['evidence_kind'])
        self.assertIn('.agentic-state/operating/changes/record.json', report['files'])
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual([], compare(report, report, {})['violations'])

    def test_exact_delta_and_identity_cannot_be_waived(self):
        before = inventory(self.root)
        path = 'PROJECT_INSTRUCTIONS.md'
        (self.root / path).write_bytes(b'changed')
        after = inventory(self.root)
        delta = json.loads(json.dumps({path: {'before': before['files'][path], 'after': after['files'][path]}}))
        self.assertFalse(compare(before, after, {})['preserved'])
        self.assertTrue(compare(before, after, delta)['preserved'])
        after['identity']['base_branch'] = 'main'
        self.assertFalse(compare(before, after, delta)['preserved'])
        after['identity']['base_branch'] = 'trunk'
        after['files'][path]['sha256'] = '0' * 64
        self.assertFalse(compare(before, after, delta)['preserved'])

    def test_unused_delta_is_not_silently_accepted(self):
        report = inventory(self.root)
        self.assertFalse(compare(report, report, {'missing': {'before': None, 'after': None}})['preserved'])

    def test_empty_directory_creation_is_detected(self):
        before = inventory(self.root)
        (self.root / 'unexpected').mkdir()
        self.assertFalse(compare(before, inventory(self.root), {})['preserved'])

    def test_plan_refuses_a_mutating_installer(self):
        def mutate(*args, **kwargs):
            (self.root / 'unexpected').mkdir()
            return {'source_manifest_sha256': '0' * 64}
        with patch('qualify_upgrade.install', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'mutated'):
                plan(ROOT, self.root, '0' * 64)

    def test_report_cannot_write_inside_source_or_project_or_overwrite(self):
        for path in (self.root / 'report.json', ROOT / 'report.json'):
            with self.assertRaises(ValueError):
                write_report(path, {}, project=self.root)
            self.assertFalse(path.exists())
        output = Path(self.temporary.name) / 'external.json'
        write_report(output, {'status': 'NOT_QUALIFIED'}, project=self.root)
        with self.assertRaises(FileExistsError):
            write_report(output, {}, project=self.root)


if __name__ == '__main__':
    unittest.main()
