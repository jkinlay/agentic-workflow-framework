# Review tiers, cap dispositions and closeout

Version 1.9.4 runtime; AWF-16 policy revision and migration ship in 1.9.5. The code (`review_tiers.py`, `review_policy.py`, `gates.py`, `jira_lifecycle.py`, `closeout.py`, `digest.py`, `host_preflight.py`) and the [lifecycle](23-TICKET-LIFECYCLE.md) are the authority; nothing here grants execution authority.

## Risk tiers

Every `ticket-contract` declares `risk_tier` (1, 2 or 3) with a `tier_justification`; uncertain is Tier 2. Tier 1 needs eligible paths, no protected paths and no true `risk_flags`. The gate recomputes the tier; highest match wins. Only an owner-signed `tier-reassignment` changes a declared tier; the owner re-issues the contract and every record rebinds. Round policy is in `review_tiers.py`; AWF-29 owns provider posting.

Tier 1 uses one critic; findings advise. Tier 2 uses three rounds; at cap, ticket P2s and route P1s to the owner. Tier 3 covers governance, release, merge/qualification and CI gates, has an unextendable three-round cap and requires an owner-review assertion binding a trusted owner and candidate. Provider verification remains the AWF-41 successor scope. Highest tier wins; escalation preserves history. Boundaries always block.

Each round records head, comment URL and body link. An unchanged diff preserves evidence; a changed diff invalidates it. Only Tier 2 may exceed the cap through authenticated owner disposition.

## Bases, lineage and dispositions

A BLOCKER or MAJOR finding carries `basis`: `{criterion_id}` from the contract or `{boundary_code}` from `PROTECTED_PATH`, `SCOPE_ESCAPE`, `CREDENTIAL_EXPOSURE`, `UNSAFE_PATH`, `UNREGISTERED_PRODUCER`, `CI_BINDING`, `INDEPENDENCE`; without one the record is refused and the critic re-emits. A new serious finding on a locus (same `path` and basis) with a lineage names `supersedes_finding_id`. A defect outside the closure standard is a successor-ticket proposal, MINOR, never a new round.

`finding-disposition` `ACCEPT_RISK` or `NOT_A_DEFECT` makes a non-boundary finding non-blocking for the exact `head_sha` and copies it into `residual_risks`; `REQUIRE_FIX` keeps it open; `HEAD_CHANGED` voids it.

`authorization.verify_owner_record` authenticates every owner record: its exact `AWF1.2 DISPOSE` comment digest matches, the actor is trusted, the unedited comment signs one record, and its verifier run is independent of worker and critic. Cap dispositions sign `cycles/extensions:ids`; tier/closure records sign `head=-`.

## Closure standard

`closure_standard.kind` is `FULL` or `DECLARED_LIMITATIONS` (at least one attributed limitation), frozen under `contract_hash` before review; changing it is `REQUIREMENTS_CHANGED`. `worker-result` and `critic-review` each record `closure.result`; the `acceptance_criteria` gate needs `MET` from both.

## The amendment cap

`execution.max_amendment_cycles` (3) bounds cycles; evidence-only amendments consume none. At `REVIEW_CAP_REACHED`, the controller records one owner-signed `review-cap-disposition`:

| Decision | Transition | Effect |
| --- | --- | --- |
| `MERGE_WITH_NOTES` | → FINAL_REVIEW | Listed open non-boundary findings become notes in `residual_risks` and the authorization text. |
| `PARK` | → BLOCKED | `reason_code: REVIEW_CAP_PARKED`, `resume_state: CHANGES_REQUESTED`. |
| `RESCOPE` | → SUPERSEDED | Successor recorded. |
| `EXTEND_ONE_CYCLE` | → CHANGES_REQUESTED | Tier 2 only: increments `cap_extensions`; refused at `execution.max_cap_extensions` (default 2, maximum 3). |

`workflow.py cap-plan` translates a disposition into event and facts or refuses it; the host loop pauses with `REVIEW_CAP_REACHED` and resumes only with `--disposition`.

## Jira lifecycle mirroring

The controller is the sole writer. `WORKER_STARTED` → `in_progress`; `PR_READY` → `in_review`; `OWNER_CHANGES_REQUESTED` or `HEAD_CHANGED` → `in_progress`; `JIRA_RECONCILED` → `done` with a closing comment naming the PR, reviewed head and merge commit. BLOCK/PARK never write. Tickets matching `jira.owner_closure_keywords` (the gate refuses a contract hiding the match) wait in `MERGED_PENDING_OWNER_CLOSURE` for an `owner-closure` record. `jira.lifecycle_writes` turns a mapping off, never adds one. Read back after every write; a mismatch or unknown result stops that ticket's writes, keeps observed actor/time or unknown, and is never reissued. `controller.auto_transition_jira: true` skips the per-write prompt.

## Closeout and history

`workflow.py validate-closeout RECORD --repository PATH` resolves reviewed head, base, merge commit, tree and bound blobs through Git plumbing and compares digests; absent objects fail closed and the working tree is never read. `JIRA_RECONCILED` requires `closeout_valid`. `verifies_history: true` requires `checkout_depth: full` (`fetch-depth: 0`, with the long-path step).

## Digests, skips, parity, resources, preflight

`workflow.py digest --ticket T --for jira|pr|owner --input RECORDS.json` renders the fixed shape (state line, gate table, open findings with basis, validation, reviewer) and the only authority footer; `--prose` refuses free text over 80 words. A posted digest is an `evidence_comment` event carrying `digest_sha256`; a status write is a `lifecycle_transition`.

Worker validation records `tests_discovered`, `tests_executed`, `declared_skips` (fixed reason codes) and `unevaluable_files`; unevaluable files or undeclared skips fail `acceptance_criteria`. `local_ci_parity` matches each required check to its local command (`local_command`, else the contract command by index) unless `validation.platform_distinction` names a regression test; a discrepancy across two candidates becomes a successor.

`execution.host_broker.resources` names resources and slots; contracts list `required_resources`, dispatches copy them, leases hold them, a missing slot refuses dispatch by name, and concurrent COMPLETE runs exceeding a resource's slots fail `provenance`. `POST_MERGE_FINDING` opens a successor (`successor_contract`) carrying `corrects`; merged state is unchanged.

Preflight records nonblocking PASS/WARN/SKIP/N_A for path length, `project_lint_scope`, `core.longpaths`, execution policy, symlinks, line endings and Git LFS. Lint scope warns when Ruff/flake8 includes `.agentic`; preflight warnings do not by themselves block adoption or review.
