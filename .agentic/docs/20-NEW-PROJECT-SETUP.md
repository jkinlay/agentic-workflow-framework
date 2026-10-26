# Adoption guide

Version 1.9.5. Read [the specification](../SPECIFICATION.md) and project-owned instructions.

| Stage | Preconditions and outcome |
| --- | --- |
| Adoption | No repository-rule precondition: install files, map configuration and prepare a draft PR. Missing/unobserved rules and absent CI are warnings. |
| Live enablement | Independent Codex review, scheduled amendments and agent pushes to repositories holding secrets require observed rules and existing mode qualification. Configure required CI and actual trusted merge owners before relevant live execution. Adoption alone enables none. |

1. First message: installing AWF changes repository governance files, so prepare a draft PR for owner merge. Jira need not exist; provisional local IDs are not tickets or approvals.
2. Locate the release through owner channel/catalog or verified bundle. Verify independent archive/manifest pins before running source. Use a separate cache; preserve differing/incomplete caches. Discovery is not adoption.
3. Prepare an isolated adoption branch/worktree. Preserve product-specific AGENTS instructions in reviewed project-owned `PROJECT_INSTRUCTIONS.md`; keep managed AGENTS byte-exact. Preserve project configuration and ownership.
4. From the verified release, optionally observe the actual default branch into new external evidence; unavailable API access must not stop installation:

```text
python -B .agentic/scripts/repository_rules.py --repository OWNER/REPO --observe --save-observation ABS_OBSERVATION.json
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 TRUSTED_SHA256 --codeowner '@handle' --dry-run
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 TRUSTED_SHA256 --runtime-wheelhouse ABS_VERIFIED_WHEELHOUSE --codeowner '@handle' --on-conflict backup
```

Ownership defaults: `@maintainer`; override with `--codeowner '@handle'` or `'@organization/team'`. Preserve CODEOWNERS/reviewers; verify access/eligibility. Optional `--rules-observation ABS_OBSERVATION.json --expected-rules-observation-sha256 OBSERVATION_PIN` supplies evidence; conflicts reject. Fresh: no predecessor. Verified 1.8.3/1.8.9/1.9.1/1.9.2/1.9.3 upgrades: `--mode upgrade`; follow release-source migration guidance. Preserve backups/journals; recover unfinished installation.

5. Bootstrap preserves existing configuration, deriving missing values from observed target metadata. Review derivation/residue:

| Configuration | Source or remedy |
| --- | --- |
| `project.id` | Generate one real UUID4; retain it in later receipts. |
| GitHub repository/numeric ID | Parse target `origin`; obtain numeric ID through authenticated `gh`. Supply `--github-repo OWNER/REPO` and `--repository-id NUMBER` if needed; never invent it. |
| `github.base_branch` | Observed metadata or `--default-branch`; unobserved/unsupplied missing values stay null (`INSTALLED_UNCONFIGURED`), never assume `main`. |
| Name/short name | Derive from repository name; override with `--project-name NAME` / `--project-short-name SHORT`. |
| Specialist reviewer identities | Default new entries to the selected code owner; preserve existing identities. Handles do not prove access, independence or approval. |
| Test command | Prefer an npm test script, then evident pytest markers; otherwise supply `--test-command COMMAND`. Run it separately. |
| Jira | Supply both `--jira-site URL` and `--jira-key KEY`. Neither sets `jira.enabled: false` with null site/key. Preserve existing values. |

Preserve `template.expected_workflow_version: 1.9.5`. Bootstrap accepts `--project-start-inventory`, `--ticket-intake` or `--assignment-change`, writing `--assignment-state` outside the worktree; enabled Jira requires the controller adapter. The controller canonicalizes paths, records `intake-assignment`, binds tier/cap to state/PR; AWF-16 consumes it. Defaults: T1/1 for docs/tests, small configuration/labels or bounded single-file fixes without high-risk effects; T2/2 for moderate one-area code; T3/3 plus owner review for security/credentials, governance/CI/merge gates, migrations, install/upgrade/packaging, controller or release PRs. Stop on critic PASS. `execution.risk_tiers` is the override; protected paths floor T2. Jira writes verified `tier-N`/comment; unknown/mismatch durably stops that ticket's writes. Disabled Jira stays local. Changes require verifier-authenticated immutable owner evidence; the ticket, PR and state retain who/when/why/old/new, and below-floor refusals are recorded. [Review tiers and closeout](30-REVIEW-TIERS-AND-CLOSEOUT.md) governs review/cap rules.

Existing projects get `governance_proposal.adoption_pr_section` with exact current/proposed ceiling and reviewer allocation/count. Ordinary upgrades preserve both. `--propose-operating-capacity` stages ceiling six (preserving higher values) and current reviewer allocation for owner review/merge; `--dry-run` writes nothing. Other governance/operating choices remain unchanged.

Bootstrap appends [narrow ignores](../templates/operating.gitignore), preserving bytes. Commit operating configuration and `changes/` audit records; review broad owner ignores. Retain `.agentic-install/`, `.agentic-backup/` and recovery files through recovery. Qualifying N2 recovery warns and preserves unproved partial runtimes in `_to_delete/<stage-name>-<timestamp>-<uuid>` for inspection, without authentication or automatic deletion. You may remove unneeded retained folders. Clean Git status requires the ignore rule before interruption.

