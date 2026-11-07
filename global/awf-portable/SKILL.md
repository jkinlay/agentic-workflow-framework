---
name: awf
description: Check and change AWF budgets through owner-merged PRs; Verify and adopt AWF releases; guide bounded project work, independent review and evidence accounting. Includes AWF 1.9.6.
metadata:
  publisher: Jonathan Kinlay
  license: Apache-2.0
---

# AWF

Skill installation preserves baselines; adoption is separate. Read
[installation](references/skill-installation.md), [workflow](references/workflow.md)
and [update channels](references/update-channel.md). Unavailable channels prove no latest.

First adoption message: installing AWF changes repository governance files, so
prepare a draft PR for the owner to merge. Adoption has no repository-rule
precondition. Report actual APPLIED/MISSING/UNOBSERVED rules; warn on missing/
unobserved rules and offer the shipped ruleset. Bootstrap never applies rules.
Preserve configuration/CODEOWNERS/instructions; derive supported values, naming
unresolved paths/remedies. Disabled Jira uses local records; empty CI/owners warn.

CONFIGURED requires successful isolated installed verification/validation,
accepted outputs and bound digests. Status recomputes checks, not past child
execution. Use the adoption PR checklist. ACTIVE requires independent release
trust, receipt-changing merged adoption and accepted raw default-branch AWF
bytes; unrelated product edits may coexist. Receipt/branch assertions alone
are insufficient. ACTIVE enables no adapter.

Live review/scheduled amendments/secret-repository pushes require observed
rules/mode qualification; execution needs CI/trusted owners. Otherwise prepare
changes/body for owner publication. Reuse permitted PR tools; never invent a PR.

Verify [bundled source](assets/agentic-workflow-template-v1.9.6.zip) against
[pins](assets/release.json):

```text
python -B PATH_TO_SKILL/scripts/prepare_release.py --cache-dir ABSOLUTE_EXTERNAL_CACHE --version 1.9.6
```

Use Python 3.11+, separate cache/existing parent and catalog; preserve differing/
incomplete caches. Read verified AGENTS, specification/configuration and runbooks.

Follow [routing](references/model-routing.md): twelve workers, shared independent
critic/adversarial handler, controller (fourteen slots); observed capacity binds.
One writer/path; independent reviewers. Preserve unknown usage/history,
reservations/finite retries; configuration cannot raise host ceilings.

At CONFIGURED, run `workflow.py operating show`; offer keep defaults, review
Epics/recommend, or custom. Echo instructions, run `operating set`, show results.
Recommendations require acceptance; governance changes require review. Use exact
`--epic EPIC-ID`; retain floors/pins/reservations and drain surplus.

Follow `.agentic/docs/23-TICKET-LIFECYCLE.md`: COMPLETE publishes draft PR;
Git-denied workers return `tested_tree`, `changes`, `ignored_untracked` for
unchanged publication; publisher requires `HEAD^{tree}` equality. Validation/CI
never permit mark-ready alone. Require an explicit owner/manager authorization
record bound to the candidate, a pre-issued request/expiry, and an authenticated
immutable provider observation binding accepted configuration and trusted current
time. Observe `draft_cleared`; otherwise retain draft/refusal. Critic/specialists/
final gate precede owner-ready, never merge authority.
Retain every publication gate.

Read verified `.agentic/docs/39-BUDGETING.md`. Fresh accounting/enforcement OFF;
dormant limits exactly 4,000,000 tokens/ticket, 1,000,000,000/project lifetime.
Limits enable neither switch; enforcement requires accounting; accounting OFF
disables enforcement. Preserve upgrades; installed 1.9.5 stays unchanged/enabled.
Recommend versioned preservation, never in-place integrity-managed bytes/receipts.

For budget questions in AWF Codex chat, run verified `workflow.py operating show`
read-only, without approval. Present both switches, both limits, source (fresh
defaults, default branch at the reported commit, or legacy retained), working-file
differences and offline rules UNOBSERVED warnings; publication readiness/adoption observe live rules. Last PR/merger is optional;
say when unobserved. No terminal command is required of the owner.

For changes, branch from the fetched configured default branch. Edit only requested
`OPERATING_CONFIG.yaml` values, preserving text/comments; accounting OFF also
disables enforcement. Run `operating validate --file PROPOSED`; invalid combinations refuse. Present exact
before/after in a draft PR under existing publication authority. Restricted agent
pushes prepare branch/body for owner publication. The owner reviews and merges
on GitHub; agents never merge. Fetch after observed merge, then show effective
settings/commit. Git history and the PR are the audit trail. Working/branch edits
have no protected effect. Unprotected `operating set` remains; protected set
explains the PR route. Keep Git metadata outside worker write authority.
Host rules and owner govern merge; direct pushes to an unprotected default branch
or the owner's full rights can get around this. Offer the shipped ruleset.
`merge_gate.trusted_owner_ids` retains its merge-gate use.
Calendar uses this same chat/PR route. Present effective timezone, continuous
work mode, review days/opening/cutoff and EoD schedule from `operating show`.
Defaults: 24/7 work; review batching enabled weekdays 07:00-17:00 Europe/London,
EoD 17:00 daily. Validate proposed calendar values before preparing the PR.
Working/PR-branch calendars cannot change batching; merged and fetched settings
apply at the next load. Malformed accepted calendars refuse with a reason.

Reporting shares this PR route: default OFF; off/teams/slack/both and complete
project/repository/scope/platform/destination/revision/secret_ref bindings. Show
effective commit/safe differences. Validate offline without credentials/providers;
reachability remains UNOBSERVED. Working/branch OFF/deletion has no effect;
fetch after owner merge. Unverified/malformed policy refuses sends. Preserve
receipts/withdrawal/correction destinations/pre-POST checks/two-send ceilings.

Budgets cover explicitly enrolled AWF sub-agents/attributable tools/retries;
exclude controller/independent/ULTRA agents. Preserve UNKNOWN/history/failures/
reservation identities; never reset totals/invent zero usage. OFF needs no new
ledger/telemetry/reservations/quotas/warnings/overrun quarantine. Independent
security/permissions/review/merge/host-capacity/bounded-recovery controls remain.

Only controller writes Jira: WORKER_STARTED=In Progress, PR_READY=In Review,
owner/head changes=In Progress, observed merge/JIRA_RECONCILED=Done with closing
comment. BLOCK/PARK/Epics never transition; owner-closure tickets wait. Read
before/after; mismatch/unknown stops that ticket without retry, retaining actor/
time or unknown and reporting external-automation conflict. Others continue.

Declare tier/closure; retain boundaries/candidate-bound reviews/owner cap
dispositions. Escalate within approved limits; increases/weaker review/
reconciliation enablement need human direction. Candidate policy cannot approve
itself. Continue authorized work; report actual results/next actions.

Publisher: Jonathan Kinlay; [Apache-2.0](LICENSE), [NOTICE](NOTICE).
