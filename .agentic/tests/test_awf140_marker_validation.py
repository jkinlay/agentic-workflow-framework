import re
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError  # noqa: E402
from agentic.installer import _validate_journal_marker, sha256  # noqa: E402


class JournalMarkerValidationOrderTests(unittest.TestCase):
    def setUp(self):
        self.journal_raw = b"complete recovery journal"
        self.marker = {
            "format": "awf-install-marker-2",
            "transaction_id": "11111111-1111-1111-1111-111111111111",
            "journal_sha256": sha256(self.journal_raw),
            "pending_journal_sha256": None,
            "pending_update_kind": None,
        }

    def assert_marker_error(self, marker, message):
        with self.assertRaisesRegex(ValidationError, rf"^{re.escape(message)}$"):
            _validate_journal_marker(marker, self.journal_raw)

    def test_non_dict_marker_is_invalid(self):
        self.assert_marker_error([], "Invalid installation recovery marker")

    def test_marker_missing_format_is_invalid(self):
        marker = dict(self.marker)
        del marker["format"]
        self.assert_marker_error(marker, "Invalid installation recovery marker")

    def test_marker_with_non_string_format_is_invalid(self):
        marker = dict(self.marker)
        marker["format"] = 2
        self.assert_marker_error(marker, "Invalid installation recovery marker")

    def test_complete_marker_with_unknown_string_format_is_unsupported(self):
        marker = dict(self.marker)
        marker["format"] = "awf-install-marker-3"
        self.assert_marker_error(marker, "Unsupported installation recovery marker")

    def test_incomplete_marker_with_unknown_string_format_is_invalid(self):
        for marker in (
                {"format": "awf-install-marker-3"},
                {
                    "format": "awf-install-marker-3",
                    "transaction_id": self.marker["transaction_id"],
                },
        ):
            with self.subTest(marker=marker):
                self.assert_marker_error(marker, "Invalid installation recovery marker")


if __name__ == "__main__":
    unittest.main()
