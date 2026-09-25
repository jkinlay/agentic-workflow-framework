#!/usr/bin/env python3
"""One bounded provider exchange, supervised and killed by run_model on its wall deadline.

Only trusted adapter code runs here. Request/credentials arrive on stdin, never
argv or temporary files. stdout contains one bounded JSON protocol; stderr never
contains provider text. This process launches no descendants and performs no retry.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awf_review_common import (AdapterError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
                               MAX_MODEL_TIMEOUT_SECONDS, json_bytes, strict_json)

MAX_PROTOCOL_BYTES = MAX_RESPONSE_BYTES + 4096
MAX_STDIN_BYTES = MAX_REQUEST_BYTES + 32768


def validate_model_url(url: str, allow_loopback_http: bool = False) -> str:
    """Validate before credentials reach transport; never echo rejected URL text.

    HTTP is only a deliberately selected numeric-loopback fixture route, not a
    DNS/localhost exception. HTTPS still requires a host-reviewed destination.
    """
    if type(allow_loopback_http) is not bool:
        raise AdapterError("invalid loopback fixture opt-in")
    if not isinstance(url, str) or not url or any(ch.isspace() or ord(ch) <= 32 or ord(ch) == 127 for ch in url) or "\\" in url:
        raise AdapterError("invalid model URL")
    try:
        parsed = urllib.parse.urlsplit(url)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme not in {"https", "http"} or not host or not parsed.netloc or \
                parsed.username is not None or parsed.password is not None or "?" in url or "#" in url or \
                "%" in parsed.netloc or not parsed.netloc.isascii() or parsed.netloc.endswith(":") or \
                (port is not None and not 1 <= port <= 65535):
            raise ValueError("invalid model URL structure")
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += ":" + str(port)
        if parsed.netloc.lower() != authority.lower():
            raise ValueError("noncanonical model authority")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if len(host) > 253 or any(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
                                      for label in host.split(".")):
                raise ValueError("invalid model hostname")
        if parsed.scheme != "https" and not (allow_loopback_http and address is not None and address.is_loopback):
            raise ValueError("model endpoint requires HTTPS")
    except ValueError as error:
        raise AdapterError("model URL requires HTTPS without credentials, query or fragment; HTTP needs explicit numeric-loopback fixture opt-in") from error
    return url


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise AdapterError("model transport refused an HTTP redirect")


def exchange(payload: dict) -> dict:
    """Perform one exchange; test this same path with a fake urllib boundary."""
    outcome = "not_submitted"
    try:
        if not isinstance(payload, dict) or set(payload) not in ({"url", "body", "headers", "timeout"},
                {"url", "body", "headers", "timeout", "allow_loopback_http"}):
            raise AdapterError("invalid transport request protocol")
        validate_model_url(payload["url"], payload.get("allow_loopback_http", False))
        timeout = payload["timeout"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_MODEL_TIMEOUT_SECONDS:
            raise AdapterError("invalid transport timeout")
        if not isinstance(payload["headers"], dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                           for key, value in payload["headers"].items()):
            raise AdapterError("invalid transport headers")
        data = json_bytes(payload["body"])
        if len(data) > MAX_REQUEST_BYTES:
            raise AdapterError("model request exceeds its byte cap")
        request = urllib.request.Request(payload["url"], data=data, method="POST", headers=payload["headers"])
        outcome = "unknown"
        # A loopback fixture must not send its plaintext request through a remote proxy.
        handlers = [RejectRedirects()]
        if urllib.parse.urlsplit(payload["url"]).scheme == "http":
            handlers.insert(0, urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            outcome = "response_received"
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AdapterError("model response exceeds its byte cap")
            parsed = strict_json(raw, "model response")
            if not isinstance(parsed, dict):
                raise AdapterError("model response was not a JSON object")
            serialized = json_bytes(parsed)
            secrets = [value.removeprefix("Bearer ") for key, value in payload["headers"].items()
                       if key.lower() in {"x-api-key", "authorization"} and value]
            if any(secret.encode("utf-8") in serialized for secret in secrets):
                raise AdapterError("model response contained credential material")
        return {"status": "ok", "response": parsed, "provider_outcome": outcome}
    except urllib.error.HTTPError as err:
        # No provider body, URL or credential text is copied into the protocol.
        return {"status": "error", "error": f"model request failed: HTTP {err.code}", "provider_outcome": "http_error_response"}
    except AdapterError as err:
        return {"status": "error", "error": str(err), "provider_outcome": outcome}
    except (OSError, http.client.HTTPException, ValueError, TypeError) as err:
        return {"status": "error", "error": f"model transport failed ({type(err).__name__})", "provider_outcome": outcome}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            raise AdapterError("transport stdin exceeded its byte cap")
        result = exchange(strict_json(raw, "transport stdin"))
        encoded = json_bytes(result)
        if len(encoded) > MAX_PROTOCOL_BYTES:
            raise AdapterError("transport response protocol exceeded its byte cap")
    except Exception:
        # An unexpected child failure must never print a traceback containing
        # provider/credential data; the supervisor still treats it as failure.
        encoded = json_bytes({"status": "error", "error": "transport process failed safely", "provider_outcome": "unknown"})
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
