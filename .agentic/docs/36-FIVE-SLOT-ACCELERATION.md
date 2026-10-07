# Five-slot acceleration contract

AWF 1.9.3 defaults to five concurrent implementation roles: one sole controller,
one dedicated adversarial-case handler, and worker streams A, B and C. The
reviewed Epic integration/PR steward is deterministic non-model infrastructure,
not a sixth agent. Reviewers run after the implementation candidate is frozen;
they do not weaken the independent-review requirements.

Capacity is negotiated before dispatch. Fewer than five observed slots is a
reported degraded mode and needs explicit authorization evidence; the host must
authenticate that authority. The reference validator only checks the evidence
shape and never grants authority. It never silently reduces the default. Every
role reports `RUNNING`, `PAUSED` or `BLOCKED`. Running roles cannot exceed the
observed slots; every non-running role carries a reason, resume trigger and
evidence digest. Degraded evidence names every paused role. A four-slot host
therefore names the paused role, and a one-slot host cannot claim concurrent
workers. `other_streams_continue` is derived from workers that are actually
running rather than asserted by the plan.

## Acceleration order

1. Freeze the exact repository/PR/base/head/tree/manifest tuple and the canonical
   provider PR-body digest.
2. Run the permanently named `pre-controller-adversarial-regression-gate` over
   the complete ordered inventory in
   `validation/five-slot-adversarial-regressions.json`. The accumulated
   `race`, `alias`, `replacement-object`, `provider-identity` and
   `receipt-replay` categories are all mandatory. Each inventory member names a
   repository-relative Python artifact and a test that must exist in it.
   Receipts preserve inventory order and bind the frozen candidate, test ID,
   evidence path, exact artifact digest and host result digest into one
   recomputed execution digest. Receipt cardinality must exactly equal the
   frozen inventory cardinality before receipt iteration; duplicate, unknown,
   missing or trailing receipts fail before the full controller cycle.
3. Partition heavy tests deterministically into at most three resource-bounded
   shards. Every shard has an exact test list and result digest; the candidate,
   assignments and receipts produce one aggregate digest.
4. Run exactly one full suite against the frozen tuple, then one exact-tuple
   controller verification.
5. Preserve frozen independent-review completion, the publication scan, the deny
   scan, and final independent review in that order. Any tuple drift invalidates
   the chain and requires a new freeze.

Private deny baseline receipts follow the narrow, exact-tuple policy in
[`37-PRIVATE-DENY-SCAN.md`](37-PRIVATE-DENY-SCAN.md). They are separate from
publication-scan receipts; neither gate grants exceptions for the other.

The PR body is canonicalized automatically as strict UTF-8 without a BOM, LF
line endings, and exactly one trailing LF. The provider body is read back as raw
bytes and must equal the canonical bytes before its SHA-256 enters the candidate
tuple. A semantically equivalent provider rewrite is a mismatch.

## Streams, Jira and findings

Each worker has exactly one dependency-ordered current deliverable. Running work
is `ACTIVE` and Jira `In Progress`. Paused and blocked started work stays
`In Progress` because those events do not write Jira; PR-ready work is `In
Review`. Remaining inactive work is `OPEN`/Jira `Open` or `ON_HOLD`/Jira `On
Hold`. Started work cannot return to `Open`. A stream blocker does not stop an
unblocked worker when capacity remains. Only the controller performs Jira
lifecycle writes and merge-count reporting; this evidence validator performs
neither.

Every critic finding accepted into the implementation must be added to the
permanent inventory with a stable regression ID, named test and evidence path.
The plan is rejected until each accepted finding has exactly one such member.
Deleting an accepted finding's regression is therefore a tracked, manifest-bound
change rather than an ephemeral review action. The PR 33 controller findings
remain individually mapped as `C33-F01` selected-engine parallel caps,
`C33-F02` unrelated-engine host caps, `C33-F03` queued sibling/retry
cancellation, `C33-F04` sanitizer receipt binding, and `CV33-F01` durable
quarantined-lease retention.

The file broker persists state format `awf-heavy-validation-broker-state-3`
under its OS lock. `PARTIAL` or `FAILED` process-tree cleanup records a bounded
outcome, mechanism and timestamp against the exact lease ID and fencing token
before the launch gate releases queued work. Expiry retains all quarantined
worker, engine, GPU and named-resource claims across restart. Ordinary expired
leases remain reclaimable; ordinary `release` cannot remove quarantine. The
separate `release_quarantined` path requires a `TERMINATED` evidence envelope
with an allowlisted source, observation time and proof digest, itself bound by
digest to the exact lease and fence. Malformed, mismatched or non-quarantined
recovery requests fail closed. Live v2 state fails closed; empty v2 state
upgrades safely.

Result aggregate and serial-equivalence digests include each partition's
Windows launch artifact method and SHA-256. Snapshot paths are omitted because
they vary per run; the sanitizer's reviewed digest remains receipt-bound.

## Reference command

`five_slot_acceleration.py` validates already collected evidence. It does not
launch the adversarial gate, shards, full suite, agents, Jira, reviews or provider
actions. The trusted host supplies pinned plan/inventory digests plus the local
body and raw provider readback. A passing receipt always reports
`execution_authority: false` and `owner_ready: NO`.
