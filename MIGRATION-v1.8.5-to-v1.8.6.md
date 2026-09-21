# Adopt the 1.8.6 patch

This patch makes configuration derivation/residue and adoption state explicit. It preserves the separation between installing AWF files and enabling live automation. Existing project history, ownership, configuration, approvals, review evidence and protected runtime state remain authoritative; no accounting reset or new model measurement follows from upgrading.

## Install and reconcile configuration

Follow the [combined adoption guide](.agentic/docs/20-NEW-PROJECT-SETUP.md). Verify independently supplied release pins, inspect active work/owners and prepare an isolated adoption branch/worktree. Explain in the first message that governance-file changes are being prepared as a draft PR for the owner to merge. Missing/unobserved default-branch rules and absent CI warn; they do not block local installation, mapping or PR preparation.

```text
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 MANIFEST_PIN --codeowner '@handle' --dry-run
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 MANIFEST_PIN --codeowner '@handle' --on-conflict backup
```

Use backed-up `install` for cross-version adoption; `--mode upgrade` is same-version maintenance. Preserve recovery journals/backups. Move existing product-specific AGENTS instructions into reviewed project-owned `PROJECT_INSTRUCTIONS.md`; keep managed AGENTS bytes exact. Preserve existing project-owned CODEOWNERS; new defaults use `@maintainer` unless `--codeowner` overrides them.

For a fresh project, bootstrap persists a real project UUID, derives repository names from `origin`, obtains numeric repository identity through available authenticated `gh`, defaults specialist identities to the selected code owner and detects a test command. Detection does not run project tests or attest reviewer eligibility. Explicit remedies are `--github-repo OWNER/REPO`, `--repository-id NUMBER`, `--project-name NAME`, `--project-short-name SHORT` and `--test-command COMMAND`. Unknown values remain named residue, never fictitious IDs.

Supply paired `--jira-site HTTPS_ORIGIN --jira-key KEY` for new Jira configuration. Neither means `jira.enabled: false` with null site/key and local provisional work records. Preserve existing Jira values/history; never disable an existing integration silently. Enabled Jira without configured scope is an adoption warning and admits no Jira tickets or mutations.

Existing configuration is preserved, not silently regenerated. Reconcile its reported paths in `.agentic/PROJECT_CONFIG.yaml`, including `template.expected_workflow_version: 1.8.6`. Preserve deliberate models, caps, owner identities, validation commands and approved scope. Empty CI and fresh empty trusted-owner IDs are adoption warnings, not live enablement.

## Verify actual installed commands

Keep adoption quiescent with exclusive ownership. Bootstrap preflights runtime imports and executes these isolated installed commands; repeat after correcting residue:

```text
python -B -I ABS_TARGET/.agentic/scripts/workflow.py verify-installation
python -B -I ABS_TARGET/.agentic/scripts/workflow.py validate-config
python -B -I ABS_TARGET/.agentic/scripts/workflow.py status
```

Read actual exits and parsed outputs, retained in `post_install_checks`. Bootstrap CONFIGURED exits 0 only when both commands exit 0, integrity_valid=true/status=ACCEPTED and source/policy digests match. Verified bytes with unresolved configuration produce `INSTALLED_UNCONFIGURED` (exit 1), exact JSON paths/reasons/remedy flags and configuration warnings. Integrity failure produces `INSTALLATION_VERIFICATION_FAILED` (exit 2). `configuration` retains child-observed residue or UNOBSERVED; `pre_install_configuration` retains earlier derivation. Unsafe imports preserve NOT_RUN outcomes, not hostile-concurrency isolation. Dry runs/library-only substitutes prove no child execution.

Status recomputes current shared checks, not past child execution: INSTALLED means receipt-consistent bytes with residue; CONFIGURED adds accepted adoption configuration. Neither independently authenticates release provenance. Use `status --json` for details, `--adoption-pr NUMBER` to select a known PR and optionally `--gh ABS_TRUSTED_GH`. Otherwise status discovers the merged PR associated with the receipt-changing commit. ACTIVE additionally requires independent release trust via `--release-source ABS_SOURCE --expected-manifest-sha256 TRUSTED_PIN` (source outside the project) or the matching installed host AWF skill bundle, receipt-changing merged-PR proof and accepted raw managed/configuration/receipt/provenance bytes on the fresh default branch, with final rechecks. Unrelated product edits may coexist; pins are not signatures. Source/unverified installations report UNVERIFIED. Copy the actual line and next action into the [adoption PR checklist](.agentic/templates/adoption-pr.md). A local default-branch name, receipt, status JSON or prepared PR cannot prove acceptance. Unknown merge/checkout evidence stays CONFIGURED with a concrete next action. Failed integrity is not a verified installation. ACTIVE does not enroll a loop, schedule work or qualify an engine.

## Rules, enablement and release evidence

Offer the shipped `~DEFAULT_BRANCH` ruleset for separate owner review/application. APPLIED/MISSING/UNOBSERVED retain their evidence meanings; bootstrap never applies server rules. An empty required-check list provides no strict up-to-date enforcement or independent-review provenance. Run installed `workflow.py validate-config --require-enablement` and the rules preflight; live execution still needs configured CI/actual trusted owners, observed applicable rules and mode qualification. Where agent pushes to a secrets-bearing repository are restricted, prepare local branch/body for owner publication or observe rules first. Never invent a hosted PR or refuse local adoption on enablement conditions.

Ordinary `scripts/self_test.py --report ABS_NEW_REPORT.json` remains unqualified component checking. Source `--release`/review inputs require current independently pinned reviews; installed `--release` cannot qualify release source. Archive/catalog acceptance retains those pins and matching qualified reports. Follow the [previous release commands](MIGRATION-v1.8.4-to-v1.8.5.md) with current-release evidence; never relabel old approvals or overwrite failed reports.

Historical **10/12 strict FAIL**, **5/12 FAIL** and **1/3 FAIL** remain unchanged; different cases/contracts prevent causal comparison. This migration runs no models or live probes. See [disposition](RED-TEAM-DISPOSITION-v1.8.6.md) and external final validation evidence for actual checks and remaining qualification.
