# AWF budgeting

AWF-242: Tier 3, FULL. Fresh accounting/enforcement OFF; dormant limits exactly
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.
Missing fields preserve legacy budgeting/reviewed limits.

## Operating choices and authentication

`OPERATING_CONFIG.yaml` block:

```json
"budgeting": {
  "accounting_enabled": false,
  "enforcement_enabled": false,
  "max_tokens_per_ticket": 4000000,
  "max_tokens_per_project": 1000000000
}
```

`operating show`/diagnostics display settings. Paths:
`budgeting.accounting_enabled`, `budgeting.enforcement_enabled`,
`budgeting.max_tokens_per_ticket` and `budgeting.max_tokens_per_project`.
Booleans are `true`/`false`; limits are positive integers within canonical JSON
and SQLite bounds. Choices are project-wide, never Epic-scoped.

Echo/run `workflow.py operating set --instruction "REQUEST" --set
budgeting.accounting_enabled=true`. Valid changes print exact changes/digest,
store a protected proposal, exit 3 `PENDING_OWNER_CONFIRMATION`; settings stay
unchanged. Owner runs `workflow.py operating confirm --digest D` in their
terminal within 15 minutes, typing its first eight characters. The skill never
runs confirm; after owner-reported completion, run `operating show` for persistence.

Under lock, validate before
`EnrollmentRegistry.admit_owner_direction` consumes the observed direction. The
observation binds project/repository, accepted governance, prior settings,
exact changes, request ID and expiry. Journal/audit retain numeric owner ID,
login, digest and time, never tokens. Invalid mixed changes preserve settings and observation. Pinned
journal recovery requires the exact consumed confirmation from protected storage,
bound to project, governance, prior settings and complete changes. Unconfirmed
journals refuse without writes; confirmed interruption completes once; a crash after consumption
before journalling requires a new observation.

Confirm requires interactive stdin/stdout (Windows console handles, excluding
NUL, pipes and files); refuses token environment variables
and foreign `GH_HOST`. `gh auth status --hostname github.com --active` and
`gh api --hostname github.com --method GET user` verify a User's numeric ID
against reviewed `merge_gate.trusted_owner_ids`; empty refuses
`OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`. No auto-enrollment. Resolve `gh` once to an absolute native binary; refuse scripts and
worker-writable locations. Audit includes its path and SHA-256. Chat supplies no assertion. This proves the machine's stored GitHub login
and an interactive terminal. It does not defend against a process with the
owner's full rights: it can replace machine software and use its own terminal. No dispatch/merge authority follows.

## Ledger and enrollment

`RoutingLedger.model_runs` is canonical history; `BudgetLedger` adds enrollment.
Both check atomically. Legacy history stays
charged across days, restarts, policies and worktrees. Never evade history through database changes.
If that database has `budget_charges`, routing bridges
each identity into `model_runs` in one write transaction. Sealed archives retain UNKNOWN timestamps/usage; reopens verify digests and deduplicate charges. Conflicting identities refuse
rather than discard history. Separate databases require migration.

`EnrollmentRegistry` stores identity/owner observations.
Host enrolls AWF runs with project, ticket, run, dispatch, observed session and role. The controller and independent Codex agents,
including ULTRA agents, are excluded; repository, model, process ancestry or
worker-authored requests cannot establish enrollment. Protect state outside
worktrees; path checks supplement isolation.

Routed `RoutingLedger.reserve` uses trusted `budget_registry`/`budget_binding`; run ID equals ledger ID. `RoutingLedger.settle` retains
model/effort/context/independence checks. The budget-only facade cannot settle
routed runs or bypass checks. Operational hosts use `BudgetLedger.reserve/settle`.
Never relaunch replays.

State store accepts `budgeting`, `budget_ledger`, `budget_binding`.
Enabled usage goes to canonical facade; no second state-store reservation is
created. Legacy reservations remain intact. Enforcement refuses until history is
imported, never silently counting from zero; migration qualification owns its
identity/deduplication proof.

## Modes and recovery

OFF `BudgetLedger.reserve` returns before opening a database or consulting
identity/telemetry; OFF state-store methods create no reservation/event. Routing
can still persist run identity, incidents, ownership/draining history and
bounded recovery without usage telemetry.
`settle` needs security/result facts, but OFF requires no token/cost
telemetry, budget warnings or overrun quarantine. Historical budget-only overruns
do not block OFF/accounting-only work; security/context/review mismatches
quarantine. Existing recovery limits and authorization to raise them bind in all
modes.

`route_model reserve` without a ledger in OFF returns a proposal with
`admitted: false`, `dispatch_performed: false` and
`operational_admission_required: true`; it neither launches nor admits. Host
must apply independent security, permission, ownership, review, merge,
host-capacity and bounded-recovery controls. Operational ledger retains checks
without budget telemetry.

Accounting-only records attributable usage without quota refusal, warnings or
quarantine. Absent estimates are UNKNOWN; a sentinel is
never zero, and unknown actual usage remains null. Enabled enforcement refuses
when unknown history/estimates prevent a reliable total. Independent/unregistered runs remain UNKNOWN and outside AWF quotas.

Enforced admission atomically counts known usage and outstanding reservations at
ticket/lifetime scope. Runs retain admission-time settings/binding.
OFF applies only to subsequent work and never deletes/refunds old charges,
reservations, failures or unknowns; late settlements use the original mode.
Known overruns remain charged; reconciliation may close incident without refund.
Observations include only tools/retries attributable to that run; enroll retries.

## Versioned preservation

Recommend versioned 1.9.6 preservation, not in-place 1.9.5 backport. Installed
CMC Quant Foundry 1.9.5 remains unchanged/enabled. Owner may retain it or authorize qualified preservation with explicit budget disposition.
Do not edit integrity-managed bytes, pins or receipts. Keep settings
absent/legacy until adoption; never initialize fresh OFF settings over an upgrade.

Hosts validate settings, enroll sub-agents, share the ledger and retain controls OFF. Migration preserves history/
reservations before totals. Live integration requires qualification.
Release lane owns manifests/qualification.
