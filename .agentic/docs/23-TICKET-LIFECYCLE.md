# Ticket lifecycle

This renders [workflow.yaml](../workflow.yaml); [lifecycle.py](../lib/agentic/lifecycle.py) is authoritative. These AWF states have no external side effects; Jira writes are mirrored below.


## Routine work

COMPLETE publishes a draft PR. Git-blocked BLOCKED records include `commit_route: PUBLISHER`, `tested_tree`, `changes` and excluded `ignored_untracked`; the publisher makes no edits and requires matching `HEAD^{tree}`. No route means `WORKER`. Mark-ready needs an explicit owner/manager authorization record for PR/head: a pre-issued request/expiry plus authenticated immutable provider observation binding accepted configuration and trusted time. Offline checks, CI or validation retain refusal/draft. Log authorization before mutation. Changes require re-review. Owner-ready needs critic/specialists/final-gate; it never authorizes merge.

Before publication, require a [publication scan](31-PUBLICATION-SAFETY.md) exit 0 and matching PASS receipt for exact base/head/final body. Rescan amendments, body edits and comments. `diff --check` is whitespace-only.

Routine publication requires accepted repository/ref bindings, branch pattern and fresh APPLIED rules. Classification is presentation only: retain scope, permissions, non-force refs and secret/adapter prerequisites. Report missing action and owner.

| From | Event → To | Required evidence |
| --- | --- | --- |
| BACKLOG | TICKET_READY → READY | Valid configuration, scope, ownership, satisfied dependencies, ready contract. |
| READY | DISPATCH_ACCEPTED → DISPATCHED | Permitted dispatch, current lease/ownership, reserved budget. |
| DISPATCHED | WORKER_STARTED → IN_PROGRESS | Registered run and verified worktree. |
| IN_PROGRESS | WORKER_COMPLETED → PR_DRAFT | Valid worker result, `branch_pushed`, `pr_exists`. |
| PR_DRAFT | PR_READY → READY_FOR_CRITIC | Validation/requirements, logged explicit owner/manager authorization for PR/head, observed `draft_cleared`; else logged refusal, unchanged draft. |
| READY_FOR_CRITIC | CRITIC_REJECTED → CHANGES_REQUESTED | Current review. |
| CHANGES_REQUESTED | AMENDMENT_ACCEPTED → AMENDING | Permitted dispatch, current lease/ownership, reserved budget, cycles available. |
| CHANGES_REQUESTED | CAP_REACHED → REVIEW_CAP_REACHED | Cycles exhausted; open findings presented to the owner. |
| REVIEW_CAP_REACHED | CAP_MERGE_WITH_NOTES / CAP_PARK / CAP_RESCOPE / CAP_EXTEND_ONE_CYCLE | Owner-signed [disposition](30-REVIEW-TIERS-AND-CLOSEOUT.md); EXTEND_ONE_CYCLE is Tier 2 only, and Tier 3 is hard-capped at three rounds. |
| AMENDING | AMENDMENT_COMPLETED → READY_FOR_CRITIC | Valid result, current validation and registered new head. |
| READY_FOR_CRITIC | CRITIC_APPROVED_SPECIALISTS → SPECIALIST_REVIEW | Current critic and required specialists. |
| READY_FOR_CRITIC | CRITIC_APPROVED_FINAL → FINAL_REVIEW | Current critic; no specialists required. |
| SPECIALIST_REVIEW | SPECIALIST_REJECTED → CHANGES_REQUESTED | Current review. |
| SPECIALIST_REVIEW | SPECIALISTS_APPROVED → FINAL_REVIEW | Current specialists and critic. |
| FINAL_REVIEW | FINAL_GATE_PASSED → READY_FOR_OWNER_AUTHORIZATION | Derived gate and requirements current; embedded atomic all-reviewer completion admission validated. |
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

`FINAL_GATE_PASSED` rehashes every round's retained critic result, completion,
verdict and provider-observation bytes. Missing or inconsistent history fails.
The current terminal artifact must pass unless its exact verdict/artifact has an
authenticated `MERGE_WITH_NOTES`. Production posting requires a
runtime-registered provider collector; configuration cannot register one.

## Jira boundary

Only the controller writes Jira: WORKER_STARTED=`in_progress`; PR_READY=`in_review`; owner changes=`in_progress`; post-merge JIRA_RECONCILED=`done` with PR/head/merge. BLOCK/PARK never write; owner-closure tickets wait. Never transition Epics. `jira.lifecycle_writes` can disable mappings. Already-target is a no-op; disabled Jira forbids writes.

Read back every write. A mismatch or unknown stops that ticket's writes: report suspected external automation and never reissue. Comments are `digest_sha256`-bound `evidence_comment`s, never transitions. The shipped adapter performs no Jira writes.

## Control and recovery

Unspecified events reject. Requirements/policy changes invalidate evidence and block, as does unavailable CI. Candidate changes invalidate review; failed CI, reopened threads, dismissed reviews or OWNER_CHANGES_REQUESTED return to CHANGES_REQUESTED. Revoked authorization invalidates its evidence. Terminal invalidations only audit. POST_MERGE_FINDING records the finding and successor (`corrects`) without changing merged state.

BLOCK/FAIL record evidence; RECOVER needs resolved blocker, current external state, verified resume guards and revoked old permit, into a listed `resume_states` value. Cancellation/closure/supersession needs source/owner disposition, current state and revoked tasks. During MERGING/MERGE_UNKNOWN, disruptive events record uncertainty and reconcile before retry. Confirmed manual merges require matched candidate/authorization. Confirmed reverts reopen merged work; merged work cannot simply be cancelled. See [native coordination](24-STREAM-STARTUP.md) and [scheduled recovery](22-AUTOMATED-REVIEW-LOOP.md).
