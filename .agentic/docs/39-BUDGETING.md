# AWF budgeting

Tier 3, FULL. Accounting/enforcement default OFF; dormant limits:
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.
Missing fields preserve legacy budgeting/reviewed limits.

## Settings in chat and PRs

Ask in AWF Codex chat to check budget settings. The skill runs read-only
`workflow.py operating show` without approval. It presents accounting/enforcement
on or off, both limits, source (fresh defaults, default branch at the reported
commit, or legacy retained), and working-file differences. Last PR/merger is optional; report when unobserved.

Ask in chat for a change to `budgeting.accounting_enabled`,
`budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket` or
`budgeting.max_tokens_per_project`. Limits are signed 64-bit integers
(1..9223372036854775807); switches are booleans. Validate with `operating validate --file PROPOSED`;
refuse invalid combinations before preparing a PR. Accounting OFF also disables
enforcement; a dormant limit enables nothing. No Epic overrides.

The skill branches from the fetched configured default branch and changes only
requested values in `OPERATING_CONFIG.yaml`, preserving text/comments. The PR
shows exact before/after under existing publication authority. Restricted agent
pushes prepare branch/body for owner publication. The owner reviews and merges
on GitHub; agents never merge. No owner terminal commands.
Git history and the PR are the audit trail. Protected `operating set` writes
nothing and explains the PR route; unprotected settings retain their existing flow.

Effective protected settings come from the locally fetched default-branch commit.
Use `origin/HEAD`; otherwise use `github.base_branch` in the HEAD commit's
PROJECT_CONFIG blob, never its working file. Report disagreements; origin/HEAD
wins. Working-tree edits and PR branches have no effect on budget admission,
recovery, controller or routing, including disabling enforcement. Only merged
and fetched changes take effect. Older checkouts read the same policy; differences
mean update the checkout when behind, otherwise propose a PR.

An absent default-branch file means no accepted policy: fresh OFF or legacy
retained. No fetched default branch also permits fresh defaults or absent legacy
blocks; nondefault working settings refuse with that reason. Present but empty,
malformed, invalid or unavailable blobs refuse. Non-repositories run no Git/gh.
OFF needs no ledger, telemetry or reservations. Preserve upgrade choices/history;
legacy policy stays until versioned adoption. No retrospective zero usage.

`show` reports the commit, last FETCH_HEAD timestamp when available, and that a
newer merge remains invisible until fetched. The controller's own checkout,
never a worker's worktree, fetches the explicit default ref at cycle start with
a 30-second bound and existing checkout credentials. Failure reports UNAVAILABLE,
retains the last known policy and permits unrelated work. `show` never fetches. The skill fetches after owner merge, then shows settings.

Merge authority rests with host rules and the owner. `show` reports rules UNOBSERVED
as a nonblocking warning; live rules are observed by publication readiness and
adoption preflight, which report APPLIED/MISSING/UNOBSERVED. Offer the shipped
ruleset separately. `merge_gate.trusted_owner_ids` keeps its merge-gate use.

Git metadata and the native Git installation must remain outside worker write
authority. Only that metadata requirement stops `update-ref` of remote-tracking
refs, fetch-refspec steering, deleting refs, and repointing or emptying a linked
worktree's `.git` gitfile, which sits inside the worktree. A linked gitfile must
have a matching common-directory backlink; this check supplements host isolation.
Direct pushes to an unprotected default branch or the owner's full rights can
get around merge governance. Neither this reader nor its digest authenticates merges.

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
