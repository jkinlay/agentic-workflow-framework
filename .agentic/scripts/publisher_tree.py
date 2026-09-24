#!/usr/bin/env python3
"""Verify that a publisher commit exactly matches a PUBLISHER worker result."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.gittree import verify_publisher_tree


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker-result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = load(args.worker_result)
        Contracts(ROOT / ".agentic/schemas").validate("worker-result", result)
        if result.get("commit_route") != "PUBLISHER":
            raise ValidationError("publisher verification requires commit_route PUBLISHER")
        actual = verify_publisher_tree(args.root, result["tested_tree"])
        print(json.dumps({"status": "PASS", "head_tree": actual, "tested_tree": result["tested_tree"]}, indent=2))
        return 0
    except (ValidationError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "SCOPE_VIOLATION", "reason": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
