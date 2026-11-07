# Balanced model routing

Version 1.9.6. [SPECIFICATION](../SPECIFICATION.md) defines authority. The router proposes routes/records usage; hosts authenticate evidence, launch models and enforce limits.

## Budget modes

Fresh settings default accounting/enforcement OFF; dormant limits are 4,000,000
tokens/ticket and 1,000,000,000/lifetime project. Missing settings preserve legacy
policy. [Budgeting](39-BUDGETING.md) defines enrollment, owner admission, canonical
history and preservation. Legacy daily/run/cost quotas apply only to legacy
admissions. Accounting-only records attributable usage without quota warnings or
refusal; enforcement uses atomic ticket/lifetime totals. OFF requires neither
budget reservations nor usage telemetry. Operational history preserves security,
review independence, draining ownership and bounded recovery. Historical
budget overruns do not block OFF; security mismatches quarantine. A no-ledger OFF
result requires independent host admission.

## Policy and escalation

Configure `execution.model_routing`; `defaults` prints settings. New projects share one critic; upgrades preserve reviewer allocation.

| Role/condition | Model | Effort |
| --- | --- | --- |
| Controller | `gpt-6.1-sol` | high |
| Workers A-L | `gpt-6.1-sol` | high |
| Simple worker | `gpt-6-luna` | high |
| Shared independent critic/adversarial handler | `gpt-6.1-sol` | high |
| Specialist; high complexity, uncertainty or risk | `gpt-6.1-sol` | high |

Simple means low complexity/risk/uncertainty, strong verification and no risk flags; Jira priority is insufficient. Preserve ticket/phase identities. Requests include observed `stream`; an `epic_id` requires host-observed `epic_risk_flags`. Never infer risk.

Under `source: default`, unpinned routes follow policy only when normalized roles, stream routes, simple_worker and stream count match a retained generated baseline. Normalization ignores only key order, empty optional additions and `pinned: false`; catalogs never identify baselines. Missing/conflicting matches preserve stored choices within floors. Edited simple workers, redundant routes or reduced counts may require explicit owner defaults.

When edited routing defaults/operating routes match no baseline, simple work uses the stored ordinary route if different from policy worker defaults.

Precedence: role default, simple/risk rules, stream, matching Epic, agent, then ticket overrides; mandatory risk/review floors follow. Root [operating configuration](29-OPERATING-CONFIGURATION.md) supplies choices within governance. Unchanged unpinned worker defaults permit qualifying simple work; custom stream/Epic routes override them. `pinned:true` blocks optional escalation, never floors. Allowlists intersect `role_allowed_models` with `execution.roles.*.approved_model_ids`. Unsupported routes return `unavailable`, without fallback. Review contexts differ from workers; unknowns are checked at settlement.

Escalation follows durable reasoning/implementation/validation failures between runs. Model rank and effort never decrease, including after reclassification. Approved models must support the effort ceiling; conflicts with monotonicity or pins block. Defaults: two escalations/ticket, three reasoning failures/phase; Sol/high, simple Luna/high, within approved policy. Legacy catalogs and budgets remain.

`resolve-failure` records verified recovery before a same-model retry and preserves budgets, floors and pins.

Observe host ID, software/version, `successful_probe`/`recorded_refusal` and `observed_at`. `execution.route_capabilities.max_age_days` defaults to 30. Missing/refused/unproven/stale routes produce warning-only `route_models_observed` preflight results; installation proceeds.

## Reservation and settlement

`suggest` is read-only. Before dispatch, `reserve` admits a run to the protected ledger outside worker checkouts. Hosts enforce reservations. Effective budgets are the minimum routing/execution caps. Monetary caps require known reservation and actual costs; unknown is not zero.

### Token-only hosts

Token-only hosts may use null cost reservations and settlements only when every effective monetary ceiling is null; token/run ceilings still apply. Any integer cost cap requires known reservation and actual cost. Legacy admissions default to 2M tokens/16 runs per ticket and 30M tokens/250 runs per project day; upgrades preserve reviewed configuration until a governance PR. For cap *N*, `WARN` starts at `ceil(.75N)` and `NEEDS_DISPOSITION` at `ceil(.90N)`; crossing succeeds, and only the cap refuses. `summary --ticket T` groups runs and tokens by role. Review-round caps remain independent.

Outstanding runs block duplicate admission. History survives policy changes; usage counts on start/closure UTC days, with no timeout refunds.

`settle` records actual model/effort/context, usage and results once. Mismatch or overrun quarantines the project and excludes positive evidence. Contexts must match. Settlements retain reserved model and operating hash; they never substitute current settings or combine policy cohorts.

