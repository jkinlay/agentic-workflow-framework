"""Archive destinations follow the selected release and retain every prior archive."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError
from agentic.upgrade import state_archive_plan


class ReleaseArchiveTests(unittest.TestCase):
    def test_selected_target_and_all_prior_archives_are_preserved(self):
        state = {'.agentic-state/note.txt': b'synthetic owner note\n',
                 '.agentic-state/archive/upgrade-to-1.9.3/files/old.txt': b'old',
                 '.agentic-state/archive/owner-backup/record': b'owner'}
        before = dict(state)
        additions, actions = state_archive_plan(state, target_version='1.9.5')
        self.assertEqual(before, state)
        self.assertEqual(1, len(actions))
        state.update(additions)
        next_additions, next_actions = state_archive_plan(state, target_version='1.9.6')
        self.assertEqual(1, len(next_actions))
        self.assertFalse(any('/files/archive/' in path for path in next_additions))
        manifest = json.loads(next_additions['.agentic-state/archive/upgrade-to-1.9.6/manifest.json'])
        self.assertEqual('1.9.6', manifest['target_version'])
        self.assertEqual(b'synthetic owner note\n', next_additions[manifest['files'][0]['archive']])
        self.assertEqual(b'old', state['.agentic-state/archive/upgrade-to-1.9.3/files/old.txt'])

    def test_invalid_target_refuses_without_mutation(self):
        state = {'.agentic-state/note': b'owner'}
        for target in ('../escape', '1.9.6/elsewhere', None):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                state_archive_plan(state, target_version=target)
        self.assertEqual({'.agentic-state/note': b'owner'}, state)


if __name__ == '__main__':
    unittest.main()
