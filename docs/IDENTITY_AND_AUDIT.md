# Identity and Audit Contract

## Principals

- Human owners approve scope, exceptions, merge, release, and promotion using
  personal human identities.
- Developer agents write through a dedicated GitHub App or service identity,
  never a human personal token.
- Required reviewers use a distinct provider or reviewer App identity.
- Deterministic publishers use a dedicated App distinct from both developer and
  human identities.

One organization-managed App may serve multiple repositories inside one
confidentiality domain when installations and tokens remain repository-scoped.
A separate App per project is optional, not a universal requirement.

## Attribution

Every agent commit and external write records the task/execution identifier.
Commits use a `Task-Id` trailer when the source-control platform supports it.
Pull requests link the approved task contract, head SHA, evidence, reviewer
identity, and human decision.

## Retention

Each project sets `audit_retention_days` in protected policy according to its
legal and operational obligations. Bootstrap fails if no period and durable
storage location are named. Retain authorization records, external writes,
review reports, CI/evidence links, bypass events, and incident records; raw
private model/tool logs are not copied into AWF.

## Permission evidence

Record effective permissions, App installation scope, token lifetime, relevant
workflow/run URL, and last verification date. Re-check after App, workflow,
provider, or ruleset changes and on the project's approved cadence.
