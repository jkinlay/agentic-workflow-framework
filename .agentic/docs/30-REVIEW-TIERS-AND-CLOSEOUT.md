# Review tiers, cap dispositions and closeout

Version 1.9.4; AWF-16 ships in 1.9.5. Code/[lifecycle](23-TICKET-LIFECYCLE.md) are authoritative.

## Risk tiers

Contracts declare justified `risk_tier` 1-3; uncertain is Tier 2. Tier 1 requires eligible, unprotected paths and no true `risk_flags`. The gate recomputes it. Changes require owner-signed `tier-reassignment`, re-issued contract and rebound records.

Tier 1 uses one critic; findings advise. Tier 2 has three rounds, then tickets P2s and routes P1s to the owner. Tier 3 covers governance, release, merge/qualification and CI gates, is capped at three, and requires candidate-bound owner review. Boundaries always block.

Each `critic_artifact_binding` identifies run, canonical `result.json`, head and round; its receipt retains those bytes and frozen completion identities. Gates reparse artifacts; copied fields cannot substitute. Unchanged diffs retain history; changed diffs require a current round. Tier 2 over-cap needs authenticated owner disposition. Tier 3 round N needs a preceding PR comment from the posting actor's trusted owner exactly:

`AWF-OVERCAP-APPROVAL: round N of cap M, approver <owner>, ruling <time>`

Wrong-round, malformed, untrusted or late records fail. Verdict/observation bind verdict/digest, repository/PR, immutable comment ID/URL, exact bodies, anchor and run/time. The GitHub collector re-reads both through the review host; its runtime registry is pinned to implementation, release, repository and per-run receipt. Configured IDs only narrow it; bundle/config/environment/fixtures cannot register. Mismatches are `NOT_READY`.

Above-cap Tier 3 collection lists all PR comments, re-fetches approvals by immutable ID and binds each receipt to live repository/PR/head/base, exact comment and critic-run start. Other/stale heads or absent approval keep the gate closed.

## Bases, lineage and dispositions

BLOCKER/MAJOR findings carry `{criterion_id}` or `{boundary_code}` from `PROTECTED_PATH`, `SCOPE_ESCAPE`, `CREDENTIAL_EXPOSURE`, `UNSAFE_PATH`, `UNREGISTERED_PRODUCER`, `CI_BINDING`, `INDEPENDENCE`; missing bases fail. A serious successor at the same path/basis names `supersedes_finding_id`. Out-of-closure defects are MINOR successor-ticket proposals, not rounds.

`finding-disposition` `ACCEPT_RISK` or `NOT_A_DEFECT` makes a non-boundary finding non-blocking for the exact `head_sha` and copies it into `residual_risks`; `REQUIRE_FIX` keeps it open; `HEAD_CHANGED` voids it.

`authorization.verify_owner_record` requires an exact `AWF1.2 DISPOSE` comment, trusted actor, one unedited comment per record, and a verifier independent of worker/critic. Cap dispositions sign `cycles/extensions:ids`; tier/closure records sign `head=-`.

Every critic run needs one AWF-native `review-verdict` plus `critic_artifact_binding`. `disposition-request` verifies its head/bytes, creates a distinct `authorization_request_id` and prints `AWF1.2 DISPOSE`. Output is non-authoritative; only the unedited comment from `merge_gate.trusted_owner_ids` supports close/extend/merge. `cap-plan` also requires that verdict.

Release 1.9.5 adds project `merge_gate.trusted_owner_ids` immediately after #154; until then tests inject it and live dispositions remain unavailable.

## Automation account separation

Use a least-privilege bot/App for publication/collection. Keep its credential only in the host secret store, never worktrees or records. Observe that the bot actor is configured and absent from `trusted_owner_ids`; ambiguity refuses. Owner credentials stay human-held for comments/manual merge. Automation never uses or impersonates the owner account.

## Closure standard

`closure_standard.kind` is `FULL` or attributed `DECLARED_LIMITATIONS`, frozen under `contract_hash`; changes are `REQUIREMENTS_CHANGED`. Worker and critic record `closure.result`; both must be `MET`.

## The amendment cap

`execution.max_amendment_cycles` (3) bounds cycles; evidence-only amendments consume none. At `REVIEW_CAP_REACHED`, record one owner-signed `review-cap-disposition`:

| Decision | Transition | Effect |
| --- | --- | --- |
| `MERGE_WITH_NOTES` | → FINAL_REVIEW | Listed open non-boundary findings become notes in `residual_risks` and the authorization text. |
| `PARK` | → BLOCKED | `reason_code: REVIEW_CAP_PARKED`, `resume_state: CHANGES_REQUESTED`. |
| `RESCOPE` | → SUPERSEDED | Successor recorded. |
| `EXTEND_ONE_CYCLE` | → CHANGES_REQUESTED | Tier 2: increments `cap_extensions`; refused at `execution.max_cap_extensions` (default 2, maximum 3). Tier 3 uses the canonical pre-round approval above. |

`workflow.py cap-plan` translates or refuses; the host pauses at `REVIEW_CAP_REACHED` and resumes only with `--disposition`.

`MERGE_WITH_NOTES` authenticates the exact terminal verdict/digest and artifact.
Otherwise the terminal artifact must be `APPROVE`/`PASS`; only this cap path may
carry its `REQUEST_CHANGES`. The gate records its ordered verified bindings.

## Jira lifecycle mirroring

Only the controller writes. `WORKER_STARTED` → `in_progress`; `PR_READY` → `in_review`; owner changes → `in_progress`; `JIRA_RECONCILED` → `done` with PR, reviewed head and merge commit. BLOCK/PARK never write. Tickets matching `jira.owner_closure_keywords` wait for an `owner-closure` record. `jira.lifecycle_writes` only disables mappings. Read back every write; mismatch or unknown stops that ticket's writes and is never reissued. `controller.auto_transition_jira: true` skips per-write prompts.

## Closeout and history

`validate-closeout` resolves recorded Git objects and digests; absent objects fail and the worktree is unread. `JIRA_RECONCILED` needs `closeout_valid`; `verifies_history` needs `fetch-depth: 0` and the long-path step.

## Digests, skips, parity, resources, preflight

`digest` renders fixed state, gates, findings, validation, reviewer and authority; `--prose` caps free text at 80 words. Posted digests are hash-bound `evidence_comment`s, never transitions.

Worker validation records discovered/executed tests, fixed-code skips and unevaluable files; undeclared skips/unevaluable files fail. `local_ci_parity` maps required checks to commands unless a declared platform distinction has a regression test.

Host resources have named slots copied contract→dispatch→lease; missing or
overlapping capacity fails dispatch/provenance. `POST_MERGE_FINDING` opens a
`corrects` successor without changing merged state.

Preflight PASS/WARN/SKIP/N_A covers paths, lint, longpaths, policy, symlinks, line endings and LFS; warnings do not block.
