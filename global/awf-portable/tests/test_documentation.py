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


if __name__ == '__main__':
    unittest.main()
