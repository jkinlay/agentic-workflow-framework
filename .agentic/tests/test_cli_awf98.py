import contextlib
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.cli import main as workflow_main  # noqa: E402
import test_authorization_awf98 as auth_fixture  # noqa: E402


class DispositionRequestCliTests(unittest.TestCase):
    def test_cap_request_outputs_exact_owner_line_and_bound_verdict(self):
        head, config, disposition, verdict = auth_fixture.cap_request_fixture()
        request_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config_path = base / "config.json"
            disposition_path = base / "disposition.json"
            verdict_path = base / "verdict.json"
            for path, value in (
                    (config_path, config),
                    (disposition_path, disposition), (verdict_path, verdict)):
                path.write_text(json.dumps(value), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch("agentic.cli.verify_installed", return_value="0" * 64), \
                    mock.patch("agentic.cli.Contracts.validate"), \
                    contextlib.redirect_stdout(stdout):
                code = workflow_main([
                    "--root", str(ROOT), "disposition-request",
                    "review-cap-disposition", str(disposition_path),
                    "--head", head, "--config", str(config_path),
                    "--review-verdict", str(verdict_path),
                    "--request-id", request_id])

        output = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(request_id, output["authorization_request_id"])
        self.assertIn(f"request={request_id}", output["expected_text"])
        self.assertEqual(
            verdict["record_id"],
            output["disposition_fields"]["review_verdict_record_id"])
        self.assertFalse(output["execution_authority"])


if __name__ == "__main__":
    unittest.main()
