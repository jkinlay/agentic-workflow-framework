### macOS — POSIX shell

The code supplies a POSIX installed interpreter, POSIX locking, and explicit destination resolution for the macOS temporary-directory alias. These establish a supported code path; native macOS 1.9.5 acceptance is **UNVERIFIED**. Sources: `7fcaeece:.agentic/lib/agentic/adoption_config.py:445`; `7fcaeece:.agentic/lib/agentic/installer.py:780`; `7fcaeece:scripts/bootstrap_project.py:177`.

1. **Provision prerequisites and the first repository.** Follow §1 and independently verify/extract the distribution. Exact bare-machine package, authentication, repository acquisition, and outer-ZIP verification commands are **UNVERIFIED**. Confirm all required inputs; stop on missing approval or artifacts.

2. **Preview and install the skill.**

   Choose a skill destination visible to the intended Codex session. These examples require that session to discover skills under `<install-dir>/skills`; installing there alone does not establish visibility. Confirm discovery of the intended AWF version before invoking `$awf`.

   ```sh
   '<python>' -B '<install-dir>/distribution/install_awf.py' --dest '<install-dir>/skills/awf' --backup-root '<install-dir>/skill-backups' --dry-run
   '<python>' -B '<install-dir>/distribution/install_awf.py' --dest '<install-dir>/skills/awf' --backup-root '<install-dir>/skill-backups'
   ```

   Sources: `7fcaeece:scripts/build_skill_distribution.py:46`; `7fcaeece:global/awf-portable/scripts/install_skill.py:387`.

   **Success/confirm:** `would-install`, then `installed`, exit 0. **Failure:** retain diagnostics/backups; investigate inventory, pin, path, or lock refusal. Source: `7fcaeece:global/awf-portable/scripts/install_skill.py:324`.

3. **Prepare the source cache.**

   ```sh
   '<python>' -B '<install-dir>/skills/awf/scripts/prepare_release.py' --cache-dir '<install-dir>/release-cache' --version 1.9.5
   ```

   Source: `7fcaeece:global/awf-portable/scripts/prepare_release.py:241`.

   **Success/confirm:** exit 0, `BUNDLED_RELEASE_PREPARED`, verified source/cache, exact 1.9.5 and independent pin match. **Failure:** preserve the cache; resolve the rejection without substituting 1.9.4. Sources: `7fcaeece:global/awf-portable/scripts/prepare_release.py:219`; `7fcaeece:global/awf-portable/scripts/prepare_release.py:121`.

4. **Prepare the bootstrap venv and locked dependencies.**

   ```sh
   '<python>' -m venv --copies '<install-dir>/bootstrap-venv'
   '<install-dir>/bootstrap-venv/bin/python' -m pip install --require-hashes --only-binary=:all: -r '<install-dir>/release-cache/source/.agentic/requirements.lock'
   ```

   Sources: `7fcaeece:scripts/tests/test_installed_self_test.py:117`; `7fcaeece:scripts/bootstrap_project.py:16`.

   **Offline pip configuration: UNVERIFIED**, as in Windows step 4. **Success/confirm:** successful environment/dependency installation. **Failure:** stop before bootstrap and resolve the dependency supply problem.

5. **Prepare the isolated adoption checkout and refresh skill discovery.** Preserve instructions, configuration, ownership, and exclusive adoption ownership. Exact branch/worktree commands are **UNVERIFIED**. Sources: `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:12`; `7fcaeece:scripts/build_skill_distribution.py:175`.

6. **Preview and bootstrap.**

   ```sh
   '<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --github-repo '<owner>/<repo>' --repository-id '<numeric-id>' --default-branch '<default-branch>' --codeowner '@<codeowner>' --test-command '<project-test-command>' --on-conflict backup --dry-run
   '<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --github-repo '<owner>/<repo>' --repository-id '<numeric-id>' --default-branch '<default-branch>' --codeowner '@<codeowner>' --test-command '<project-test-command>' --on-conflict backup
   ```

   Sources: `7fcaeece:scripts/bootstrap_project.py:141`; `7fcaeece:scripts/validate_git_checkout.py:337`.

   **Success/confirm:** PLAN, then CONFIGURED with two successful installed checks and matching digests. **Failure:** resolve exact residue or recover retained installation intent. Source: `7fcaeece:.agentic/lib/agentic/adoption_config.py:873`.

   **[Unmerged d2-006d branch]** If the integrated release includes its directory rollback implementation, POSIX directory removal can refuse and retain recovery authority. Do not promise complete automatic directory rollback. Source: `9f44c2a1:.agentic/lib/agentic/installer.py:683`.

7. **Verify the installed runtime and configuration.**

   ```sh
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' doctor --json
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' verify-installation
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' validate-config
   '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' operating show
   ```

   Sources: `7fcaeece:.agentic/lib/agentic/runtime_commands.py:10`; `7fcaeece:.agentic/lib/agentic/cli.py:343`.

   **Success/confirm:** canonical files exist, integrity true, configuration ACCEPTED, successful exits, Show and retain the operating table, paste it into the adoption PR, and offer: keep defaults, review Epics and recommend, or custom. Apply direct instructions within accepted governance; obtain acceptance before applying recommendations. **Failure:** repair named blockers and repeat both checks. Source: `7fcaeece:.agentic/lib/agentic/adoption_config.py:904`.

8. **Review and record the candidate, then run the installed self-test (§4).** For an existing repository, review the installation diff and scoped LF attributes, record the adoption candidate in a local commit, and confirm that its governed bytes match the checkout before self-test. Retain the report and every skip reason. The separately supported commitless fresh-install case may skip tests requiring committed HEAD.

   ```sh
   '<repo-path>/.agentic/.venv/bin/python' -B '<repo-path>/.agentic/scripts/self_test.py' --checks-only --report '<install-dir>/reports/first-installed.json'
   ```

   Source: `7fcaeece:scripts/tests/test_installed_self_test.py:133`.

   **Success/confirm:** exit 0, PASS, zero failures/errors, reviewed skips. **Failure:** preserve report/progress and resolve the exact failure. Source: `7fcaeece:.agentic/scripts/self_test.py:407`.

9. **Review, publish, and obtain owner merge.** Preserve scoped LF attributes, execute project tests separately, fill the adoption checklist, satisfy publication/review gates, and observe the receipt-changing merge. Exact project/publication commands are **UNVERIFIED/project-specific**. Sources: `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:54`; `7fcaeece:.agentic/docs/23-TICKET-LIFECYCLE.md:8`.

10. **Verify ACTIVE on the observed default checkout.**

    ```sh
    '<repo-path>/.agentic/.venv/bin/python' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' status --json --require-active --adoption-pr '<adoption-pr-number>' --release-source '<install-dir>/release-cache/source' --expected-manifest-sha256 '<release-manifest-sha256>'
    ```

    Sources: `7fcaeece:.agentic/lib/agentic/cli.py:141`; `7fcaeece:.agentic/lib/agentic/providers/github_status.py:640`.

    **Success/confirm:** exit 0, ACTIVE, accepted checkout VERIFIED. **Failure:** retain CONFIGURED and resolve reported blockers; offline/provider-unavailable execution cannot establish ACTIVE. Source: `7fcaeece:.agentic/tests/test_adoption_status.py:963`.

