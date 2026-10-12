# Reviewed epic PR integration queue

The optional epic queue tests interaction without changing owner review or
merge sources. Task branches remain merge sources; `awf/int/<epic>` is
disposable evidence. Serial mode is the default.

## Authority and state

Enable epic mode only through a closed, identity-checked record produced from
reviewed queue-configuration provenance or an owner instruction bound to the
epic. A runtime-registered, receipted, fresh in-process observer must
authenticate the reviewed configuration
bytes or the exact raw owner text, provider/repository identity, immutable
numeric owner ID, and source event against accepted `PROJECT_CONFIG`. The same
owner observation is required again for reorder or skip; a digest or actor
assertion is not authority. Enforced mode
requires a separately observed repository-rule binding for `awf-integration`
from the configured App integration ID. Advisory
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

Initialization and target rebuilds first observe the exact repository target
tip through a registered provider. Admissions run alone under the lock. The
provider returns a closed observation
whose input binds target, ordered heads, and command. Construction resolves each
merge step; tests bind each prefix commit and tree. Determinism compares the
stable result tree for identical inputs, while retaining each run's distinct
merge commits and test evidence. A divergent tree fails closed. Conflicts are
accepted only from a receipted failed-merge observation bound to the current
target, candidate head, actual paths, and observed task commits; commit
ownership remains reserved. A red admission removes only the new entry. A red
rebuild ejects the first failing prefix entry and quarantines its dependent
closure for ordered revalidation. No failure edits another branch.

Each trigger records a distinct build instance with its generation, result,
tests, and provider observation. Delta/readiness evidence names that instance,
preventing replay after rebuild. Target movement, head change, removal,
reordering, skipping, and post-merge processing require a build.

## Ordering and merge readiness

Owner reorder and skip use one-use instructions bound to the exact payload,
then rebuild. Dependencies stay first and cannot be skipped while needed. The
stall clock starts when a PR/head becomes front; after the authenticated mode
record's `stall_after_hours` threshold, the next
independent entry may move first with owner notice. Merged dependencies are
removed and dependents retargeted. Only unchanged patch IDs retain review.

Only position one can receive `awf-integration: success`. Every declared
dependency must still be present earlier in the queue. The front needs a green
current-target build beginning with the exact PR/head. The framework evaluates
the complete evidence bundle again, including registered independent critic
and specialist runs, CI, publication, and final-gate checks; the existing gate
must match that evaluation. The framework authorization contract then verifies
the request, exact raw owner text, trusted numeric owner, expiry, and candidate
binding. Its producer must also be a complete registered verifier run
independent of implementation and review. Caller-authored hashes or an
`authenticated` flag do not qualify.

Overlapping paths merged since the review base require a fresh critic delta
review bound to head/base, target/build/tree, the exact reviewed contribution
R, and the exact effective post-integration contribution R-prime. Separate
digests bind R and R-prime, and the critic-result digest binds both. A clean
result consumes a run, not a round; a code finding consumes the normal
amendment round. No overlap needs no critic result, but the registered observer
still supplies exact R and R-prime evidence.

After an observed merge, rebuild from the newly observed target before
admitting the next front. A fresh receipted observation of post-merge target CI decides recovery;
failure requests a revert PR and never authorizes a
target push. Post-merge counts require validated closeout and observed Jira
reconciliation when Jira is enabled; disabled Jira invents no transition.
Empty-queue closure records integration-branch deletion without execution
authority.

## Reference entry point

Use `.agentic/scripts/integration_queue.py --help`; `--state` stays outside the
checkout. Mutations require in-process authenticated adapters; standalone
outcome documents, caller conflict paths, and caller stall thresholds are not
accepted. The CLI grants no Git, CI, review, Jira, or merge authority.
