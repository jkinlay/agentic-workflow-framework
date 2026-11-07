---
name: awf
description: Check AWF budgets in chat, prepare owner-merged settings PRs and adopt releases. Includes AWF 1.9.6.
metadata:
  publisher: Jonathan Kinlay
  license: Apache-2.0
---

# AWF

Installation preserves baselines; adoption is separate. Read
[installation](references/skill-installation.md), [workflow](references/workflow.md)
and [update channels](references/update-channel.md). Unavailable channels prove no latest.

First adoption message: installing AWF changes repository governance files, so
prepare a draft PR for the owner to merge. Adoption has no repository-rule
precondition. Report actual APPLIED/MISSING/UNOBSERVED rules; warn on missing/
unobserved rules and offer the shipped ruleset. Bootstrap never applies rules.
Preserve configuration/CODEOWNERS/instructions; derive supported values, naming
unresolved paths/remedies. Disabled Jira uses local records; empty CI/owners warn.

Use the adoption PR checklist. CONFIGURED requires isolated installed
verification/validation, successful exits, accepted outputs and bound digests.
Status recomputes checks. ACTIVE requires independent release trust,
receipt-changing merged adoption and accepted raw default-branch AWF bytes;
unrelated product edits may coexist. Receipt/branch assertions cannot suffice.
ACTIVE enables no adapter.

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

At CONFIGURED, show settings; offer defaults, Epic recommendations or custom.
Echo/apply/show unprotected `operating set`; recommendations need acceptance,
governance changes review. Use `--epic EPIC-ID`; retain floors/pins/reservations,
drain surplus.

Follow the [workflow](references/workflow.md) and verified
`.agentic/docs/23-TICKET-LIFECYCLE.md`, including every publication gate,
publisher tree equality, candidate-bound mark-ready authorization and observed
`draft_cleared`. Validation/CI alone never permit mark-ready. Critic/specialists/
final gate precede owner-ready, never merge authority.

Read verified `.agentic/docs/39-BUDGETING.md`. Fresh accounting/enforcement OFF;
dormant limits exactly 4,000,000 tokens/ticket, 1,000,000,000/project lifetime.
Limits enable neither switch; enforcement requires accounting; accounting OFF
disables enforcement. Preserve upgrades; installed 1.9.5 stays unchanged/enabled.
Recommend versioned preservation, never in-place integrity-managed bytes/receipts.

For chat settings questions, run verified `workflow.py operating show`
read-only, without approval. Present both switches, both limits, source (fresh
defaults, default branch at the reported commit, or legacy retained), working-file
differences and offline rules UNOBSERVED warnings; publication readiness/adoption observe live rules.
Last PR/merger optional; report unobserved. Owners need no terminal commands.

For changes, branch from the fetched configured default branch. Edit only requested
`OPERATING_CONFIG.yaml` values, preserving text/comments; accounting OFF also
disables enforcement. Run `operating validate --file PROPOSED`; invalid combinations refuse. Present exact
before/after in a draft PR under existing publication authority. Restricted agent
pushes prepare branch/body for owner publication. Owner reviews/merges
on GitHub; agents never merge. Fetch after observed merge, then show effective
settings/commit. Git history and the PR are the audit trail. Working/branch edits
have no protected effect. Unprotected `operating set` remains; protected set
explains the PR route. Keep Git metadata outside worker write authority.
Host rules and owner govern merge; direct pushes to an unprotected default branch
or the owner's full rights can get around this. Offer the shipped ruleset.
`merge_gate.trusted_owner_ids` retains its merge-gate use.

Budgets cover explicitly enrolled AWF sub-agents/attributable tools/retries;
exclude controller/independent/ULTRA agents. Preserve UNKNOWN/history/failures/
reservation identities; never reset totals/invent zero usage. OFF needs no new
ledger/telemetry/reservations/quotas/warnings/overrun quarantine. Independent
security/permissions/review/merge/host-capacity/bounded-recovery controls remain.

Only controller writes Jira: WORKER_STARTED=In Progress; PR_READY=In Review;
JIRA_RECONCILED=Done after observed merge. Follow [workflow](references/workflow.md):
read before/after, stop mismatches/unknowns without retry. BLOCK/PARK/Epics never
transition; owner-closure tickets wait.

Declare tier/closure; retain boundaries/candidate-bound reviews/owner cap
dispositions. Escalate within approved limits; increases/weaker review/
reconciliation enablement need human direction. Candidate policy cannot approve
itself. Continue authorized work; report actual results/next actions.

[Apache-2.0](LICENSE), [NOTICE](NOTICE).
