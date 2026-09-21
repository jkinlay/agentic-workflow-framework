"""Strict serialization, timestamp parsing, and domain-separated fingerprints.

AWF-C14N-1 is intentionally not RFC 8785: floating-point JSON numbers are
forbidden. Strings preserve codepoints; requirement normalization is explicit.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import ValidationError

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValidationError(f"Duplicate mapping key: {key}")
        result[key] = value
    return result


def validate_value(value: Any, depth=0):
    if depth > 64:
        raise ValidationError("Document nesting exceeds 64 levels")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError("Mapping keys must be strings")
            validate_value(key, depth + 1)
            validate_value(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            validate_value(child, depth + 1)
    elif isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValidationError("Invalid Unicode scalar value") from exc
    elif value is None or isinstance(value, bool):
        pass
    elif isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValidationError("Integer exceeds interoperable JSON range")
    else:
        raise ValidationError("Only JSON values with integer numbers are supported")


def loads(text: str):
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Document exceeds 8 MiB")
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
        validate_value(value)
        return value
    except (ValueError, RecursionError) as exc:
        raise ValidationError(f"Invalid JSON: {exc}") from exc


def load(path: Path):
    data = path.read_bytes()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Document exceeds 8 MiB")
    if path.suffix.lower() == ".json":
        return loads(data.decode("utf-8"))
    return load_yaml(data)


def load_yaml(data):
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Document exceeds 8 MiB")
    import yaml

    class StrictLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValidationError("YAML aliases are not supported")
            return super().compose_node(parent, index)

    def mapping(loader, node):
        return _pairs([(loader.construct_object(k), loader.construct_object(v))
                       for k, v in node.value])

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        value = yaml.load(data.decode("utf-8"), Loader=StrictLoader)
        validate_value(value)
        return value
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise ValidationError(f"Invalid YAML: {exc}") from exc


def canonical(value) -> bytes:
    validate_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def fingerprint(domain: str, value) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", domain):
        raise ValidationError("Invalid hash domain")
    return hashlib.sha256(b"AWF-C14N-1\0" + domain.encode() + b"\0" + canonical(value)).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValidationError("Timestamp must be RFC3339 with explicit timezone")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise ValidationError(f"Invalid timestamp: {value}") from exc


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def fresh(observed: str, now: str, max_age_seconds: int, skew_seconds=30):
    age = (timestamp(now) - timestamp(observed)).total_seconds()
    if age < -skew_seconds or age > max_age_seconds:
        raise ValidationError("Evidence is stale or implausibly future-dated")


def unique(items, key, label):
    values = [item[key] for item in items]
    if len(set(values)) != len(values):
        raise ValidationError(f"Duplicate {label}")
    return set(values)
