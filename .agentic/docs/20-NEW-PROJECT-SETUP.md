# Adoption guide

Version 1.9.3. Read [the specification](../SPECIFICATION.md) and project-owned instructions.

| Stage | Preconditions and outcome |
| --- | --- |
| Adoption | No repository-rule precondition: install files, map configuration and prepare a draft PR. Missing/unobserved rules and absent CI are warnings. |
| Live enablement | Independent Codex review, scheduled amendments and agent pushes to repositories holding secrets require observed rules and existing mode qualification. Configure required CI and actual trusted merge owners before relevant live execution. Adoption alone enables none. |

1. Explain in the first message: installing AWF changes repository governance files, so you will prepare a draft PR for the owner to merge. Inspect accepted version, changes, worktrees, owners and active PRs. Preserve partial work/history. Jira need not exist first; provisional local IDs are not invented tickets or approvals.
2. Locate the requested release through owner channel/catalog or verified bundle. Verify independently supplied archive/manifest pins before running source. Use a separate cache; preserve differing/incomplete caches. Discovery is not adoption.
3. Prepare an isolated adoption branch/worktree. Preserve product-specific AGENTS instructions in reviewed project-owned `PROJECT_INSTRUCTIONS.md`; keep managed AGENTS byte-exact. Preserve project configuration and ownership.
4. From the verified release, optionally observe the actual default branch into new external evidence; unavailable API access must not stop installation:

```text
python -B .agentic/scripts/repository_rules.py --repository OWNER/REPO --observe --save-observation ABS_OBSERVATION.json
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 TRUSTED_SHA256 --codeowner '@handle' --dry-run
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 TRUSTED_SHA256 --codeowner '@handle' --on-conflict backup
```

New ownership defaults to `@maintainer`; `--codeowner '@handle'` or `'@organization/team'` overrides new entries. Preserve existing CODEOWNERS/reviewers; verify access and eligible review. Optional `--rules-observation ABS_OBSERVATION.json --expected-rules-observation-sha256 OBSERVATION_PIN` supplies rules evidence; `--default-branch EXPECTED` avoids assuming `main`; observed conflicts reject. Fresh install needs no predecessor. Verified 1.8.3/1.8.9/1.9.1/1.9.2 use `--mode upgrade`; see [direct migration](../../MIGRATION-to-v1.9.3.md). Preserve backups/journals; recover unfinished installation before use.

5. Bootstrap derives fresh configuration from observed target metadata; existing project values remain preserved, not silently replaced. Review its derivation and residue report:

| Configuration | Source or remedy |
| --- | --- |
| `project.id` | Generate a real UUID4 once for a new project; retain its identity in the receipt on later installation. |
| GitHub repository/numeric ID | Parse target `origin`; obtain the numeric ID through available authenticated `gh`. Supply `--github-repo OWNER/REPO` and `--repository-id NUMBER` when needed. Never invent an ID. |
| Name/short name | Derive from repository name; override with `--project-name NAME` / `--project-short-name SHORT`. |
| Specialist reviewer identities | Default new entries to the selected code owner; preserve existing identities. A handle is not proof of access, independence or approval. |
| Test command | Prefer an npm test script, then evident pytest markers; otherwise supply `--test-command COMMAND`. Run the selected project tests separately. |
| Jira | Supply both `--jira-site URL` and `--jira-key KEY`. Neither sets `jira.enabled: false`, with null site/key, for a fresh project. Preserve existing Jira configuration. |

Keep `template.expected_workflow_version: 1.9.3`, real paths, scope and protected limits. Bootstrap preserves root operating choices or seeds three streams within ceiling six, one reviewer each. Disabled/unscoped Jira permits no ticket mutations; use provisional local records.

Existing projects get `governance_proposal.adoption_pr_section` with exact current/proposed ceiling and reviewer count. Ordinary upgrades preserve both. Explicit `--propose-operating-capacity` stages ceiling six (higher preserved) and derived one-per-stream reviewers for owner review/merge; `--dry-run` writes nothing. Other governance/operating choices remain unchanged.

Bootstrap appends [narrow ignore rules](../templates/operating.gitignore), preserving existing bytes. Commit operating configuration and `changes/` audit records; review broad owner rules that hide them. Preserve ignored recovery files until recovery completes.

Empty `validation.required_ci_checks: []` is accepted for adoption with `ci_gate: NOT_CONFIGURED`; fresh empty trusted-owner IDs also warn. Neither is a fabricated identity or live-ready policy. Configure observed CI identities and real merge owners before relevant live enablement.

6. With exclusive ownership and quiescent adoption, bootstrap checks runtime imports and executes isolated installed commands. Repeat after correcting configuration:

