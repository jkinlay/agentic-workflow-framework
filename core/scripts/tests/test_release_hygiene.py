from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from release_hygiene import check_release, stale_versions, word_budget
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
        (self.root / 'MIGRATION-v1.6-to-v1.8.md').write_text('Historical release 1.6.0\n', encoding='utf-8')
        (self.root / 'README.md').write_text('# Guide\nMIGRATION-v1.6-to-v1.8.md\nAWF1.2 urn:awf:1.2\n', encoding='utf-8')
        check_release(self.root)

    def test_second_h1_rejected(self):
        (self.root / 'README.md').write_text('# Guide\n# Appendix\n', encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'H1'):
            check_release(self.root)

    def test_documentation_budget_is_enforced(self):
        (self.root / 'README.md').write_text('excess ' * 801, encoding='utf-8')
        with self.assertRaisesRegex(ValidationError, 'documentation budget'):
            check_release(self.root)

    def test_each_document_can_pass_even_when_total_exceeds_6000(self):
        for index in range(9):
            (self.root / f'guide-{index}.md').write_text('word ' * 800, encoding='utf-8')
        result = check_release(self.root)
        self.assertGreater(result['documentation_words'], 6000)
        self.assertEqual(result['documentation_words'], result['markdown_total_words'])

    def test_generated_manifest_excluded_from_caps_but_counted(self):
        before = check_release(self.root)
        (self.root / 'MANIFEST.md').write_text('generated ' * 9000, encoding='utf-8')
        after = check_release(self.root)
        self.assertEqual(before['documentation_words'], after['documentation_words'])
        self.assertEqual(9000, after['generated_manifest_words'])
        self.assertEqual(before['markdown_total_words'] + 9000, after['markdown_total_words'])
        self.assertIsNone(next(x for x in after['documentation_file_budgets'] if x['path'] == 'MANIFEST.md')['limit'])

    def test_specification_and_runbook_have_1200_word_caps(self):
        for name in ['SPECIFICATION.md', '.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md', 'EXAMPLE-RUNBOOK.md']:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('word ' * 1200, encoding='utf-8')
                check_release(self.root)
                path.write_text('word ' * 1201, encoding='utf-8')
                with self.assertRaisesRegex(ValidationError, '1201 > 1200'):
                    check_release(self.root)
                path.unlink()

    def test_native_prompt_and_template_each_have_350_word_caps(self):
        for name in ['.agentic/prompts/worker.md', 'scripts/prompt_templates/worker.md', '.agentic/review-loop/worker-prompt.md']:
            self.assertEqual((350, 'native_prompt'), word_budget(Path(name)))
        template = self.root / 'scripts/prompt_templates/worker.md'
        generated = self.root / '.agentic/prompts/worker.md'
        for count in [350, 351]:
            template.write_text('word ' * count, encoding='utf-8')
            generated.write_text('word ' * count, encoding='utf-8')
            if count == 350:
                check_release(self.root)
            else:
                with self.assertRaisesRegex(ValidationError, '351 > 350'):
                    check_release(self.root)

    def test_stale_scan_includes_powershell_yml_and_text(self):
        for suffix in ['.ps1', '.yml', '.txt']:
            with self.subTest(suffix=suffix):
                path = self.root / ('operator' + suffix)
                path.write_text('AWF 1.7 release\n', encoding='utf-8')
                with self.assertRaisesRegex(ValidationError, 'stale operational'):
                    check_release(self.root)
                path.unlink()

    def test_stale_detection_survives_major_boundaries_and_preserves_protocol(self):
        for current, old in [('2.0.0', '1.8.0'), ('3.0.0', '2.9.0')]:
            with self.subTest(current=current):
                for body in [f'AWF {old}', f'AWF-v{old}.zip', f'agentic-workflow-template-v{old}/',
                             f'"template_version": "{old}"', f'--version {old}', f'Version {old}']:
                    self.assertEqual([old], stale_versions(body, current))
                self.assertEqual([], stale_versions(f'AWF {current}', current))
        for body in ['AWF1.2 authorization', 'urn:awf:1.2:gate', 'PyYAML==6.0.2\njsonschema==4.25.1',
                     'python 3.11\nrequirements version 1.6.0',
                     '{"$id":"urn:awf:1.2:gate", "title":"Agentic Workflow 1.2 — gate"}']:
            self.assertEqual([], stale_versions(body, '3.0.0'))

    def test_future_major_real_tree_fixture_uses_current_release(self):
        for version in ['2.0.0', '3.0.0']:
            for path in (self.root / 'scripts/prompt_templates').glob('*.md'):
                (self.root / '.agentic/prompts' / path.name).write_text(
                    path.read_text(encoding='utf-8').replace('{{VERSION}}', version), encoding='utf-8')
            (self.root / 'README.md').write_text(f'# AWF {version}\n', encoding='utf-8')
            self.assertEqual(version, check_release(self.root, version=version)['version'])


if __name__ == '__main__':
    unittest.main()
