"""JSON Schema validation using a closed local reference registry."""
from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import threading
import uuid
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from . import ValidationError
from .canonical import load, timestamp, validate_value


def schema_inventory(schema_dir: Path):
    """Return generated schema names from the on-disk closed catalog."""
    return {path.name.removesuffix(".schema.json")
            for path in schema_dir.glob("*.schema.json")}


def checker():
    formats = FormatChecker()

    @formats.checks("date-time", raises=(ValidationError, ValueError))
    def date_time(value):
        if not isinstance(value, str):
            return True
        timestamp(value)
        return True

    @formats.checks("uuid", raises=(ValueError, AttributeError))
    def uuid_format(value):
        return not isinstance(value, str) or str(uuid.UUID(value)) == value

    @formats.checks("uri", raises=(ValueError, TypeError))
    def uri_format(value):
        return not isinstance(value, str) or (
            urlsplit(value).scheme in {"https", "urn"} and not any(c.isspace() for c in value))

    return formats


_SCHEMA_CHECK_CACHE_MAX_ENTRIES = 128
_SCHEMA_CHECK_CACHE_MAX_BYTES = 8 * 1024 * 1024
_SCHEMA_CHECK_CACHE = OrderedDict()
_SCHEMA_CHECK_CACHE_BYTES = 0
_SCHEMA_CHECK_CACHE_LOCK = threading.Lock()


def _schema_check_key(value, validator_class):
    """Encode ordinary JSON only; keep types and the complete schema identity."""
    def ordinary(item):
        if type(item) is dict:
            return all(type(key) is str and ordinary(child) for key, child in item.items())
        if type(item) is list:
            return all(ordinary(child) for child in item)
        return type(item) in (str, int, float, bool, type(None))

    try:
        if not ordinary(value):
            return None
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        return None
    if len(raw) > _SCHEMA_CHECK_CACHE_MAX_BYTES:
        return None
    # The full representation includes $id and avoids digest collisions.
    return validator_class, raw


def _check_schema(value):
    """Reuse successful checks, never loaded objects or instance validation."""
    global _SCHEMA_CHECK_CACHE_BYTES
    validator_class = Draft202012Validator
    key = _schema_check_key(value, validator_class)
    if key is None:
        validator_class.check_schema(value)
        return
    with _SCHEMA_CHECK_CACHE_LOCK:
        if key in _SCHEMA_CHECK_CACHE:
            _SCHEMA_CHECK_CACHE.move_to_end(key)
            return
        validator_class.check_schema(value)
        if _SCHEMA_CHECK_CACHE_MAX_ENTRIES < 1:
            return
        size = len(key[1])
        while _SCHEMA_CHECK_CACHE and (
                len(_SCHEMA_CHECK_CACHE) >= _SCHEMA_CHECK_CACHE_MAX_ENTRIES or
                _SCHEMA_CHECK_CACHE_BYTES + size > _SCHEMA_CHECK_CACHE_MAX_BYTES):
            old_key, _ = _SCHEMA_CHECK_CACHE.popitem(last=False)
            _SCHEMA_CHECK_CACHE_BYTES -= len(old_key[1])
        _SCHEMA_CHECK_CACHE[key] = None
        _SCHEMA_CHECK_CACHE_BYTES += size


class Contracts:
    def __init__(self, schema_dir: Path):
        self.schemas = {}
        resources = []
        for path in sorted(schema_dir.glob("*.schema.json")):
            value = load(path)
            _check_schema(value)
            name = path.name.removesuffix(".schema.json")
            if value.get("$id") != f"urn:awf:1.2:{name}":
                raise ValidationError(f"Invalid schema identity: {path.name}")
            self.schemas[name] = value
            resources.append((value["$id"], Resource.from_contents(value)))
        self.registry = Registry().with_resources(resources)
        self.formats = checker()

    def validate(self, name: str, value):
        if name in self.schemas and name in {'status-report', 'status-report-input'}:
            from .status_report import validate_report_input, validate_report_value
            walker = validate_report_value if name == 'status-report' else validate_report_input
            walker(value)
        else:
            validate_value(value)
        if name not in self.schemas:
            raise ValidationError(f"Unknown contract {name}")
        validator = Draft202012Validator(self.schemas[name], registry=self.registry,
                                         format_checker=self.formats)
        errors = sorted(validator.iter_errors(value), key=lambda e: str(list(e.absolute_path)))
        if errors:
            details = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
                                for e in errors[:12])
            raise ValidationError(f"{name}: {details}")
        return value
