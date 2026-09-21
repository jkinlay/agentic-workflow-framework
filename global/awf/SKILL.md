---
name: awf
description: Locate and verify catalogued Agentic Workflow releases for adoption. Source-only compatibility helper; the portable awf skill additionally bundles the release.
---

# Catalogued AWF

Use `scripts/awf.py --catalog CATALOG locate --version 1.9.1` and `inspect --project TARGET --version 1.9.1`. Preserve selectors, verify pins and read located AGENTS/configuration. Prepare reviewed adoption; do not claim the project changed from discovery alone. Prefer the separately installed portable skill for bundled-source preparation and skill upgrades.

For adoption read [workflow](references/workflow.md). In the first message explain that installing AWF changes repository governance files, so you will prepare a draft PR for the owner to merge. Missing or unobserved default-branch rules are warnings, not preconditions for installing files, mapping configuration or preparing that PR. Report APPLIED/MISSING/UNOBSERVED accurately and offer the shipped ruleset.

Preserve existing configuration; derive supported values and report exact residue/remedies. Disabled Jira uses local records. Missing CI/owners warn during adoption. Bootstrap must execute isolated installed verification/validation commands with successful exits, accepted outputs and bound digests before CONFIGURED. Status recomputes current checks, not past child execution. Record actual status/next action in the PR checklist. ACTIVE needs independent release trust, receipt-changing merged adoption and accepted raw AWF bytes on the fresh default branch; unrelated product edits may coexist. Receipt/branch assertions alone are insufficient. Use the shipped adoption PR checklist.

Live external review, scheduled amendments and agent pushes to repositories holding secrets require observed rules and existing mode qualification; relevant live execution also needs configured CI and actual trusted merge owners. Where that push restriction applies, prepare local changes and the PR body for owner publication or observe rules first. Never claim an uncreated PR, apply server rules through bootstrap, or treat installation as live activation.
When adoption reaches CONFIGURED, show `workflow.py operating show` and offer: keep defaults, review Epics and recommend, or custom. Read the located operating guide. Direct user instructions change choices within governance ceilings/allowlists/floors: translate, echo, run `operating set`, show results. Recommendations require acceptance; governance changes require a PR. Resolve an explicit Epic ID for `--epic EPIC-ID` routes. Preserve running reservations and drain surplus streams.

Follow the located ticket lifecycle. On COMPLETE, push the assigned feature branch/open a draft PR before handoff. Observe PR/head and current validation before mark-ready and critic review. READY_FOR_CRITIC precedes READY_FOR_OWNER_AUTHORIZATION after critic, specialists and final gate; neither permits merge.

The controller is the sole Jira writer and mirrors the lifecycle: WORKER_STARTED → `in_progress`, PR_READY → `in_review`, owner-requested changes → `in_progress`, JIRA_RECONCILED after the observed merge → `done` (closing comment names PR, reviewed head, merge commit); BLOCK/PARK never write; owner-closure tickets stay In Review; never Epics. Read before/after; mismatch/unknown stops that ticket's writes, retains observed actor/time or unknown, reports a suspected external conflict, never retries. Post digests, not prose.

Declare each contract's risk tier and closure standard before review; block only on an acceptance criterion or a mandatory boundary; at the amendment cap record one owner disposition (merge with notes, park, rescope, bounded extension). Read the located review-tiers runbook.
