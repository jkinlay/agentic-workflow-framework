"""Environment construction for every AWF child process."""
from __future__ import annotations

import os


PROVIDER_API_KEY_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
})

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


def scrub_process_env():
    """Remove provider API keys from this process environment."""
    for key in list(os.environ):
        if key.upper() in PROVIDER_API_KEY_ENV_VARS:
            os.environ.pop(key, None)


def child_env(base=None, *, extra=None):
    """Copy a child environment and remove provider API keys.

    ``base=None`` copies the current process environment. Optional additions are
    applied before filtering so callers cannot accidentally restore a key.
    """
    environment = dict(os.environ if base is None else base)
    if extra is not None:
        environment.update(extra)
    for key in list(environment):
        if key.upper() in PROVIDER_API_KEY_ENV_VARS:
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
