# AWF budgeting

AWF-242 is Tier 3 with FULL closure. Fresh generated operating configuration
contains accounting OFF and enforcement OFF, with dormant limits of exactly
4,000,000 tokens per ticket and 1,000,000,000 tokens for the project lifetime.
Setting a limit enables neither switch. Enforcement requires accounting;
disabling accounting also disables enforcement. Missing budgeting fields in
existing installations preserve the legacy engine and its actual reviewed
limits. They never imply permission to disable installed budgeting.

## Operating choices and authenticated host boundary

The project-wide `OPERATING_CONFIG.yaml` block is:

```json
"budgeting": {
  "accounting_enabled": false,
  "enforcement_enabled": false,
  "max_tokens_per_ticket": 4000000,
  "max_tokens_per_project": 1000000000
}
```

`operating show` and operating diagnostics display persisted effective settings.
The set paths are `budgeting.accounting_enabled`,
`budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket` and
`budgeting.max_tokens_per_project`. Booleans use `true` or `false`; limits must
be positive integers within canonical JSON and SQLite storage bounds. Budget
choices are project-wide and cannot be scoped to an Epic.

A trusted host calls `set_operating(..., budget_registry=registry)` or injects
that registry into `operating.main`. The ordinary standalone CLI cannot
authenticate an owner and refuses budget mutations without this host boundary.
A chat instruction, `source: user`, actor string or caller-authored observation
file is insufficient. No configuration field registers a trusted host.

Under the existing operating lock, the complete proposed change is validated
before `EnrollmentRegistry.admit_owner_direction` consumes an independently
observed owner direction. The observation binds immutable source identity,
authenticated actor, project, prior budget-settings hash and exact changes.
The resulting settings and observation enter the existing operating journal
and append-only change audit. Mixed invalid changes alter neither settings nor
consume an observation. Interrupted publication uses the existing pinned
journal recovery; recovery does not ask the host to replay the owner direction.
A crash after consumption and before journalling requires a new observation.

The host operator must supply the real authenticated owner/UI observation
source. Fixture hosts verify the protocol, not actual live Codex authentication.
These APIs grant no dispatch, merge or release authority.

## One usage ledger and explicit enrollment

`RoutingLedger.model_runs` is the canonical usage history. `BudgetLedger` is a
lazy enrollment facade over those same rows, not a second usage ledger. Both
interfaces check enabled ticket and lifetime-project totals under that database's
`BEGIN IMMEDIATE` transaction. Routing history, including legacy rows, remains
charged across days, restarts, policies and worktrees. Use one protected database
for the entire project. Never switch to a newly empty database to evade history.
If the stage-one helper's `budget_charges` table exists in that database, routing
initialization bridges each original identity into `model_runs` in one write
transaction. Original rows are retained and sealed against writes as archival
source bytes. Reopens verify the source digest, preserve UNKNOWN timestamps and
usage, and never duplicate a charge. Conflicting run identities refuse rather
than discarding either history. A separate old database still requires explicit
preservation migration into the selected canonical database.

`EnrollmentRegistry` stores operational identity and owner observations, not a
second usage total. The host explicitly enrolls each eligible AWF run with its
exact project, ticket, run, dispatch, observed session and role. Controller and
independent Codex agents, including ULTRA agents, are excluded. Repository,
model, process ancestry or a worker-authored request cannot establish enrollment.
Protect state outside all worker worktrees; path checks supplement host isolation.

For routed enrolled runs, call `RoutingLedger.reserve` with trusted
`budget_registry` and `budget_binding` keyword arguments. The binding's run ID
is the ledger run ID. Finish through `RoutingLedger.settle`, which retains model,
effort, context and review-independence checks. The budget-only facade cannot
settle a routed run and bypass those checks. A host using its own operational
run checks may use `BudgetLedger.reserve/settle` instead. Never launch twice
from an idempotent reservation replay.

The state store accepts explicit `budgeting`, `budget_ledger` and
`budget_binding` arguments. New enabled usage goes to the canonical facade;
no second state-store reservation is created. Legacy state-store reservations
remain intact. Enforcement refuses while such history needs preservation import;
it must not silently start counting from zero. Migration qualification owns
that import and its identity/deduplication proof.

## Mode and recovery semantics

OFF `BudgetLedger.reserve` returns before opening a database or consulting
identity/telemetry. OFF state-store budget methods create no reservation or
budget event. Routing can still persist operational run identity, incidents,
ownership/draining history and bounded recovery without usage telemetry.
Operational completion through `settle` needs security/result facts, but OFF
requires no token/cost telemetry, budget warnings or budget-overrun quarantine.
A historical budget-only overrun does not block OFF or accounting-only work;
security/context/review mismatches still quarantine. Existing incident recovery
limits and authorization to raise them remain binding in every mode.

`route_model reserve` without a ledger in OFF mode returns a route proposal,
`admitted: false`, `dispatch_performed: false` and
`operational_admission_required: true`. It neither launches nor grants admission.
The host must apply independent security, permission, ownership, review, merge,
host-capacity and bounded-recovery controls. With an operational ledger supplied,
the routing engine retains its operational checks without budget telemetry.

Accounting-only records attributable usage without quota refusal, budget warnings
or overrun quarantine. An absent estimate is explicitly UNKNOWN; a storage
sentinel is never measured zero. Unknown actual usage remains null. Enabled
enforcement refuses when unknown historical usage/estimates prevent a reliable
total. Independent/unregistered runs remain UNKNOWN attribution and do not enter
AWF quotas.

Enforced admission counts actual known usage and outstanding reservations at
ticket and lifetime-project scope atomically. Each run retains its admission-time
settings and binding. OFF applies to subsequent work and never deletes/refunds
old charges, running reservations, failures or unknowns. Late settlements use
the original mode. Known overruns remain charged; operator reconciliation can
close an incident without refunding it. Host usage observations must include
only tools/retries attributable to that enrolled run; enroll retries explicitly.

## Versioned preservation and remaining integration

Recommend versioned 1.9.6 adoption with preservation, without an in-place 1.9.5
backport. Installed CMC Quant Foundry 1.9.5 remains unchanged and budget-enabled.
The owner may retain it or authorize a qualified preservation upgrade with an
explicit budget disposition. Do not edit integrity-managed bytes, published
pins or receipts. Keep existing settings absent/legacy until explicit adoption;
never initialize fresh OFF settings over an upgraded installation.

Controller/reference-adapter owners must pass the validated operating budget
settings into lifecycle/state-store calls, explicitly enroll eligible dispatches,
use the same canonical ledger and retain independent operational controls when
OFF. Review-host owners must do the same for critic/specialist runs and exclude
the controller. Migration owners must preserve old routing and state-store
history and reservations before enabling the new totals. Actual live identity,
usage and enforcement capability remain separate qualification evidence.
Generated schemas/examples are regenerated by the lane; release manifests and
full candidate qualification remain with the orchestrator/release lane.
