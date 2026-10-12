# Reviewed epic PR integration queue

The optional epic queue tests interaction without changing owner review or
merge sources. Task branches remain merge sources; `awf/int/<epic>` is
disposable evidence. Serial mode is the default.

## Authority and state

Epic mode needs a closed record from reviewed queue configuration or an
epic-bound owner instruction. A fresh, receipted, runtime-registered observer
authenticates those bytes, provider/repository identity, owner and source
against accepted `PROJECT_CONFIG`; reorder and skip re-observe their owner
instructions. Enforced mode separately observes that `awf-integration` comes
from the configured App integration ID. Advisory mode still refuses pending
entries. The queue retains observation times and producer/release receipts, and
re-observes activation authority and App binding before every authority use,
including admission and readiness. Stored evidence is never perpetual
authority. Epic mode permits merge commits only.

The steward has no model route or tokens, cannot edit task branches, and never
pushes or merges the target. Provider work stays behind adapters. Its SQLite
ledger lives outside worktrees; a write transaction holds the admission lock.

Schemas and templates remain a generator-turn follow-up; this logic needs no
contracts absent from the release head.

## Entries and builds

An entry binds PR, ticket, reviewed head, branches, dependencies, validation,
paths, ordered commits and patch ID. Independent PRs target the epic target;
dependent entries target their last dependency. Admission and head replacement
observe diff commits; extra, reordered, shared or reused commits are rejected.

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

Each trigger records a distinct build generation, result, tests and provider
observation. Delta/readiness evidence names it, preventing replay. Target move,
head change, removal, reorder, skip and post-merge processing rebuild.

## Ordering and merge readiness

Owner reorder and skip use one-use, payload-bound instructions, then rebuild.
Dependencies stay first and cannot be skipped while needed. After the mode
record's `stall_after_hours` from observed front time, the next independent
entry may move first with owner notice. Merged dependencies are
removed only after a fresh receipted merge observation binds the exact front
PR, reviewed head, old target, and resulting target tip. A caller-named PR or
unrelated target movement cannot remove an entry. Dependents are retargeted.
Only an observed unchanged patch ID retains review and authorization; readiness
re-observes that patch equivalence for the head and target. A changed
patch clears retention and returns to ordinary amendment and review.

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
target push. Post-merge counts revalidate closeout from Git objects. Jira
enablement, provider identity, and the exact Done status come only from accepted
`PROJECT_CONFIG`; enabled Jira also requires a fresh registered observation of
the matching successful transition and readback. Disabled Jira invents no transition.
Empty-queue closure records integration-branch deletion without execution
authority.

## Reference entry point

Use `.agentic/scripts/integration_queue.py --help`; `--state` stays outside the
checkout. Mutations require in-process authenticated adapters; standalone
outcome documents, caller conflict paths, and caller stall thresholds are not
accepted. The CLI grants no Git, CI, review, Jira, or merge authority.
