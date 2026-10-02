"""Windows-only contained launcher; reads one bounded argv after Job assignment."""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    raw = sys.stdin.buffer.readline(64 * 1024 + 1)
    if not raw or len(raw) > 64 * 1024 or not raw.endswith(b"\n"):
        return 125
    try:
        value = json.loads(raw.decode("ascii"))
        argv = value["argv"]
        if (set(value) != {"argv"} or not isinstance(argv, list) or not argv
                or not all(isinstance(token, str) and token for token in argv)):
            return 125
    except (KeyError, UnicodeError, json.JSONDecodeError, TypeError):
        return 125
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, shell=False, check=False)
    except OSError:
        return 126
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
