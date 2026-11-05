# Balanced model routing

Version 1.9.6. [SPECIFICATION](../SPECIFICATION.md) defines authority. Router
proposes routes/records usage; host authenticates evidence, launches models
and enforces limits.

## Budget modes

Fresh settings default accounting/enforcement OFF, with dormant 4,000,000-ticket
and 1,000,000,000-lifetime-project token limits. Missing settings preserve legacy
policy. [Budgeting](39-BUDGETING.md) defines host enrollment, owner admission,
canonical usage history and preservation. Legacy daily/run/cost quotas apply only
to legacy admissions. Accounting-only runs record attributable usage without
quota warnings/refusal; enforcement uses atomic ticket/lifetime totals.
OFF needs no budget reservation or usage telemetry, while operational history
retains security, review independence, draining ownership and bounded recovery.
Budget-only historical overruns do not block OFF; security mismatches quarantine.
A no-ledger OFF result is only a proposal requiring independent host admission.

## Policy and escalation

Configure `execution.model_routing`; `defaults` prints settings.
New projects use one shared critic; upgrades preserve allocation.

| Role/condition | Model | Effort |
| --- | --- | --- |
| Controller | `gpt-6.1-sol` | high |
| Workers A-L | `gpt-6.1-sol` | high |
| Simple worker | `gpt-6-luna` | high |
| Shared independent critic/adversarial handler | `gpt-6.1-sol` | high |
| Specialist; high complexity, uncertainty or risk | `gpt-6.1-sol` | high |

Simple means low complexity/risk/uncertainty, strong verification and no risk
flags; Jira priority is insufficient. Preserve ticket/phase identities. Requests
include observed `stream`; `epic_id` requires host-observed `epic_risk_flags`.
Never infer risk.

Precedence is role default, simple/risk rules, stream override, matching Epic
override, agent override, then ticket override; mandatory risk/review floors apply
afterward. Root [operating configuration](29-OPERATING-CONFIGURATION.md) supplies
choices without changing governance. Only unchanged unpinned worker default
permits qualifying simple work; custom stream/Epic routes override it. `pinned:true`
blocks optional escalation, never floors. Allowlists intersect `role_allowed_models`
with `execution.roles.*.approved_model_ids`. Unsupported routes return
`unavailable`, without fallback. Review contexts must differ from workers;
unknown contexts checked at settlement.

Escalation occurs between runs from durable reasoning/implementation/validation
failure history. Model rank and effort remain nondecreasing, including after
reclassification. Each approved model must support the ceiling; conflict
with a ceiling or pin blocks. Defaults allow two escalations/ticket and three
reasoning failures per phase. Fresh Sol/high; simple Luna/high may
escalate within policy. Legacy catalogs remain; budgets unchanged.

`resolve-failure` records verified recovery before same-model retry, preserving
budgets, floors and pins.

Policy/host model/effort is a claim until observed. Entries record host ID,
software/version, `successful_probe` or
`recorded_refusal`, and `observed_at`. `execution.route_capabilities.max_age_days`
defaults to 30; missing, refused, unproven or stale routes produce warning-only
`route_models_observed` and never block installation.

## Reservation and settlement

`suggest` is read-only. Before dispatch, `reserve` admits a run to the protected ledger outside worker checkouts. Hosts enforce reservations. Effective budgets are the minimum routing/execution caps. Monetary caps require known reservation and actual costs; unknown is not zero.

### Token-only hosts

Token-only hosts may use null cost reservations/settlements only when every
effective monetary ceiling is null; token/run ceilings still apply. Any integer
cost cap requires known reservation and actual cost. New adoptions default to 2M
tokens/16 runs per ticket and 30M tokens/250 runs per project day; upgrades
preserve reviewed configuration until a governance PR. For cap *N*, `WARN`
starts at `ceil(.75N)` and `NEEDS_DISPOSITION` at `ceil(.90N)`; crossing
succeeds, only the cap refuses. `summary --ticket T` groups runs/tokens by role;
review-round caps remain independent.

Outstanding runs block duplicate admission. History survives policy changes; usage
counts on start/closure UTC days, with no timeout refunds.

`settle` records actual model/effort/context, usage/results once. Mismatch or
overrun quarantines the project and excludes positive evidence. Contexts must
match. Settlements retain reserved model and operating hash; they never substitute
current settings or combine policy cohorts.

### Settlement outcome shapes

