from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PortableDocumentationTests(unittest.TestCase):
    def test_jira_text_mirrors_in_progress_in_review_and_done_events(self):
        for relative in ('SKILL.md', 'references/workflow.md'):
            with self.subTest(relative=relative):
                body = (ROOT / relative).read_text(encoding='utf-8')
                for event, targets in (
                        ('WORKER_STARTED', ('In Progress', 'status_map.in_progress')),
                        ('PR_READY', ('In Review', '`in_review`')),
                        ('JIRA_RECONCILED', ('Done', '`done`'))):
                    self.assertIn(event, body)
                    self.assertTrue(any(target in body for target in targets))
                self.assertIsNone(re.search(r'Done[- ]only|one[^.]*Done transition|non-done[^.]*reserved', body, re.I))

    def test_upgrade_guidance_supports_verified_192_receipts(self):
        text_paths = [ROOT / 'SKILL.md']
        text_paths.extend(sorted((ROOT / 'references').glob('*.md')))
        text_paths.extend(sorted((ROOT / 'scripts').glob('*.py')))
        portable_text = '\n'.join(path.read_text(encoding='utf-8') for path in text_paths)
        self.assertIsNone(re.search(r'upgrade mode\s+is\s+same-version only', portable_text, re.I))

        workflow = (ROOT / 'references/workflow.md').read_text(encoding='utf-8')
        self.assertRegex(workflow, r'verified 1\.9\.2 receipt[^\n]*--mode upgrade')
        self.assertRegex(workflow, r'other cross-version adoption uses backed-up install')


if __name__ == '__main__':
    unittest.main()
