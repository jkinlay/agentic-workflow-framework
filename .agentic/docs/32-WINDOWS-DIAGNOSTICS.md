# Windows host diagnostics

Version 1.9.3. Run commands from the project root. Bootstrap refreshes the ignored canonical runtime at `.agentic\.venv\Scripts\python.exe` without fetching packages and refuses linked/reparse runtime roots; POSIX uses `.agentic/.venv/bin/python`. The runtime excludes system and user site-packages. Bootstrap copies only exact versions named by `.agentic/requirements.lock`, verifies every copied file against its installed wheel `RECORD` SHA-256 before and after copying, and rejects missing, mismatched, linked or unverified distributions. `workflow.py doctor` reports resolved absolute paths and emits single-line PowerShell commands as plain text or ASCII-safe JSON:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' doctor
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' doctor --json
```

The generated commands quote every path as a PowerShell literal, including repositories with spaces or apostrophes. They contain no Markdown links, filename escaping or line continuations. Put long hashes and paths in a command-supported JSON or response file. Doctor exposes an `activation_summary` extension point but does not aggregate blockers; `status` owns activation decisions.

## Preflight

Each external child observation records `executable`, `exit_code` and a bounded `diagnostic_category`. The child commands are `git config --get core.longpaths`, the PowerShell execution-policy query, `git config --get core.autocrlf`, and, only when attributes select it, `git lfs version`. A missing executable or timeout is SKIP or WARN. Every nonzero exit is WARN or SKIP, never PASS. Execution-policy text is interpreted only after exit 0 and an exact row-shape check. The effective policy is the first defined value in `MachinePolicy`, `UserPolicy`, `Process`, `CurrentUser`, `LocalMachine` precedence; restrictive lower scopes do not override a permissive effective scope, while Group Policy cannot be bypassed by lower scopes. An all-`Undefined` or `Default` result warns because the platform-specific default is not established by the row list.

## Quick installation check and full self-test

The quick installation check verifies installed bytes and configuration only. It is not a full self-test and never establishes a full pass:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' verify-installation
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' validate-config
```

The full release self-test is `python -B scripts/self_test.py`. It writes an immediate start event, phase changes, test identities and heartbeats to stderr. The bounded heartbeat interval is 15 seconds. Its final JSON remains on stdout and records separate discovery, syntax, documentation, release-hygiene and test-suite timings. `created_at`, `elapsed_seconds` and `phase_timings_seconds` are the documented nondeterministic timing fields. An interruption records the last completed phase and current test when available, returns nonzero and never reports PASS.

## Output encoding

The stable first status line is ASCII. JSON uses ASCII escapes, while other CLI text is emitted as explicit UTF-8 with backslash replacement for otherwise unencodable output. Redirected JSON therefore does not depend on the active Windows console code page.
