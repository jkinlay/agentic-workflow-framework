#!/usr/bin/env python3
"""Validate a frozen five-slot acceleration plan without running live work."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError  # noqa: E402
from agentic.canonical import MAX_DOCUMENT_BYTES  # noqa: E402
from agentic.five_slot_acceleration import validate_plan  # noqa: E402
from agentic.installer import verify_installed  # noqa: E402


def _read(path: Path, label: str) -> bytes:
    raw = path.read_bytes()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValidationError(f"{label} exceeds 8 MiB")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--pr-body", type=Path, required=True,
                        help="Locally rendered PR body; canonicalized before comparison")
    parser.add_argument("--provider-readback", type=Path, required=True,
                        help="Raw provider-returned PR body bytes")
    parser.add_argument("--inventory", type=Path,
                        default=ROOT / ".agentic/validation/five-slot-adversarial-regressions.json")
    parser.add_argument("--expected-inventory-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        verify_installed(ROOT)
        plan = _read(args.plan, "Acceleration plan")
        body = _read(args.pr_body, "PR body")
        provider = _read(args.provider_readback, "Provider readback")
        inventory = _read(args.inventory, "Permanent adversarial inventory")
        result = validate_plan(
            plan, args.expected_plan_sha256, local_pr_body=body,
            provider_readback=provider, inventory_raw=inventory,
            expected_inventory_sha256=args.expected_inventory_sha256,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "REJECTED",
            "reason": f"{type(exc).__name__}: {exc}",
            "owner_ready": "NO",
            "execution_authority": False,
        }, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
