import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.private_deny_receipt import scan_private_deny_blob


class PrivateDenyBinaryScanTests(unittest.TestCase):
    def test_invalid_utf8_ascii_match_is_scanned_as_bytes(self):
        result = scan_private_deny_blob(b"\xffsample-marker\xfe", re.compile("sample-marker"))
        self.assertEqual(sum(result.values()), 3)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(len(digest) == 64 for digest in result))

    def test_valid_utf8_unicode_match_is_scanned_as_text(self):
        result = scan_private_deny_blob("café-marker".encode("utf-8"), re.compile("café-marker"))
        self.assertEqual(sum(result.values()), 2)
        self.assertEqual(len(result), 2)

    def test_null_heavy_utf16_bytes_are_scanned_in_both_orders(self):
        content = "sample-marker".encode("utf-16-le")
        result = scan_private_deny_blob(content, re.compile("sample-marker"))
        self.assertEqual(sum(result.values()), 1)

    def test_binary_utf8_match_is_scanned_despite_invalid_prefix(self):
        content = b"\xff" + "café-marker".encode("utf-8")
        result = scan_private_deny_blob(content, re.compile("café-marker"))
        self.assertEqual(sum(result.values()), 2)

    def test_raw_byte_match_and_every_supported_text_view_are_preserved(self):
        result = scan_private_deny_blob(b"sample-marker", re.compile("sample-marker"))
        self.assertEqual(sum(result.values()), 3)
        self.assertEqual(len(result), 3)

    def test_null_heavy_odd_length_binary_completes_all_replacement_views(self):
        content = b"sample-marker\x00\x00\xff"
        result = scan_private_deny_blob(content, re.compile("sample-marker"))
        self.assertGreaterEqual(sum(result.values()), 3)
        self.assertTrue(all(len(digest) == 64 for digest in result))

    def test_unsupported_byte_pattern_fails_closed(self):
        with self.assertRaisesRegex(ValidationError, "no supported byte representation"):
            scan_private_deny_blob(b"sample-marker", re.compile(r"\u1234"))

    def test_timeout_during_a_decoded_view_fails_closed(self):
        class TimeoutMatcher:
            pattern = "sample-marker"
            flags = 0

            def finditer(self, _text):
                raise TimeoutError("bounded matcher timed out")

        with self.assertRaisesRegex(ValidationError, "scan was incomplete"):
            scan_private_deny_blob(b"sample-marker", TimeoutMatcher())

    def test_oversize_binary_remains_unscanned(self):
        with self.assertRaisesRegex(ValidationError, "exceeds 8 MiB"):
            scan_private_deny_blob(b"x" * (8 * 1024 * 1024 + 1), re.compile("x"))


if __name__ == "__main__":
    unittest.main()
