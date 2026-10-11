# Adoption guide

Version 1.9.4. Read [the specification](../SPECIFICATION.md) and project-owned instructions.

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

New ownership defaults to `@maintainer`; `--codeowner '@handle'` or `'@organization/team'` overrides it. Preserve existing CODEOWNERS/reviewers; verify access and eligibility. Optional `--rules-observation ABS_OBSERVATION.json --expected-rules-observation-sha256 OBSERVATION_PIN` supplies evidence; `--default-branch EXPECTED` avoids assuming `main`; conflicts reject. Fresh installs need no predecessor. Verified 1.8.3/1.8.9/1.9.1/1.9.2/1.9.3 use `--mode upgrade` and the release-source migration guide. Preserve backups/journals; recover unfinished installation first.

5. Bootstrap preserves existing configuration, deriving missing values from observed target metadata. Review derivation/residue:

| Configuration | Source or remedy |
| --- | --- |
| `project.id` | Generate one real UUID4; retain it in later receipts. |
| GitHub repository/numeric ID | Parse target `origin`; obtain numeric ID through authenticated `gh`. Supply `--github-repo OWNER/REPO` and `--repository-id NUMBER` if needed; never invent it. |
| Name/short name | Derive from repository name; override with `--project-name NAME` / `--project-short-name SHORT`. |
| Specialist reviewer identities | Default new entries to the selected code owner; preserve existing identities. Handles do not prove access, independence or approval. |
| Test command | Prefer an npm test script, then evident pytest markers; otherwise supply `--test-command COMMAND`. Run it separately. |
| Jira | Supply both `--jira-site URL` and `--jira-key KEY`. Neither sets `jira.enabled: false` with null site/key. Preserve existing values. |

Preserve `template.expected_workflow_version: 1.9.4`, paths, limits and choices. At project start/intake, the controller records an audited `intake-assignment`, binding tier/cap to state/PR. Defaults are T1/cap 1 for docs/tests-only, small configuration/labels and bounded single-file fixes without security/install/upgrade effects; T2/cap 2 for moderate one-area code; T3/cap 3 plus trusted-owner review for security/credentials, governance/merge gates, migrations, install/upgrade/packaging, controller and release PRs. Stop at the first critic PASS. `execution.risk_tiers` is the reviewed override; protected paths floor T2 and below-floor changes record refusal. Enabled Jira writes `tier-N`/comment with verified read-back; disabled Jira stays local. Only trusted owners update assignments; ticket, PR and state retain who, when, why and old/new values. AWF-16 caps/escalation read the current record with history. [Review tiers and closeout](30-REVIEW-TIERS-AND-CLOSEOUT.md) governs review/cap rules.

Existing projects get `governance_proposal.adoption_pr_section` with exact current/proposed ceiling and reviewer allocation/count. Ordinary upgrades preserve both. `--propose-operating-capacity` stages ceiling six (preserving higher values) and current reviewer allocation for owner review/merge; `--dry-run` writes nothing. Other governance/operating choices remain unchanged.

Bootstrap appends [narrow ignore rules](../templates/operating.gitignore), preserving existing bytes. Commit operating configuration and `changes/` audit records; review broad owner rules hiding them. Preserve ignored recovery files through recovery.

Empty `validation.required_ci_checks: []` is accepted for adoption as `ci_gate: NOT_CONFIGURED`; fresh empty trusted-owner IDs also warn. Neither fabricates identity or live-ready policy. Configure observed CI identities and real merge owners before relevant live enablement.

6. Supply `--runtime-wheelhouse ABS_VERIFIED_WHEELHOUSE`: exactly one compatible wheel per locked dependency, prepared externally. Offline bootstrap verifies hashes, identities and inventories; unsafe paths, links and startup hooks reject. It validates a sibling runtime before atomic replacement and restores the prior runtime on failure. Failed rollback retains/reports its recovery backup. Use `doctor`; quick checks are not a full self-test. See [Windows diagnostics](32-WINDOWS-DIAGNOSTICS.md).

