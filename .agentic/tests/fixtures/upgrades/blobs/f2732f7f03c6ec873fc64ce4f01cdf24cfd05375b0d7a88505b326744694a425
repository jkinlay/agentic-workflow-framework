# Ticket lifecycle

This renders [workflow.yaml](../workflow.yaml); [lifecycle.py](../lib/agentic/lifecycle.py) is authoritative. These are AWF states; Jira writes are mirrored below. The reference state machine has no external side effects.

## Routine work

On worker COMPLETE, the publisher opens a draft PR from the branch. If Git-metadata writes are denied, the worker leaves the validated tree uncommitted, reports BLOCKED only for commit with `commit_route: PUBLISHER`, and records tested-tree evidence; the publisher commits that exact worktree without content edits and verifies equality. Differences return to the worker as scope violations; absent `commit_route` means `WORKER`. Observe PR/head/base/target and finalize records. Current validation permits mark-ready and critic review of that head; changes require re-review. READY_FOR_CRITIC precedes READY_FOR_OWNER_AUTHORIZATION after critic, specialists and final gate. Neither authorizes merge.

Routine publication classification requires accepted repository/ID/default/ref bindings, the ticket/slug's `github.branch_pattern` and fresh live APPLIED rules evidence. It is presentation only: retain task scope, platform permissions, non-force feature refs and secret/adapter prerequisites. Reuse existing authorization; where missing, report the concrete action/owner.

| From | Event → To | Required evidence |
| --- | --- | --- |
| BACKLOG | TICKET_READY → READY | Valid configuration, scope, ownership, satisfied dependencies, ready contract. |
| READY | DISPATCH_ACCEPTED → DISPATCHED | Permitted dispatch, current lease/ownership, reserved budget. |
| DISPATCHED | WORKER_STARTED → IN_PROGRESS | Registered run and verified worktree. |
| IN_PROGRESS | WORKER_COMPLETED → PR_DRAFT | Valid worker result, `branch_pushed`, `pr_exists`. |
| PR_DRAFT | PR_READY → READY_FOR_CRITIC | Current validation/requirements and observed `draft_cleared`. |
| READY_FOR_CRITIC | CRITIC_REJECTED → CHANGES_REQUESTED | Current review. |
| CHANGES_REQUESTED | AMENDMENT_ACCEPTED → AMENDING | Permitted dispatch, current lease/ownership, reserved budget, cycles available. |
| CHANGES_REQUESTED | CAP_REACHED → REVIEW_CAP_REACHED | Cycles exhausted; open findings presented to the owner. |
| REVIEW_CAP_REACHED | CAP_MERGE_WITH_NOTES / CAP_PARK / CAP_RESCOPE / CAP_EXTEND_ONE_CYCLE | Owner-signed [disposition](30-REVIEW-TIERS-AND-CLOSEOUT.md). |
| AMENDING | AMENDMENT_COMPLETED → READY_FOR_CRITIC | Valid result, current validation and registered new head. |
| READY_FOR_CRITIC | CRITIC_APPROVED_SPECIALISTS → SPECIALIST_REVIEW | Current critic and required specialists. |
| READY_FOR_CRITIC | CRITIC_APPROVED_FINAL → FINAL_REVIEW | Current critic; no specialists required. |
| SPECIALIST_REVIEW | SPECIALIST_REJECTED → CHANGES_REQUESTED | Current review. |
| SPECIALIST_REVIEW | SPECIALISTS_APPROVED → FINAL_REVIEW | Current specialists and critic. |
| FINAL_REVIEW | FINAL_GATE_PASSED → READY_FOR_OWNER_AUTHORIZATION | Derived gate ready; requirements current. |
| READY_FOR_OWNER_AUTHORIZATION | OWNER_AUTHORIZED → OWNER_AUTHORIZED | Verified authorization, unused request, current gate. |
| OWNER_AUTHORIZED | MERGE_STARTED → MERGING | Certified executor, atomic candidate protocol, consumed permit, current gate. |
| MERGING | MERGE_OBSERVED → MERGED | Confirmed merge and matched candidate. |
| MERGE_UNKNOWN | MERGE_OBSERVED → MERGED | Confirmed merge and matched candidate. |
| MERGE_UNKNOWN | NO_MERGE_CONFIRMED → FINAL_REVIEW | Confirmed no merge, current external state, revoked old permit. |
| MERGED | JIRA_UNAVAILABLE → MERGED_PENDING_JIRA | Confirmed merge. |
| MERGED | OWNER_CLOSURE_REQUIRED → MERGED_PENDING_OWNER_CLOSURE | Confirmed merge; contract `owner_closure_required`. |
| MERGED | JIRA_RECONCILED → DONE | Confirmed merge, valid closeout record, confirmed Jira Done, refreshed dependencies, no owner closure required. |
| MERGED_PENDING_JIRA | JIRA_RECONCILED → DONE | Same reconciliation evidence; no blind repeat write. |
| MERGED_PENDING_OWNER_CLOSURE | JIRA_RECONCILED → DONE | Same plus verified owner closure. |
| FAILED | RECOVERABLE_FAILURE → BLOCKED | Recoverable failure and recorded blocker. |
| REOPENED | REOPEN_DISPOSITION → CANCELLED | Owner disposition and recorded successor. |

## Jira boundary

The controller is the sole Jira writer, one mapped write per event: WORKER_STARTED writes `status_map.in_progress`; PR_READY writes `in_review`; OWNER_CHANGES_REQUESTED or HEAD_CHANGED in review writes `in_progress`; JIRA_RECONCILED after the observed merge writes `done` with a closing comment naming PR, reviewed head and merge commit. BLOCK and PARK never write. A ticket matching `jira.owner_closure_keywords` waits in `MERGED_PENDING_OWNER_CLOSURE` for an owner-closure record. Never transition an Epic. `jira.lifecycle_writes` turns mappings off, never adds one. Already at target is a no-op. Disabled Jira permits no writes.

Read back after every write; keep actor/timestamp only when observed. A mismatch or unknown result stops that ticket's writes, not unaffected streams: report a suspected external automation conflict, never reissue. Comments are digests bound by `digest_sha256` (`evidence_comment`), never transitions. The shipped adapter performs no Jira writes.

## Control and recovery

Unspecified events reject. Requirements/policy changes invalidate evidence and block, as does unavailable CI. Candidate changes invalidate review; failed CI, reopened threads, dismissed reviews or OWNER_CHANGES_REQUESTED return to CHANGES_REQUESTED. Revoked authorization invalidates its evidence. Terminal invalidations only audit. POST_MERGE_FINDING records the finding and successor (`corrects`) without changing merged state.

BLOCK/FAIL record evidence; RECOVER needs resolved blocker, current external state, verified resume guards and revoked old permit, into a listed `resume_states` value. Cancellation/closure/supersession needs source/owner disposition, current state and revoked tasks. During MERGING/MERGE_UNKNOWN, disruptive events record uncertainty and reconcile before retry. Confirmed manual merges require matched candidate/authorization. Confirmed reverts reopen merged work; merged work cannot simply be cancelled. See [native coordination](24-STREAM-STARTUP.md) and [scheduled recovery](22-AUTOMATED-REVIEW-LOOP.md).
