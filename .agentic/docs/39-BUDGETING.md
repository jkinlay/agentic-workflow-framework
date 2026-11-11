# AWF budgeting

Tier 3, FULL. Accounting/enforcement default OFF; dormant limits:
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.
Missing fields preserve legacy budgeting/reviewed limits.

## Settings in chat and PRs

Read-only `workflow.py operating show`: no approval. Present switches/limits/source/commit/differences; PR/merger optional/unobserved.

Chat changes cover `budgeting.accounting_enabled`, `budgeting.enforcement_enabled`,
`budgeting.max_tokens_per_ticket`, `budgeting.max_tokens_per_project`.
Limits: integers 1..9007199254740991; switches: booleans. No Epic overrides.
Run `operating validate --file PROPOSED`; invalid combinations refuse before PR preparation.

Branch from fetched default; preserve text/comments. Show before/after under publication authority. Restricted pushes: branch/body for owner publication. Owners merge.
No owner terminal commands. Git/PR history audits protected changes;
protected `operating set` refuses without writes. Calendar uses the same PR route,
effective source and validation; see [calendar](operating-calendar.md).
Reporting defaults OFF; Teams/Slack/both share this PR route with project-bound destinations; preserve delivery state.

Effective protected settings come from the locally fetched default-branch commit.
Only `refs/remotes/origin/HEAD` identifies it. Configuration is never an identity
input; `show` reports disagreement with configured `github.base_branch`.
Working-tree edits and PR branches have no effect on budget admission,
recovery, controller or routing, including disabling enforcement. Only merged
and fetched changes take effect. Older checkouts read the same policy; differences
mean update the checkout when behind, otherwise propose a PR.

An absent default-branch file is accepted: fresh defaults or legacy retained. With configured origin but no origin/HEAD, the default is unverified.
Show/validation/installation retain the warning; working values are provisional. Admission tries refresh first. If still unverified,
tracking refs or nondefault working values refuse budget work even if enforcement
says OFF. Without refs, fresh OFF/legacy can continue offline. The cycle stops
before planning/dispatch on that refusal; other commands remain available.
Next cycle or `git remote set-head origin --auto` establishes identity.
With no origin configured there is no remote policy refresh. Empty,
malformed, invalid or unavailable policy blobs refuse. Non-repositories run no Git/gh.
OFF needs no ledger/telemetry/reservations. Preserve upgrade choices/history; never invent usage.

`show` reports commit and FETCH_HEAD time as the fetch attempt of any
ref, never evidence of policy refresh. New merges remain invisible until fetched.
The controller's own checkout requires repository-local remote.origin.url;
relative/inside-checkout paths refuse. Use Git 2.29+ ([notes](https://git-scm.com/docs/RelNotes/2.29.0)); older-binary execution remains unproven. Lock before 30-second discovery/fetch,
advertised-commit checking and publication. Mismatches retry next cycle.
UTF-8 names use check-ref-format. Preserve unverified tracking HEAD when identity
is absent; delete conflicts, update target, then origin/HEAD. Hierarchical renames recover.
Failures retain policy/unverified refusal with remedies. Readers ignore locks;
unlocked locks older than ten minutes are replaced/reported. Locking prevents
clock-jump takeover. Shared filtering preserves Git helpers/home/PATH/SSH-agent.
`show` never fetches. The skill fetches after owner merge, then shows settings.

Host rules and owner retain merge authority. `show` reports rules UNOBSERVED
as a nonblocking warning; live rules are observed by publication readiness and
adoption preflight, which report APPLIED/MISSING/UNOBSERVED. Offer the shipped
ruleset separately. `merge_gate.trusted_owner_ids` keeps its merge-gate use.

Keep Git metadata/native Git outside worker write authority. This stops
`update-ref`, fetch-refspec steering, ref deletion and linked `.git` gitfile
replacement. Linked gitfiles need matching common-directory backlinks, supplementing
host isolation. Direct default-branch pushes/owner rights bypass merge governance;
reader digests cannot authenticate merges.

## Ledger and enrollment

Only explicitly enrolled AWF sub-agents count, bound to project/ticket/run/dispatch/session/role.
Exclude controller/independent Codex agents, including ULTRA. Repository/model/ancestry/worker
requests cannot enroll. Protect state outside worktrees; paths supplement isolation.

`RoutingLedger.model_runs` is canonical; `BudgetLedger` adds enrollment. Atomic
checks retain history across days/restarts/policies/worktrees. Bridge legacy
`budget_charges` transactionally; verify sealed archives, deduplicate identities,
refuse conflicts. Separate databases require migration; enforcement refuses until
history is imported. Never evade history by changing databases.

Routed reserve requires trusted registry/binding matching run/ledger IDs;
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

Use versioned preservation; installed CMC Quant Foundry 1.9.5 stays unchanged/enabled. Only owners authorize qualified preservation/budget disposition. Never edit integrity-managed bytes/pins/receipts or initialize OFF over upgrades. Live integration requires qualification; release owns manifests.

## Upgrade and adoption

Absent budgeting remains LEGACY_RETAINED. Owner-requested `operating set --adopt-budgeting` prepares dormant OFF controls by PR; owner merge/fetch applies. Enabling needs another settings PR/locked shared-ledger import proofs. Quiesce writers; independently verify `prepare_budget_import`/`check_budget_import`, retained sources, reservations, UNKNOWN usage and recovery history. See `MIGRATION-v1.9.5-to-v1.9.6.md`.
