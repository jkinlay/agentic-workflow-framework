# AWF budgeting

Tier 3, FULL. Accounting/enforcement default OFF; dormant limits:
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.
Missing fields preserve legacy budgeting/reviewed limits.

## Settings in chat and PRs

In AWF Codex chat, read-only `workflow.py operating show` needs no approval.
Present switches/limits, source/commit, differences; last PR/merger
is optional and may be unobserved.

Chat changes cover `budgeting.accounting_enabled`, `budgeting.enforcement_enabled`,
`budgeting.max_tokens_per_ticket`, `budgeting.max_tokens_per_project`.
Limits: integers 1..9007199254740991; switches: booleans. No Epic overrides.
Run `operating validate --file PROPOSED`; invalid combinations refuse before PR preparation.

Branch from the fetched default, change requested values preserving text/comments,
and show before/after under existing publication authority. Restricted pushes
prepare branch/body for owner publication. The owner merges; agents never merge.
No owner terminal commands. Git/PR history audits protected changes;
protected `operating set` refuses without writes. Calendar uses the same PR route,
effective source and validation; see [calendar](operating-calendar.md).

Effective protected settings come from the locally fetched default-branch commit.
Only `refs/remotes/origin/HEAD` identifies it. Configuration is never an identity
input; `show` reports disagreement with configured `github.base_branch`.
Working-tree edits and PR branches have no effect on budget admission,
recovery, controller or routing, including disabling enforcement. Only merged
and fetched changes take effect. Older checkouts read the same policy; differences
mean update the checkout when behind, otherwise propose a PR.

An absent file means no accepted policy: fresh OFF or legacy
retained. With configured origin but no origin/HEAD, the default is unverified.
Show/validation/installation retain the warning; working values are provisional. Admission tries refresh first. If still unverified,
protected decisions refuse, including reporting and calendar decisions; working
OFF cannot establish accepted policy. Fresh defaults in non-repositories remain offline. The cycle stops
before planning/dispatch on that refusal; other commands remain available.
The next controller cycle or `git remote set-head origin --auto` establishes identity.
With no origin configured there is no remote policy refresh. Present but empty,
malformed, invalid or unavailable policy blobs refuse. Non-repositories run no Git/gh.
OFF needs no ledger, telemetry or reservations. Preserve upgrade choices/history;
legacy policy stays until versioned adoption. No retrospective zero usage.

`show` reports commit and FETCH_HEAD time as the fetch attempt of any
ref, never evidence of policy refresh. New merges remain invisible until fetched.
The controller's own checkout requires repository-local remote.origin.url;
relative/inside-checkout paths refuse. Git 2.29+ ([notes](https://git-scm.com/docs/RelNotes/2.29.0));
older-binary execution unproven. Lock before 30-second discovery/fetch,
advertised-commit checking and publication. Mismatches retry next cycle.
UTF-8 names use check-ref-format. Preserve unverified tracking HEAD when identity
is absent; delete conflicts, update target, then origin/HEAD. Hierarchical renames recover.
Failures retain policy/unverified refusal with remedies. Readers ignore locks;
unlocked locks older than ten minutes are replaced/reported. Locking prevents
clock-jump takeover. Shared filtering preserves Git helpers/home/PATH/SSH-agent.
`show` never fetches. The skill fetches after owner merge, then shows settings.

Merge authority rests with host rules and the owner. `show` reports rules UNOBSERVED
as a nonblocking warning; live rules are observed by publication readiness and
adoption preflight, which report APPLIED/MISSING/UNOBSERVED. Offer the shipped
ruleset separately. `merge_gate.trusted_owner_ids` keeps its merge-gate use.

Keep Git metadata/native Git outside worker write authority. This stops
`update-ref`, fetch-refspec steering, ref deletion and linked `.git` gitfile
replacement. Linked gitfiles need matching common-directory backlinks, supplementing
host isolation. Direct default-branch pushes/owner rights bypass merge governance;
reader digests cannot authenticate merges.

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
