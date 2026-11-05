"""AWF-244 calendar contract and boundary regressions."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import ValidationError
from agentic.operating_calendar import effective_calendar, validate_calendar, next_review_presentation


class CalendarTests(unittest.TestCase):
    def test_defaults_are_non_mutating_and_detached(self):
        config = {"version": 1, "streams": {"count": 12}}
        before = deepcopy(config)
        value = effective_calendar(config)
        self.assertEqual(config, before)
        self.assertEqual(value["timezone"], "Europe/London")
        self.assertEqual(value["work_mode"], "continuous")
        self.assertEqual(value["eod"]["weekdays"], list(range(1, 8)))
        value["eod"]["weekdays"].clear()
        self.assertEqual(len(effective_calendar(config)["eod"]["weekdays"]), 7)

    def test_invalid_contract(self):
        invalid = [None, [], {"unknown": 1}, {"timezone": "Bad/Zone"},
                   {"work_mode": "office"}, {"review_batching": None},
                   {"review_batching": {"enabled": 1}},
                   {"review_batching": {"weekdays": [True]}},
                   {"review_batching": {"weekdays": [1, 1]}},
                   {"review_batching": {"weekdays": []}},
                   {"review_batching": {"presentation_time": "7:00"}},
                   {"review_batching": {"cutoff_time": "24:00"}},
                   {"review_batching": {"presentation_time": "17:00"}},
                   {"eod": {"weekdays": None}}, {"eod": {"weekdays": [8]}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_calendar(value)

    def test_missing_iana_fails_closed_even_when_batching_disabled(self):
        with patch("agentic.operating_calendar.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            with self.assertRaisesRegex(ValidationError, "IANA.*tzdata"):
                effective_calendar({"operating_calendar": {"review_batching": {"enabled": False}}})

    def schedule(self, ready, expected, override=None):
        calendar = effective_calendar({"operating_calendar": override or {}})
        result = next_review_presentation(ready, calendar)
        self.assertEqual(result["presentation_at"], expected)
        self.assertEqual(result["ready_at"], ready)
        self.assertEqual(len(result["calendar_sha256"]), 64)
        self.assertFalse(result["merge_authorized"])
        return result

    def test_default_boundaries_and_weekends(self):
        cases = [
            ("2026-11-05T06:59:59Z", "2026-11-05T07:00:00Z"),
            ("2026-11-05T07:00:00Z", "2026-11-05T07:00:00Z"),
            ("2026-11-05T16:59:59Z", "2026-11-05T16:59:59Z"),
            ("2026-11-05T17:00:00Z", "2026-11-06T07:00:00Z"),
            ("2026-11-06T17:00:00Z", "2026-11-09T07:00:00Z"),
            ("2026-11-07T10:00:00Z", "2026-11-09T07:00:00Z"),
            ("2026-11-08T10:00:00Z", "2026-11-09T07:00:00Z"),
            ("2026-11-09T06:00:00Z", "2026-11-09T07:00:00Z")]
        for ready, expected in cases:
            with self.subTest(ready=ready):
                self.schedule(ready, expected)

    def test_london_dst_weekends(self):
        self.schedule("2026-03-27T17:00:00Z", "2026-03-30T06:00:00Z")
        self.schedule("2026-10-23T16:00:00Z", "2026-10-26T07:00:00Z")

    def test_custom_calendar_and_timezone(self):
        custom = {"timezone": "America/New_York", "review_batching": {
            "weekdays": [7], "presentation_time": "09:30", "cutoff_time": "12:00"}}
        self.schedule("2026-11-06T22:00:00Z", "2026-11-08T14:30:00Z", custom)
        self.schedule("2026-11-08T15:00:00Z", "2026-11-08T15:00:00Z", custom)

    def test_gap_first_valid_and_fold_later_occurrence(self):
        custom = {"review_batching": {"weekdays": [7], "presentation_time": "01:30"}}
        self.schedule("2026-03-29T00:00:00Z", "2026-03-29T01:00:00Z", custom)
        self.schedule("2026-10-25T00:45:00Z", "2026-10-25T01:30:00Z", custom)

    def test_disabled_and_aware_datetime(self):
        self.schedule("2026-11-07T23:00:00Z", "2026-11-07T23:00:00Z",
                      {"review_batching": {"enabled": False}})
        value = next_review_presentation(datetime(2026, 11, 5, 8, tzinfo=timezone.utc), {})
        self.assertEqual(value["ready_at"], "2026-11-05T08:00:00Z")
        with self.assertRaises(ValidationError):
            next_review_presentation(datetime(2026, 11, 5), {})


if __name__ == "__main__":
    unittest.main()
