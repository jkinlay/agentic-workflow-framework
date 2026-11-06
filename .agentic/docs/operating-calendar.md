# Operating calendar and routine final-review batching

AWF-244: Tier 3, FULL. Local wiring activates no scheduler or adapter.
Implementation, tests, independent review and enabled hourly reporting continue
24/7. Only routine presentation of `READY_FOR_OWNER_AUTHORIZATION` candidates
waits. Draft readiness, safety escalation and authenticated owner overrides
retain their policies. Receipts authorize no merge, release, Jira or activation.

## Calendar contract

The optional `operating_calendar` subtree uses version 1 `OPERATING_CONFIG.yaml`:

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

Omissions resolve in memory; nulls/unknown keys refuse. Times require local
`HH:MM`, presentation before cutoff. Days require nonempty unique ISO integers
1-7, excluding booleans. Only `continuous` work is supported.
Disabled batching presents immediately without authority. EoD days are independent;
changes never enable reporting or suppress hourly reports. Legacy bytes/hashes/history
remain unchanged.

The window includes 07:00, excludes 17:00. Early weekdays wait until morning;
later readiness waits for the next review day. Friday evening/weekends wait
until Monday. Late ticks recheck the current window. IANA rules resolve ambiguous
boundaries to the later occurrence, gaps to the first valid instant; collapsed
windows skip.
Weekdays have no holiday exclusions: Christmas Day gets a digest when selected.

The lock pins `tzdata==2026.6` and its reviewed SHA-256. Missing/invalid IANA data
refuses, even with batching disabled; no offset fallback exists.

## Persistence and delivery

`ReviewBatchStore` retains protected-path integrity, separate from dispatch,
lifecycle and reporting.

1. `enqueue(candidate, ready_at, calendar)` preserves first readiness/schedule.
   Repository/PR/head/base/tree/final-gate identity is independently validated
   against current evidence. Repeats deduplicate; changed tuples withdraw pending
   predecessors.
2. `claim(now, calendar, observer)` recomputes schedules and atomically claims
   one due digest. Immutable payloads record timestamped catch-up. Catch-up means a
   held opening or earlier window was missed, never ordinary clock precision.
3. Trusted fresh observation requires exact candidate, `observed_at == now`,
   later `expires_at`, literal `open: true`, `merged: false`,
   `final_gate_valid: true`, and owner-ready `readiness`. Stored flags cannot
   authenticate observations. Invalid observations withdraw; exceptions roll back.
   Optional trusted `ready_at <= observed_at` preserves actual readiness;
   absent that field, readiness uses observation time. Cutoff still applies.
   Withdrawn identities require fresh qualification, never replay.
4. Immediately before initial send, `recheck(batch_id, now, calendar, observer)`
   persists fresh member/window/calendar evidence.
   Refusal releases only durable PREPARED claims with proof no send started;
   fresh invalidation withdraws only that member. Survivors form another batch.
   Serialize check/mutation; checks are never reusable send permits.
5. Only verified readback calls `acknowledge`/`reconcile`. Closed receipts require
   matching `batch_id`, `payload_sha256`, status, nonempty `provider_receipt_id`
   and `observed_at` not before preparation. Identical receipts deduplicate;
   conflicts/reused identities refuse. DELIVERED completes; confirmed
   NOT_DELIVERED requeues once. Two delivery attempts exhaust the window; claims
   consume none. Lifetime counts persist. Fresh qualification allows two bounded
   attempts from the next presentation opening, never a retry of an unknown send.
   Newer tuples take precedence; transport retry ceilings remain independent.

`begin_delivery` durably fences/counts before invoking transport. UNKNOWN never
sends twice. Restart releases PREPARED claims only; missing legacy phase evidence and started sends require
terminal readback through `pending_batches()`. Unknowns fence their PR, allowing
unrelated digests. No blind resend, operator reset or timeout release exists.
`view(now, calendar)` exposes held, withdrawn, exhausted and unresolved identities,
reasons/times, counts and retry openings read-only for AWF-241. Exhausted/unknown
states raise owner alerts and remain in controller status digests.

AWF-241 owns OFF/Teams/Slack/both and per-platform two-attempt ceilings. Partial
delivery must reconcile before logical terminal receipts; one failed destination
cannot mean NOT_DELIVERED. Never multiply retry allowances.

## Settings and controller ports

Validation/set/show retains lock/journal/audit/recovery. Weekdays use JSON lists.
`operating set`: exit 3 `PENDING_OWNER_CONFIRMATION`, unchanged settings. Owner runs
`operating confirm --digest D` within 15 minutes, typing eight digest characters.
Terminal/stored GitHub User identity must match reviewed numeric owners.
OFF/unchanged assignments require admission. Under lock: revalidate domains/governance/bytes,
consume once across mixed changes, journal/audit/readback. Calendar creates no
budget/enrollment state.

Each controller cycle resolves the validated calendar for AWF-241's separate
EoD/hourly scheduler. Batching follows dispatch and immediate status/action delivery.
Reviewed adapters expose optional ports:

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

The host supplies transport and receipt authentication, serializes cycles
and bounds adapter calls. Without delivery ports, collection persists without
claiming. Restart reconciles started sends without resending. Held/unknown reviews
never gate implementation, tests, independent review, amendments or hourly reports.
Safety and action alerts stay immediate. No port grants merge authority.
Delivery/OFF qualification, current-head independent review, specialists,
final gate and owner review remain required for FULL closure.
