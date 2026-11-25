# Shared owner account candidate boundary

This is AWF-307's GitHub slice. Keep the GitHub
setting null until a release ships it. The global separation refusal remains.
Prefer separate accounts.
Agents never propose an opt-out without explicit human direction.

Only these nine modules have accepted-default-policy bindings for the named
shared GitHub account: `streams`, `intake_assignment`, `provider_identity`,
`policy`, `authorization`, `gates`, `review_completion`, `integration_queue`,
`continuous_controller`. Acceptance requires the configured expected actor to
be the named trusted owner, explicit trusted host policy-root context and
unchanged accepted merged default-branch bytes. Other
policy cannot enable acceptance or authorize its own merge.
Retained evidence binds the accepted policy digest, shared-account annotation
and warning. Request, expiry, replay, candidate, provider, reviewer-floor and
tier protections and signed provider bodies are unchanged.

Shared records prove which account signed, not which person.
Agents must never sign human decisions through the shared account. Human merge
and qualification boundaries remain mandatory.

## Excluded paths

These receive no shared-account exception. Authority entry points refuse;
selectors, projections and observers grant no authority.
Nested validation still applies when a bound module calls an excluded path.
Tier 2 extensions and Tier 3 over-cap approvals retain separation;
round ceilings and extension limits are unchanged.

| Module | Excluded functions |
| --- | --- |
| `lifecycle` | `ready_transition_record`, `revalidate_ready_transition_record`, `review_round_transition` |
| `review_policy` | `authorize_tier3_round`, `cap_disposition_plan` |
| `review_tiers` | `runtime_assignment`, `validate_round`, `review_decision` |
| `merge_train` | `_validate_admission_evidence`, `admit_source`, `_validate_admitted_source`, `order_sources`, `freeze_generation`, `generation_status` |
| `provenance` | `_reviewed_owner_ids`, `_provider_owner_config`, `verify_owner_override`, `record_owner_override`, `supersede_owner_override`, `verify_provider_owner_override`, `_verify_override_chain`, `check_commit_range` |
| `providers/github_review_host` | `collect_provenance_observation`, `collect_default_branch_observation` |

Rootless callers keep their refusal or non-authoritative result:
`cli.main`, `closeout.require_jira_reconciliation_before_counts` (both also make
the hidden-setting check below), `host_preflight._provider_identity_rows`, `interaction.gate_handoff`,
`owner_publication.resume_owner_publication`,
`publication_readiness.publication_readiness`, `status_report_store._identity`,
`provider_identity.observe_github_identity`.

## Required automation identity

Excluded owner verification obtains `github.expected_actor_id` only from
accepted default-branch configuration. Missing identity fails closed; the
opt-out never bypasses separation here. Distinct owners keep their controls
when accepted identity is supplied.

With the opt-out null or absent and the automation account also listed in
`trusted_owner_ids`, `policy.validate_config` and identity preflight refuse that
overlap; `inspect_config`, which the review freeze command uses, does not. Below
`validate_config`:

- `review_completion.review_authority_from_config` and the
  `integration_queue` authority projections return the whole owner list, both
  accounts included. Reviewer removal and queue owner instructions
  therefore accept either account's authenticated record.
- A distinct owner's ready, Jira-binding, intake and stream decisions and
  over-cap approvals keep their pre-opt-out results. The automation account's
  own records refuse there, and a record replaced during verification refuses.
- With the opt-out unset, over-cap collection refuses a signer outside
  `trusted_owner_ids`.
- Controller overlap and replan approvals refuse. Excluded round-admission
  prerequisites remain.

These callers refuse when their known root's accepted setting is set
and the proposal leaves it unset or invalid, whatever its identity, owners or
section shapes: review freeze, confirmed Jira binding, over-cap and cap
dispositions, CLI owner records and gate evaluation, intake, bootstrap
assignment, streams, rooted preflight and authorization, the queue store and
review-store removals (protected roots), controller owner approvals, the
production cycle's publication configuration, and post-merge counts. Relative
roots count.

Limits: a root is unobservable, and this check changes nothing, when Git or
`origin/HEAD` is missing, its ref dangles, isolated Git fails (no native Git on
absolute PATH entries outside the project and current directory, missing
objects, broken repository configuration, timeout) or the accepted file is absent,
not mode 100644, malformed, oversized or without mappings. Others may
refuse. An absent path refuses if the accepted tree holds it under equivalent
case, normalization or names, or as a submodule now an ordinary directory;
accepted symbolic links are followed. Shared policy from bare-repository files
refuses. A candidate can move a project to another name, where the path is
absent; the host must run the accepted path and code. Protect Git metadata,
including a linked worktree's `.git` file, which a worker owning that directory
can rewrite unless the host protects it. Refresh `origin/HEAD`. Protected roots
are checked without comparing repositories, so another opted-in worktree can
refuse. CLI evaluation and authorization refuse a retiring proposal; only
library `accepted_policy_root` gates it.

Unchanged: rootless interfaces; reports consuming no owner record (doctor,
status, publication, gate handoff); and planning overrides, which accept either
account's signature.

The closed provenance owner projection now requires `expected_actor_id` beside
`config_sha256`, `trusted_owner_ids` and `binding_sha256`; its binding payload
includes that identity. Verification matches both to accepted bytes.
Reviewed owner IDs may only narrow the accepted owner list. Provider snapshots remain
version 1 with additive identity and accepted project-configuration digest;
the host-file digest is not project policy. Old projections/snapshots without
identity are audit data only. Signed v1/v2 override formats, digests and rendering are unchanged. Legacy sealing grants no override authority.

## Jira remains separate

The Jira opt-out (slice O2) is unavailable. Shared owner/controller
closure remains held; controllers never write Done for owner-closure tickets
or transition Epics. A future history check can rule out only instrumented
operations, not direct writes with the same credentials.
