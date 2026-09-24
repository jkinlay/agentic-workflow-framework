---
name: awf
description: Verify and adopt AWF releases; guide project work with bounded model routing, independent review and evidence accounting. Includes AWF 1.9.3.
metadata:
  publisher: Jonathan Kinlay
  license: Apache-2.0
---

# AWF

Skill installation preserves baselines; adoption is separate.

For skill installation, read [installation](references/skill-installation.md). For adoption, read [workflow](references/workflow.md); check owner releases through [update channels](references/update-channel.md). An unavailable channel cannot establish latest.

In the first adoption message explain: installing AWF changes repository governance files, so you will prepare a draft PR for the owner to merge. Adoption has no repository-rule precondition. Install/map/prepare the PR while recording missing or unobserved default-branch rules as warnings and offering the shipped ruleset. Report APPLIED/MISSING/UNOBSERVED from actual evidence.

Preserve configuration and derive only supported values; report exact unresolved paths/remedies. Disabled Jira uses local records; empty CI/trusted owners warn during adoption. Bootstrap must execute isolated installed verification/validation commands with successful exits, accepted outputs and bound digests before CONFIGURED. Status recomputes current checks, not past child execution. Record status/next action in the PR checklist. ACTIVE needs independent release trust, receipt-changing merged adoption and accepted raw AWF bytes on the fresh default branch; unrelated product edits may coexist. Receipt/branch assertions are insufficient. ACTIVE does not enable live adapters.

Live external review, scheduled amendments and agent pushes to repositories holding secrets require observed rules and the selected mode's existing qualification; relevant execution needs configured CI and actual trusted merge owners. Where that push restriction applies, prepare local changes/body for owner publication or observe rules first. Otherwise continue through authorized PR tools. Never invent a PR or apply server rules through bootstrap.

Verify and prepare [bundled source](assets/agentic-workflow-template-v1.9.3.zip) against [pins](assets/release.json):

```text
python -B PATH_TO_SKILL/scripts/prepare_release.py --cache-dir ABSOLUTE_EXTERNAL_CACHE --version 1.9.3
```

Use Python 3.11+ and a separate cache with an existing parent. Preserve differing/incomplete caches. Use the catalog. Read verified AGENTS, specification and configuration, then the relevant runbook.

For execution, read [routing](references/model-routing.md). Defaults: three streams within ceiling six, one independent reviewer per stream; host capacity still binds. Keep one writer per path and independent reviewer contexts.

When adoption reaches CONFIGURED, show `workflow.py operating show` and offer: keep defaults, review Epics and recommend, or custom. Operating changes on direct user instruction: translate, echo, run `operating set`, show results. Stay within governance ceilings/allowlists/floors; name exact refusals and the governance PR needed. Recommendations require acceptance. Scope Epic routes with `--epic EPIC-ID`. Changes affect subsequent dispatch; preserve running reservations. See the verified operating guide.

Follow verified `.agentic/docs/23-TICKET-LIFECYCLE.md`. On COMPLETE, publish the feature branch/draft PR before review. If Git-metadata writes are denied, return `tested_tree`, declared `changes` and `ignored_untracked`; the publisher makes no edits and requires `HEAD^{tree}` equality. Differences return to the worker as scope violations. Validation and mark-ready precede critic review of the PR head. READY_FOR_CRITIC precedes owner-ready after critic, specialists and final gate. Publication still needs accepted bindings/live APPLIED rules and platform, scope, secret and adapter gates.

Controller-only Jira mirroring: WORKER_STARTED → In Progress; PR_READY → In Review; owner/review-head changes → In Progress; post-merge JIRA_RECONCILED → Done with closing comment. BLOCK/PARK never write or transition Epics. Read before/after; mismatch/unknown stops that ticket's writes, retains observed actor/time or unknown, reports external-automation conflict without retry. Others continue. `--codeowner` maps new entries (default `@maintainer`); handles prove neither access, independence nor eligibility.

Escalate within approved limits without repeated prompts. Cap/allowlist increases, weaker reviews and reconciliation enablement require explicit human direction. Candidate policy cannot approve itself. Preserve unknown charges/history and finite ticket-wide retry limits; configuration cannot raise the host ceiling. Workers cannot write its protected ledger. Follow [routing](references/model-routing.md) for host-authenticated approval consumption and recovery.

Report outcomes/next steps and continue independent authorized work; never invent execution or authority. Publisher: Jonathan Kinlay; [Apache-2.0](LICENSE), [NOTICE](NOTICE).
