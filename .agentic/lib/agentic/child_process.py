"""Environment construction for every AWF child process."""
from __future__ import annotations

import os


PROVIDER_API_KEY_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
})


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
