### Windows — PowerShell

1. **Provision the bare-machine prerequisites and first repository.**

   Install the approved Python, Git, GitHub CLI, and Codex distributions; obtain the independently verified AWF distribution, compatible offline wheels, and first repository. Exact OS package-installation, authentication, repository acquisition, and outer-ZIP verification commands are **UNVERIFIED** in the supplied sources.

   **Success/confirm:** all prerequisite inputs in §1 exist and their independent approvals are recorded. **Failure:** stop at the missing input; do not execute an unverified distribution.

2. **Preview and install the portable skill.**

   Choose a skill destination visible to the intended Codex session. These examples require that session to discover skills under `<install-dir>/skills`; installing there alone does not establish visibility. Confirm discovery of the intended AWF version before invoking `$awf`.

   ```powershell
   & '<python>' -B '<install-dir>/distribution/install_awf.py' --dest '<install-dir>/skills/awf' --backup-root '<install-dir>/skill-backups' --dry-run
   & '<python>' -B '<install-dir>/distribution/install_awf.py' --dest '<install-dir>/skills/awf' --backup-root '<install-dir>/skill-backups'
   ```

   Launcher forwarding: `7fcaeece:scripts/build_skill_distribution.py:46`. Destination and backup arguments: `7fcaeece:global/awf-portable/scripts/install_skill.py:387`.

   **Success/confirm:** preview reports `would-install`; application reports `installed`, exit 0, with the selected destination. Fresh installation requires no previous AWF skill. Sources: `7fcaeece:global/awf-portable/scripts/install_skill.py:324`; `7fcaeece:global/awf-portable/scripts/install_skill.py:377`; `7fcaeece:global/awf-portable/tests/test_install_skill.py:78`.

   **Failure:** retain the diagnostic and any reported backup. Resolve pin, inventory, link, destination, or lock errors before another attempt. Do not blindly delete crash artifacts. Source: `7fcaeece:global/awf-portable/references/skill-installation.md:11`.

3. **Prepare the verified 1.9.5 source cache.**

   ```powershell
   & '<python>' -B '<install-dir>/skills/awf/scripts/prepare_release.py' --cache-dir '<install-dir>/release-cache' --version 1.9.5
   ```

   Interface and generated source location: `7fcaeece:global/awf-portable/scripts/prepare_release.py:202`; `7fcaeece:global/awf-portable/scripts/prepare_release.py:241`.

   **Success/confirm:** exit 0 and the §2 cache-success fields; compare the returned manifest digest with the independent release pin. **Failure:** preserve the rejected cache and investigate; a version mismatch must stop this sequence.

4. **Create the bootstrap environment and install locked dependencies.**

   ```powershell
   & '<python>' -m venv '<install-dir>/bootstrap-venv'
   & '<install-dir>/bootstrap-venv/Scripts/python.exe' -m pip install --require-hashes --only-binary=:all: -r '<install-dir>/release-cache/source/.agentic/requirements.lock'
   ```

   Venv command precedent: `7fcaeece:scripts/tests/test_installed_self_test.py:119`. Locked dependency command: `7fcaeece:scripts/bootstrap_project.py:16`.

   **Offline restriction:** the cited pip command does not itself prohibit network access. A complete offline pip configuration/command for this bootstrap environment is **UNVERIFIED** in the inspected sources. Execute it only with an independently approved offline package configuration; otherwise stop here.

   **Success/confirm:** environment creation and locked dependency installation exit successfully. **Failure:** resolve the interpreter, dependency, or artifact problem before bootstrap.

5. **Prepare the isolated adoption checkout.**

   Refresh Codex skill discovery and invoke `$awf` for the intended repository. Explain that installation changes governance files and will be proposed for owner merge. Preserve project-specific instructions separately from managed AGENTS content. Sources: `7fcaeece:scripts/build_skill_distribution.py:175`; `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:10`.

   **Exact branch/worktree commands: UNVERIFIED.** The guide requires isolation but does not provide one universally applicable command. **Success/confirm:** `<repo-path>` is the exclusively owned adoption checkout, separate from release source. **Failure:** resolve ownership, isolation, or existing policy conflicts before writing installation files.

6. **Preview, then bootstrap.**

   ```powershell
   & '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --github-repo '<owner>/<repo>' --repository-id '<numeric-id>' --default-branch '<default-branch>' --codeowner '@<codeowner>' --test-command '<project-test-command>' --on-conflict backup --dry-run
   & '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --github-repo '<owner>/<repo>' --repository-id '<numeric-id>' --default-branch '<default-branch>' --codeowner '@<codeowner>' --test-command '<project-test-command>' --on-conflict backup
   ```

   Supported flags: `7fcaeece:scripts/bootstrap_project.py:141`. End-to-end bootstrap example and assertions: `7fcaeece:scripts/validate_git_checkout.py:337`.

   **Success/confirm:** preview reports `PLAN`; actual bootstrap reports CONFIGURED, exit 0, with two successful installed subprocess checks. Check recorded commands, source pin, accepted configuration, and matching policy/operating digests. Sources: `7fcaeece:.agentic/lib/agentic/adoption_config.py:857`; `7fcaeece:scripts/validate_git_checkout.py:344`.

   **Failure:** for exit 1, resolve exact configuration residue. For integrity failure or NOT_RUN checks, preserve unexpected files and repair the reported integrity/import problem. For retained installation intent, follow §5 recovery. Source: `7fcaeece:.agentic/lib/agentic/adoption_config.py:864`.

