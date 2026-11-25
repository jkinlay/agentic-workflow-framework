# Shared owner account candidate boundary

AWF-307 O1b is an unmerged candidate. The GitHub setting is reserved and must
stay null until its bindings ship. The global separation refusal remains.
Separate owner and automation accounts are recommended wherever possible.
Agents never propose an opt-out without explicit human direction.

Only these nine modules have accepted-default-policy bindings for the named
shared GitHub account: `streams`, `intake_assignment`, `provider_identity`,
`policy`, `authorization`, `gates`, `review_completion`, `integration_queue`,
`continuous_controller`. Acceptance requires the configured expected actor to
be the named trusted owner, explicit trusted host policy-root context and
unchanged accepted merged default-branch bytes. Supplied, working-tree and
feature-branch policy cannot enable acceptance or authorize their own merge.
Retained evidence binds the accepted policy digest, shared-account annotation
and warning. Request, expiry, replay, candidate, provider, reviewer-floor and
tier protections remain. Signed provider bodies remain unchanged.

Shared records prove which account signed, not which person controlled it.
Agents must never sign human decisions through the shared account. Human merge
and repository/adapter qualification boundaries remain mandatory.

## Excluded paths

These receive no shared-account exception. Authority entry points refuse;
selectors, projections and observers grant no independent owner authority.
Nested validation still applies when a bound module calls an excluded path.
Tier 2 extensions and canonical Tier 3 over-cap approvals both retain separation;
round ceilings and extension limits remain unchanged.

| Module | Excluded functions |
| --- | --- |
| `lifecycle` | `ready_transition_record`, `revalidate_ready_transition_record`, `review_round_transition` |
| `review_policy` | `authorize_tier3_round`, `cap_disposition_plan` |
| `review_tiers` | `runtime_assignment`, `validate_round`, `review_decision` |
| `merge_train` | `_validate_admission_evidence`, `admit_source`, `_validate_admitted_source`, `order_sources`, `freeze_generation`, `generation_status` |
| `provenance` | `_reviewed_owner_ids`, `_provider_owner_config`, `verify_owner_override`, `record_owner_override`, `supersede_owner_override`, `verify_provider_owner_override`, `_verify_override_chain`, `check_commit_range` |
| `providers/github_review_host` | `collect_provenance_observation`, `collect_default_branch_observation` |

Rootless callers retain their refusal or non-authoritative result:
`cli.main`, `closeout.require_jira_reconciliation_before_counts`,
`host_preflight._provider_identity_rows`, `interaction.gate_handoff`,
`owner_publication.resume_owner_publication`,
`publication_readiness.publication_readiness`, `status_report_store._identity`,
`provider_identity.observe_github_identity`.

## Required automation identity

Excluded owner verification obtains `github.expected_actor_id` only from
accepted default-branch configuration. Missing identity fails closed; the
opt-out never bypasses separation here. Distinct owners retain their existing
controls when accepted identity context is supplied.

With the opt-out null or absent and the automation account also listed in
`trusted_owner_ids`, `policy.validate_config` and identity preflight refuse that
overlap; `inspect_config`, which the review freeze command uses, does not. Below
`validate_config`:

- `review_completion.review_authority_from_config` and the
  `integration_queue` authority projections return the whole owner list, both
  accounts included. Reviewer removal and queue owner instructions validated
  against them therefore accept either account's authenticated record.
- A distinct owner's ready, Jira-binding, intake and stream decisions and
  over-cap approvals keep their pre-opt-out results. The automation account's
  own records refuse there, and a record replaced during verification refuses.
- With the opt-out unset, over-cap collection refuses a signer outside
  `trusted_owner_ids`.
- Empty gate collections return empty and accept no approval.
- Controller overlap and replan approvals refuse. Excluded round-admission
  prerequisites remain.

A caller that knows its policy root decides by the accepted policy: with the
accepted opt-out set, owner authority refuses any proposal that leaves it unset
or invalid, whatever its identity, owners or section shapes. This includes
review freeze, Jira binding, over-cap and cap dispositions, CLI owner records
and gate evaluation, intake, streams, the queue store, controller cycles
(their publication configuration) and post-merge counts. The check can only refuse; an unobservable default branch
changes nothing. Rootless interfaces and reports that consume no owner record
(doctor, status, publication, gate handoff) are unchanged.

The closed provenance owner projection now requires `expected_actor_id` beside
`config_sha256`, `trusted_owner_ids` and `binding_sha256`; its binding payload
includes that identity. Verification matches the configuration digest and
automation identity to accepted bytes. Reviewed owner IDs may narrow the
accepted owner list; they cannot add owners. Host narrowing is preserved. Provider snapshots remain
version 1 with additive identity and accepted project-configuration digest;
the host-file digest is not project policy. Old projections/snapshots remain
audit data but cannot verify overrides without the identity. Signed v1/v2
override record formats, digests and rendering remain unchanged. Legacy sealing
alone grants no override authority.

## Jira remains separate

The Jira opt-out is slice O2 and is not yet available. Shared owner/controller
closure remains held; controllers never write Done for owner-closure tickets
or transition Epics. A future controller-history check can rule out only its
own instrumented operations, not direct writes using the same credentials.
