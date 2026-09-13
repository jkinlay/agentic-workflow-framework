from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from release_hygiene import check_release
from generate_prompts import render_prompts
from agentic import ValidationError

class ReleaseHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        shutil.copytree(ROOT / 'scripts/prompt_templates', self.root / 'scripts/prompt_templates')
        (self.root / '.agentic/prompts').mkdir(parents=True)
        (self.root / '.agentic/docs').mkdir()
        for name, content in render_prompts(self.root).items():
            (self.root / '.agentic/prompts' / name).write_text(content, encoding='utf-8')
        for name in ['AGENTS.md','README.md']:
            (self.root / name).write_text('# Current instructions\n', encoding='utf-8')

    def test_current_generated_prompts_accepted(self):
        self.assertEqual(5, check_release(self.root)['prompts'])

    def test_old_operational_command_rejected(self):
        (self.root / 'README.md').write_text('# Guide\nUse template 1.6.0\n', encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'README.md'):
            check_release(self.root)

    def test_prompt_drift_rejected_even_with_current_version(self):
        target = self.root / '.agentic/prompts/worker.md'
        target.write_text(target.read_text(encoding='utf-8') + 'Altered instruction\n', encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'regenerate'):
            check_release(self.root)

    def test_history_and_protocol_are_not_rewritten(self):
        (self.root / 'MIGRATION-v1.6-to-v1.7.md').write_text('Historical release 1.6.0\n', encoding='utf-8')
        (self.root / 'README.md').write_text('# Guide\nMIGRATION-v1.6-to-v1.7.md\nAWF1.2 urn:awf:1.2\n', encoding='utf-8')
        check_release(self.root)

    def test_second_h1_rejected(self):
        (self.root / 'README.md').write_text('# Guide\n# Appendix\n', encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'H1'):
            check_release(self.root)

    def test_documentation_budget_is_enforced(self):
        (self.root / 'README.md').write_text('# Guide\n' + 'excess ' * 6001, encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'documentation budget'):
            check_release(self.root)
