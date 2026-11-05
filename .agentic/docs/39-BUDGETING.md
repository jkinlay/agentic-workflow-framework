# AWF budgeting

AWF-242 is Tier 3 with FULL closure. Fresh configuration has accounting/
enforcement OFF, dormant limits exactly 4,000,000 tokens per ticket and
1,000,000,000 per project lifetime. A limit enables neither switch; enforcement
requires accounting, and disabling accounting disables enforcement. Missing
budgeting fields preserve the legacy engine/reviewed limits; they never authorize
disabling installed budgeting.

## Operating choices and authenticated host boundary

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

Trusted host calls `set_operating(..., budget_registry=registry)` or injects it
into `operating.main`. The standalone CLI cannot authenticate an owner and
refuses mutations without this boundary. Chat text, `source: user`, actor string
or caller-authored observation file is insufficient; no config field registers a
trusted host.

Under lock, validate change before
`EnrollmentRegistry.admit_owner_direction` consumes the observed direction. The
observation binds immutable source identity, authenticated actor, project, prior
settings hash and exact changes. Write to journal and append-only audit. Invalid
mixed changes alter neither settings nor consume observation. Pinned
journal recovery handles interruption without replay; a crash after consumption
before journalling requires a new observation.

Host supplies authenticated owner/UI source. Fixtures verify
protocol, not live Codex authentication. APIs grant no dispatch, merge/release
authority.

## One usage ledger and explicit enrollment

`RoutingLedger.model_runs` is canonical history. `BudgetLedger` enrolls rows, not
a second ledger. Both check ticket and
lifetime-project totals under that database's `BEGIN IMMEDIATE` transaction.
History, including legacy rows, remains charged across days, restarts,
policies and worktrees. Use one protected database; never switch databases
to evade history.
If that database has `budget_charges`, routing bridges
each identity into `model_runs` in one write transaction. Rows remain
sealed archival bytes. Reopens verify digest, preserve UNKNOWN
timestamps/usage and never duplicate charges. Conflicting identities refuse
rather than discard history. Separate old database needs explicit migration into
the chosen canonical database.

`EnrollmentRegistry` stores identity/owner observations, not totals.
Host enrolls eligible AWF runs with exact project, ticket, run,
dispatch, observed session and role. The controller and independent Codex agents,
including ULTRA agents, are excluded; repository, model, process ancestry or
worker-authored requests cannot establish enrollment. Protect state outside
worktrees; path checks supplement isolation.

For routed runs, call `RoutingLedger.reserve` with trusted
`budget_registry` and `budget_binding`; its binding run ID is the ledger ID.
Finish with `RoutingLedger.settle`, retaining model, effort, context and
review-independence checks. The budget-only facade cannot settle routed runs or
bypass checks. Host with operational checks may use `BudgetLedger.reserve/settle`.
Never launch twice from an idempotent replay.

State store accepts explicit `budgeting`, `budget_ledger`, `budget_binding`.
Enabled usage goes to canonical facade; no second state-store reservation is
created. Legacy reservations remain intact. Enforcement refuses until history is
imported, never silently counting from zero; migration qualification owns its
identity/deduplication proof.

## Mode and recovery semantics

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
quarantine. An absent estimate is explicitly UNKNOWN; a storage sentinel is
never zero, and unknown actual usage remains null. Enabled enforcement refuses
when unknown history/estimates prevent a reliable total. Independent/unregistered
runs remain UNKNOWN attribution; they do not enter AWF quotas.

Enforced admission atomically counts known usage and outstanding reservations at
ticket/lifetime scope. Runs retain admission-time settings/binding.
OFF applies only to subsequent work and never deletes/refunds old charges,
reservations, failures or unknowns; late settlements use the original mode.
Known overruns remain charged; reconciliation may close incident without refund.
Observations include only tools/retries attributable to that run; enroll retries.

## Versioned preservation and remaining integration

Recommend versioned 1.9.6 preservation, not in-place 1.9.5 backport. Installed
CMC Quant Foundry 1.9.5 remains unchanged/enabled. Owner may retain it or
authorize qualified preservation with explicit budget disposition.
Do not edit integrity-managed bytes, pins or receipts. Keep settings
absent/legacy until adoption; never initialize fresh OFF settings over an upgrade.

Controller/reference-adapter owners pass validated settings into lifecycle/state-
store calls, enroll dispatches, use the same ledger and retain independent
controls OFF. Review-host owners do likewise for critic/specialist runs, excluding
the controller. Migration owners preserve old routing/state-store history/
reservations before enabling totals. Live identity, usage and enforcement
capability remain separate qualification evidence.
Lane regenerates schemas/examples; orchestrator/release lane owns
manifests/full candidate qualification.
