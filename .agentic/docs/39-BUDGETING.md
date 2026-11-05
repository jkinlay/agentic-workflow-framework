# AWF budgeting contract

AWF-242, Tier 3, FULL closure. This is an additive implementation contract.
The budget helpers have fixture-level verification; the existing routing CLI,
state-store reservations and controller still use the legacy admission path.
This document does not establish completed integration or authenticated live
Codex control.

## Settings and adoption

The project-wide `budgeting` block has four fields:

| Field | Fresh setup value | Meaning |
| --- | --- | --- |
| `accounting_enabled` | `false` | Record attributable AWF sub-agent usage when enabled. |
| `enforcement_enabled` | `false` | Enforce enabled token limits; requires accounting. |
| `max_tokens_per_ticket` | `4000000` | Dormant ticket token limit. |
| `max_tokens_per_project` | `1000000000` | Dormant lifetime project token limit, never a daily allowance. |

`agentic.budgeting.fresh_settings()` supplies these exact values.
`transition_settings(current, changes)` validates an atomic change without
mutating its inputs. A limit edit enables neither switch. Enabling enforcement
without accounting refuses. Disabling accounting also disables enforcement;
an explicitly contradictory change refuses as a whole. Boolean integers,
unknown fields, nulls, nonpositive limits and values outside signed 64-bit
storage refuse.

`resolve_settings(operating)` treats an absent block as a legacy-preservation
marker. It must not classify a missing upgrade field as permission to turn off
the installed budget engine. Only a trusted fresh-setup caller uses
`fresh=True`. An explicit malformed block always refuses. The legacy marker
cannot enter the new ledger or be edited as new settings; retain the installed
engine and actual reviewed limits until explicit versioned adoption.

Recommend **versioned 1.9.6 adoption with preservation, without an in-place 1.9.5
backport**. Installed CMC Quant Foundry 1.9.5 remains unchanged and budget-enabled.
The owner may keep it, or authorize a qualified preservation upgrade and choose
the new budget disposition. Do not edit integrity-managed installed bytes,
published pins or receipts. Upgrade must preserve every original run,
reservation identity, failure, known charge and unresolved charge. The new
ledger is currently for fresh state only: it has no legacy import operation.
Never switch an upgraded project to an empty ledger. Import and verify its
complete history before using the new engine. Rejected migration must leave
original state intact; migration execution and proof are separate work.

## Owner-direction admission

The UI route and owner identity source are pending owner selection. A chat
instruction, `source: user`, an actor string or a caller-authored observation
file cannot authenticate an owner. No budget-setting CLI command is claimed
available by this contract.

`EnrollmentRegistry.admit_owner_direction(project_id, current, changes)` calls
the infrastructure-installed host's `observe_owner_direction` method. The host
must independently authenticate the owner and current provider direction,
including its validity/freshness, and return only that observation. Its closed
record binds `observation_id`, `actor_id`, `project_id`, `prior_settings_hash`
and exact `changes`. The registry rejects mismatches and consumes immutable
observation identities durably once. Configuration, workers and CLI input must
never select, instantiate or supply the trusted host object. These Python
interfaces are a trust boundary, not an identity provider or a signature check.

The operating integration must hold its existing settings lock, validate the
current snapshot, call this admission boundary, and commit returned settings
through its existing journal/audit transaction. It must display persisted
configured and effective values after reopening. A failure after observation
consumption requires a fresh observation; never replay blindly. Do not create
a second settings-persistence mechanism. Operating integration, concurrent
settings edits and interrupted-write recovery remain unverified here.

## Enrollment and attribution

The protected `EnrollmentRegistry` records exact project, ticket, run, dispatch,
observed session and role bindings. Its host adapter's `observe_enrollment`
must independently observe the explicit AWF dispatch. The main controller
cannot enroll. Repository, process ancestry, model and effort establish no
enrollment. Independent agents, including ULTRA agents, remain outside AWF
quotas even when they share these properties with AWF workers.

Every retry receives its own explicit run enrollment. Missing or mismatched
session/run/dispatch identity returns UNKNOWN attribution; it does not enroll
or block independent activity. Identical enrollment is idempotent; rebinding
an existing run to another ticket/session refuses. Enrollments survive reopen.
They are operational identity and can exist while budget accounting is OFF.
The host must protect the registry outside every worker worktree and supply
its complete worktree inventory to the path checker. Filesystem checks do not
replace host isolation or permissions.

## New ledger behavior

`BudgetLedger` is lazy: an OFF `reserve` returns before opening any budget
database, consulting enrollment or asking for reservation/usage telemetry.
OFF new work produces no budget warnings, quotas, settlements or overrun
quarantine. This does not waive independent security, permission, ownership,
review, host-capacity, recovery or merge checks. Callers must preserve those
controls separately; bypassing the legacy ledger alone is unsafe.

With accounting enabled, only exact enrolled runs enter the ledger. The host
supplies trusted effective settings and reservations. Accounting-only mode
records usage without quota refusal or overrun quarantine. Enforced admission
atomically checks ticket and lifetime-project usage and outstanding
reservations under one SQLite write transaction. Totals span tickets, reopen,
settings changes and calendar boundaries; there is no daily partition or reset.
Use one canonical protected database across worktrees and restarts. A copied
or replaced database cannot establish the project's complete history.

Each reservation retains its complete admission-time settings and enrollment
binding. Changing settings never refunds, deletes or rewrites it. Late
settlement uses that original mode even after accounting is OFF. An identical
reservation replay returns `created: false`; it cannot authorize another launch.
Even a newly created budget reservation grants no dispatch authority by itself.
Hosts submit
one verified final cumulative usage observation for the enrolled run, including
only attributable tools/retries. Repeating the identical settlement is
idempotent; changed usage or reusing its observation identity for another run
refuses. The helper cannot itself collect or verify host telemetry.

Null actual usage remains UNKNOWN with its original reservation retained;
summary separates known tokens, outstanding reservations and unresolved
reserved tokens. Unknown usage prevents new enforced admissions, while OFF
and accounting-only work remain unaffected. Known enforced overruns stay
recorded and prevent further enforced admission. There is deliberately no
refund/reset/recovery operation in this helper; a reviewed preservation and
recovery integration is needed before production use. Original legacy
ledgers remain authoritative until their history is safely integrated.

## Integration still required

Wire the budgeting block through operating validation, set/show, locking,
audit and recovery after its current writer hands off the file. Integrate the
existing routing/state-store engines and lifecycle guards without discarding
their independent security/retry/recovery controls. Their current APIs still
require legacy budget reservations. Controller and host adapters must enroll
explicit dispatches and attribute observed usage before using the new ledger.
Do not independently double-charge both ledgers. Qualify real host enforcement
and the chosen owner identity/UI path before claiming FULL closure.

The central generator owners must update configuration/schema/example sources
and regenerate operating/project configuration, schemas, examples, workflow
and release inventories after integration. This lane does not regenerate them.
Existing routing/operating/lifecycle documentation must be reconciled with
completed behavior; this contract does not silently supersede shipped runtime.
