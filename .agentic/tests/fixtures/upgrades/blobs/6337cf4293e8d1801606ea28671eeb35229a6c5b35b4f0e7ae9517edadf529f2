"""Supervised transport tests: local loopback fixtures only, no provider calls."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

if __package__:
    from . import test_claude_adapter as fixtures
    from .test_runtime_boundaries import answer
else:
    import test_claude_adapter as fixtures
    from test_runtime_boundaries import answer

run_model = fixtures.run_model
common = fixtures.common


@contextmanager
def local_endpoint(mode):
    stopped = threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if mode == "headers":
                stopped.wait(5)
                return
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", "/credential-sink")
                self.end_headers()
                return
            finding = {"severity": "high", "title": "Retained blind finding", "description": "Synthetic evidence",
                       "file": "src/a.py", "blocking": True}
            raw = common.json_bytes(answer([finding] if mode == "claims" else None))
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if mode == "success" or mode == "claims" and len(calls) == 1:
                self.wfile.write(raw)
                self.wfile.flush()
                return
            # Bytes arrive more often than the two-second socket inactivity
            # timeout; only the supervisor's whole-attempt deadline stops this.
            try:
                for byte in raw:
                    if stopped.wait(0.05):
                        return
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class ProcessTransportTests(unittest.TestCase):
    def run_local(self, mode, *, claims=False):
        children = []
        real_popen = subprocess.Popen
        def track(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as tmp, local_endpoint(mode) as (url, calls):
            report_path, meta_path = Path(tmp) / "report.json", Path(tmp) / "meta.json"
            argv = ["--in", fixtures.RunModelTests().make_input(tmp, claims), "--schema", fixtures.SCHEMA_PATH,
                    "--out", str(report_path), "--meta-out", str(meta_path), "--model", "synthetic-model",
                    "--reviewer-identity", fixtures.IDENTITY, "--base-url", url, "--allow-loopback-http", "--timeout",
                    "5" if mode in {"success", "redirect"} else "2"]
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "LOCAL-TEST-SECRET", "ANTHROPIC_AUTH_TOKEN": "",
                                              "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}), \
                    mock.patch.object(run_model.subprocess, "Popen", side_effect=track), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                start = time.monotonic()
                code = run_model.main(argv)
                elapsed = time.monotonic() - start
            report, meta = json.loads(report_path.read_bytes()), json.loads(meta_path.read_bytes())
            self.assertEqual([], common.validate_report(report, fixtures.SCHEMA))
            self.assertTrue(all(child.poll() is not None for child in children))
            self.assertNotIn("LOCAL-TEST-SECRET", json.dumps([report, meta]) + stderr.getvalue())
            return code, report, meta, elapsed, list(calls), len(children)

    def test_slow_drip_has_real_wall_deadline_and_retained_error_report(self):
        code, report, meta, elapsed, calls, children = self.run_local("drip")
        self.assertEqual((1, "error", 1, 1), (code, report["status"], len(calls), children))
        self.assertGreaterEqual(elapsed, 1.9)
        self.assertLess(elapsed, 5)
        phase = meta["phases"][0]
        self.assertEqual(1, phase["transport_attempts"])
        self.assertEqual("unknown", phase["provider_outcome"])
        self.assertFalse(phase["usage_known"])
        self.assertTrue(phase["local_transport_terminated"])
        self.assertIn("wall deadline", report["error"])

    def test_waiting_for_headers_is_also_bounded(self):
        code, report, meta, elapsed, calls, children = self.run_local("headers")
        self.assertEqual((1, "error", 1, 1), (code, report["status"], len(calls), children))
        self.assertLess(elapsed, 5)
        self.assertTrue(meta["phases"][0]["local_transport_terminated"])

    def test_claims_timeout_preserves_successful_blind_evidence(self):
        code, report, meta, elapsed, calls, children = self.run_local("claims", claims=True)
        self.assertEqual((1, "error", 2, 2), (code, report["status"], len(calls), children))
        self.assertEqual("Retained blind finding", report["findings"][0]["title"])
        self.assertEqual("blind", report["findings"][0]["phase"])
        self.assertTrue(meta["phases"][0]["usage_known"])
        self.assertFalse(meta["phases"][1]["usage_known"])
        self.assertLess(elapsed, 5)

    def test_successful_real_child_reports_observed_usage(self):
        code, report, meta, _, calls, children = self.run_local("success")
        self.assertEqual((0, "no_findings", 1, 1), (code, report["status"], len(calls), children))
        self.assertTrue(meta["phases"][0]["usage_known"])
        self.assertEqual("response_received", meta["phases"][0]["provider_outcome"])

    def test_redirect_never_receives_a_second_credential_bearing_request(self):
        code, report, _, _, calls, children = self.run_local("redirect")
        self.assertEqual((1, "error", ["/v1/messages"], 1), (code, report["status"], calls, children))
        self.assertIn("redirect", report["error"])

    def test_protocol_rejects_trailing_duplicate_or_wrong_shapes(self):
        for raw in (b'{} {}', b'{"status":"ok","status":"error"}', b'[]',
                    b'{"status":"ok","response":[],"provider_outcome":"response_received"}',
                    b'x' * (run_model.MAX_PROTOCOL_BYTES + 1)):
            with self.subTest(raw=raw[:80]):
                process = mock.Mock(returncode=0)
                process.communicate.return_value = raw, None
                with mock.patch.object(run_model.subprocess, "Popen", return_value=process), \
                        self.assertRaises(run_model.ModelRequestError):
                    run_model._run_transport_once({}, 1)
                self.assertEqual(1, process.communicate.call_count)

    def test_cleanup_failures_are_structured_unknown_outcomes_and_never_retry(self):
        for mode in ("kill-denied", "reap-error", "reap-timeout", "pipe-error"):
            with self.subTest(mode=mode):
                process = mock.Mock()
                failure = OSError("LOCAL-TEST-SECRET") if mode == "pipe-error" else subprocess.TimeoutExpired("child", 1)
                cleanup = subprocess.TimeoutExpired("child", 2) if mode == "reap-timeout" else OSError("LOCAL-TEST-SECRET")
                process.communicate.side_effect = [failure, cleanup]
                if mode == "kill-denied":
                    process.kill.side_effect = OSError("LOCAL-TEST-SECRET")
                with mock.patch.object(run_model.subprocess, "Popen", return_value=process) as start, \
                        self.assertRaises(run_model.ModelRequestError) as caught:
                    run_model._run_transport_once({}, 1)
                self.assertEqual(1, start.call_count)
                self.assertFalse(caught.exception.transport["local_transport_terminated"])
                self.assertEqual("unknown", caught.exception.transport["provider_outcome"])
                self.assertNotIn("LOCAL-TEST-SECRET", str(caught.exception))

    def test_late_child_success_is_rejected_without_accepting_its_proof(self):
        process = mock.Mock(returncode=0)
        process.communicate.return_value = common.json_bytes({"status": "ok", "response": {}, "provider_outcome": "response_received"}), None
        with mock.patch.object(run_model.subprocess, "Popen", return_value=process), \
                mock.patch.object(run_model.time, "monotonic", side_effect=[0, 0.1, 1.1]), \
                self.assertRaises(run_model.ModelRequestError) as caught:
            run_model._run_transport_once({}, 1)
        self.assertEqual("unknown", caught.exception.transport["provider_outcome"])
        self.assertFalse(caught.exception.transport["usage_known"])
        self.assertTrue(caught.exception.transport["local_transport_terminated"])
        self.assertEqual(1, process.communicate.call_count)

    def test_credentials_travel_only_on_stdin_and_stderr_is_discarded(self):
        process = mock.Mock(returncode=0)
        process.communicate.return_value = common.json_bytes({"status": "ok", "response": {}, "provider_outcome": "response_received"}), None
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "LOCAL-TEST-SECRET", "GH_TOKEN": "GITHUB-TEST-SECRET"}), \
                mock.patch.object(run_model.subprocess, "Popen", return_value=process) as start:
            run_model._run_transport_once({"credential": "LOCAL-TEST-SECRET"}, 1)
        self.assertNotIn("LOCAL-TEST-SECRET", str(start.call_args))
        self.assertNotIn("GITHUB-TEST-SECRET", str(start.call_args))
        self.assertEqual(subprocess.DEVNULL, start.call_args.kwargs["stderr"])
        self.assertIn(b"LOCAL-TEST-SECRET", process.communicate.call_args.args[0])

    def test_output_token_budget_rejects_before_transport(self):
        with mock.patch.object(run_model, "anthropic_request") as request:
            for cap in (0, True, 8001, 20000):
                with self.subTest(cap=cap), self.assertRaisesRegex(common.AdapterError, "max_tokens"):
                    run_model.call_tool("https://synthetic.invalid", "synthetic", cap, 300, [], [], run_model.SUBMIT_REVIEW_TOOL)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
