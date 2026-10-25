"""Regression checks for the approved full-text and shipped-summary layout."""
from pathlib import Path
import hashlib
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from release_hygiene import word_budget
from agentic.installer import release_member

LIMITATION = (
    'Release publishing is not supported on macOS in 1.9.5. The release step fails '
    'closed because the host lacks atomic no-follow release materialization '
    'primitives. Publish from Windows or Linux.'
)
PINS = {
    'Whats-New': 'd3612a905b96063a0fe2361902c1760eb38b7c638144dbc2f27c484a2f12296f',
    'User-Interface': '3c6e78ee044061ad8242b16bb08f3d83874b70990ee91f63c46fb20540c2c9ae',
}


class PublicationDocsTests(unittest.TestCase):
    def test_full_text_bytes_and_publication_membership(self):
        for name, pin in PINS.items():
            with self.subTest(name=name):
                path = Path(f'docs/showcase/AWF-1.9.5-{name}.md')
                self.assertEqual(pin, hashlib.sha256((ROOT / path).read_bytes()).hexdigest())
                self.assertEqual((None, 'showcase_material'), word_budget(path))
                self.assertFalse(release_member(path.as_posix()))
                summary = Path(f'docs/AWF-1.9.5-{name}.md')
                self.assertTrue(release_member(summary.as_posix()))
                self.assertEqual((800, 'documentation'), word_budget(summary))

    def test_summaries_have_strict_budget_and_standalone_limitation(self):
        for name in PINS:
            with self.subTest(name=name):
                text = (ROOT / f'docs/AWF-1.9.5-{name}.md').read_text(encoding='utf-8')
                self.assertTrue(0 < len(text.split()) < 800)
                self.assertEqual(1, len(re.findall(r'^# ', text, re.M)))
                section = text.split('## Known limitations\n', 1)[1].split('\n## ', 1)[0]
                self.assertIn(LIMITATION, section.strip().split('\n\n'))
                self.assertIn('UNVERIFIED', text)

    def test_exact_readme_discovery_under_release(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        line = '[1.9.5 changes](docs/AWF-1.9.5-Whats-New.md) [UI](docs/AWF-1.9.5-User-Interface.md)'
        self.assertEqual(1, text.count(line))
        release = text.split('## Release\n', 1)[1].strip().split('\n\n')
        self.assertEqual(line, release[1])
        self.assertLessEqual(len(text.split()), 800)
        for target in re.findall(r'\]\(([^)]+)\)', line):
            self.assertTrue((ROOT / target).is_file())
            self.assertTrue(release_member(target))

    def test_required_summary_headings_and_literal_links(self):
        headings = {
            'Whats-New': ['Changes covered by the inventory', 'Upgrade and operating cautions', 'Pending and deferred work'],
            'User-Interface': ['Entry and command surfaces', 'Separate writer and critic runs', 'Adapting AWF to specification work'],
        }
        labels = {'Whats-New': "What's New", 'User-Interface': 'User Interface'}
        for name in PINS:
            with self.subTest(name=name):
                text = (ROOT / f'docs/AWF-1.9.5-{name}.md').read_text(encoding='utf-8')
                for heading in headings[name] + ['Known limitations']:
                    self.assertIn(f'## {heading}\n', text)


if __name__ == '__main__':
    unittest.main()
