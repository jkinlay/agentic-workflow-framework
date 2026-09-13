"""JSON Schema validation using a closed local reference registry."""
from __future__ import annotations

from pathlib import Path
import uuid
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from . import ValidationError
from .canonical import load, timestamp, validate_value


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

    @formats.checks("uri")
    def uri_format(value):
        return not isinstance(value, str) or (
            urlsplit(value).scheme in {"https", "urn"} and not any(c.isspace() for c in value))

    return formats


class Contracts:
    def __init__(self, schema_dir: Path):
        self.schemas = {}
        resources = []
        for path in sorted(schema_dir.glob("*.schema.json")):
            value = load(path)
            Draft202012Validator.check_schema(value)
            name = path.name.removesuffix(".schema.json")
            if value.get("$id") != f"urn:awf:1.2:{name}":
                raise ValidationError(f"Invalid schema identity: {path.name}")
            self.schemas[name] = value
            resources.append((value["$id"], Resource.from_contents(value)))
        self.registry = Registry().with_resources(resources)
        self.formats = checker()

    def validate(self, name: str, value):
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
