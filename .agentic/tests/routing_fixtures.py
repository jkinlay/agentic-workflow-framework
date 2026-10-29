"""Explicit legacy routing inputs for policy-algorithm regression scenarios."""
from pathlib import Path
from agentic.model_routing import default_policy

LEGACY_ROUTING = Path(__file__).resolve().parent / "fixtures/routing/valid.yaml"


def fixture_policy():
    return default_policy(LEGACY_ROUTING)
