# Balanced model routing

Version 1.9.3. [SPECIFICATION](../SPECIFICATION.md) defines authority. The router proposes routes and records usage; the host authenticates evidence, launches models and enforces limits.

## Policy and escalation

Configure `execution.model_routing`; `defaults` prints its complete schema-compatible settings.

| Role/condition | Model | Effort |
| --- | --- | --- |
| Controller | `gpt-5.6-sol` | medium |
| Worker | `gpt-5.6-terra` | medium |
| Simple worker | `gpt-5.6-luna` | low |
| Independent critic | `gpt-5.6-sol` | high |
| Specialist; high complexity, uncertainty or risk | `gpt-5.6-sol` | high |

Simple means low complexity/risk/uncertainty, strong verification and no risk flags; Jira priority is insufficient. Preserve ticket/phase identities. Requests include observed `stream`; an `epic_id` also requires host-observed `epic_risk_flags`, explicitly empty when none. Never infer risk context.

Precedence: role default, simple/risk rules, stream override, matching Epic-scoped override, agent override, ticket override; mandatory risk/review floors apply afterward. Root [operating configuration](29-OPERATING-CONFIGURATION.md) supplies stream/role choices without changing governance. Only the unchanged unpinned worker default permits qualifying simple work; custom stream/Epic routes override it. `pinned:true` blocks optional escalation, never floors. Allowlists intersect `role_allowed_models` with `execution.roles.*.approved_model_ids`. The host must support the route; otherwise return `unavailable`, without fallback. Review contexts must differ from workers; initially unknown contexts are checked at settlement.

Escalation occurs between runs, from durable reasoning/implementation/validation failure history. Both model rank and effort remain nondecreasing, including after reclassification. Each approved model must support the configured effort ceiling. If monotonicity conflicts with that ceiling or a pin, block. Defaults allow two escalations per ticket and three reasoning failures per phase. Within accepted limits, escalation needs no repeated user prompt.

Credentials, infrastructure, rate limits, cancellation and unknown outcomes require investigation. `resolve-failure` records verified recovery before a same-model retry; it preserves budgets, floors and pins.

A model/effort listed by policy or a host is a claim until a current observation proves it. Observation entries record host ID, host software/version, `successful_probe` or `recorded_refusal`, and `observed_at`. `execution.route_capabilities.max_age_days` defaults to 30; missing, refused, unproven or stale configured routes produce the warning-only `route_models_observed` preflight row and never block installation.

## Reservation and settlement

`suggest` is read-only. Before dispatch, `reserve` admits a run to the protected ledger outside worker checkouts. Hosts enforce reservations. Effective budgets are the minimum routing/execution caps. Monetary caps require known reservation and actual costs; unknown is not zero.

### Token-only hosts

When every effective monetary ceiling is `null`, a host without verified dollar accounting may reserve and settle with `reservation_cost_microusd: null` and `actual_cost_microusd: null`; token and run ceilings remain mandatory. If any effective monetary ceiling is an integer, the existing hard cost reservation and actual-cost requirements still apply. New adoptions default to 2,000,000 tokens and 12 runs per ticket, plus 30,000,000 tokens and 250 runs per project day; upgrades preserve reviewed project configuration until an owner opts in through a governance PR.

Outstanding runs block duplicate ticket/role/phase admission and remain charged across midnight. Ticket history survives policy changes. Usage counts on its start and closure UTC days; there are no timeout refunds.

`settle` records actual model/effort/context, usage and results once. Mismatch or overrun quarantines the project and excludes positive evidence. Contexts must match. A reservation retains its model and `operating_hash`, separately from `policy_hash`; settlement never substitutes current settings. Cohorts stay separate and absent historical hashes stay unobserved.

### Settlement outcome shapes

Every role supplies actual model, effort, context, nonnegative tokens, nullable cost, and booleans for success, validation, independent review and escaped defect. Failed runs add `failure_kind`; successful runs omit it.

| Role | Required interpretation |
| --- | --- |
| `worker` | `validation_passed` reports its validation; independent review is not inferred. |
| `fix` | Same shape as worker, for a bounded amendment/fix run. |
| `critic` | Always `independent_review_passed: false`; record the verdict in `critic-review`. |
| `specialist` | Always `independent_review_passed: false`; record the verdict in `specialist-review`. |

`route_model.py outcome-template --role ROLE` emits each shape. Replace its observed-value placeholders from host evidence. A review run cannot self-attest independence; independence and verdict come from the separately bound review record.

## Operator reconciliation and evidence

Optional `reconciliation` defaults to disabled; enablement requires `authorized_operator_ids`. `reconcile` records operator assertions about authorization, termination and remediation for the exact project/run; host authentication remains required. Fields/fixtures: [CLI tests](../tests/test_routing_cli.py); validation: [implementation](../lib/agentic/model_routing.py).

`reconciliation.max_reconciled_incident_retries_per_ticket` defaults to 2 when absent. After a third reconciled incident, further ticket admission stops; safe closure remains permitted. Counts span policies, roles and phases. Unknown hangs remain non-reasoning failures.

The ledger initializes a project retry ceiling to the lower of 2 and the reviewed cap. Append-only events record seeds/reductions/increases with provenance. A reduction persists even if admission then fails; one lock/savepoint rolls back partial reservation work. It binds `attempted_run_id` and a fingerprint of project, effective policy, request and capabilities. Successful reservation uses that UUID; denial creates no run/charge. Commit/OS failure proves no durability. Higher configuration returns `RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION`. Migration preserves legacy ceilings/provenance; safe closure is independent of admission capacity.

Only a trusted host may call `RoutingLedger.authorize_retry_limit` after authenticating explicit human direction; the CLI does not expose it. Required fields and tests are in [retry authority tests](../tests/test_retry_authority.py). Timestamps satisfy `approved_at <= now < expires_at <= approved_at + 24 hours`; keep the original approval time. The helper authenticates neither direction nor timestamps, so retain immutable host evidence.

References are consumed atomically across all projects in one database. Use stable identities; NFC preserves case, new references reject whitespace/control characters, and aliases remain undetectable. Historical references import unchanged. Exact retries return their saved result; fresh operations cannot reuse references/old shapes. Copied databases have no global replay protection. The ceiling lasts until reduced; revision/value checks reject stale changes and history exposes events/authorizations.

Pause admissions, stage the proposed policy, record approval, install the identical policy and resume. An old-policy admission can lower a newly raised ceiling. Workers must lack ledger writes and access to this operation. SQL guards do not authenticate humans or protect against database owners.

Reconciliation closes one reservation/quarantine, preserves known charges/outcomes, and charges an orphan's full reservation with actual usage unknown. Verified closure ends future daily carry; ticket charges and other quarantines remain. Audit retains prior state and references. Exact operation retries return the saved result; conflicting reuse fails. `reconciliation-history` reads the audit. Reconciled runs supply no positive evidence/refund. Typed claims do not authenticate operators.

`evidence` is shadow-only: 20 independently reviewed low-risk tickets, 95% acceptance and no known escaped defects qualify a cohort for a reviewed experiment, never automatic downgrades. `record-defect` appends discoveries. Fingerprints include effective reconciliation defaults. Only exact legacy encodings omitting the default cap or disabled/default block are equivalent. Stored hashes remain visible; rows are never rewritten. Other policy changes remain separate cohorts. Equivalence authenticates neither outcomes nor model quality.

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
