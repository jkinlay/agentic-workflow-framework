# Operating calendar and routine final-review batching

AWF-244: Tier 3, FULL. Wiring activates no scheduler/adapter.
Implementation/tests/independent review/enabled hourly reporting continue 24/7;
only routine `READY_FOR_OWNER_AUTHORIZATION` presentation waits.
Draft readiness/safety escalation/authenticated owner overrides retain policy.
Receipts authorize no merge/release/Jira/activation.

## Calendar contract

Optional version 1 `OPERATING_CONFIG.yaml` subtree:

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
presentation before cutoff. Days: nonempty unique ISO integers 1-7, never booleans.
Work: `continuous` only.
Disabled batching presents immediately without authority. Independent EoD days
never enable/suppress reporting. Preserve legacy bytes/hashes/history.

Window: includes 07:00, excludes 17:00. Early weekdays wait until morning;
late readiness waits until next review day; Friday evening/weekends until Monday.
Late ticks recheck windows. IANA rules resolve ambiguous
boundaries to the later occurrence, gaps to the first valid instant; collapsed
windows skip.
Weekdays have no holiday exclusions: Christmas Day gets a digest when selected.

Lock: `tzdata==2026.6`, reviewed SHA-256. Missing/invalid IANA data refuses
even with batching disabled; no offset fallback.

## Persistence and delivery

`ReviewBatchStore`: protected-path integrity; separate dispatch/lifecycle/reporting.

1. `enqueue(candidate, ready_at, calendar)` preserves first readiness/schedule.
   Independently validate repository/PR/head/base/tree/final-gate against current
   evidence. Deduplicate repeats; changed tuples withdraw pending predecessors.
2. `claim(now, calendar, observer)` recomputes schedules and atomically claims
   one due digest. Immutable timestamped catch-up means a missed held opening/earlier
   window, never ordinary clock precision.
3. Trusted fresh observation requires exact candidate, `observed_at == now`,
   later `expires_at`, literal `open: true`, `merged: false`,
   `final_gate_valid: true`, and owner-ready `readiness`. Stored flags authenticate nothing. Invalid observations withdraw; exceptions roll back.
   Optional trusted `ready_at <= observed_at` preserves readiness; otherwise use observation time. Cutoff still applies.
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
   NOT_DELIVERED requeues once. Two attempts exhaust the window; claims consume none. Lifetime counts persist. Fresh qualification permits two attempts from next presentation opening; never retry unknown sends.
   Newer tuples take precedence; transport retry ceilings remain independent.

`begin_delivery` durably fences/counts before invoking transport. UNKNOWN never resends. Restart releases PREPARED claims only; missing legacy phase evidence and started sends require
terminal readback through `pending_batches()`. Unknowns fence their PR, allowing
unrelated digests. No blind resend/operator reset/timeout release.
`view(now, calendar)` exposes held, withdrawn, exhausted and unresolved identities,
reasons/times, counts and retry openings read-only for AWF-241. Exhausted/unknown
states raise owner alerts and remain in controller status digests.

AWF-241 owns OFF/Teams/Slack/both and per-platform two-attempt ceilings. Reconcile partial delivery before logical terminal receipts; one failed destination
cannot mean NOT_DELIVERED. Never multiply retries.

## Settings and controller ports

Validation/set/show retains lock/journal/audit/recovery. Weekdays: JSON lists.
`operating set`: exit 3 `PENDING_OWNER_CONFIRMATION`, unchanged settings. Owner runs
`operating confirm --digest D` within 15 minutes, typing eight digest characters.
Terminal/stored GitHub User identity must match reviewed numeric owners.
OFF/unchanged assignments require admission. Under lock: revalidate domains/governance/bytes,
consume once across mixed changes, register before journal/audit/readback.
Calendar creates no budget/enrollment state. Both domains bind clone-common-directory
and OS-account storage, never environment; latest confirmation supersedes.
Load-time digest checks reject external edits/removal. Unconfirmed defaults load without Git/gh executables.

Each cycle resolves calendar for AWF-241's separate EoD/hourly scheduler.
Batching follows dispatch/immediate status/action delivery. Optional reviewed ports:

- `observe_owner_ready` supplies candidate locators bound to scope/time,
  never authority or inventory-derived readiness.
- `observe_review_candidate(candidate, now)` independently observes repository,
  PR/head/base/tree, open/merged state, lifecycle and current final-gate bytes.
  Only `READY_FOR_OWNER_AUTHORIZATION` enqueues. Claim/pre-send recheck repeat observation;
  post-read trusted time guards cutoff/expiry; changed members withdraw; expired evidence stays pending.
- `deliver_review_batch(payload)` conditionally sends once using immutable
  `batch_id` as idempotency key; returns verified terminal receipt or `None`.
- `observe_review_delivery(payload)` reads receipts only. Unknown returns `None`.
  Confirmed NOT_DELIVERED must fence delayed sends before allowing a retry.

Host authenticates transport/receipts, serializes cycles, bounds adapter calls. Without delivery ports, collection persists without
claiming. Restart reconciles started sends without resending. Held/unknown reviews never gate work/tests/independent review/amendments/hourly reports.
Safety and action alerts stay immediate. No port grants merge authority.
Delivery/OFF qualification, current-head independent review, specialists,
final gate and owner review remain required for FULL closure.