Empty `validation.required_ci_checks: []` is accepted for adoption as `ci_gate: NOT_CONFIGURED`; fresh empty trusted-owner IDs warn. Neither establishes identity or live readiness. Configure observed CI and real merge-owner identities before live enablement.

6. Supply `--runtime-wheelhouse ABS_VERIFIED_WHEELHOUSE`: one externally prepared compatible wheel per locked dependency. Offline bootstrap verifies hashes/identities/inventories; unsafe paths, links and startup hooks reject. Sibling runtime validation precedes atomic replacement; failure restores prior runtime. Failed rollback retains/reports recovery backups. Use `doctor`; quick checks are not full self-tests. [Windows diagnostics](32-WINDOWS-DIAGNOSTICS.md).

CONFIGURED requires verification exit 0/`integrity_valid: true`, validation exit 0/`status: ACCEPTED`, and matching source/policy/operating digests. Rejection: `INSTALLED_UNCONFIGURED` (exit 1), exact remedies; integrity failure: `INSTALLATION_VERIFICATION_FAILED` (exit 2). `post_install_checks` retains commands/results, child-observed residue and derivation. Unsafe imports leave NOT_RUN; dry runs install nothing.

At CONFIGURED, paste `workflow.py operating show` table into PR; offer defaults, Epic review/recommendation, or custom. Follow [operating configuration](29-OPERATING-CONFIGURATION.md) on direct instructions; recommendations require acceptance. Change legacy fixed reviewer counts through an adoption governance PR.

| State | Evidence and next action |
| --- | --- |
| INSTALLED | Managed bytes match the receipt; configuration is unresolved. Fill reported paths and repeat both commands. |
| CONFIGURED | Installation and adoption configuration pass. Follow the reported merge, observation or accepted-checkout reconciliation action. |
| ACTIVE | Independently trusted release bytes, observed merged adoption and accepted default-branch AWF bytes verify. This does not activate adapters. |

INSTALLED/CONFIGURED prove local consistency; retain recomputed state, blockers, capabilities, next command and JSON. `status --require-active` exits 4 below ACTIVE ([activation status](32-ACTIVATION-STATUS.md)). `--adoption-pr NUMBER`: receipt-changing PR; `--gh ABS_TRUSTED_GH`: client. ACTIVE requires pinned release trust; fresh repository/default-tip/adoption-merge observations; accepted project Git objects and raw managed/configuration/receipt/provenance bytes; byte-exact immutable-file verification; final rechecks. `status --release-source` requires a `build_release.py` release ZIP/verified extraction outside the project and independently approved `--expected-manifest-sha256`. Git checkouts/`git archive` cannot establish status trust, nor can bootstrap checkout acceptance. Unrelated product edits may coexist. Receipts/branches/imported JSON cannot authorize ACTIVE; missing proof stays CONFIGURED, unverified installation UNVERIFIED. Merge only installed-path-scoped Git attributes; never renormalize unrelated files or rehash corruption. Verify fresh-checkout bytes and project tests.

7. Offer the [default-branch ruleset](../templates/awf-main-ruleset.json) for separate owner application; bootstrap never mutates server rules. Inspect/update existing rules rather than duplicating them. An authorized administrator can create a new ruleset:

```text
gh api --method POST repos/OWNER/REPO/rulesets --input .agentic/templates/awf-main-ruleset.json
```

`~DEFAULT_BRANCH` follows the default. Zero approvals avoids sole-maintainer self-approval. CODEOWNERS approval needs eligible non-author reviewers, not handles. Multi-maintainer projects may raise counts. [Rules activation](35-RULES-ACTIVATION.md) matches the merge method; incompatible rules stay `MISSING`. [GitHub rule behavior](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

An empty required-check list makes strict up-to-date policy ineffective. Approval dismissal does not refresh COMMENT evidence; thread resolution is not authenticity. Before live enablement configure observed `awf/review` with actual App `integration_id` and applicable CI. [Rules API](https://docs.github.com/en/rest/repos/rules).

Run installed `workflow.py validate-config --require-enablement`; re-observe rules with `--review-app-id APP_ID --require-enablement`. APPLIED is adequate; MISSING is not; UNOBSERVED means unavailable, stale, malformed or incomplete evidence. Fixtures never enable live work. Retain [host](28-EXTERNAL-REVIEW.md)/[loop](22-AUTOMATED-REVIEW-LOOP.md) qualification. The helper enables no adapter; host enforces prerequisites.

8. Fill [adoption PR checklist](../templates/adoption-pr.md) with observed state, commands, residue and rules. Restricted pushes require owner publication or satisfied prerequisites; otherwise use authorized draft-PR tools. Never invent PRs. Per [lifecycle](23-TICKET-LIFECYCLE.md), mark-ready requires logged PR/head-bound owner/manager authorization; otherwise retain refusal/draft. Validation/CI is insufficient. Observe `draft_cleared`, then dispatch the critic on that head. Specialists/final gate precede human merge acceptance; review-ready is not owner-ready. Routine classification grants no authority/exemption. Installation never enrolls scheduling, upgrades other projects or authorizes Jira/Epic transitions.
