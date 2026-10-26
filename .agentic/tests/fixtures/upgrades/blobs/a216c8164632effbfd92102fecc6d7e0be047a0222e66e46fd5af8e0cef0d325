# Windows host diagnostics

Version 1.9.4. Run commands from the project root. Bootstrap refreshes the ignored canonical runtime at `.agentic\.venv\Scripts\python.exe` without fetching packages and refuses linked/reparse runtime roots; POSIX uses `.agentic/.venv/bin/python`. Supply `--runtime-wheelhouse ABS_VERIFIED_WHEELHOUSE`, containing exactly one compatible wheel per locked dependency and no other files. Bootstrap verifies each complete artifact SHA-256 against `.agentic/requirements.lock`, then validates exact METADATA identity plus the complete WHEEL/RECORD member inventory, hashes, sizes, paths and types before extraction. It rejects links, unsupported install schemes and startup hooks such as `*.pth`, `sitecustomize.py` and `usercustomize.py`; mutable installed distributions are never an artifact source.

The runtime excludes system and user site-packages. Bootstrap builds it in a sibling staging directory, validates exact imports/versions under isolated Python, atomically swaps only a fully validated tree, revalidates at the final path and rolls back to the previous good runtime on failure. Completed transactions leave no staging or backup directories. If rollback itself fails, the named recovery backup is retained and reported instead of destroyed. `workflow.py doctor` reports resolved absolute paths and emits single-line PowerShell commands as plain text or ASCII-safe JSON:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' doctor
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' doctor --json
```

The generated commands quote every path as a PowerShell literal, including repositories with spaces or apostrophes. They contain no Markdown links, filename escaping or line continuations. Every supported value, including long hashes and paths, is passed as one literal argv element; these commands implement no generic JSON carrier or response-file transport. Doctor exposes an `activation_summary` extension point but does not aggregate blockers; `status` owns activation decisions.

## Preflight

Each external child observation records `executable`, `exit_code` and a bounded `diagnostic_category`. The child commands are `git config --get core.longpaths`, the PowerShell execution-policy query, `git config --get core.autocrlf`, and, only when attributes select it, `git lfs version`. A missing executable or timeout is SKIP or WARN. Every nonzero exit is WARN or SKIP, never PASS. Execution-policy text is interpreted only after exit 0 and an exact row-shape check. The effective policy is the first defined value in `MachinePolicy`, `UserPolicy`, `Process`, `CurrentUser`, `LocalMachine` precedence; restrictive lower scopes do not override a permissive effective scope, while Group Policy cannot be bypassed by lower scopes. An all-`Undefined` or `Default` result warns because the platform-specific default is not established by the row list.

## Quick installation check and full self-test

The quick installation check verifies installed bytes and configuration only. It is not a full self-test and never establishes a full pass:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' verify-installation
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' validate-config
```

The full release self-test is `python -B scripts/self_test.py`. It writes an immediate start event, phase changes, test identities and heartbeats to stderr. The bounded heartbeat interval is 15 seconds. Its final JSON remains on stdout and records separate discovery, syntax, documentation, release-hygiene and test-suite timings. `created_at`, `elapsed_seconds` and `phase_timings_seconds` are the documented nondeterministic timing fields. A caught interruption or timeout records the last completed phase and current test when available, returns nonzero and never reports PASS; an external deadline retains the last emitted progress record as partial evidence.

## Output encoding

The stable first status line is ASCII. JSON uses ASCII escapes, while other CLI text is emitted as explicit UTF-8 with backslash replacement for otherwise unencodable output. Redirected JSON therefore does not depend on the active Windows console code page.
