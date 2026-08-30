"""Shared helpers for the AWF Claude review adapter.

Standard library only. No third-party package is imported anywhere in the
adapter so that the model job, which holds the model credential, runs no code
that was not reviewed into this repository.

Contents:
  - http_json / github_paginate: minimal HTTP client over urllib
  - validate_report: a small JSON Schema validator covering exactly the
    keywords used by schemas/review-report.schema.json
  - load_yaml_lite: a deliberately minimal YAML reader for .agentic/project.yaml
  - sha256_file, is_hex40, bounded_text, get_nested
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from typing import Any

GITHUB_API_DEFAULT = "https://api.github.com"
HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
BOT_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*\[bot\]$")
REVIEW_STATUS_MARKER = re.compile(r"awf-review-status:\s*(no_findings|findings)\b")


class AdapterError(RuntimeError):
    """A fail-closed condition. The caller exits non-zero."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_json(method: str, url: str, token: str | None = None, body: Any = None,
              extra_headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[int, Any, dict[str, str]]:
    """Perform one HTTP request and return (status, parsed_json_or_text, headers).

    Never raises on 4xx/5xx; callers decide what a status means. Tests replace
    this function to avoid the network.
    """
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "awf-review-adapter"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as err:
        raw = err.read()
        status = err.code
        resp_headers = {k.lower(): v for k, v in err.headers.items()} if err.headers else {}
    text = raw.decode("utf-8", errors="replace") if raw else ""
    try:
        parsed: Any = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = text
    return status, parsed, resp_headers


def github_paginate(url: str, token: str | None, max_pages: int = 10) -> list[Any]:
    """Collect list results across Link-header pages (up to max_pages)."""
    items: list[Any] = []
    next_url: str | None = url
    pages = 0
    while next_url and pages < max_pages:
        status, parsed, headers = http_json("GET", next_url, token)
        if status != 200 or not isinstance(parsed, list):
            raise AdapterError(f"GitHub GET {next_url} returned {status}: {str(parsed)[:300]}")
        items.extend(parsed)
        pages += 1
        next_url = None
        link = headers.get("link", "")
        for part in link.split(","):
            segment = part.strip()
            if segment.endswith('rel="next"'):
                start = segment.find("<")
                end = segment.find(">")
                if 0 <= start < end:
                    next_url = segment[start + 1:end]
    return items


# ---------------------------------------------------------------------------
# JSON Schema subset validator
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _check(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPE_CHECKS[t](value) for t in types):
            errors.append(f"{path}: expected type {types}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{index}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required '{key}'")
        properties = schema.get("properties", {})
        for key, sub in properties.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}", errors)
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unknown property '{key}'")
    for sub in schema.get("allOf", []):
        _check(value, sub, path, errors)
    if "if" in schema:
        trial: list[str] = []
        _check(value, schema["if"], path, trial)
        if not trial and "then" in schema:
            _check(value, schema["then"], path, errors)
        if trial and "else" in schema:
            _check(value, schema["else"], path, errors)


def validate_report(report: Any, schema: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty means valid)."""
    errors: list[str] = []
    _check(report, schema, "$", errors)
    return errors


# ---------------------------------------------------------------------------
# Minimal YAML reader for .agentic/project.yaml
# ---------------------------------------------------------------------------

def _scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if (text[0] == text[-1]) and text[0] in ("'", '"') and len(text) >= 2:
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    return text


def _strip_comment(line: str) -> str:
    in_quote: str | None = None
    for index, char in enumerate(line):
        if in_quote:
            if char == in_quote:
                in_quote = None
        elif char in ("'", '"'):
            in_quote = char
        elif char == "#" and (index == 0 or line[index - 1] in (" ", "\t")):
            return line[:index]
    return line


def load_yaml_lite(text: str) -> dict[str, Any]:
    """Parse the subset of YAML used by AWF project policy files.

    Supported: nested block mappings, block lists of scalars, quoted and plain
    scalars, integers, booleans, inline [] and {} , comments. Anything else
    raises AdapterError so that policy parsing fails closed rather than
    guessing.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        cleaned = _strip_comment(raw).rstrip()
        if not cleaned.strip():
            continue
        if "\t" in cleaned[: len(cleaned) - len(cleaned.lstrip())]:
            raise AdapterError("tab indentation is not supported in policy files")
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        lines.append((indent, cleaned.strip()))

    def parse_block(start: int, indent: int) -> tuple[Any, int]:
        result: Any = None
        index = start
        while index < len(lines):
            line_indent, content = lines[index]
            if line_indent < indent:
                break
            if line_indent > indent:
                raise AdapterError(f"unexpected indentation near: {content}")
            if content.startswith("- "):
                if result is None:
                    result = []
                if not isinstance(result, list):
                    raise AdapterError(f"mixed list and mapping near: {content}")
                item = content[2:].strip()
                if item.endswith(":") or re.match(r"^[^\s'\"]+:\s", item):
                    raise AdapterError(f"lists of mappings are not supported near: {content}")
                result.append(_scalar(item))
                index += 1
                continue
            match = re.match(r"^([^\s'\"][^:]*?|'[^']*'|\"[^\"]*\"):(\s+(.*))?$", content)
            if not match:
                raise AdapterError(f"cannot parse policy line: {content}")
            key = _scalar(match.group(1))
            value_text = (match.group(3) or "").strip()
            if result is None:
                result = {}
            if not isinstance(result, dict):
                raise AdapterError(f"mixed list and mapping near: {content}")
            if value_text:
                result[key] = _scalar(value_text)
                index += 1
            else:
                if index + 1 < len(lines) and lines[index + 1][0] > indent:
                    child, index = parse_block(index + 1, lines[index + 1][0])
                    result[key] = child
                else:
                    result[key] = None
                    index += 1
        return result, index

    parsed, _ = parse_block(0, lines[0][0] if lines else 0)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise AdapterError("policy file root must be a mapping")
    return parsed


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def get_nested(data: Any, dotted: str, default: Any = None) -> Any:
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_hex40(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX40.match(value))


def bounded_text(text: str, limit: int, note: str = "[truncated by AWF input bound]") -> tuple[str, bool]:
    """Truncate text to limit characters, returning (text, truncated)."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n" + note + "\n", True


def read_text_file(path: str, limit: int) -> tuple[str | None, bool, bool]:
    """Read a file as text. Returns (text, truncated, binary)."""
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if b"\x00" in raw[:8192]:
        return None, False, True
    text = raw.decode("utf-8", errors="replace")
    truncated = len(raw) > limit
    if truncated:
        text = text[:limit]
    return text, truncated, False
