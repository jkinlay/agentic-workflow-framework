### WinError 1314 — symlink privilege

Preflight reports SKIP with the specific symlink-privilege diagnostic. Its documented remedy is enabling Developer Mode or granting the symbolic-link privilege; tests needing that privilege are declared platform skips. Source: `7fcaeece:.agentic/lib/agentic/host_preflight.py:484`.

Confirm the exact preflight row and inspect self-test skips. Do not turn a skipped privilege-dependent case into PASS. A second source explicitly labels WinError 1314 as `PLATFORM_PRIVILEGE`. Source: `47c5a0b4:scripts/tests/test_cli_failure_paths.py:308`. That second file was only partially inspected; broader coverage is **UNVERIFIED**.
