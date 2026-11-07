---
name: awf
description: Check AWF budgets and prepare settings PRs; locate verified releases for adoption. Source-only; portable skill bundles releases.
---

# Catalogued AWF

Use `scripts/awf.py --catalog CATALOG locate --version 1.9.6` and `inspect --project TARGET --version 1.9.6`. Preserve selectors, verify pins and read located AGENTS/configuration. Prepare reviewed adoption; do not claim the project changed from discovery alone. Prefer the separately installed portable skill for bundled-source preparation and skill upgrades.

For adoption read [workflow](references/workflow.md). In the first message explain that installing AWF changes repository governance files, so you will prepare a draft PR for the owner to merge. Missing or unobserved default-branch rules are warnings, not preconditions for installing files, mapping configuration or preparing that PR. Report APPLIED/MISSING/UNOBSERVED accurately and offer the shipped ruleset.

Preserve existing configuration; derive supported values and report exact residue/remedies. Disabled Jira uses local records. Missing CI/owners warn during adoption. Bootstrap must execute isolated installed verification/validation commands with successful exits, accepted outputs and bound digests before CONFIGURED. Status recomputes current checks, not past child execution. Record actual status/next action in the PR checklist. ACTIVE needs independent release trust, receipt-changing merged adoption and accepted raw AWF bytes on the fresh default branch; unrelated product edits may coexist. Receipt/branch assertions alone are insufficient. Use the shipped adoption PR checklist.

Live Codex review, scheduled amendments and agent pushes to repositories holding secrets require observed rules and existing mode qualification; relevant live execution also needs configured CI and actual trusted merge owners. Where that push restriction applies, prepare local changes and the PR body for owner publication or observe rules first. Never claim an uncreated PR, apply server rules through bootstrap, or treat installation as live activation.
When adoption reaches CONFIGURED, show `workflow.py operating show` and offer: keep defaults, review Epics and recommend, or custom. Read the operating guide. Direct user instructions change choices within governance ceilings/allowlists/floors: translate, echo, run `operating set`, show results. Recommendations require acceptance; governance changes require a PR. Resolve an explicit Epic ID for `--epic EPIC-ID` routes. Preserve running reservations and drain surplus streams.

Follow the located ticket lifecycle. On COMPLETE, push the assigned feature branch/open a draft PR before handoff. Observe PR/head and current validation before mark-ready and critic review. READY_FOR_CRITIC precedes READY_FOR_OWNER_AUTHORIZATION after critic, specialists and final gate; neither permits merge.

For budget choices, read verified `.agentic/docs/39-BUDGETING.md` and inspect
installed controls first. Fresh 1.9.6 defaults accounting/enforcement OFF, with
dormant limits exactly 4,000,000 tokens per ticket and 1,000,000,000 per project
lifetime. A limit enables neither switch; enforcement requires accounting, and
disabling accounting disables enforcement. Preserve upgraded behavior until
owner adoption; installed 1.9.5 stays unchanged/enabled. Recommend versioned
preservation, never in-place edits to integrity-managed bytes or receipts.

For budget questions in chat, run `workflow.py operating show`
without approval. Present switches, limits, source (fresh
defaults, default branch at the reported commit, or legacy retained), working-file
differences and offline rules UNOBSERVED warnings; publication readiness/adoption observe live rules. Last PR/merger optional;
say when unobserved. No terminal command is required of the owner.

Branch from the fetched default branch. Edit only requested
`OPERATING_CONFIG.yaml` values, preserving text/comments; accounting OFF also
disables enforcement. Run `operating validate --file PROPOSED`; invalid combinations refuse. Present exact
before/after in a draft PR under publication authority. Restricted agent
pushes prepare branch/body for owner publication. The owner reviews and merges
on GitHub; agents never merge. Fetch after observed merge, then show effective
settings/commit. Git history and the PR are the audit trail. Working/branch edits
have no protected effect. Unprotected `operating set` remains; protected set
explains the PR route. Keep Git metadata outside worker write authority.
Host rules and owner govern merge; direct pushes to an unprotected default branch
or the owner's full rights can get around this. Offer the shipped ruleset.
`merge_gate.trusted_owner_ids` retains its merge-gate use.

Enabled budgets cover only explicitly enrolled AWF sub-agent runs and attributable
tools/retries; exclude the main controller and independent agents, including
ULTRA agents. Preserve UNKNOWN attribution, historical charges, failures and
running reservation identities across OFF/ON; never reset lifetime totals or
invent retrospective zero usage. OFF new work needs no ledger, telemetry,
reservations, quotas, warnings or overrun quarantine; independent security,
permissions, review, merge, host capacity and bounded recovery still apply.
Inspect integration before claiming.

The controller is the sole Jira writer and mirrors the lifecycle: WORKER_STARTED → `in_progress`, PR_READY → `in_review`, owner-requested changes → `in_progress`, JIRA_RECONCILED after the observed merge → `done` (closing comment names PR, reviewed head, merge commit); BLOCK/PARK never write; owner-closure tickets stay In Review; never Epics. Read before/after; mismatch/unknown stops that ticket's writes, retains observed actor/time or unknown, reports a suspected external conflict, never retries. Post digests, not prose.

Declare each contract's risk tier and closure standard before review; block only on an acceptance criterion or a mandatory boundary; at the amendment cap record one owner disposition (merge with notes, park, rescope, bounded extension). Read the located review-tiers runbook.
