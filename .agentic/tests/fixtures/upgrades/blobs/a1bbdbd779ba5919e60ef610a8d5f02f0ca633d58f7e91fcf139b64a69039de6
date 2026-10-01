"""No-network endpoint admission and parent/child protocol regressions."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

if __package__:
    from . import test_claude_adapter as fixtures
    from .test_runtime_boundaries import Response, answer
else:
    import test_claude_adapter as fixtures
    from test_runtime_boundaries import Response, answer
import model_transport

common = fixtures.common
run_model = fixtures.run_model

BAD_URLS = [
    "http://provider.invalid", "http://127.0.0.1", "//provider.invalid", "https:///missing",
    "ftp://provider.invalid", "file:///key", "gopher://provider.invalid", "https://",
    "https://USER-SECRET:PASS-SECRET@provider.invalid", "https://@provider.invalid",
    "https://provider.invalid?x=SECRET", "https://provider.invalid?", "https://provider.invalid#SECRET",
    "https://provider.invalid#", "https://provider.invalid:0", "https://provider.invalid:65536",
    "https://provider.invalid:word", "https://provider.invalid:", "https://provider.invalid:0443",
    "https://provider.invalid\\other", "https://provider.invalid\nX-Key:SECRET", " https://provider.invalid",
    "https://provider.invalid/white space", "https://provider.invalid/\u2003space",
    "https://%70rovider.invalid", "https://provider.invalid..", "https://-provider.invalid",
    "https://provider.invalid_", "https://[::1]suffix", "https://[::1", "https://[::1%25scope]",
    "https://\u00e9xample.invalid", "https://provider.invalid/\x7f",
]


class EndpointPolicyTests(unittest.TestCase):
    def payload(self, url, **changes):
        value = {"url": url, "body": {}, "headers": {"x-api-key": "SYNTHETIC-SECRET"}, "timeout": 300}
        value.update(changes)
        return value

    def test_parent_rejects_invalid_urls_before_credentials_or_transport(self):
        for url in BAD_URLS:
            with self.subTest(url=url), mock.patch.object(run_model, "_run_transport_once") as transport, \
                    mock.patch.object(run_model.os.environ, "get", side_effect=AssertionError("credentials must not be read")):
                with self.assertRaises(common.AdapterError) as error:
                    run_model.anthropic_request(url, {}, 300)
                transport.assert_not_called()
                self.assertNotIn("SECRET", str(error.exception))

    def test_standalone_child_rejects_same_urls_without_http(self):
        for url in BAD_URLS:
            with self.subTest(url=url), mock.patch.object(model_transport.urllib.request, "build_opener") as opener:
                result = model_transport.exchange(self.payload(url))
                self.assertEqual(("error", "not_submitted"), (result["status"], result["provider_outcome"]))
                self.assertNotIn("SECRET", json.dumps(result))
                opener.assert_not_called()

    def test_https_hosts_prefixes_ports_and_ipv6_are_supported(self):
        for url in ("https://api.anthropic.com", "https://proxy.invalid/messages-prefix/", "HTTPS://API.INVALID:8443",
                    "https://[2001:db8::1]:8443", "https://127.0.0.1:443", "https://xn--example.invalid"):
            with self.subTest(url=url):
                self.assertEqual(url, model_transport.validate_model_url(url))

    def test_http_exception_requires_explicit_numeric_loopback_at_both_boundaries(self):
        for url in ("http://provider.invalid", "http://localhost:8000", "http://127.1:8000", "http://2130706433",
                    "http://127.0.0.1.evil.invalid", "http://0.0.0.0", "http://[::]", "http://192.168.0.1",
                    "http://[::1]evil", "http://user@127.0.0.1", "http://127.0.0.1#fragment"):
            with self.subTest(url=url), mock.patch.object(run_model, "_run_transport_once") as transport, \
                    mock.patch.object(model_transport.urllib.request, "build_opener") as opener:
                with self.assertRaises(common.AdapterError):
                    run_model.anthropic_request(url, {}, 300, allow_loopback_http=True)
                self.assertEqual("not_submitted", model_transport.exchange(self.payload(url, allow_loopback_http=True))["provider_outcome"])
                transport.assert_not_called(); opener.assert_not_called()
        for url in ("http://127.0.0.1:8000", "http://[::1]:8000", "http://127.0.0.2/prefix"):
            self.assertEqual(url, model_transport.validate_model_url(url, True))
            with self.assertRaises(common.AdapterError):
                model_transport.validate_model_url(url)

    def test_protocol_opt_in_is_an_exact_boolean(self):
        for flag in (None, 1, "true", [], {}):
            with self.subTest(flag=flag), mock.patch.object(run_model, "_run_transport_once") as transport, \
                    mock.patch.object(model_transport.urllib.request, "build_opener") as opener:
                with self.assertRaises(common.AdapterError):
                    run_model.anthropic_request("http://127.0.0.1", {}, 300, allow_loopback_http=flag)
                result = model_transport.exchange(self.payload("http://127.0.0.1", allow_loopback_http=flag))
                self.assertEqual("not_submitted", result["provider_outcome"])
                transport.assert_not_called(); opener.assert_not_called()

    def test_loopback_fixture_disables_inherited_proxies_and_passes_only_local_url(self):
        fake = mock.Mock()
        fake.open.return_value = Response(common.json_bytes(answer()))
        with mock.patch.object(model_transport.urllib.request, "build_opener", return_value=fake) as build:
            result = model_transport.exchange(self.payload("http://127.0.0.1:8000/v1/messages", allow_loopback_http=True))
        self.assertEqual("ok", result["status"])
        proxies = [handler for handler in build.call_args.args if isinstance(handler, model_transport.urllib.request.ProxyHandler)]
        self.assertEqual(1, len(proxies)); self.assertEqual({}, proxies[0].proxies)
        self.assertEqual("http://127.0.0.1:8000/v1/messages", fake.open.call_args.args[0].full_url)

    def test_cli_opt_in_reaches_both_phases_and_default_invalid_url_retains_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = fixtures.RunModelTests().make_input(tmp, True)
            argv = ["--in", input_path, "--schema", fixtures.SCHEMA_PATH, "--out", str(Path(tmp) / "report.json"),
                    "--model", "synthetic-model", "--reviewer-identity", fixtures.IDENTITY,
                    "--base-url", "http://127.0.0.1:8000/prefix/"]
            with mock.patch.object(run_model, "_run_transport_once") as transport, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, run_model.main(argv))
                transport.assert_not_called()
            report = json.loads(Path(tmp, "report.json").read_bytes())
            self.assertEqual([], common.validate_report(report, fixtures.SCHEMA))
            self.assertEqual("error", report["status"])
            calls = []
            def fake_transport(payload, timeout):
                calls.append(payload)
                self.assertIs(payload["allow_loopback_http"], True)
                self.assertEqual("http://127.0.0.1:8000/prefix/v1/messages", payload["url"])
                self.assertEqual(payload["url"], model_transport.validate_model_url(payload["url"], payload["allow_loopback_http"]))
                response = answer()
                if len(calls) == 2:
                    response["content"][0].update(name="assess_claims", input={"claim_assessments": [], "additional_findings": []})
                return {"status": "ok", "response": response, "provider_outcome": "response_received"}
            with mock.patch.object(run_model, "_run_transport_once", side_effect=fake_transport), \
                    mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "SYNTHETIC-SECRET", "ANTHROPIC_AUTH_TOKEN": ""}), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(0, run_model.main(argv + ["--allow-loopback-http"]))
            self.assertEqual(2, len(calls))

    def test_remote_https_never_receives_loopback_override_by_default(self):
        with mock.patch.object(run_model, "_run_transport_once", return_value={"status": "ok", "response": {}, "provider_outcome": "response_received"}) as transport, \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "SYNTHETIC-SECRET", "ANTHROPIC_AUTH_TOKEN": ""}):
            run_model.anthropic_request("https://proxy.invalid/base/", {}, 300)
        self.assertNotIn("allow_loopback_http", transport.call_args.args[0])
        self.assertEqual("https://proxy.invalid/base/v1/messages", transport.call_args.args[0]["url"])


if __name__ == "__main__":
    unittest.main()
