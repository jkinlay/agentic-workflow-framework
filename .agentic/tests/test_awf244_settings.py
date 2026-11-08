"""Calendar subtree validation only; operating.py integration is separately owned."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.operating_calendar import effective_calendar, next_review_presentation, validate_calendar


class CalendarSettingsTests(unittest.TestCase):
    def test_explicit_settings_survive_resolution_without_rewriting_legacy_bytes(self):
        root = {"version": 1, "streams": {"count": 12}, "reporting": {"mode": "off"},
                "operating_calendar": {"timezone": "America/New_York",
                                       "eod": {"time": "19:15", "weekdays": [7, 1]}}}
        before = canonical(root)
        first = effective_calendar(root)
        second = effective_calendar(deepcopy(root))
        self.assertEqual(first, second)
        self.assertEqual(before, canonical(root))
        self.assertEqual(first["eod"], {"time": "19:15", "weekdays": [1, 7]})
        self.assertEqual(root["reporting"]["mode"], "off")

    def test_mixed_invalid_subtree_leaves_caller_unchanged(self):
        root = {"streams": {"count": 12}, "operating_calendar": {
            "timezone": "Europe/London", "eod": {"time": "18:00"},
            "review_batching": {"cutoff_time": "06:00"}}}
        before = deepcopy(root)
        with self.assertRaises(ValidationError):
            effective_calendar(root)
        self.assertEqual(root, before)

    def test_calendar_never_accepts_adapter_or_reporting_enablement(self):
        for key in ("reporting", "budgeting", "chat", "adapters", "epics", "merge_authorized"):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                validate_calendar({key: True})

    def test_equivalent_defaults_share_calendar_identity(self):
        ready = "2026-11-05T08:00:00Z"
        default = next_review_presentation(ready, {})
        explicit = effective_calendar({})
        explicit["review_batching"]["weekdays"].reverse()
        self.assertEqual(default, next_review_presentation(ready, explicit))


if __name__ == "__main__":
    unittest.main()
