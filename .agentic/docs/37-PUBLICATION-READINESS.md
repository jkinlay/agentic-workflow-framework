# Publication readiness before dispatch

Version 1.9.3. `agentic.publication_readiness.publication_readiness` checks the
completion path for one assigned ticket and feature branch. The six checks are
authentication, remote reachability, branch eligibility, applicable rules, push
permission, and draft-PR creation permission. Each result carries its precise
blocker and observation time. A failed check refuses dispatch; it never selects
owner publication implicitly.

The production continuous controller requires accepted project configuration
and an `observe_publication(ticket)` function from its pinned reviewed adapter.
It obtains fresh observations before assigning eligible work. The adapter must
observe the exact numeric repository, configured actor/profile, feature ref and
applicable rules through bounded read-only provider calls. It must distinguish
permission to push from permission to create a draft PR; repository read access
does not establish either. The adapter must report `UNOBSERVED` when it cannot
establish the applicable rules. Observation flags from candidate files are not
trusted provider evidence.

The observation uses format `awf-publication-readiness-1`, source
`host_observation`, host, observed_at, ticket, slug, branch, identity,
authenticated, remote_reachable, rules, push_permitted and draft_pr_permitted.
The identity has the K12 GitHub identity shape. `rules` contains `state`
(`ALLOWED`, `BLOCKED` or `UNOBSERVED`) and bounded `evidence`, scoped to the
record's exact feature branch. All permission and reachability flags are strict
Booleans. Records and identity observations expire after five minutes; future
observations, foreign tickets/repositories, and malformed records fail closed.

When a stream is blocked, other eligible streams continue. Pending dispatches
remain unlaunched until a fresh observation passes. Previously uncertain host
dispatches are reconciled by observation and never blindly reissued.

`publication_capabilities` derives `branch_publication` and `pr_creation` rows
for the existing activation capability matrix. Supply those rows through the
trusted host's capability observations. ACTIVE alone never proves publication
readiness. Independent reviewer availability and merge executor availability
remain separate host observations. A readiness result grants no push, PR,
merge, Jira or adapter authority and does not replace the publication/deny scans.

## Owner publication and restart

When an authenticated policy explicitly requires owner publication, the
controller prepares an `awf-owner-publication-1` handoff through
`OwnerPublicationStore`. Store it in a separate protected SQLite database
outside every worker/reviewer worktree. The request retains the policy evidence,
absolute command worktree and one to six completed streams. Each stream carries
its identity, ticket/slug, feature branch, exact completed head, title, canonical
body, completed-result digest and complete stream/lifecycle snapshot. Budgets,
amendment counts and completed validation remain in that retained snapshot.

`owner-publication-prepare` produces one PowerShell literal command of at most
8192 characters. It uses `git push --atomic`, an exact configured repository URL
and commit-to-feature-ref mappings, without force or deletion. It never executes
the command. The trusted host must authenticate the policy route and complete
the ordinary candidate/body publication and deny scans before presenting the
command for execution. A push-permission failure alone grants no fallback.

After the owner runs the command, `owner-publication-resume` obtains fresh
GitHub actor/repository identity and a complete observation of the exact remote
heads. A missing, foreign, stale or changed head leaves the handoff intact.
Matching heads automatically continue to draft-PR creation through the reviewed
adapter. Each PR operation has a persisted UUID and enters `PR_IN_FLIGHT` before
the provider callback. A separate exact head/base/body/repository readback
establishes `PR_CREATED`; the callback return alone cannot establish success.

An interruption or uncertain result retains `PR_IN_FLIGHT`/`PR_UNKNOWN` and
resumes by observing that same operation. It never repeats PR creation blindly,
restarts completed worker work or discards lifecycle state. A completed handoff
is idempotent. The adapter enforces normal publication permission and scan
preconditions as well as the operation UUID and exact candidate. No merge or
Jira transition is performed by this component.

The existing controller entry point exposes `owner-publication-prepare`
(`--request`, `--now`), `owner-publication-resume` (`--batch`, `--now`) and
`owner-publication-status` (`--batch`). All use `--state`, `--worktree-root` and
accepted `--project-config`; resume additionally requires the digest-pinned
adapter with `observe_identity`, `observe_remote_heads`, `create_draft_pr` and
`observe_draft_pr`. Protect those adapter and configuration inputs as controller
authority; candidate-authored records cannot grant provider permissions.
