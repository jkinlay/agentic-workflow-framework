# Adopt AWF 1.9.2

This draft changes repository governance files and preserves project-owned instructions, configuration and history. Complete every field from observed evidence; this template supplies no proof or authorization.

## Installation and configuration

- Previous accepted AWF version: TO_RECORD.
- Requested release and independently verified archive/manifest pins: TO_RECORD.
- Target repository, numeric ID and actual default branch: TO_RECORD.
- Installation state after writing files: TO_RECORD (`INSTALLED_UNCONFIGURED` or `CONFIGURED`, or `INSTALLATION_VERIFICATION_FAILED`).
- Managed-file verification command: `python -B -I ABS_INSTALLED_SCRIPT verify-installation`.
- Actual verification exit code and `integrity_valid`: TO_RECORD; retained output: TO_RECORD.
- Configuration command: `python -B -I ABS_INSTALLED_SCRIPT validate-config`.
- Actual validation exit code and `status`: TO_RECORD; retained output: TO_RECORD.
- Exact status command: `python -B -I ABS_INSTALLED_SCRIPT status`.
- Actual status line, next action and retained evidence: TO_RECORD. Do not substitute an anticipated `ACTIVE` result.
- Optional observed PR selector/trusted gh path and `status --json` details: TO_RECORD, or None.

Bootstrap CONFIGURED requires both commands to exit 0, verification `integrity_valid: true`, validation `status: ACCEPTED` and matching source/policy digests; record those comparisons. A dry run is preparation only. Status recomputes current checks; INSTALLED/CONFIGURED establish local consistency, not release provenance or historical child execution. Failed integrity is not an installed baseline. Record exclusive ownership/quiescence and any runtime-import rejection; preserve NOT_RUN outcomes.

| Remaining configuration JSON path | Observed reason | Flag or project file remedy | Owner / next action |
| --- | --- | --- | --- |
| TO_RECORD, or None after actual validation | TO_RECORD | TO_RECORD | TO_RECORD |

Record preserved versus derived project identity, repository metadata, test command, specialist identities and overrides. New Jira is configured by site/key or disabled with null site/key; record scope and provisional-ID handling. Preserve existing Jira mappings. Record `ci_gate`, required checks (with `verifies_history` and `checkout_depth`) and trusted merge-owner configuration; missing CI/owners are adoption warnings. List project tests actually executed with exit codes, or the concrete reason not run.

## Host preflight

Paste actual `adoption_pr_host_preflight_section`, including `project_lint_scope`: TO_RECORD. Its WARN means Ruff/flake8 includes `.agentic`; add `extend-exclude = [".agentic"]` or the flake8 equivalent. Rows never block INSTALLED; WARN is next action; Windows-only rows are `N_A` elsewhere.

## Operating configuration at adoption

Run installed `python -B -I ABS_INSTALLED_SCRIPT operating show` and paste the actual complete table here: TO_RECORD. Include stream count/ceiling, each worker/reviewer route, controller/specialist/simple-worker settings and escalation/floor limits. Do not paste anticipated defaults as observed output.

Record operating hash/source/last change, preservation or initialization, and the user's choice: keep defaults, review Epics and recommend, or custom. Operating validation must be ACCEPTED and its hash must match bootstrap's actual installed check. Preserve the verbatim instruction for applied changes. Recommendations remain unapplied until accepted. Note any retained legacy reviewer-count constraint requiring a governance PR.

### Optional operating-capacity governance proposal

Paste bootstrap's actual `governance_proposal.adoption_pr_section`: TO_RECORD, including current/proposed ceiling and reviewer count. Ordinary upgrades preserve accepted limits. Before `--propose-operating-capacity`, record explicit user direction; retain its `PLANNED_FOR_REVIEW`/`STAGED_FOR_REVIEW` result and exact diff. It raises a lower ceiling to six and derives one reviewer per operating stream by removing the fixed count. Higher ceilings, operating choices, other limits, review floors and automation switches remain unchanged. Owner review and merge are required.

Record `.gitignore` merge/preservation and verify that local operating lock/journal/staging files are ignored while `OPERATING_CONFIG.yaml` and `.agentic-state/operating/changes/` remain versioned audit evidence. Existing broad project ignore rules need explicit review if they hide these records.

## Repository rules and publication

- Actual default branch: TO_RECORD.
- Observed `repository_rules`: TO_RECORD (`APPLIED`, `MISSING` or `UNOBSERVED`).
- Observation source/time/pin and missing or unobservable controls: TO_RECORD.
- Owner application/re-observation next step, or None when supported by evidence: TO_RECORD.
- Draft PR URL only after creation, or prepared branch/body and owner publication prerequisite: TO_RECORD.

Missing rules do not block local installation, configuration or PR preparation. Offer the shipped ruleset for owner review; bootstrap never applies it. If agent pushes to this secrets-bearing repository are restricted, hand publication to the owner or observe rules first. APPLIED with an empty required-check list establishes no CI/review provenance.

## Effect after merge

ACTIVE requires independently trusted release bytes, observed adoption merge bound to the receipt change, and accepted raw managed/configuration/receipt/provenance bytes on the fresh default branch. Unrelated product edits may coexist. Record `--release-source ABS_SOURCE --expected-manifest-sha256 TRUSTED_PIN` (source outside the project) or the actual matching installed host skill bundle; pins are not signatures. Run `workflow.py status` again and retain its actual result. A branch name, receipt or PR body cannot establish ACTIVE.

List unchanged project-owned instructions/CODEOWNERS, retained partial work/history, reviewed configuration changes, rollback location and remaining actions: TO_RECORD.

Adoption does not enable Jira writes, enroll a PR, start a scheduler, qualify a Codex review host or grant merge authority. Live execution still needs configured CI/trusted owners, observed rules and the mode's qualification. Retain human merge acceptance and continue unrelated authorized streams while remaining actions are resolved.
