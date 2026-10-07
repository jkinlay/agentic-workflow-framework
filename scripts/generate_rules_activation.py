#!/usr/bin/env python3
"""Generate the deterministic configuration-bound rules activation template."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.providers.github import synthetic_observation
from agentic.rules_activation import activation_rules_decision


OBSERVED_AT = "2026-10-02T10:00:00Z"
NOW = "2026-10-02T10:01:10Z"


def render():
    config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
    workflow = load(ROOT / ".agentic/workflow.yaml")
    contracts = Contracts(ROOT / ".agentic/schemas")
    observation = synthetic_observation(
        config["github"]["repository"], config["github"]["base_branch"],
        observed_at=OBSERVED_AT, repository_id=config["github"]["repository_id"])
    value = activation_rules_decision(
        config, workflow, contracts, before_observation=observation, now=NOW)
    contracts.validate("rules-activation-decision", value)
    draft = {"template_for": "rules-activation-decision", "status": "UNFILLED",
             "instructions": "Replace all draft values, extract record, then validate shape AND semantics. This wrapper cannot satisfy a runtime record schema.",
             "record": value}
    return json.dumps(draft, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main():
    target = ROOT / ".agentic/templates/rules-activation-decision.yaml"
    target.write_text(render(), encoding="utf-8", newline="\n")
    print(target.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
