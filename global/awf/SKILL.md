---
name: awf
description: Check AWF budgets and prepare settings PRs; locate verified releases for adoption. Source-only; portable skill bundles releases.
---

# Catalogued AWF

Use `scripts/awf.py --catalog CATALOG locate --version 1.9.6` and `inspect --project TARGET --version 1.9.6`. Preserve selectors, verify pins and read located AGENTS/configuration. Prepare reviewed adoption; do not claim the project changed from discovery alone. Prefer the separately installed portable skill for bundled-source preparation and skill upgrades.

For adoption read [workflow](references/workflow.md). In the first message explain that installing AWF changes repository governance files, so you will prepare a draft PR for the owner to merge. Missing or unobserved default-branch rules are warnings, not preconditions for installing files, mapping configuration or preparing that PR. Report APPLIED/MISSING/UNOBSERVED accurately and offer the shipped ruleset.

Preserve configuration; report exact residue/remedies. Disabled Jira uses local records. Missing CI/owners warn during adoption. Bootstrap must execute isolated installed verification/validation commands with successful exits, accepted outputs and bound digests before CONFIGURED. Status recomputes current checks, not past child execution. Record actual status/next action in the PR checklist. ACTIVE needs independent release trust, receipt-changing merged adoption and accepted raw AWF bytes on the fresh default branch; unrelated product edits may coexist. Receipt/branch assertions alone are insufficient. Use the shipped adoption PR checklist.

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

For budget/calendar questions, run `workflow.py operating show`
without approval. Present switches/limits, source/commit, working differences
and rules UNOBSERVED warnings. Publication/adoption observe live rules. Last PR/merger optional;
Report unobserved. No owner terminal commands.

Branch from the fetched default branch. Edit only requested
`OPERATING_CONFIG.yaml` values, preserving text/comments; accounting OFF also
disables enforcement. Run `operating validate --file PROPOSED`; invalid combinations refuse. Present exact
before/after in a draft PR under publication authority. Restricted agent
pushes prepare branch/body for owner publication. The owner reviews and merges
on GitHub; agents never merge. Fetch after observed merge, then show effective
settings/commit. Git history and the PR are the audit trail. Working/branch edits
have no protected effect. Unprotected `operating set` remains; protected set
explains the PR route. Keep Git metadata outside worker write authority.
Host rules govern merge; default-branch push/owner rights can bypass this. Offer the ruleset.
`merge_gate.trusted_owner_ids` retains its merge-gate use.
Calendar uses this same chat/PR route. Present effective timezone, continuous
work mode, review days/opening/cutoff and EoD schedule from `operating show`.
Defaults: 24/7; weekday review batching enabled 07:00-17:00 Europe/London; daily EoD 17:00. Validate proposed calendar values before preparing the PR.
Working/PR-branch calendars cannot change batching; merged and fetched settings
apply at the next load. Malformed accepted calendars refuse with a reason.

Budgets cover enrolled AWF sub-agents/tools/retries only, excluding controller/independent
agents/ULTRA. Preserve UNKNOWN usage, charges, failures and running identities across
OFF/ON; never reset totals. OFF needs no ledger/telemetry/reservations/quotas.
Security, review, merge, capacity and bounded recovery still apply.

Only the controller writes Jira: WORKER_STARTED to in_progress; PR_READY to in_review;
owner changes to in_progress; observed merge to done with PR/head/merge comment.
BLOCK/PARK never write; owner-closure tickets wait; never transition Epics.
Read before/after; mismatch/unknown stops writes, retains actor/time, reports
suspected external conflict, and never retries. Post validated digests.

Declare each contract's risk tier and closure standard before review; block only on an acceptance criterion or a mandatory boundary; at the amendment cap record one owner disposition (merge with notes, park, rescope, bounded extension). Read the located review-tiers runbook.

Legacy upgrades retain absent budgeting/history. Owner-requested `operating set --adopt-budgeting` prepares dormant OFF controls by PR; owner merge/fetch applies. Enabling requires another settings PR/verified shared-ledger history import.
