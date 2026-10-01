#!/usr/bin/env python3
"""Compatibility entry point: validates the installed bundle and manual policy."""
from pathlib import Path
import argparse
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
try:
    from agentic.cli import main
except ImportError as exc:
    print(f"Missing dependency: {exc}. Install .agentic/requirements.lock with --require-hashes.", file=sys.stderr)
    raise SystemExit(3)
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    command = ["--root", str(args.root), "validate-config"]
    if args.config:
        command += ["--config", str(args.config)]
    raise SystemExit(main(command))
