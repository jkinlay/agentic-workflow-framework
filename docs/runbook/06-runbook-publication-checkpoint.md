### Publication checkpoint

Do not treat the supplied development revisions as one assembled release.

| Supplied source | Treatment in this runbook |
|---|---|
| Main `7fcaeece` | Baseline installation/adoption interfaces; declares 1.9.4. Source: `7fcaeece:VERSION:1`. |
| PR **#205**, head `c407bcf7` | Unmerged Git-isolation and Windows alias-related evidence. Sources: `c407bcf7:.agentic/lib/agentic/git_isolation.py:63`; `c407bcf7:.agentic/tests/test_git_isolation_safe_directory.py:380`. |
| PR **#219**, head `47c5a0b4` | Unmerged planning/controller work. Its operating guide changes the pending live-wiring statement to durable admission constraints. Integration is **UNVERIFIED**. Source: `47c5a0b4:.agentic/docs/29-OPERATING-CONFIGURATION.md:39`. |
| Branch **`awf/1.9.5-awf54-657-canonical-key-successor`**, `7ddb195a` | Unmerged Jira reconciliation identity changes; not a fresh-install prerequisite when Jira is disabled. Source: `7ddb195a:.agentic/adapters/reference_controller_adapter.py:646`. |
| Branch **`awf/1.9.5-awf195up-upgrade-path-1-9-4`**, `9f44c2a1` — d2-006d | Unmerged registered 1.9.4 predecessor and recovery changes. The branch itself still declares 1.9.4. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:148`; `9f44c2a1:VERSION:1`. |
| Branch **`awf/1.9.5-awf195up-postsuite001-interrupt-recovery`** — POSTSUITE-001 | **UNVERIFIED:** no written successor revision was supplied. |

Before treating a package as an installable 1.9.5 release, integrate matching release identities and the verified predecessor registration; repair archive validation’s all-controller-values-false assertion while preserving cadence 600; resolve its undefined `source_external_unit` and `installed_external_unit` references and demonstrate complete report serialization; update AC42 preservation acceptance for the exact intended migration delta; and cover its required external-resource admission check. Reproduce generated outputs, regenerate both manifests after all included changes, and obtain candidate-bound review, required platform evidence and publication acceptance. Release readiness remains UNVERIFIED until those gates pass. The release documentation requires a pinned clean-Windows portable check and an exact-candidate publication scan. These requirements are source-supported; their successful execution for 1.9.5 is **UNVERIFIED**. Sources: `7fcaeece:.agentic/docs/32-RELEASE-PUBLISHING.md:7`; `7fcaeece:.agentic/docs/32-RELEASE-PUBLISHING.md:9`; `7fcaeece:.agentic/docs/32-RELEASE-PUBLISHING.md:11`.