```text
python -B -I ABS_TARGET/.agentic/scripts/workflow.py verify-installation
python -B -I ABS_TARGET/.agentic/scripts/workflow.py validate-config
python -B -I ABS_TARGET/.agentic/scripts/workflow.py status
```

CONFIGURED (exit 0) requires actual verification exit 0/`integrity_valid: true`, validation exit 0/`status: ACCEPTED`, and matching source/policy/operating digests. Rejected configuration gives `INSTALLED_UNCONFIGURED` (exit 1) with exact paths/remedies; integrity failure gives `INSTALLATION_VERIFICATION_FAILED` (exit 2). `post_install_checks` retains commands/results, `configuration` child-observed residue, and `pre_install_configuration` derivation. Unsafe imports leave NOT_RUN; dry runs prove no installation.

When CONFIGURED, show `workflow.py operating show` and offer keep defaults, review Epics and recommend, or custom. Paste its table into the PR. Follow [operating configuration](29-OPERATING-CONFIGURATION.md): direct instructions permit choices within governance; recommendations require acceptance. Preserve legacy fixed reviewer counts until an adoption governance PR removes/updates that constraint.

| State | Evidence and next action |
| --- | --- |
| INSTALLED | Managed bytes match the receipt; configuration is unresolved. Fill reported paths and repeat both commands. |
| CONFIGURED | Installation and adoption configuration pass. Follow the reported merge, observation or accepted-checkout reconciliation action. |
| ACTIVE | Independently trusted release bytes, observed merged adoption and accepted default-branch AWF bytes verify. This does not activate adapters. |

INSTALLED/CONFIGURED prove local consistency. Status recomputes checks, not historical child execution; retain its actual `AWF 1.9.3: STATE` line/next action and `--json` details. `--adoption-pr NUMBER` must select the receipt-changing PR; optional `--gh ABS_TRUSTED_GH` selects the client. ACTIVE requires independent release trust (`--release-source ABS_SOURCE --expected-manifest-sha256 TRUSTED_PIN`, external to the project, or matching installed host skill), fresh GitHub repository/default-tip and merged adoption observations, plus accepted raw managed/configuration/receipt/provenance bytes and final rechecks. Unrelated product edits may coexist. Receipts, branch names and imported JSON cannot authorize ACTIVE; pins are not signatures. Missing acceptance proof stays CONFIGURED; unverified installation stays UNVERIFIED. Merge scoped installed-path attributes into project Git rules; never copy source-wide rules, renormalize unrelated files or rehash corruption. Verify fresh-checkout bytes and project tests.

7. Offer the [default-branch ruleset](../templates/awf-main-ruleset.json) for separate owner application; bootstrap never mutates server rules. Inspect/update existing rules rather than duplicating them. An authorized administrator can create a new ruleset:

```text
gh api --method POST repos/OWNER/REPO/rulesets --input .agentic/templates/awf-main-ruleset.json
```

`~DEFAULT_BRANCH` follows the actual default branch. Zero required approvals avoids sole-maintainer self-approval. Required CODEOWNERS approval needs eligible non-author reviewers, not merely two handles. Multi-maintainer projects may raise counts. Squash/rebase must also be permitted by repository settings. [GitHub rule behavior](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

An empty required-check list makes strict up-to-date policy ineffective. Approval dismissal does not refresh COMMENT evidence; thread resolution is not authenticity. Before live enablement configure observed `awf/review` with actual App `integration_id` and applicable CI. [Rules API](https://docs.github.com/en/rest/repos/rules).

Run installed `workflow.py validate-config --require-enablement` and re-observe rules to a new path with `--review-app-id APP_ID --require-enablement`. APPLIED means an adequate observed baseline with visible no-bypass policy; MISSING means observed inadequate rules; UNOBSERVED covers unavailable access/capability, stale/malformed or incomplete evidence. The helper discovers/paginates effective default-branch rules and ruleset details. Fixtures never enable live work. Retain [independent-review host](28-EXTERNAL-REVIEW.md) / [loop](22-AUTOMATED-REVIEW-LOOP.md) qualification. The helper neither enables adapters nor forces unchanged legacy entrypoints to consult it; the host/operator must enforce these prerequisites.

8. Fill the [adoption PR checklist](../templates/adoption-pr.md) with observed state, commands, residue and rules. Restricted pushes need owner publication or satisfied prerequisites; otherwise use authorized draft-PR tools. Never invent a hosted PR. Follow the [lifecycle](23-TICKET-LIFECYCLE.md): current validation/requirements, mark-ready, critic on the observed head, specialists/final gate, then human merge acceptance. Review-ready is not owner-ready; routine classification grants no authority or adapter exemption. Installation never enrolls scheduling, upgrades other projects or authorizes Jira/Epic transitions.
