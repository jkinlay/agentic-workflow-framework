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
New projects share one critic; upgrades preserve allocation.

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

Under `source: default`, unpinned routes follow policy only when the whole normalized operating content matches a retained generated baseline: roles, stream routes, simple_worker and stream count. Normalization ignores only key order, empty optional additions and `pinned: false`; catalogs never identify baselines. Missing or conflicting matches preserve stored choices, subject to floors. Simple-worker edits, redundant routes or reduced counts can prevent substitution; owners must set defaults explicitly.

If routing defaults and operating routes were both edited and no baseline matches, simple work uses the stored ordinary route when it differs from the policy worker default.

Precedence: role default, simple/risk rules, stream, matching Epic, agent, then ticket overrides; mandatory risk/review floors follow. Root [operating configuration](29-OPERATING-CONFIGURATION.md) supplies choices within governance. Unchanged unpinned worker defaults permit qualifying simple work; custom stream/Epic routes override them. `pinned:true` blocks optional escalation, never floors. Allowlists intersect `role_allowed_models` with `execution.roles.*.approved_model_ids`. Unsupported routes return `unavailable`, without fallback. Review contexts differ from workers; unknowns are checked at settlement.

Escalation occurs between runs, from durable reasoning/implementation/validation failure history. Both model rank and effort remain nondecreasing, including after reclassification. Each approved model must support the configured effort ceiling. If monotonicity conflicts with that ceiling or a pin, block. Defaults allow two escalations per ticket and three reasoning failures per phase. Fresh defaults use Sol/high; simple Luna/high work may escalate within approved policy. Legacy catalogs remain valid; budgets are unchanged.

`resolve-failure` records verified recovery before same-model retry, preserving
budgets, floors and pins.

Policy/host model capabilities require current observations: host ID, software/version, `successful_probe` or `recorded_refusal`, and `observed_at`. `execution.route_capabilities.max_age_days` defaults to 30; missing/refused/unproven/stale routes produce warning-only `route_models_observed` preflight results, never installation blockers.

## Reservation and settlement

`suggest` is read-only. Before dispatch, `reserve` admits a run to the protected ledger outside worker checkouts. Hosts enforce reservations. Effective budgets are the minimum routing/execution caps. Monetary caps require known reservation and actual costs; unknown is not zero.

### Token-only hosts

Token-only hosts permit null reservation/settlement costs only with all effective
monetary ceilings null; token/run ceilings apply. Integer caps require known
reserved/actual costs. New adoptions: 2M tokens/16 runs/ticket; 30M tokens/250
runs/project-day. Upgrades retain reviewed configuration until governance PR.
For cap *N*: `WARN` at `ceil(.75N)`, `NEEDS_DISPOSITION` at `ceil(.90N)`;
crossing succeeds; only caps refuse. `summary --ticket T` groups runs/tokens
by role; review-round caps remain independent.

Outstanding runs block duplicate admission. History survives policy changes; usage
counts on start/closure UTC days, with no timeout refunds.

`settle` records actual model/effort/context, usage/results once. Mismatch or
overrun quarantines the project and excludes positive evidence. Contexts must
match. Settlements retain reserved model and operating hash; they never substitute
current settings or combine policy cohorts.

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

Ledger seeds retry ceilings at min(2, reviewed cap). Append-only seed/reduction/increase
events retain provenance. Reductions survive denied admission; one lock/savepoint
rolls back partial reservations, binding `attempted_run_id` and project/policy/request/
capabilities fingerprints. Reservation uses that UUID; denial creates no run/charge.
Commit/OS failure proves no durability. Higher configuration returns
`RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION`. Migration preserves legacy ceilings/
provenance; safe closure is independent of capacity.

Only trusted hosts may call `RoutingLedger.authorize_retry_limit` after authenticating explicit human direction; no CLI exposes it. Fields/tests: [retry authority tests](../tests/test_retry_authority.py). Require `approved_at <= now < expires_at <= approved_at + 24 hours`; preserve original approval time. The helper authenticates neither direction nor timestamps: retain immutable host evidence.

References are atomically consumed across projects/database. Stable identities use
case-preserving NFC; new references reject whitespace/control characters; aliases
remain undetectable. Historical references import unchanged. Exact retries return
saved results; fresh operations cannot reuse references/old shapes. Copied databases
lack global replay protection. Ceilings persist until reduced; revision/value
checks reject stale changes; history exposes events/authorizations.

Pause admissions, stage policy, record approval, install identically, resume.
Old-policy admission can lower raised ceilings. Workers must lack ledger writes/
operation access. SQL guards authenticate neither humans nor database owners.

Reconciliation closes one reservation/quarantine, preserves known charges/outcomes,
and charges an orphan's full reservation with usage unknown. Verified closure
ends future daily carry; ticket charges/other quarantines remain. Audit retains
state/references. Exact retries return the saved result; conflicting reuse fails.
`reconciliation-history` reads audit. Reconciled runs supply no evidence/refund;
typed claims do not authenticate operators.

`evidence` is shadow-only: 20 independently reviewed low-risk tickets, 95%
acceptance, no known escaped defects qualify reviewed experiments, never automatic
downgrades. `record-defect` appends discoveries. Fingerprints include reconciliation
defaults; only exact legacy encodings omitting default cap or disabled/default
block are equivalent. Preserve stored hashes/rows; other policies remain separate
cohorts. Equivalence authenticates neither outcomes nor quality.

## Commands

From release root, substitute reviewed paths/observations; create protected ledger directory

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