7. **Confirm through the installed runtime.**

   ```powershell
   & '<repo-path>/.agentic/.venv/Scripts/python.exe' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' doctor --json
   & '<repo-path>/.agentic/.venv/Scripts/python.exe' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' verify-installation
   & '<repo-path>/.agentic/.venv/Scripts/python.exe' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' validate-config
   & '<repo-path>/.agentic/.venv/Scripts/python.exe' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' operating show
   ```

   Canonical paths and command construction: `7fcaeece:.agentic/lib/agentic/runtime_commands.py:10`; `7fcaeece:.agentic/lib/agentic/runtime_commands.py:27`. Doctor and check results: `7fcaeece:.agentic/lib/agentic/cli.py:343`; `7fcaeece:.agentic/lib/agentic/cli.py:394`.

   **Success/confirm:** doctor identifies existing canonical runtime/script; verification exits 0 with `integrity_valid: true`; validation exits 0 with `status: ACCEPTED`; Show and retain the operating table, paste it into the adoption PR, and offer: keep defaults, review Epics and recommend, or custom. Apply direct instructions within accepted governance; obtain acceptance before applying recommendations. **Failure:** use exact reported remedies and repeat both checks after repair.

8. **Review and record the candidate, then run the installed self-test (§4).** For an existing repository, review the installation diff and scoped LF attributes, record the adoption candidate in a local commit, and confirm that its governed bytes match the checkout before self-test. Retain the report and every skip reason. The separately supported commitless fresh-install case may skip tests requiring committed HEAD.

   ```powershell
   & '<repo-path>/.agentic/.venv/Scripts/python.exe' -B '<repo-path>/.agentic/scripts/self_test.py' --checks-only --report '<install-dir>/reports/first-installed.json'
   ```

   Installed invocation precedent: `7fcaeece:scripts/tests/test_installed_self_test.py:133`. Report restrictions: `7fcaeece:.agentic/scripts/self_test.py:243`.

   **Success/confirm:** exit 0, report PASS, zero failures/errors; review every skip. **Failure:** retain report, stderr progress, and failing test identity. Do not claim a full pass from step 7.

9. **Review and merge the adoption change.**

   Merge only the scoped installed-path LF rules into project attributes, preserve existing product rules, run the actual project tests, complete the adoption checklist, and prepare the draft PR. Sources: `7fcaeece:.agentic/templates/installed.gitattributes:1`; `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:54`; `7fcaeece:.agentic/templates/adoption-pr.md:5`.

   **Exact project-test, publication, and owner-merge commands: UNVERIFIED/project-specific.** The recorded test command is not executed by bootstrap. Mark-ready requires explicit PR/head-bound authorization; critic, required specialists, and final gate precede owner acceptance. Sources: `7fcaeece:scripts/bootstrap_project.py:157`; `7fcaeece:.agentic/docs/23-TICKET-LIFECYCLE.md:8`.

   **Success/confirm:** observe the actual receipt-changing PR merge. **Failure:** retain draft/refusal state and resolve the named gate; installation alone does not authorize merge.

10. **Verify the accepted current default checkout.**

    On the observed default branch at its observed tip:

    ```powershell
    & '<repo-path>/.agentic/.venv/Scripts/python.exe' -B -I '<repo-path>/.agentic/scripts/workflow.py' --root '<repo-path>' status --json --require-active --adoption-pr '<adoption-pr-number>' --release-source '<install-dir>/release-cache/source' --expected-manifest-sha256 '<release-manifest-sha256>'
    ```

    Flags: `7fcaeece:.agentic/lib/agentic/cli.py:141`. Default-checkout and receipt acceptance: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:615`.

    **Success/confirm:** exit 0, `project_state: ACTIVE`, `accepted_checkout: VERIFIED`, and recorded acceptance/default-head identities. Source: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:674`.

    **Failure:** exit 4 means ACTIVE remains unestablished. Resolve reported trust, merge, identity, checkout, or access blockers. This step requires provider observations and **cannot be completed under the offline review restrictions used to prepare this runbook**. Missing provider access retains CONFIGURED. Source: `7fcaeece:.agentic/tests/test_adoption_status.py:963`.
