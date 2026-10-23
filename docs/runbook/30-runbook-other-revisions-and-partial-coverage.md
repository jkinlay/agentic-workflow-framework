### Other revisions and partial coverage

| Revision | File | Lines | Inspection |
|---|---|---:|---|
| `c407bcf7` — PR #205 | `.agentic/lib/agentic/git_isolation.py` | 218 | Full. |
| `c407bcf7` — PR #205 | `.agentic/tests/test_git_isolation_safe_directory.py` | 417 | Full. |
| `9f44c2a1` — d2-006d | `.agentic/tests/test_upgrade_195_path.py` | 1,044 | Full; not executed. |
| `9f44c2a1` — d2-006d | `VERSION` | 1 | Full. |
| `9f44c2a1` — d2-006d | `.agentic/lib/agentic/installer.py` | 2,374 | Baseline plus diff and targeted new-code reads; not a separate full-file read. Exhaustive verification **UNVERIFIED**. |
| `9f44c2a1` — d2-006d | `.agentic/lib/agentic/upgrade.py` | 915 | Full baseline plus complete diff; transitive coverage **UNVERIFIED**. |
| `9f44c2a1` — d2-006d | `.agentic/upgrade/known-versions.json` | 174 | Partial; metadata inspected, complete embedded-manifest review **UNVERIFIED**. |
| `47c5a0b4` — PR #219 | `.agentic/docs/29-OPERATING-CONFIGURATION.md` | 92 | Baseline plus diff; controller implementation coverage **UNVERIFIED**. |
| `47c5a0b4` — PR #219 | `scripts/tests/test_cli_failure_paths.py` | **UNVERIFIED** | Matching WinError lines only. |
| `7ddb195a` — AWF-54 successor | `.agentic/adapters/reference_controller_adapter.py` | 1,456 | Changed reconciliation hunk only; remainder **UNVERIFIED**. |
| `7ddb195a` — AWF-54 successor | `.agentic/tests/test_reference_controller_adapter.py` | 1,634 | Matching lines only; remainder **UNVERIFIED**. |

Diff/stat inventories were also inspected for the supplied heads. They do not constitute full file reads. All files outside the full-read inventory, remaining changed-file contents, and uninspected transitive callers/callees are **UNVERIFIED**. A complete per-function, per-call-site, per-hunk audit is **UNVERIFIED** and is not claimed by this runbook.

## Unverified until publish

- Closure of every §2 publication-checkpoint blocker, followed by the published 1.9.5 tag, integrated commit, complete distribution, independent pins, candidate-bound release review, clean-Windows evidence and publication acceptance.
- Integration and combined behavior of PR #205, PR #219, the AWF-54 successor, and d2-006d.
- POSTSUITE-001 content and interruption-recovery acceptance.
- Native Windows, macOS, and Linux end-to-end installation, self-test, upgrade, rollback, and ACTIVE observations for the final 1.9.5 artifacts.
- Complete POSIX directory rollback under the d2-006d implementation.
- Bare-machine package installation/authentication, offline bootstrap dependency configuration, complete backup/restoration, runtime rehydration CLI, exact `ORIG_HEAD.lock` handling, and the unidentified pre-push gate.
- Exhaustive scoped-file and transitive caller/callee inspection, complete function/call-site/hunk tables, and all uninspected code identified in Appendix A.

STATUS: GAPS