### Linux — POSIX shell

**Linux is supported by the installer’s POSIX code path**, rather than excluded by an OS allowlist: it selects `bin/python`, builds a venv, and uses POSIX file locking. The source manifest workflow also declares Linux runners. Native integrated 1.9.5 results remain **UNVERIFIED**. Sources: `7fcaeece:.agentic/lib/agentic/adoption_config.py:445`; `7fcaeece:.agentic/lib/agentic/adoption_config.py:564`; `7fcaeece:.agentic/lib/agentic/installer.py:780`; `7fcaeece:.github/workflows/manifest-check.yml:29`.

1. **Provision §1 prerequisites, verified distribution, wheels, and first repository.** Exact distribution-specific package-installation, authentication, acquisition, and ZIP-verification commands are **UNVERIFIED**. Confirm all inputs before proceeding.

2. **Preview and install the portable skill.**

   Choose a skill destination visible to the intended Codex session. These examples require that session to discover skills under `<install-dir>/skills`; installing there alone does not establish visibility. Confirm discovery of the intended AWF version before invoking `$awf`.

   ```sh
   '<python>' -B '<install-dir>/distribution/install_awf.py' --dest '<install-dir>/skills/awf' --backup-root '<install-dir>/skill-backups' --dry-run
   '<python>' -B '<install-dir>/distribution/install_awf.py' --dest '<install-dir>/skills/awf' --backup-root '<install-dir>/skill-backups'
   ```

   Sources: `7fcaeece:scripts/build_skill_distribution.py:46`; `7fcaeece:global/awf-portable/scripts/install_skill.py:387`.

   **Success/confirm:** `would-install`, then `installed`, exit 0. **Failure:** retain diagnostics/backups and resolve the named refusal. Source: `7fcaeece:global/awf-portable/scripts/install_skill.py:377`.

3. **Prepare the verified source cache.**

   ```sh
   '<python>' -B '<install-dir>/skills/awf/scripts/prepare_release.py' --cache-dir '<install-dir>/release-cache' --version 1.9.5
   ```

   Source: `7fcaeece:global/awf-portable/scripts/prepare_release.py:241`.

   **Success/confirm:** exact 1.9.5, verified source/cache, exit 0, independent manifest-pin match. **Failure:** preserve rejected cache and resolve the discrepancy. Source: `7fcaeece:global/awf-portable/scripts/prepare_release.py:219`.

4. **Create the bootstrap environment and install locked dependencies.**

   ```sh
   '<python>' -m venv --copies '<install-dir>/bootstrap-venv'
   '<install-dir>/bootstrap-venv/bin/python' -m pip install --require-hashes --only-binary=:all: -r '<install-dir>/release-cache/source/.agentic/requirements.lock'
   ```

   Sources: `7fcaeece:scripts/tests/test_installed_self_test.py:117`; `7fcaeece:scripts/bootstrap_project.py:16`.

   **Offline pip configuration: UNVERIFIED.** **Success/confirm:** successful environment and dependency installation. **Failure:** stop and resolve interpreter/artifact availability.

5. **Prepare the exclusively owned isolated adoption checkout; refresh skill discovery.** Exact branch/worktree commands are **UNVERIFIED**. Preserve existing instructions/configuration/ownership. Sources: `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:12`; `7fcaeece:scripts/build_skill_distribution.py:175`.

6. **Preview and bootstrap.**

   ```sh
   '<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --github-repo '<owner>/<repo>' --repository-id '<numeric-id>' --default-branch '<default-branch>' --codeowner '@<codeowner>' --test-command '<project-test-command>' --on-conflict backup --dry-run
   '<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --github-repo '<owner>/<repo>' --repository-id '<numeric-id>' --default-branch '<default-branch>' --codeowner '@<codeowner>' --test-command '<project-test-command>' --on-conflict backup
   ```

   Sources: `7fcaeece:scripts/bootstrap_project.py:141`; `7fcaeece:scripts/validate_git_checkout.py:337`.

   **Success/confirm:** PLAN, then CONFIGURED; inspect actual installed checks and matching digests. **Failure:** resolve residue or retain and recover the transaction.

   **[Unmerged d2-006d branch]** Its POSIX directory rollback removal refuses when identity-bound deletion cannot be guaranteed. Recovery may retain its anchor instead of completing directory cleanup. Sources: `9f44c2a1:.agentic/lib/agentic/installer.py:683`; `9f44c2a1:.agentic/tests/test_upgrade_195_path.py:897`.

