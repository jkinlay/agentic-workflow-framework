# Balanced model routing

Version 1.8.0. [SPECIFICATION](../SPECIFICATION.md) defines authority. The router proposes routes and records usage; the host authenticates evidence, launches models and enforces limits.

## Policy and escalation

Configure `execution.model_routing`; `defaults` prints its complete schema-compatible settings.

| Role/condition | Model | Effort |
| --- | --- | --- |
| Controller | `gpt-5.6-sol` | medium |
| Worker | `gpt-5.6-terra` | medium |
| Simple worker | `gpt-5.6-luna` | low |
| Independent critic | `gpt-5.6-sol` | high |
| Specialist; high complexity, uncertainty or risk | `gpt-6-astra` | high |

Simple means low complexity/risk/uncertainty, strong verification and no risk flags. Classify actual work; Jira priority alone is insufficient. Preserve stable ticket/phase identities.

Precedence: role default, simple/risk rules, agent override, ticket override. Overrides cannot weaken risk/review floors. `pinned:true` holds an explicit user choice fixed. Effective allowlists intersect `role_allowed_models` with `execution.roles.*.approved_model_ids`. The observed host must support the selected model/effort; otherwise return `unavailable`, without fallback. Review requires a context distinct from the worker; an initially unknown context is checked at settlement.

Escalation occurs between runs, from durable reasoning/implementation/validation failure history. Both model rank and effort remain nondecreasing, including after reclassification. Each approved model must support the configured effort ceiling. If monotonicity conflicts with that ceiling or a pin, block. Defaults allow two escalations per ticket and three reasoning failures per phase. Within accepted limits, escalation needs no repeated user prompt.

Credentials, infrastructure, rate limits, cancellation and unknown outcomes require investigation. `resolve-failure` records verified recovery before a same-model retry; it preserves budgets, floors and pins.

## Reservation and settlement

`suggest` is read-only. Before dispatch, `reserve` atomically admits a run into the single protected project ledger outside all worker checkouts. Reservations are hard upper bounds the host must enforce. Effective budgets are the minimum of routing and existing execution caps. Monetary caps require known reservation and actual costs; unknown is not zero.

Outstanding runs block duplicate ticket/role/phase admission and remain charged across midnight. Ticket history survives policy changes. Usage counts on its start and closure UTC days; there are no timeout refunds.

`settle` records actual model/effort/context, usage and validation/review results once. Mismatch or overrun quarantines the project and excludes positive evidence. Supplied preallocated contexts must match. Models never change merely because configuration changed.

## Operator reconciliation and evidence

Optional `reconciliation` defaults to disabled; enablement requires `authorized_operator_ids`. `reconcile` records operator assertions about authorization, termination and remediation for the exact project/run; host authentication remains required. Fields/fixtures: [CLI tests](../tests/test_routing_cli.py); validation: [implementation](../lib/agentic/model_routing.py).

`reconciliation.max_reconciled_incident_retries_per_ticket` defaults to 2 when absent, without rewriting policy hashes. After a third reconciled incident, further ticket admission stops; safe closure remains permitted. Counts span policies, roles and phases. Unknown hangs remain non-reasoning failures. Cap increases require human direction.

Reconciliation closes one outstanding reservation or quarantine incident. It preserves known charges/outcomes and charges an orphan's full reservation while leaving actual usage unknown. Verified closure ends future daily carry; ticket charges remain. Other quarantines still block. The append-only audit retains prior state and authorization/evidence references. Exact operation-ID retries return the saved result; conflicting reuse fails. `reconciliation-history` reads the audit. Reconciled runs cannot supply positive evidence or receive later settlement refunds. Typed identities/booleans do not authenticate operators; host controls remain required.

`evidence` is shadow-only: 20 independently reviewed low-risk tickets, 95% acceptance and no known escaped defects qualify an observed cohort for a reviewed experiment, never automatic downgrades. `record-defect` appends discoveries. Historical cohorts remain queryable with their original policies; different policy hashes are never merged.

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