CONFIGURED requires verification exit 0/`integrity_valid: true`, validation exit 0/`status: ACCEPTED`, and matching source/policy/operating digests. Rejection gives `INSTALLED_UNCONFIGURED` (exit 1) with exact remedies; integrity failure gives `INSTALLATION_VERIFICATION_FAILED` (exit 2). `post_install_checks` retains commands/results, child-observed residue and derivation. Unsafe imports leave NOT_RUN; dry runs prove no installation.

When CONFIGURED, show `workflow.py operating show`; offer defaults, Epic review/recommendation, or custom, and paste its table into the PR. Follow [operating configuration](29-OPERATING-CONFIGURATION.md): direct instructions permit governed choices; recommendations require acceptance. Preserve legacy fixed reviewer counts until an adoption governance PR changes them.

| State | Evidence and next action |
| --- | --- |
| INSTALLED | Managed bytes match the receipt; configuration is unresolved. Fill reported paths and repeat both commands. |
| CONFIGURED | Installation and adoption configuration pass. Follow the reported merge, observation or accepted-checkout reconciliation action. |
| ACTIVE | Independently trusted release bytes, observed merged adoption and accepted default-branch AWF bytes verify. This does not activate adapters. |

INSTALLED/CONFIGURED prove local consistency. Status recomputes checks; retain its state, blockers, capabilities, next command and JSON. `status --require-active` exits 4 below ACTIVE. See [activation status](32-ACTIVATION-STATUS.md). `--adoption-pr NUMBER` selects the receipt-changing PR; `--gh ABS_TRUSTED_GH` selects the client. ACTIVE requires pinned release trust; fresh repository/default-tip and adoption-merge observations; accepted project Git objects and raw managed/configuration/receipt/provenance bytes; byte-exact immutable-file verification; and final rechecks. `--release-source` accepts Git-free extraction or ZIP. Unrelated product edits may coexist. Receipts/branches/imported JSON cannot authorize ACTIVE; missing proof stays CONFIGURED, unverified installation UNVERIFIED. Merge only scoped installed-path attributes into project Git rules; never renormalize unrelated files or rehash corruption. Verify fresh-checkout bytes and project tests.

7. Offer the [default-branch ruleset](../templates/awf-main-ruleset.json) for separate owner application; bootstrap never mutates server rules. Inspect/update existing rules rather than duplicating them. An authorized administrator can create a new ruleset:

```text
gh api --method POST repos/OWNER/REPO/rulesets --input .agentic/templates/awf-main-ruleset.json
```

`~DEFAULT_BRANCH` follows the default. Zero approvals avoids sole-maintainer self-approval. CODEOWNERS approval needs eligible non-author reviewers, not handles. Multi-maintainer projects may raise counts. [Rules activation](35-RULES-ACTIVATION.md) matches the merge method; incompatible rules stay `MISSING`. [GitHub rule behavior](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

An empty required-check list makes strict up-to-date policy ineffective. Approval dismissal does not refresh COMMENT evidence; thread resolution is not authenticity. Before live enablement configure observed `awf/review` with actual App `integration_id` and applicable CI. [Rules API](https://docs.github.com/en/rest/repos/rules).

Run installed `workflow.py validate-config --require-enablement`; re-observe rules with `--review-app-id APP_ID --require-enablement`. APPLIED is adequate; MISSING is not; UNOBSERVED means unavailable, stale, malformed or incomplete evidence. Fixtures never enable live work. Retain [host](28-EXTERNAL-REVIEW.md)/[loop](22-AUTOMATED-REVIEW-LOOP.md) qualification. The helper enables no adapter; host enforces prerequisites.

8. Fill the [adoption PR checklist](../templates/adoption-pr.md) with observed state, commands, residue and rules. Restricted pushes need owner publication or satisfied prerequisites; otherwise use authorized draft-PR tools. Never invent a PR. Per the [lifecycle](23-TICKET-LIFECYCLE.md), validation/CI alone never permits mark-ready: require an owner/manager authorization record bound to PR/head; log it before mutation, or retain the refusal/draft. Observe `draft_cleared`, then dispatch the critic on that head. Specialists/final gate precede human merge acceptance. Review-ready is not owner-ready; routine classification grants no authority/exemption. Installation never enrolls scheduling, upgrades other projects or authorizes Jira/Epic transitions.
