# AWF budgeting

AWF-242: Tier 3, FULL. Fresh accounting/enforcement default OFF; dormant limits:
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.
Missing fields preserve legacy budgeting/reviewed limits.

## Settings in chat and PRs

Ask in AWF Codex chat to check budget settings. The skill runs read-only
`workflow.py operating show` without approval. It presents accounting/enforcement
on or off, both limits, source (fresh defaults, default branch at the reported
commit, or legacy retained), and working-file differences. Last PR/merger is
optional information; if unobserved, say so without failure.

Ask in chat for a change to `budgeting.accounting_enabled`,
`budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket` or
`budgeting.max_tokens_per_project`. Limits are positive signed 64-bit integers
(1..9223372036854775807); switches are booleans. Validate with `operating validate --file PROPOSED`;
refuse invalid combinations before preparing a PR. Accounting OFF also disables
enforcement; a dormant limit enables nothing. No Epic overrides.

The skill branches from the fetched configured default branch and changes only
requested values in `OPERATING_CONFIG.yaml`, preserving text/comments. The PR
shows exact before/after under existing publication authority. Restricted agent
pushes prepare branch/body for owner publication. The owner reviews and merges
on GitHub; agents never merge. No terminal command is required of the owner.
Git history and the PR are the audit trail. Protected `operating set` writes
nothing and explains the PR route; unprotected settings retain their existing flow.

Effective protected settings are the locally known commit at
`refs/remotes/origin/<github.base_branch>`. Working-tree edits and PR branches
have no effect on budget admission, recovery, controller or routing, including
disabling enforcement. After owner merge and fetch, the next controller load
reads the new commit. Older worktrees read the same settings. `show` and
`validate-config` explain differences: open a PR. Missing, unreadable or malformed
default-branch files refuse budget work with a reason; never silently assume OFF.

Fresh defaults without remote metadata and absent legacy blocks load without
Git commands, gh or network. Nondefault protected settings without Git/a fetched
ref refuse budget work. Once remote metadata exists, it must be inspected even
when working bytes say OFF, to prevent disabling an accepted policy.
OFF needs no usage ledger, telemetry or reservations. Bootstrap/adoption/upgrade
preserve existing choices/history; legacy fields retain legacy policy until
manual versioned adoption. No retrospective zero usage.

Merge authority rests with the host's repository rules and the owner.
`show` reports rules APPLIED, MISSING or UNOBSERVED as warnings, never blocking.
Git metadata must be outside worker write authority. A process able to push
directly to an unprotected default branch, or one with the owner's full rights,
can get around this: offer the shipped ruleset separately.
`merge_gate.trusted_owner_ids` remains for its existing merge-gate use.

## Ledger and enrollment

Only explicitly enrolled AWF sub-agents count; enrollment binds project/ticket/run/dispatch/session/role.
Exclude controller/independent Codex agents, including ULTRA. Repository/model/ancestry/worker
requests cannot enroll. Protect state outside worktrees; paths supplement isolation.

`RoutingLedger.model_runs` is canonical; `BudgetLedger` adds enrollment. Atomic
checks retain history across days/restarts/policies/worktrees. Bridge legacy
`budget_charges` transactionally; verify sealed archives, deduplicate identities,
refuse conflicts. Separate databases require migration; enforcement refuses until
history is imported. Never evade history by changing databases.

Routed reserve requires trusted registry/binding and matching run/ledger IDs;
settlement retains model/effort/context/independence checks. Budget-only facade
cannot settle routed runs or bypass checks. State-store uses that facade without
duplicate reservations. Preserve legacy identities; never relaunch replays.

## Modes and preservation

OFF requires no budget database, telemetry, reservations/events, warnings or
overrun quarantine. Retain security/permissions/ownership/review/merge/host-capacity
and bounded recovery controls; security/context/review mismatches quarantine.
Existing recovery limits and owner authorization to raise them bind in every mode.
Historical budget overruns do not block OFF/accounting-only work.
Ledger-free routing proposals never admit/launch; operational admission remains required.
Accounting-only records usage without quota refusal/warnings/quarantine. Unknown
estimates/actuals stay UNKNOWN/null, never zero; independent runs remain outside quotas.
Enforcement atomically counts known usage/outstanding reservations per ticket/lifetime;
unreliable unknown totals refuse. Runs/late settlements retain admission-time bindings/mode.
OFF applies only to subsequent work, never deletes/refunds history, reservations,
failures or unknowns; known overruns stay charged. Reconciliation closes
incidents without refund. Count only attributable tools/retries; enroll retries.

Use versioned 1.9.6 preservation, not in-place backport. Installed CMC Quant Foundry
1.9.5 stays unchanged/enabled. Owner may retain it or authorize qualified preservation
with explicit budget disposition. Never edit integrity-managed bytes/pins/receipts,
or initialize fresh OFF over upgrades. Hosts share ledgers; migration preserves
history/reservations. Live integration requires qualification; release owns manifests.
