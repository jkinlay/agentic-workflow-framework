"""Source-only Jira defaults; this module is outside installed self-tests."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / "scripts"))
from agentic.canonical import load
from agentic.publication import _detectors, _scan_text
from generate_contracts import catalog, render_catalog
KEY = "shared_owner_controller_actor_id"


class JiraSourceDefaultsTests(unittest.TestCase):
    def test_canonical_account_patterns_keep_existing_string_semantics(self):
        # Keep the previous expression literally as the independent reference.
        previous = re.compile(r"^\S(?:[\s\S]*\S)?$")
        schemas = catalog()
        patterns = [schemas['project-config']['properties']['jira']['properties'][KEY]['oneOf'][0]['pattern'],
            schemas['jira-shared-owner-evidence']['properties']['accepted_owner_policy']['properties'][KEY]['pattern']]
        characters = [chr(value) for value in range(256)]
        characters += [chr(value) for value in range(256, 0x110000) if chr(value).isspace()]
        cases = ['', 'controller', 'controller\n', 'a\nb', 'a\tb', '\u200b', '\u0301', '\U0001f642']
        for character in characters:
            cases.extend((character, character + 'a', 'a' + character, 'a' + character + 'b'))
        for pattern in patterns:
            current = re.compile(pattern)
            for value in cases:
                with self.subTest(pattern=pattern, value=repr(value)):
                    self.assertEqual(bool(previous.search(value)), bool(current.search(value)))

    def test_all_jira_owner_policy_schemas_are_publication_scan_safe(self):
        detectors, allows = _detectors({}, {})
        checked = []
        for name, rendered in render_catalog().items():
            if KEY.encode() not in rendered:
                continue
            checked.append(name)
            findings = _scan_text(rendered.decode(), commit='candidate',
                path='.agentic/schemas/' + name + '.schema.json', source='head',
                detectors=detectors, allows=allows)
            self.assertEqual([], findings, name)
        self.assertIn('project-config', checked)
        self.assertIn('jira-shared-owner-evidence', checked)

    def test_source_and_generated_defaults_null(self):
        for relative in ('defaults/PROJECT_CONFIG.yaml', 'PROJECT_CONFIG.yaml', 'templates/source-config/PROJECT_CONFIG.yaml',
                         'examples/PROJECT_CONFIG.yaml', 'examples/unconfigured-project.yaml',
                         'examples/workflow-content-binding-project.yaml'):
            with self.subTest(path=relative):
                self.assertIsNone(load(ROOT / '.agentic' / relative)['jira'][KEY])


if __name__ == "__main__":
    unittest.main()
