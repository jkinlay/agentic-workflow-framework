"""AWF-55 doc 46 T16: generated report contracts, Tier 3 / FULL.

Tests red for the right reason; non-empty diff; no production code;
no test deleted, skipped or weakened; NO-FIXUP rules apply.
"""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))

import generate_contracts  # noqa: E402


class StatusReportGenerationTests(unittest.TestCase):
    def test_generator_check_clean_for_status_report_schema(self):
        names = ('status-report', 'status-report-input')
        catalog = generate_contracts.catalog()
        rendered = generate_contracts.render_catalog()
        for name in names:
            self.assertTrue(name in catalog, 'Missing generator member: ' + name)
            self.assertIn(name, rendered, 'Missing rendered member: ' + name)
            schema = json.loads(rendered[name])
            self.assertEqual(schema, catalog[name])
            self.assertEqual(schema['$id'], 'urn:awf:1.2:' + name)
            self.assertIs(schema['additionalProperties'], False)
            self.assertEqual((ROOT / '.agentic/schemas' / (name + '.schema.json')).read_bytes(),
                             rendered[name])
        # Generate and check outside the repository; compare the whole catalog.
        with tempfile.TemporaryDirectory(prefix='awf-report-generation-') as temporary:
            root = Path(temporary)
            with mock.patch.object(generate_contracts, 'ROOT', root), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(generate_contracts.main(()), 0)
                files = root / '.agentic/schemas'
                before = {p.name: p.read_bytes() for p in files.glob('*.schema.json')}
                self.assertEqual(before, {name + '.schema.json': raw
                                          for name, raw in rendered.items()})
                self.assertEqual(generate_contracts.main(('--check',)), 0)
                self.assertEqual(before, {p.name: p.read_bytes()
                                          for p in files.glob('*.schema.json')})
                for name in names:
                    path = files / (name + '.schema.json')
                    path.write_bytes(b'{}\n')
                    self.assertEqual(generate_contracts.main(('--check',)), 1)
                    self.assertEqual(generate_contracts.main(()), 0)
                    self.assertEqual(path.read_bytes(), rendered[name])
                    self.assertEqual(generate_contracts.main(('--check',)), 0)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(generate_contracts.main(('--check',)), 0)


if __name__ == '__main__':
    unittest.main()
