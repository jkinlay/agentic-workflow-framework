# Reviewed epic PR integration queue

The optional epic queue tests interaction without changing owner review or
merge sources. Task branches remain merge sources; `awf/int/<epic>` is
disposable evidence. Serial mode is the default.

## Authority and state

Enable epic mode only through a closed, identity-checked record produced from
reviewed queue-configuration provenance or an owner instruction bound to the
epic. A live in-process observer must authenticate the reviewed configuration
bytes or the exact raw owner text, provider/repository identity, immutable
numeric owner ID, and source event against accepted `PROJECT_CONFIG`. The same
owner observation is required again for reorder or skip; a digest or actor
assertion is not authority. Enforced mode
requires `awf-integration` from the configured App integration ID. Advisory
mode still refuses pending entries. Epic mode permits merge commits only.

The steward has no model route or tokens, cannot edit task branches, and never
pushes or merges the target. Provider work stays behind adapters. Its SQLite
ledger lives outside worktrees; a write transaction holds the admission lock.

Schemas and templates remain a marked generator-turn follow-up; this pure logic
does not require contracts absent from the release head.

## Entries and builds

An entry binds its PR, ticket, reviewed head, branches, dependencies, validation,
reviewed paths, ordered commits, and stable patch ID. Independent PRs target the
epic target; dependent entries target their last dependency branch. Admission
and head replacement require observed diff commits. Extra, reordered, shared,
or previously used commits are rejected, including after removal.

Admissions run alone under the lock. The provider returns a closed observation
whose input binds target, ordered heads, and command. Construction resolves each
merge step; tests bind each prefix commit and tree. Divergent results for one
input fail closed. Conflicts require paths and return the entry to amendment. A
red admission removes only the new entry; a red rebuild removes the first
failing prefix entry. No failure edits another branch.

A build binds its epic, target SHA, ordered `(PR, head)` pairs, observed
construction, result commit and tree, test command, prefix results, producer,
and trigger. The stable input identity detects divergent results, while every
trigger gets a distinct build-instance identity bound to its generation,
reason, result, and provider observation. Delta and readiness evidence names
that instance, so an older rebuild cannot replay even if ordering later returns
to identical inputs.
Target movement, head change, removal, reorder, skip, and post-merge processing
require a build.

## Ordering and merge readiness

Owner reorder and skip use one-use instructions bound to the exact payload,
then rebuild. Dependencies stay first and cannot be skipped while needed. The
stall clock starts when a PR/head becomes front; after the threshold, the next
independent entry may move first with owner notice. Merged dependencies are
removed and dependents retargeted. Only unchanged patch IDs retain review.

Only position one can receive `awf-integration: success`. It needs a green
current-target build beginning with the exact PR/head. The framework evaluates
the complete evidence bundle again, including registered independent critic
and specialist runs, CI, publication, and final-gate checks; the existing gate
must match that evaluation. The framework authorization contract then verifies
the request, exact raw owner text, trusted numeric owner, expiry, and candidate
binding. Its producer must also be a complete registered verifier run
independent of implementation and review. Caller-authored hashes or an
`authenticated` flag do not qualify.

Overlapping paths merged since the review base require a fresh critic delta
review bound to head/base, target/build, intervening-merge digest, and diff
digest. A clean result consumes a run, not a round; a code finding consumes the
normal amendment round. No overlap needs no delta review.

After an observed merge, rebuild from the new target before admitting the next
front. Post-merge target CI failure requests a revert PR; it never authorizes a
target push. Post-merge counts are allowed only after observed Jira
reconciliation when Jira is enabled. The framework validates the actual
`closeout-record` and `jira-transition` contracts: their bindings and transition
record ID must match, the lifecycle event must be `JIRA_RECONCILED`, and the
successful transition must read back its configured target status from the
provider, with an observed time. Disabled Jira requires no invented
transition. Empty-queue closure records integration-branch deletion without
granting execution authority.

## Reference entry point

Use `.agentic/scripts/integration_queue.py --help`; `--state` must be outside
the checkout. Inputs are strict JSON/YAML records and output is JSON. Admission
and head changes require observed diff commits. Build mutations require an
in-process provider-adapter runner: the CLI refuses standalone outcome
documents because it cannot authenticate or execute that adapter. Initialization,
readiness checks, reorder, and skip likewise require in-process authenticated
observers. The CLI grants no Git, CI, review, Jira, or merge authority.
