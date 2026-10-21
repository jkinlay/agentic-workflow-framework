# Review tiers, cap dispositions and closeout

Version 1.9.4; AWF-16 ships in 1.9.5. [Lifecycle](23-TICKET-LIFECYCLE.md)/code govern.

## Risk tiers

Contracts justify `risk_tier` 1-3; uncertainty means Tier 2. Tier 1 needs eligible, unprotected paths, no true `risk_flags`, and gate recomputation. Changes need owner-signed `tier-reassignment`, reissued contract and rebound records.

Tier 1: one advisory critic. Tier 2: three rounds, then ticket P2s; send P1s to owner. Tier 3: governance, release, merge/qualification, CI; three-round cap, candidate-bound owner review. Boundaries block.

Each `critic_artifact_binding` binds run, canonical `result.json`, head/round; receipts retain bytes/frozen completion identities. Gates reparse. Unchanged diffs retain history; changes need current rounds. Tier 2 over-cap needs authenticated owner disposition. Tier 3 round N needs this prior PR comment from the posting actor's trusted owner:

`AWF-OVERCAP-APPROVAL: round N of cap M, approver <owner>, ruling <time>`

Wrong-round, malformed, untrusted or late records fail. Verdict/observation bind verdict/digest, repository/PR, immutable comment ID/URL, exact bodies, anchor and run/time. The GitHub collector re-reads both under a registry pinned to implementation, release, repository and receipt. Configured IDs only narrow it; candidate data cannot register. Mismatches are `NOT_READY`.

Above-cap Tier 3 collection lists all PR comments, re-fetches approvals by immutable ID and binds receipts to live repository/PR/head/base, exact comment and critic-run start. Other/stale heads or absent approval keep the gate closed.

## Bases, lineage and dispositions

BLOCKER/MAJOR findings carry `{criterion_id}` or `{boundary_code}` from `PROTECTED_PATH`, `SCOPE_ESCAPE`, `CREDENTIAL_EXPOSURE`, `UNSAFE_PATH`, `UNREGISTERED_PRODUCER`, `CI_BINDING`, `INDEPENDENCE`; missing bases fail. A serious successor at the same path/basis names `supersedes_finding_id`. Out-of-closure defects are MINOR successor-ticket proposals, not rounds.

`finding-disposition` `ACCEPT_RISK` or `NOT_A_DEFECT` makes a non-boundary finding non-blocking for the exact `head_sha` and copies it into `residual_risks`; `REQUIRE_FIX` keeps it open; `HEAD_CHANGED` voids it.

`authorization.verify_owner_record` requires exact `AWF1.2 DISPOSE`, a trusted actor, one unedited comment per record, and a verifier independent of worker/critic. Cap dispositions sign `cycles/extensions:ids/successor` (`-` when absent; URL-encoded for `RESCOPE`); tier/closure records sign `head=-`.

Every lane review critic run needs an AWF-native `review-verdict` and `critic_artifact_binding`; auxiliary critic analyses are not rounds. `disposition-request` verifies head/bytes, creates a unique `authorization_request_id` and renders exact `AWF1.2 DISPOSE`. Only the resulting unedited comment from `merge_gate.trusted_owner_ids` supports close/extend/merge. `cap-plan` requires that verdict and matching project, ticket, policy and candidate.

Project `merge_gate.trusted_owner_ids` supplies the reviewed numeric human owners. Empty shipped lists cannot authorize live dispositions; runtime provider observation remains required.

## Automation account separation

Publish/collect with a least-privilege bot/App; credentials stay in host secrets, never worktrees/records. Exclude bots from `trusted_owner_ids`; ambiguity refuses. Owner credentials remain human-held; automation never uses/impersonates owners.

## Closure standard

`closure_standard.kind` is `FULL` or attributed `DECLARED_LIMITATIONS`, frozen under `contract_hash`; changes are `REQUIREMENTS_CHANGED`. Worker and critic record `closure.result`; both must be `MET`.

## The amendment cap

`execution.max_amendment_cycles` (3) excludes evidence-only amendments. `REVIEW_CAP_REACHED` needs one owner-signed `review-cap-disposition`:

| Decision | Transition | Effect |
| --- | --- | --- |
| `MERGE_WITH_NOTES` | → FINAL_REVIEW | Listed open non-boundary findings become notes in `residual_risks` and the authorization text. |
| `PARK` | → BLOCKED | `reason_code: REVIEW_CAP_PARKED`, `resume_state: CHANGES_REQUESTED`. |
| `RESCOPE` | → SUPERSEDED | Successor recorded. |
| `EXTEND_ONE_CYCLE` | → CHANGES_REQUESTED | Tier 2: increments `cap_extensions`; refused at `execution.max_cap_extensions` (default 2, maximum 3). Tier 3 uses the canonical pre-round approval above. |

`workflow.py cap-plan` translates/refuses; hosts pause at `REVIEW_CAP_REACHED` until `--disposition`.

`MERGE_WITH_NOTES` authenticates the exact terminal verdict/digest and artifact.
Otherwise the terminal artifact must be `APPROVE`/`PASS`; only this cap path may
carry its `REQUEST_CHANGES`. The gate records its ordered verified bindings.

## Jira lifecycle mirroring

Only the controller writes. `WORKER_STARTED` → `in_progress`; `PR_READY` → `in_review`; owner changes → `in_progress`; `JIRA_RECONCILED` → `done` with PR, reviewed head and merge commit. BLOCK/PARK never write. Tickets matching `jira.owner_closure_keywords` wait for an `owner-closure` record. `jira.lifecycle_writes` only disables mappings. Read back every write; mismatch or unknown stops that ticket's writes and is never reissued. `controller.auto_transition_jira: true` skips per-write prompts.

## Closeout and history

`validate-closeout` resolves recorded Git objects and digests; absent objects fail and the worktree is unread. `JIRA_RECONCILED` needs `closeout_valid`; `verifies_history` needs `fetch-depth: 0` and the long-path step.

## Validation

`digest` renders fixed state, gates, findings, validation, reviewer and authority; `--prose` caps free text at 80 words. Posted digests are hash-bound `evidence_comment`s, never transitions.

Worker validation records discovered/executed tests, fixed-code skips and unevaluable files; undeclared skips/unevaluable files fail. `local_ci_parity` maps required checks to commands unless a declared platform distinction has a regression test.

Host resources have named slots copied contract→dispatch→lease; missing or
overlapping capacity fails dispatch/provenance. `POST_MERGE_FINDING` opens a
`corrects` successor without changing merged state.

Preflight PASS/WARN/SKIP/N_A covers paths, lint, longpaths, policy, symlinks, line endings and LFS; warnings do not block.

## Final-gate commit provenance

The final gate recomputes commit provenance using `check_commit_range` on
`candidate.merge_base_sha..candidate.head_sha`. It fails for a missing Writer
trailer (untagged commit) and refuses a non-mechanical script commit that is
not reproducibly mechanical. See [provenance](41-WRITER-CRITIC-PROVENANCE.md#final-gate-commit-provenance)
for acquisition, policy, result binding and compatibility requirements.