### Settlement outcome shapes

All roles supply model/effort/context and success/validation/independent-review/escaped-defect
booleans. Legacy settlement requires nonnegative tokens/nullable cost; enabled
accounting permits UNKNOWN tokens; OFF completion requires no telemetry. Failed
runs add `failure_kind`; successful runs omit it.

| Role | Required interpretation |
| --- | --- |
| `worker` | `validation_passed` reports its validation; independent review is not inferred. |
| `fix` | Same shape as worker, for a bounded amendment/fix run. |
| `critic` | Always `independent_review_passed: false`; record the verdict in `critic-review`. |
| `specialist` | Always `independent_review_passed: false`; record the verdict in `specialist-review`. |

`route_model.py outcome-template --role ROLE` emits placeholders for host observations. Reviews cannot self-attest independence; independence/verdict require separately bound review records.

## Operator reconciliation and evidence

`reconciliation` defaults to disabled; enablement requires `authorized_operator_ids`. `reconcile` records authorization/termination/remediation assertions for the exact project/run; hosts authenticate them. Fields/fixtures: [CLI tests](../tests/test_routing_cli.py); validation: [implementation](../lib/agentic/model_routing.py).

`reconciliation.max_reconciled_incident_retries_per_ticket` defaults to 2. A third incident stops ticket admission, preserving safe closure. Counts span policies/roles/phases. Unknown hangs remain non-reasoning failures.

Ledger seeds the retry ceiling at min(2, reviewed cap), retaining append-only seed/reduction/increase provenance. Reductions survive admission denial; one lock/savepoint rolls back partial reservations. Bind `attempted_run_id` and project/policy/request/capabilities fingerprint. Successful reservations use that UUID; denial creates no run/charge. Commit/OS failure proves no durability. Higher configuration returns `RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION`. Migration preserves ceilings/provenance; admission capacity never blocks safe closure.

Only trusted hosts call `RoutingLedger.authorize_retry_limit` after authenticating human direction; no CLI exposes it. Fields/tests: [retry authority tests](../tests/test_retry_authority.py). Require `approved_at <= now < expires_at <= approved_at + 24 hours`; preserve original approval time. Retain immutable host evidence; the helper authenticates neither direction nor timestamps.

References consume atomically across database projects. Stable NFC identities preserve case; new references reject whitespace/controls; aliases remain undetectable. Historical references import unchanged. Exact retries return saved results; fresh operations cannot reuse references/old shapes. Database copies lack global replay protection. Ceilings persist until reduced; revisions reject stale changes; history retains events/authorizations.

Pause admissions, stage the proposed policy, record approval, install the identical policy and resume. An old-policy admission can lower a newly raised ceiling. Workers must lack ledger writes and access to this operation. SQL guards do not authenticate humans or protect against database owners.

Reconciliation closes one reservation/quarantine, preserves known charges/outcomes, and charges an orphan's full reservation with actual usage unknown. Verified closure ends future daily carry; ticket charges and other quarantines remain. Audit retains prior state and references. Exact operation retries return the saved result; conflicting reuse fails. `reconciliation-history` reads the audit. Reconciled runs supply no positive evidence/refund. Typed claims do not authenticate operators.

`evidence` is shadow-only. Reviewed experiments require 20 independently reviewed low-risk tickets, 95% acceptance and no known escaped defects; never downgrade automatically. `record-defect` appends discoveries. Fingerprints include reconciliation defaults. Only exact legacy encodings omitting default cap or disabled/default block are equivalent. Preserve hashes/rows; other policies remain separate cohorts. Equivalence authenticates neither outcomes nor quality.

## Commands

From the release root, substitute reviewed paths/observations and create the protected ledger directory:

```text
python .agentic/scripts/route_model.py defaults
python .agentic/scripts/route_model.py outcome-template --role worker
python .agentic/scripts/route_model.py suggest --config PROJECT/.agentic/PROJECT_CONFIG.yaml --request request.json --capabilities observed-host.json
python .agentic/scripts/route_model.py reserve --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --request request.json --capabilities observed-host.json --ledger STATE/routing.sqlite
python .agentic/scripts/route_model.py settle --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --outcome outcome.json
python .agentic/scripts/route_model.py reconcile --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --observation operator-reconciliation.json
```

`--help` lists other commands. Results/errors are JSON; blocked/unavailable/quarantined/invalid returns exit 2. Exit 0 is not launch evidence. JSON inputs use standard-library Python; YAML needs the existing YAML dependency.
