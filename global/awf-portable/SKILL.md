---
name: awf
description: Verify and adopt AWF releases; guide project work with bounded model routing, independent review and evidence accounting. Includes AWF 1.9.6.
metadata:
  publisher: Jonathan Kinlay
  license: Apache-2.0
---

# AWF

Skill installation preserves baselines; adoption is separate.

For skill installation, read [installation](references/skill-installation.md). For adoption, read [workflow](references/workflow.md); check owner releases through [update channels](references/update-channel.md). An unavailable channel cannot establish latest.

In the first adoption message explain: installing AWF changes repository governance files, so you will prepare a draft PR for the owner to merge. Adoption has no repository-rule precondition. Install/map/prepare the PR while recording missing or unobserved default-branch rules as warnings and offering the shipped ruleset. Report APPLIED/MISSING/UNOBSERVED from actual evidence.

Preserve configuration and derive only supported values; report exact unresolved paths/remedies. Disabled Jira uses local records; empty CI/trusted owners warn during adoption. Bootstrap must execute isolated installed verification/validation commands with successful exits, accepted outputs and bound digests before CONFIGURED. Status recomputes current checks, not past child execution. Record status/next action in the PR checklist. ACTIVE needs independent release trust, receipt-changing merged adoption and accepted raw AWF bytes on the fresh default branch; unrelated product edits may coexist. Receipt/branch assertions are insufficient. ACTIVE does not enable live adapters.

Live external review, scheduled amendments and agent pushes to repositories holding secrets require observed rules and the selected mode's existing qualification; relevant execution needs configured CI and actual trusted merge owners. Where that applies, prepare local changes/body for owner publication or observe rules first. Otherwise continue through authorized PR tools. Never invent a PR or apply server rules through bootstrap.

Verify and prepare [bundled source](assets/agentic-workflow-template-v1.9.6.zip) against [pins](assets/release.json):

```text
python -B PATH_TO_SKILL/scripts/prepare_release.py --cache-dir ABSOLUTE_EXTERNAL_CACHE --version 1.9.6
```

Use Python 3.11+ and separate cache with existing parent. Preserve differing/incomplete caches. Use catalog. Read verified AGENTS, specification/configuration, then relevant runbook.

For execution, read [routing](references/model-routing.md). Fresh defaults: twelve workers, one shared independent critic/adversarial handler and one controller (fourteen slots); host capacity binds. Keep one writer per path and independent reviewer contexts.

When adoption reaches CONFIGURED, show `workflow.py operating show` and offer: keep defaults, review Epics and recommend, or custom. Operating changes on direct user instruction: translate, echo, run `operating set`, show results. Stay within governance ceilings/allowlists/floors; name exact refusals and the governance PR needed. Recommendations require acceptance. Scope Epic routes with `--epic EPIC-ID`. Changes affect subsequent dispatch; preserve running reservations. See the verified operating guide.

Follow `.agentic/docs/23-TICKET-LIFECYCLE.md`. COMPLETE publishes a draft PR. A Git-denied worker returns `tested_tree`, `changes` and `ignored_untracked`; the publisher requires `HEAD^{tree}` equality. Validation or green CI never permits mark-ready alone. Require an explicit owner/manager authorization record for PR/head, a pre-issued request and an authenticated immutable provider observation binding accepted configuration and trusted time; otherwise retain the refusal and draft. Authorized mark-ready plus observed `draft_cleared` permits critic review. READY_FOR_CRITIC precedes owner-ready. All publication gates remain.

For budget choices, read verified `.agentic/docs/39-BUDGETING.md` and inspect
installed controls first. Fresh 1.9.6 defaults accounting/enforcement OFF, with
dormant limits exactly 4,000,000 tokens per ticket and 1,000,000,000 per project
lifetime. A limit enables neither switch; enforcement requires accounting, and
disabling accounting disables enforcement. Preserve upgraded behavior until
owner adoption; installed 1.9.5 stays unchanged/enabled. Recommend versioned
preservation, never in-place edits to integrity-managed bytes or receipts.

Budget owner-direction admission requires an infrastructure-installed trusted
host observation bound to project, exact changes and prior settings hash. Chat
text, `source: user`, caller-authored identity and observation files cannot
authenticate the owner. Until owner selection, do not claim live control or
invent `operating set`; qualified integration must echo the change, use the
supported admission/persistence transaction and show reopened effective settings.
Otherwise state the exact missing integration without changing policy. Accept
recommendations.

Enabled budgets cover only explicitly enrolled AWF sub-agent runs and attributable
tools/retries; exclude the main controller and independent agents, including
ULTRA agents. Preserve UNKNOWN attribution, historical charges, failures and
running reservation identities across OFF/ON; never reset lifetime totals or
invent retrospective zero usage. OFF new work needs no ledger, telemetry,
reservations, quotas, warnings or overrun quarantine; independent security,
permissions, review, merge, host capacity and bounded recovery still apply.
Inspect integration before claiming.

Controller-only Jira mirroring: WORKER_STARTED → In Progress; PR_READY → In Review; owner/review-head changes → In Progress; post-merge JIRA_RECONCILED → Done with closing comment. BLOCK/PARK never write or transition Epics. Read before/after; mismatch/unknown stops that ticket's writes, retains observed actor/time or unknown, reports external-automation conflict without retry. Others continue. `--codeowner` maps new entries (default `@maintainer`); handles prove neither access, independence nor eligibility.

Escalate within approved limits without repeated prompts. Cap/allowlist increases, weaker reviews and reconciliation enablement require explicit human direction. Candidate policy cannot approve itself. Preserve unknown charges/history and finite ticket-wide retry limits; configuration cannot raise the host ceiling. Workers cannot write its protected ledger. Follow [routing](references/model-routing.md) for host-authenticated approval consumption and recovery.

Report outcomes/next steps and continue independent authorized work; never invent execution or authority. Publisher: Jonathan Kinlay; [Apache-2.0](LICENSE), [NOTICE](NOTICE).