7. **Verify through the installed canonical runtime.**

   ```sh
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' doctor --json
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' verify-installation
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' validate-config
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' operating show
   ```

   Sources: `7fcaeece:.agentic/lib/agentic/runtime_commands.py:10`; `7fcaeece:.agentic/lib/agentic/cli.py:394`.

   **Success/confirm:** existing canonical runtime/script, integrity true, ACCEPTED configuration, successful exits, Show and retain the operating table, paste it into the adoption PR, and offer: keep defaults, review Epics and recommend, or custom. Apply direct instructions within accepted governance; obtain acceptance before applying recommendations. **Failure:** repair exact reported blockers and repeat verification/validation.

8. **Review and record the candidate, then run the installed self-test (§4).** For an existing repository, review the installation diff and scoped LF attributes, record the adoption candidate in a local commit, and confirm that its governed bytes match the checkout before self-test. Retain the report and every skip reason. The separately supported commitless fresh-install case may skip tests requiring committed HEAD.

   ```sh
   '<repo-path>/.agentic/.venv/bin/python' -B '<repo-path>/.agentic/scripts/self_test.py' --checks-only --report '<install-dir>/reports/first-installed.json'
   ```

   Source: `7fcaeece:scripts/tests/test_installed_self_test.py:133`.

   **Success/confirm:** exit 0, PASS, zero failures/errors, reviewed skips. **Failure:** retain report and stderr progress; do not substitute quick-check success. Source: `7fcaeece:.agentic/scripts/self_test.py:407`.

9. **Complete adoption review and obtain owner merge.** Review scoped attributes, separately run project tests, complete the checklist, satisfy publication/review gates, and observe the actual receipt-changing merge. Exact project/publication commands are **UNVERIFIED/project-specific**. Sources: `7fcaeece:.agentic/templates/adoption-pr.md:5`; `7fcaeece:.agentic/docs/23-TICKET-LIFECYCLE.md:8`.

10. **Observe ACTIVE on the accepted current default checkout.**

    ```sh
    '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' status --json --require-active --adoption-pr '<adoption-pr-number>' --release-source '<install-dir>/release-cache/source' --expected-manifest-sha256 '<release-manifest-sha256>'
    ```

    Sources: `7fcaeece:.agentic/lib/agentic/cli.py:141`; `7fcaeece:.agentic/lib/agentic/providers/github_status.py:640`.

    **Success/confirm:** exit 0, ACTIVE, accepted checkout VERIFIED. **Failure:** retain the observed state and resolve named blockers. This requires live provider evidence; no such observation was performed for this runbook.

## 4. Verifying with the installed self-test

Run this at **step 8 of each OS sequence**, after installation/configuration verification and candidate preparation. For an existing repository, record the reviewed adoption or upgrade candidate before self-test so committed governed bytes match the checkout. For the supported commitless fresh-install case, retain the reasons for committed-HEAD-dependent skips.

The installed entry point is the managed `.agentic/scripts/self_test.py`, using the target repository’s canonical runtime. The source-only wrapper is not required in an installed project. Sources: `7fcaeece:scripts/tests/test_installed_self_test.py:104`; `7fcaeece:scripts/tests/test_installed_self_test.py:133`.

Acceptance requires:

- Exit **0** and final `status: PASS`.
- `tests.failures: 0`, `tests.errors: 0`, and successful test result.
- Review and retain every skipped test and its reason.
- Preserve a new report outside the installed root; existing reports are not overwritten.

Sources: `7fcaeece:.agentic/scripts/self_test.py:243`; `7fcaeece:.agentic/scripts/self_test.py:407`; `7fcaeece:.agentic/scripts/self_test.py:440`; `7fcaeece:scripts/tests/test_installed_self_test.py:138`.

The runner performs discovery, syntax, documentation, release-hygiene applicability, and test-suite phases. It emits start/phase/test events and 15-second heartbeats to stderr. Sources: `7fcaeece:.agentic/scripts/self_test.py:26`; `7fcaeece:.agentic/scripts/self_test.py:61`.

For an installed component check, source release review is `NOT_APPLICABLE`; `release_qualified` remains false. A component PASS does not qualify a release or establish ACTIVE. Sources: `7fcaeece:.agentic/scripts/self_test.py:295`; `7fcaeece:.agentic/scripts/self_test.py:421`.

Caught interruption/timeout produces `INTERRUPTED`, retains phase/test information, and exits 130. Other failures exit nonzero. An externally terminated run without a completed report is **UNVERIFIED**, not PASS. Sources: `7fcaeece:.agentic/scripts/self_test.py:422`; `7fcaeece:.agentic/scripts/self_test.py:451`.

**[POSTSUITE-001 branch]** Any additional interruption-recovery behavior is **UNVERIFIED** because no successor revision was supplied.

## 5. UPGRADE

