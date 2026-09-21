# Balanced model routing

Version 1.9.1. [SPECIFICATION](../SPECIFICATION.md) defines authority. The router proposes routes and records usage; the host authenticates evidence, launches models and enforces limits.

## Policy and escalation

Configure `execution.model_routing`; `defaults` prints its complete schema-compatible settings.

| Role/condition | Model | Effort |
| --- | --- | --- |
| Controller | `gpt-5.6-sol` | medium |
| Worker | `gpt-5.6-terra` | medium |
| Simple worker | `gpt-5.6-luna` | low |
| Independent critic | `gpt-5.6-sol` | high |
| Specialist; high complexity, uncertainty or risk | `gpt-6-astra` | high |

Simple means low complexity/risk/uncertainty, strong verification and no risk flags. Classify actual work; Jira priority alone is insufficient. Preserve stable ticket/phase identities. Requests include observed `stream`; when `epic_id` is supplied, include host-observed `epic_risk_flags` (explicitly empty when none). Never infer missing risk context.

Precedence: role default, simple/risk rules, stream override, matching Epic-scoped override, agent override, ticket override; mandatory risk/review floors apply afterward. Root [operating configuration](29-OPERATING-CONFIGURATION.md) supplies stream/role choices without changing governance. Only the unchanged unpinned worker default permits qualifying simple work; custom stream/Epic routes override it. `pinned:true` blocks optional escalation, never floors. Allowlists intersect `role_allowed_models` with `execution.roles.*.approved_model_ids`. The host must support the route; otherwise return `unavailable`, without fallback. Review contexts must differ from workers; initially unknown contexts are checked at settlement.

Escalation occurs between runs, from durable reasoning/implementation/validation failure history. Both model rank and effort remain nondecreasing, including after reclassification. Each approved model must support the configured effort ceiling. If monotonicity conflicts with that ceiling or a pin, block. Defaults allow two escalations per ticket and three reasoning failures per phase. Within accepted limits, escalation needs no repeated user prompt.

Credentials, infrastructure, rate limits, cancellation and unknown outcomes require investigation. `resolve-failure` records verified recovery before a same-model retry; it preserves budgets, floors and pins.

## Reservation and settlement

`suggest` is read-only. Before dispatch, `reserve` atomically admits a run into the single protected project ledger outside all worker checkouts. Reservations are hard upper bounds the host must enforce. Effective budgets are the minimum of routing and existing execution caps. Monetary caps require known reservation and actual costs; unknown is not zero.

Outstanding runs block duplicate ticket/role/phase admission and remain charged across midnight. Ticket history survives policy changes. Usage counts on its start and closure UTC days; there are no timeout refunds.

`settle` records actual model/effort/context, usage and validation/review results once. Mismatch or overrun quarantines the project and excludes positive evidence. Supplied contexts must match. Running models never change because operating choices changed. Dispatch/reservation binds `operating_hash` separately from `policy_hash`; settlement inherits the reservation's hash, never current settings. Cohorts remain separated; historical absent hashes stay unobserved.

## Operator reconciliation and evidence

Optional `reconciliation` defaults to disabled; enablement requires `authorized_operator_ids`. `reconcile` records operator assertions about authorization, termination and remediation for the exact project/run; host authentication remains required. Fields/fixtures: [CLI tests](../tests/test_routing_cli.py); validation: [implementation](../lib/agentic/model_routing.py).

`reconciliation.max_reconciled_incident_retries_per_ticket` defaults to 2 when absent. After a third reconciled incident, further ticket admission stops; safe closure remains permitted. Counts span policies, roles and phases. Unknown hangs remain non-reasoning failures.

The host-controlled ledger initializes a project retry ceiling to the lower of 2 and the reviewed configured cap. Append-only events record seeds, reductions and increases with project, revision, prior/new values and provenance. A validated reduction persists even when subsequent admission fails for outstanding work, quarantine, budgets or missing cost. One write lock and an admission savepoint preserve that observation while rolling back partial reservation work. Reductions bind `attempted_run_id` and a fingerprint of project, effective policy hash, full request and capabilities. Successful reservation uses the same run UUID; denial creates no run or charge. Commit/OS failures do not imply durable evidence. Higher configuration returns `RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION`. Migration preserves legacy ceilings and unknown historical provenance. Safe closure remains independent of admission capacity.

Only a trusted host may call `RoutingLedger.authorize_retry_limit(project_id, proposed_policy, observation)` after authenticating explicit human direction. Ordinary routing CLI commands do not expose it. Required fields are `operation_id`, `expected_limit`, `expected_revision`, `new_limit`, `policy_sha256`, `operator_id`, `authorization_ref`, `evidence_ref`, `reason`, `approved_at` and `expires_at`; see [tests](../tests/test_retry_authority.py). Aware timestamps must satisfy `approved_at <= now < expires_at <= approved_at + 24 hours`. Supply the original source approval time, never reset it to submission time. The helper authenticates neither human direction nor source timestamps; retain immutable approval evidence at the trusted host.

Both references are consumed atomically with the increase across both fields and all projects in the same canonical database. Supply stable canonical identities; NFC preserves case, while new references reject surrounding whitespace/control characters. The helper cannot detect arbitrary URL or source aliases. Historical references are imported without modifying or rejecting duplicate old grants. Exact recorded requests, including old shapes, return their historical result after expiry without reapplication; fresh operations cannot reuse consumed references or old request shapes. A copied/new database does not provide global replay protection. The ceiling lasts until reduced. Revision/value comparison rejects stale changes; `retry_limit_history` exposes events and authorizations.

Pause admissions, stage the proposed policy, record approval, install the identical policy and resume. An old-policy admission can lower a newly raised ceiling. Workers must lack ledger writes and access to this operation. SQL guards do not authenticate humans or protect against database owners.

Reconciliation closes one outstanding reservation or quarantine incident. It preserves known charges/outcomes and charges an orphan's full reservation while leaving actual usage unknown. Verified closure ends future daily carry; ticket charges remain. Other quarantines still block. The append-only audit retains prior state and authorization/evidence references. Exact operation-ID retries return the saved result; conflicting reuse fails. `reconciliation-history` reads the audit. Reconciled runs cannot supply positive evidence or receive later settlement refunds. Typed identities/booleans do not authenticate operators; host controls remain required.

`evidence` is shadow-only: 20 independently reviewed low-risk tickets, 95% acceptance and no known escaped defects qualify an observed cohort for a reviewed experiment, never automatic downgrades. `record-defect` appends discoveries. New fingerprints include effective reconciliation defaults. Evidence accepts only exact legacy encodings that omit the default cap, or omit the completely disabled/default reconciliation block. It reports included stored hashes; historical rows are never rewritten. Different non-default caps, operator lists or any other policy changes remain separate cohorts. This narrow representation equivalence does not authenticate past outcomes or establish runtime/model quality.

## Commands

From the release root, substitute reviewed paths/observations and create the protected ledger directory:

```text
python .agentic/scripts/route_model.py defaults
python .agentic/scripts/route_model.py suggest --config PROJECT/.agentic/PROJECT_CONFIG.yaml --request request.json --capabilities observed-host.json
python .agentic/scripts/route_model.py reserve --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --request request.json --capabilities observed-host.json --ledger STATE/routing.sqlite
python .agentic/scripts/route_model.py settle --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --outcome outcome.json
python .agentic/scripts/route_model.py reconcile --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --observation operator-reconciliation.json
```

`--help` lists other commands. Results/errors are JSON; blocked/unavailable/quarantined/invalid returns exit 2. Exit 0 is not launch evidence. JSON inputs use standard-library Python; YAML needs the existing YAML dependency.
