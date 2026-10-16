"""Environment construction for every AWF child process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from dataclasses import dataclass
import hashlib
import threading
from types import MappingProxyType


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

_MIB = 1024 * 1024


class ChildProcessPolicyError(ValueError):
    """A closed runner policy or its shared budget was violated."""


@dataclass(frozen=True)
class RunnerPolicy:
    """Immutable resource policy for one class of owned child operation."""

    name: str
    stdin_limit: int
    stdout_limit: int
    stderr_limit: int
    aggregate_output_limit: int
    watched_file_limit: int
    timeout: float
    operation_budget: int

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ChildProcessPolicyError("RUNNER_POLICY: name must be non-empty text")
        integer_values = (
            self.stdin_limit,
            self.stdout_limit,
            self.stderr_limit,
            self.aggregate_output_limit,
            self.watched_file_limit,
            self.operation_budget,
        )
        if any(type(value) is not int or value < 0 for value in integer_values):
            raise ChildProcessPolicyError("RUNNER_POLICY: caps must be non-negative integers")
        if (not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool)
                or self.timeout <= 0):
            raise ChildProcessPolicyError("RUNNER_POLICY: timeout must be positive")
        if self.aggregate_output_limit > self.stdout_limit + self.stderr_limit:
            raise ChildProcessPolicyError(
                "RUNNER_POLICY: aggregate cap cannot exceed the channel caps")
        if self.operation_budget < self.aggregate_output_limit:
            raise ChildProcessPolicyError(
                "RUNNER_POLICY: operation budget cannot be smaller than one operation")

    @property
    def operation_budget_limit(self):
        return self.operation_budget

    def make_budget(self, name=None, *, operation_limit=None):
        """Create mutable accounting without making the policy itself mutable."""
        return OperationBudget(
            name or self.name,
            byte_limit=self.operation_budget,
            operation_limit=operation_limit,
        )


GIT_RUNNER_POLICY = RunnerPolicy(
    "git", _MIB, 32 * _MIB, 32 * _MIB, 32 * _MIB, 32 * _MIB, 300, 64 * _MIB)
HOST_PROVIDER_RUNNER_POLICY = RunnerPolicy(
    "host-provider", _MIB, 8 * _MIB, _MIB, 8 * _MIB, 8 * _MIB, 120, 8 * _MIB)
CODEX_LOG_RUNNER_POLICY = RunnerPolicy(
    "codex-log", _MIB, 32 * _MIB, 8 * _MIB, 32 * _MIB, 32 * _MIB, 3600, 64 * _MIB)
STREAMING_RUNNER_POLICY = RunnerPolicy(
    "streaming", _MIB, 8 * _MIB, 8 * _MIB, 8 * _MIB, 8 * _MIB, 300, 64 * _MIB)

# Aliases keep policy selection readable without making the mapping mutable.
GIT_POLICY = GIT_RUNNER_POLICY
HOST_PROVIDER_POLICY = HOST_PROVIDER_RUNNER_POLICY
CODEX_LOG_POLICY = CODEX_LOG_RUNNER_POLICY
STREAMING_POLICY = STREAMING_RUNNER_POLICY
RUNNER_POLICIES = MappingProxyType({
    policy.name: policy for policy in (
        GIT_RUNNER_POLICY,
        HOST_PROVIDER_RUNNER_POLICY,
        CODEX_LOG_RUNNER_POLICY,
        STREAMING_RUNNER_POLICY,
    )
})


class OperationBudget:
    """Thread-safe, non-resettable accounting shared by related operations."""

    def __init__(self, name, *, byte_limit, operation_limit=None):
        if not isinstance(name, str) or not name:
            raise ChildProcessPolicyError("OPERATION_BUDGET: name must be non-empty text")
        if type(byte_limit) is not int or byte_limit < 0:
            raise ChildProcessPolicyError("OPERATION_BUDGET: byte limit is invalid")
        if (operation_limit is not None
                and (type(operation_limit) is not int or operation_limit < 1)):
            raise ChildProcessPolicyError("OPERATION_BUDGET: operation limit is invalid")
        self._name = name
        self._byte_limit = byte_limit
        self._operation_limit = operation_limit
        self._consumed_bytes = 0
        self._operations = 0
        self._lock = threading.Lock()

    @property
    def name(self):
        return self._name

    @property
    def byte_limit(self):
        return self._byte_limit

    @property
    def consumed_bytes(self):
        with self._lock:
            return self._consumed_bytes

    @property
    def remaining_bytes(self):
        with self._lock:
            return self._byte_limit - self._consumed_bytes

    @property
    def operations(self):
        with self._lock:
            return self._operations

    def start_operation(self):
        with self._lock:
            if (self._operation_limit is not None
                    and self._operations >= self._operation_limit):
                raise ChildProcessPolicyError(
                    f"OPERATION_BUDGET: {self._name} operation limit exceeded")
            self._operations += 1

    def consume(self, amount):
        if type(amount) is not int or amount < 0:
            raise ChildProcessPolicyError("OPERATION_BUDGET: debit is invalid")
        with self._lock:
            if amount > self._byte_limit - self._consumed_bytes:
                raise ChildProcessPolicyError(
                    f"OPERATION_BUDGET: {self._name} byte limit exceeded")
            self._consumed_bytes += amount


_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(\bauthorization\s*:\s*(?:bearer|token|basic)\s+)[^\s,;]+"),
    re.compile(
        r"(?i)((?<![a-z0-9_])[\"']?(?:(?:[a-z0-9]+[_-])*api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|private[_-]?token|client[_-]?secret|password|"
        r"passwd|secret|token)"
        r"[\"']?\s*[=:]\s*[\"']?)[^\s,;&\"']+"),
    re.compile(
        r"(?i)((?<![a-z0-9_])--(?:(?:[a-z0-9]+[_-])*api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|private[_-]?token|client[_-]?secret|password|"
        r"passwd|secret|token)(?:=|\s+))[^\s,;&\"']+"),
    re.compile(r"(?i)(https?://)[^/@\s]+@"),
    re.compile(r"(?i)\b(?:github_pat_|gh[pousr]_|sk-)[A-Za-z0-9_-]+"),
)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\s:;,]*)*"),
    re.compile(r"(?i)\b[A-Z]:[\\/](?:[^\\/\s:;,]+[\\/])*[^\\/\s:;,]*"),
    re.compile(r"(?<![:/\w])/(?:[^/\s:;,\"'\[\]{}()]+/)*[^/\s:;,\"'\[\]{}()]+"),
)


def _diagnostic_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return str(value)
    return value


def render_diagnostic(value, *, credentials=(), paths=()):
    """Render untrusted diagnostics without credentials, local paths, or controls."""
    text = _diagnostic_text(value)
    for credential in credentials:
        if isinstance(credential, bytes):
            credential = credential.decode("utf-8", errors="replace")
        if isinstance(credential, str) and credential:
            text = text.replace(credential, "<redacted-credential>")
    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: match.group(1) + "<redacted-credential>", text)
        else:
            text = pattern.sub("<redacted-credential>", text)
    for path in paths:
        rendered_path = os.fspath(path)
        if isinstance(rendered_path, bytes):
            rendered_path = rendered_path.decode("utf-8", errors="replace")
        if rendered_path:
            text = text.replace(rendered_path, "<redacted-path>")
    for pattern in _LOCAL_PATH_PATTERNS:
        text = pattern.sub("<redacted-path>", text)
    rendered = []
    for character in text:
        codepoint = ord(character)
        if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
            rendered.append(f"\\x{codepoint:02x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def diagnostic_digest(value):
    """Return a digest of raw diagnostic bytes without retaining those bytes."""
    raw = (value if isinstance(value, bytes)
           else _diagnostic_text(value).encode("utf-8", errors="replace"))
    return hashlib.sha256(raw).hexdigest()

ISOLATED_GIT_ENV = {
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


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
        text = raw.decode("utf-8")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            # PROJECT_CONFIG.yaml is JSON-shaped YAML; owner comments are
            # supported and preserved by upgrades, so read it as strict YAML.
            from .canonical import load_yaml
            value = load_yaml(raw)
        configured = value.get("execution", {}).get("child_env_strip_extra", [])
    except Exception as exc:
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


def isolated_git_env(base=None, *, extra=None):
    """Build a Git child environment without inherited ``GIT_*`` controls.

    Git treats environment names case-insensitively on some supported hosts, so
    filtering is case-insensitive everywhere.  Controlled values are added only
    after the inherited namespace has been removed; callers may then add an
    explicit, operation-specific value such as a fixed author date.
    """
    environment = child_env(base)
    for key in list(environment):
        if key.upper().startswith("GIT_") or key.upper() in {"PYTHONHOME", "PYTHONPATH"}:
            environment.pop(key)
    environment.update(ISOLATED_GIT_ENV)
    if extra:
        environment.update(extra)
    return child_env(environment)
