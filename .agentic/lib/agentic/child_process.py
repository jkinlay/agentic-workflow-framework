"""Environment construction for every AWF child process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re


PROVIDER_API_KEY_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
})
HOST_AUTH_ENV_VARS = frozenset({"GH_TOKEN", "GITHUB_TOKEN"})
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_CONFIG_BYTES = 1024 * 1024
_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "PROJECT_CONFIG.yaml"


class ChildEnvironmentError(ValueError):
    """A configured child-environment exclusion is unsafe or ambiguous."""


def validate_child_env_strip_extra(value):
    """Return case-insensitive configured exclusions after strict validation."""
    if not isinstance(value, list):
        raise ChildEnvironmentError("execution.child_env_strip_extra must be an array")
    normalized = []
    seen = set()
    for index, name in enumerate(value):
        if not isinstance(name, str) or not name or not name.strip():
            raise ChildEnvironmentError(
                f"execution.child_env_strip_extra[{index}] must be a non-empty environment name")
        if name != name.strip() or not _ENVIRONMENT_NAME.fullmatch(name):
            raise ChildEnvironmentError(
                f"execution.child_env_strip_extra[{index}] is not a valid environment name")
        folded = name.upper()
        if folded in HOST_AUTH_ENV_VARS:
            raise ChildEnvironmentError(
                f"execution.child_env_strip_extra[{index}] may not name {folded}")
        if folded in seen:
            raise ChildEnvironmentError(
                "execution.child_env_strip_extra contains a case-insensitive duplicate")
        seen.add(folded)
        normalized.append(folded)
    return frozenset(normalized)


def configured_child_env_strip_extra(config_path=None):
    """Read the reviewed project exclusions; malformed configured policy fails closed."""
    path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG
    if not path.exists():
        return frozenset()
    raw = path.read_bytes()
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ChildEnvironmentError("PROJECT_CONFIG.yaml exceeds the child-environment bound")
    try:
        value = json.loads(raw.decode("utf-8"))
        configured = value.get("execution", {}).get("child_env_strip_extra", [])
    except (UnicodeError, json.JSONDecodeError, AttributeError) as exc:
        raise ChildEnvironmentError(
            "PROJECT_CONFIG.yaml cannot supply child-environment exclusions") from exc
    return validate_child_env_strip_extra(configured)


def _excluded_environment_names(strip_extra=None, config_path=None):
    configured = (configured_child_env_strip_extra(config_path)
                  if strip_extra is None else validate_child_env_strip_extra(strip_extra))
    return PROVIDER_API_KEY_ENV_VARS | configured


def scrub_process_env(*, strip_extra=None, config_path=None):
    """Remove provider API keys from this process environment."""
    excluded = _excluded_environment_names(strip_extra, config_path)
    for key in list(os.environ):
        if key.upper() in excluded:
            os.environ.pop(key, None)


def child_env(base=None, *, extra=None, strip_extra=None, config_path=None):
    """Copy a child environment and remove provider API keys.

    ``base=None`` copies the current process environment. Optional additions are
    applied before filtering so callers cannot accidentally restore a key.
    """
    excluded = _excluded_environment_names(strip_extra, config_path)
    environment = dict(os.environ if base is None else base)
    if extra is not None:
        environment.update(extra)
    for key in list(environment):
        if key.upper() in excluded:
            environment.pop(key)
    return environment
