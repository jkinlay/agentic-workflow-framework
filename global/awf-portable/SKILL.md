---
name: awf
description: Verify and adopt AWF releases; guide bounded project work, independent review and evidence accounting. Includes AWF 1.9.6.
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

Echo the budget request; run `operating set` with exact instruction/assignments.
Exit 3 `PENDING_OWNER_CONFIRMATION` changes nothing. Give the exact returned
`workflow.py operating confirm --digest D` command with verified interpreter,
script/root for the owner's terminal. Never run confirm yourself. After the
owner says they ran it, run `operating show`; report persisted effective settings.
Never claim application without show. Typed identity/chat authenticates nobody.
Confirm verifies machine-stored GitHub User login, numeric trusted-owner ID and
interactive terminal. Empty owners require reviewed configuration, never automatic
enrollment. This cannot resist a process with the owner's full rights and its
own terminal.

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
