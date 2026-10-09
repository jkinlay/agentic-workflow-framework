"""Windows-only contained launcher; reads one bounded argv after Job assignment."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

if __package__:
    from .child_process import child_env
else:
    # The isolated Windows launcher runs outside package mode. Load the shared
    # sanitizer from the reviewed, sealed checkout snapshot supplied as cwd.
    sys.path.insert(0, str(Path.cwd() / ".agentic" / "lib"))
    from agentic.child_process import child_env


def main() -> int:
    raw = sys.stdin.buffer.readline(64 * 1024 + 1)
    if not raw or len(raw) > 64 * 1024 or not raw.endswith(b"\n"):
        return 125
    try:
        value = json.loads(raw.decode("ascii"))
        argv = value["argv"]
        environment = value["environment"]
        if (set(value) != {"argv", "environment"}
                or not isinstance(argv, list) or not argv
                or not all(isinstance(token, str) and token for token in argv)
                or not isinstance(environment, dict)
                or not all(isinstance(name, str) and name and isinstance(item, str)
                           for name, item in environment.items())):
            return 125
    except (KeyError, UnicodeError, json.JSONDecodeError, TypeError):
        return 125
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, shell=False, check=False,
                              env=child_env(environment))
    except OSError:
        return 126
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