Every role supplies model, effort and context. Legacy settlement also
requires nonnegative tokens and nullable cost; enabled accounting permits UNKNOWN
tokens, while OFF completion needs no usage telemetry. Every role supplies
booleans for success, validation, independent review and escaped defect. Failed
runs add `failure_kind`; successful runs omit it.

| Role | Required interpretation |
| --- | --- |
| `worker` | `validation_passed` reports its validation; independent review is not inferred. |
| `fix` | Same shape as worker, for a bounded amendment/fix run. |
| `critic` | Always `independent_review_passed: false`; record the verdict in `critic-review`. |
| `specialist` | Always `independent_review_passed: false`; record the verdict in `specialist-review`. |

`route_model.py outcome-template --role ROLE` emits shapes; replace
placeholders from host evidence. A review run cannot self-attest independence;
independence and verdict come from the separately bound review record.

## Operator reconciliation and evidence

`reconciliation` defaults disabled; enablement requires
`authorized_operator_ids`. `reconcile` records operator assertions about
authorization, termination and remediation for the project/run; host
authentication remains required. Fields/fixtures: [CLI tests](../tests/test_routing_cli.py);
validation: [implementation](../lib/agentic/model_routing.py).

`reconciliation.max_reconciled_incident_retries_per_ticket` defaults to 2.
After a third reconciled incident, admission stops; safe closure remains
permitted. Counts span policies, roles and phases; unknown hangs remain
non-reasoning failures.

Ledger initializes project retry ceiling at min(2, reviewed cap). Append-only
events record seed/reduction/increase with provenance. Reductions persist if
admission fails; one lock/savepoint rolls back partial reservations. It binds
`attempted_run_id` and fingerprints project, effective policy, request and
capabilities. Successful reservation uses that UUID; denial creates no
run/charge. Commit/OS failure proves no durability. Higher configuration returns
`RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION`. Migration preserves legacy
ceilings/provenance; safe closure is independent of admission capacity.

Only a trusted host may call `RoutingLedger.authorize_retry_limit` after
authenticating explicit human direction; the CLI does not expose it. Fields/tests
are in [retry authority tests](../tests/test_retry_authority.py). Timestamps
satisfy `approved_at <= now < expires_at <= approved_at + 24 hours`; keep the
original approval time. The helper authenticates neither direction nor timestamps;
retain immutable host evidence.

References are atomically consumed across projects in one database. Use stable
identities; NFC preserves case, new references reject whitespace/control characters,
and aliases remain undetectable. Historical references import unchanged. Exact
retries return saved results; fresh operations cannot reuse references/old shapes.
Copied databases have no global replay protection. The ceiling lasts until
reduced; revision/value checks reject stale changes and history exposes events/
authorizations.

Pause admissions, stage the policy, record approval, install it identically and
resume. An old-policy admission can lower a newly raised ceiling. Workers must
lack ledger writes/access to this operation. SQL guards authenticate neither
humans nor database owners.

Reconciliation closes one reservation/quarantine, preserves known charges/outcomes,
and charges an orphan's full reservation with usage unknown. Verified closure
ends future daily carry; ticket charges/other quarantines remain. Audit retains
state/references. Exact retries return the saved result; conflicting reuse fails.
`reconciliation-history` reads audit. Reconciled runs supply no evidence/refund;
typed claims do not authenticate operators.

`evidence` is shadow-only: 20 independently reviewed low-risk tickets, 95%
acceptance and no known escaped defects qualify a cohort for a reviewed experiment,
never automatic downgrades. `record-defect` appends discoveries. Fingerprints
include reconciliation defaults. Only exact legacy encodings omitting the default
cap or disabled/default block are equivalent. Stored hashes remain visible; rows
are never rewritten. Other policy changes remain separate cohorts. Equivalence
authenticates neither outcomes nor model quality.

## Commands

From release root, substitute reviewed paths/observations and create protected ledger directory

```text
python .agentic/scripts/route_model.py defaults
python .agentic/scripts/route_model.py outcome-template --role worker
python .agentic/scripts/route_model.py suggest --config PROJECT/.agentic/PROJECT_CONFIG.yaml --request request.json --capabilities observed-host.json
python .agentic/scripts/route_model.py reserve --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --request request.json --capabilities observed-host.json --ledger STATE/routing.sqlite
python .agentic/scripts/route_model.py settle --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --outcome outcome.json
python .agentic/scripts/route_model.py reconcile --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --observation operator-reconciliation.json
```

`--help` lists commands. Results/errors are JSON; blocked/unavailable/quarantined/
invalid returns exit 2. Exit 0 is not launch evidence. JSON uses standard-library
Python; YAML needs existing dependency.
