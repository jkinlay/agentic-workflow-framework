# Five-slot acceleration contract

AWF 1.9.3 defaults to five concurrent implementation roles: one sole controller,
one dedicated adversarial-case handler, and worker streams A, B and C. The
reviewed Epic integration/PR steward is deterministic non-model infrastructure,
not a sixth agent. Reviewers run after the implementation candidate is frozen;
they do not weaken the independent-review requirements.

Capacity is negotiated before dispatch. Fewer than five observed slots is a
reported degraded mode and needs explicit authorization evidence; the host must
authenticate that authority. The reference validator only checks the evidence
shape and never grants authority. It never silently reduces the default.

## Acceleration order

1. Freeze the exact repository/PR/base/head/tree/manifest tuple and the canonical
   provider PR-body digest.
2. Run the permanently named `pre-controller-adversarial-regression-gate` over
   the complete ordered inventory in
   `validation/five-slot-adversarial-regressions.json`.
3. Partition heavy tests deterministically into at most three resource-bounded
   shards. Every shard has an exact test list and result digest; the candidate,
   assignments and receipts produce one aggregate digest.
4. Run exactly one full suite against the frozen tuple, then one exact-tuple
   controller verification.
5. Preserve frozen independent-review completion, the publication scan, the deny
   scan, and final independent review in that order. Any tuple drift invalidates
   the chain and requires a new freeze.

The PR body is canonicalized automatically as strict UTF-8 without a BOM, LF
line endings, and exactly one trailing LF. The provider body is read back as raw
bytes and must equal the canonical bytes before its SHA-256 enters the candidate
tuple. A semantically equivalent provider rewrite is a mismatch.

## Streams, Jira and findings

Each worker has exactly one dependency-ordered active deliverable. Its remaining
work is explicitly `Open` or `On Hold`; unresolved work is never disguised as an
active item. A stream blocker carries evidence and does not stop unblocked
streams. Only the controller performs Jira lifecycle writes and merge-count
reporting; this evidence validator performs neither.

Every critic finding accepted into the implementation must be added to the
permanent inventory with a stable regression ID, named test and evidence path.
The plan is rejected until each accepted finding has exactly one such member.
Deleting an accepted finding's regression is therefore a tracked, manifest-bound
change rather than an ephemeral review action.

## Reference command

`five_slot_acceleration.py` validates already collected evidence. It does not
launch the adversarial gate, shards, full suite, agents, Jira, reviews or provider
actions. The trusted host supplies pinned plan/inventory digests plus the local
body and raw provider readback. A passing receipt always reports
`execution_authority: false` and `owner_ready: NO`.
