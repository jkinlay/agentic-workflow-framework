# Calendar and batching

Tier 3, FULL.
Implementation/tests/independent review/enabled hourly reporting continue 24/7;
only routine `READY_FOR_OWNER_AUTHORIZATION` presentation waits.
Safety escalation/owner overrides retain policy. Receipts authorize no merge.

## Calendar contract

Optional `OPERATING_CONFIG.yaml` subtree:

```json
{
  "operating_calendar": {
    "timezone": "Europe/London",
    "work_mode": "continuous",
    "review_batching": {
      "enabled": true,
      "weekdays": [1, 2, 3, 4, 5],
      "presentation_time": "07:00",
      "cutoff_time": "17:00"
    },
    "eod": {"time": "17:00", "weekdays": [1, 2, 3, 4, 5, 6, 7]}
  }
}
```

Omissions resolve in memory; nulls/unknown keys refuse. Local `HH:MM` requires
presentation before cutoff. Days: unique ISO integers 1-7.
Work: `continuous`.
Disabled batching presents immediately without authority. EoD never enables reporting; preserve legacy bytes/history.

Window: includes 07:00, excludes 17:00. Early weekdays wait until morning;
late readiness waits until next review day; Friday evening/weekends until Monday.
Late ticks recheck windows. IANA rules resolve ambiguous
boundaries to the later occurrence, gaps to the first valid instant; collapsed
windows skip.
Weekdays have no holiday exclusions: Christmas Day gets a digest when selected.

Missing/invalid IANA tzdata refuses even with batching disabled.

## Persistence and delivery

`ReviewBatchStore`: protected-path integrity; separate dispatch/lifecycle/reporting.

1. `enqueue(candidate, ready_at, calendar)` preserves first readiness/schedule.
   Independently validate repository/PR/head/base/tree/final-gate against current
   evidence. Deduplicate repeats; changed tuples withdraw pending predecessors.
2. `claim(now, calendar, observer)` recomputes schedules and atomically claims
   one due digest. Catch-up marks missed windows.
3. Fresh observation requires exact candidate, `observed_at == now`,
   later `expires_at`, literal `open: true`, `merged: false`,
   `final_gate_valid: true`, and owner-ready `readiness`. Stored flags authenticate nothing. Invalid observations withdraw; exceptions roll back.
   Trusted `ready_at <= observed_at` preserves readiness; otherwise observation time applies.
   Withdrawn identities require fresh qualification, never replay.
4. Before initial send, `recheck(batch_id, now, calendar, observer)`
   persists fresh member/window/calendar evidence.
   Refusal releases only durable PREPARED claims with proof no send started;
   fresh invalidation withdraws only that member. Survivors form another batch.
   Serialize check/mutation; checks are never reusable send permits.
5. Verified readback alone calls `acknowledge`/`reconcile`. Closed receipts require
   matching `batch_id`, `payload_sha256`, status, nonempty `provider_receipt_id`
   and `observed_at` not before preparation. Identical receipts deduplicate;
   conflicts/reused identities refuse. DELIVERED completes; confirmed
   NOT_DELIVERED requeues once. Two attempts exhaust each opening across restart; claims consume none. Retain counts/receipts. Current merged calendars govern recovery, including changed windows/disabled batching; retained times cannot override them. Fresh qualification remains mandatory; never retry unknown sends.
   Newer tuples take precedence; transport retry ceilings remain independent.

`begin_delivery` durably fences/counts. Reload accepted settings/recheck clock/window/expiry after commit, immediately before transport. Refusal/exception cancels only that live uninvoked fence, returning to waiting without an attempt. The small gap between this last check and provider receipt, including later merges/scheduling/transport latency, is unavoidable. UNKNOWN never resends. Restart releases PREPARED claims only; missing legacy phase evidence and started sends require
terminal readback through `pending_batches()`. Unknowns fence their PR, allowing
unrelated digests. Operators obtain verified terminal provider readback for `observe_review_delivery` to reconcile; no blind resend/operator reset/timeout release.
`view(now, calendar)` exposes held, withdrawn, exhausted and unresolved identities,
reasons/times, counts and retry openings read-only for AWF-241. Exhausted/unknown
states raise owner alerts and remain in controller status digests.

AWF-241 owns reporting/destination ceilings. Reconcile partial delivery; one failed destination cannot mean NOT_DELIVERED. Never multiply retries.

## Settings and controller ports

Ask in AWF Codex chat to check or change calendar settings. `operating show` reports effective calendar, source/commit and working differences. PR/merger is optional; rules warnings never block show. For changes it branches from the fetched default, preserves comments,
validates `operating validate --file PROPOSED`, and prepares a PR showing exact
before/after under existing publication authority. Restricted pushes prepare
branch/body for owner publication. The owner reviews and merges; agents never
merge. No owner terminal command is required. Protected `operating set` refuses
without writes, including OFF or unchanged calendar assignments.

Calendar/budget share default-branch policy, identified by `refs/remotes/origin/HEAD`. Working-tree/PR-branch edits cannot alter windows,
batching or sends. Merged changes apply at the next load after fetch, reporting
the commit. An absent calendar retains the defaults above; malformed accepted
policy refuses with a reason. Non-repository fresh defaults invoke no Git/gh.
Git/PR history audits changes; unprotected recovery remains.
Keep Git metadata outside worker authority; host rules and owner govern merge.
Direct default pushes or owner rights can bypass this; offer the shipped ruleset.

Cycles expose calendar for EoD/hourly scheduling. Batching follows dispatch/status. Reviewed ports:

- `observe_owner_ready` supplies candidate locators bound to scope/time,
  never authority or inventory-derived readiness.
- `observe_review_candidate(candidate, now)` independently observes repository,
  PR/head/base/tree, open/merged state, lifecycle and current final-gate bytes.
  Only owner-ready enqueues. Rechecks observe again; trusted time guards cutoff/expiry; changed members withdraw; expired evidence waits.
- `deliver_review_batch(payload)` conditionally sends once using immutable
  `batch_id` as idempotency key; returns verified terminal receipt or `None`.
- `observe_review_delivery(payload)` reads receipts only. Unknown returns `None`.
  Confirmed NOT_DELIVERED must fence delayed sends before allowing a retry.

Host authenticates receipts, serializes/bounds calls. Missing ports collect only. Restart reconciles without resending; held/unknown reviews never gate work.
Safety/action alerts stay immediate.
FULL requires qualification, current-head independent review, specialists, final gate and owner review.
