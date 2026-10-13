"""AWF-73 regression coverage for authorization candidate identifiers."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.authorization import make_request


GIT_SHA_MESSAGE = "candidate_id is the AWF candidate fingerprint, not a Git SHA"
SCHEMA_MESSAGE = "final-gate schema rejected candidate_id"


class RejectingContracts:
    def validate(self, schema, _value):
        raise ValidationError(f"{schema} schema rejected candidate_id")


class AuthorizationCandidateIdTests(unittest.TestCase):
    def test_make_request_reports_git_sha_candidate_id_before_schema_validation(self):
        gate = {"binding": {"candidate_id": "a" * 40}}

        with self.assertRaisesRegex(ValidationError, f"^{GIT_SHA_MESSAGE}$"):
            make_request(gate, RejectingContracts(), "2026-10-13T12:00:00Z")

    def test_make_request_preserves_non_sha_validation_error(self):
        gate = {"binding": {"candidate_id": "a" * 39}}

        with self.assertRaisesRegex(ValidationError, f"^{SCHEMA_MESSAGE}$"):
            make_request(gate, RejectingContracts(), "2026-10-13T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
