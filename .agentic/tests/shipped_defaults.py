"""Shipped default configuration for tests that must not read adopter files.

An installed project's ``.agentic/PROJECT_CONFIG.yaml``, ``.agentic/routing.yaml``
and ``OPERATING_CONFIG.yaml`` belong to the adopter and may legitimately differ
from the release defaults: a Jira ``done`` status of "Resolved", a different
approved-model set, legacy operating settings. Tests that need release
defaults read the managed copies under ``.agentic/defaults/`` instead. The
installation verifies those copies by hash, and in the AWF source repository
they are byte-identical to the live files (AWF-305).
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / ".agentic/lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

DEFAULTS = ROOT / ".agentic/defaults"
PROJECT_CONFIG = DEFAULTS / "PROJECT_CONFIG.yaml"
ROUTING = DEFAULTS / "routing.yaml"
OPERATING_CONFIG = DEFAULTS / "OPERATING_CONFIG.yaml"


def project_config_bytes() -> bytes:
    return PROJECT_CONFIG.read_bytes()


def project_config() -> dict:
    """Return an independent copy of the shipped default project configuration."""
    return json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))


def operating_config() -> dict:
    return json.loads(OPERATING_CONFIG.read_text(encoding="utf-8"))


@contextmanager
def shipped_routing():
    """Resolve ``.agentic/routing.yaml`` references to the shipped routing defaults."""
    from agentic.model_routing import routing_context

    with routing_context(ROUTING.read_bytes(), ROUTING):
        yield
