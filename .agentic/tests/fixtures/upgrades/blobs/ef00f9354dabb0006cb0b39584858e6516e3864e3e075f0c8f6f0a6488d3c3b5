#!/usr/bin/env python3
"""Portable source/installed workflow reference CLI."""
from pathlib import Path
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
    raise SystemExit(main(default_root=ROOT))
