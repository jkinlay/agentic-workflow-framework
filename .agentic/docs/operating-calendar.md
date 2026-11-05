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
1-7, excluding booleans; lists sort. Only `continuous` work is supported.
Disabled batching presents immediately without authority. EoD days are independent;
changes never enable reporting or suppress hourly reports. Legacy bytes/hashes/history
remain unchanged.

`effective_calendar(root)` returns detached validated defaults;
`validate_calendar(subtree)` validates the subtree or raises `ValidationError`.
`next_review_presentation(ready_at, subtree)` accepts RFC3339 or aware `datetime`.
Results contain original `ready_at` (datetimes normalize), UTC-Z `presentation_at`, effective
`calendar_sha256`, `held`, and always-false `merge_authorized`.

The window includes 07:00, excludes 17:00. Early weekdays wait until morning;
later readiness waits for the next review day. Friday evening/weekends wait
until Monday. Late ticks recheck the current window. IANA rules resolve ambiguous
boundaries to the later occurrence, gaps to the first valid instant; collapsed
windows skip. Bounded search includes the next weekly recurrence.

The lock pins `tzdata==2026.6` and its reviewed SHA-256. Wheelhouses and installed
inventories include it. Missing/invalid IANA data refuses, even with batching
disabled; no offset fallback exists. Tests require London/New York data.

## Persistence and delivery

`ReviewBatchStore(controller_store)` retains protected-path integrity,
full-synchronous SQLite and serialized `BEGIN IMMEDIATE`. Versioned review tables
remain separate from `controller_outbox`, dispatch, lifecycle and reporting.

1. `enqueue(candidate, ready_at, calendar)` preserves first readiness/schedule.
   Closed identity requires positive integer repository/PR IDs, lowercase
   40-hex head/base/tree and 64-hex `final_gate_sha256` independently validated
   against current evidence. Repeats deduplicate; changed tuples withdraw pending
   predecessors.
2. `claim(now, calendar, observer)` recomputes schedules and atomically claims
   one due digest. Immutable payloads retain original/current schedules,
   readiness, observation, attempt and timestamped catch-up. Unresolved claims
   block review digests only.
3. Trusted fresh observation requires exact candidate, `observed_at == now`,
   later `expires_at`, literal `open: true`, `merged: false`,
   `final_gate_valid: true`, and owner-ready `readiness`. Stored flags cannot
   authenticate observations. Invalid observations withdraw; exceptions roll back.
   Withdrawn identities require fresh qualification/new pins, never replay.
4. Immediately before initial send, `recheck(batch_id, now, calendar, observer)`
   persists fresh member/window/calendar evidence; drift refuses the whole digest.
   Payload/identity remain immutable. Serialize check/mutation; checks are never
   reusable send permits.
5. Only verified readback calls `acknowledge`/`reconcile`. Closed receipts require
   matching `batch_id`, `payload_sha256`, status, nonempty `provider_receipt_id`
   and `observed_at` not before preparation. Identical receipts deduplicate;
   conflicts/reused identities refuse. DELIVERED completes; confirmed
   NOT_DELIVERED requeues once. Two failures exhaust; newer tuples take precedence.

After restart, `pending_batches()` permits readback only. Unknown sends and
refused rechecks remain unresolved until verified delivery/non-delivery; no blind
resend, expiry retry, operator reset or timeout release exists.

AWF-241 owns OFF/Teams/Slack/both and per-platform two-attempt ceilings. Partial
delivery must reconcile before logical terminal receipts; one failed destination
cannot mean NOT_DELIVERED. Never multiply retry allowances.

## Settings and controller ports

Operating validation/set/show uses existing lock/journal/audit/recovery. Dotted
paths use JSON weekday lists. Generated defaults/schema include the calendar;
legacy files omit it unchanged.
`CalendarSettingsRegistry` reuses budgeting's protected registry and trusted-host
`observe_owner_direction` pattern, with separate immutable replay records.
Infrastructure injects `calendar_registry` into `set_operating`/`main`; standalone
CLI instructions refuse calendar mutations. Host observations bind the project,
effective prior-calendar hash and exact changes, consumed after mixed-root
validation under the operating lock. Recovery retains that observation.

`production_controller_cycle` resolves the validated snapshot's effective calendar
each cycle, returning `operating_calendar` for AWF-241's separate EoD/hourly
scheduler. After dispatch and immediate status/action delivery it calls
`production_review_batching_cycle`, constructing protected `ReviewBatchStore`.
Reviewed CLI adapters may inject four optional ports:

- `observe_owner_ready({binding, observed_at})` returns exactly
  `{binding, observed_at, candidates}` with matching scope/time. Candidates are
  locators, never authority or inventory-derived readiness.
- `observe_review_candidate(candidate, now)` independently observes repository,
  PR/head/base/tree, open/merged state, lifecycle and current final-gate bytes.
  Its closed observation above must pass `enqueue_observed`; only
  `READY_FOR_OWNER_AUTHORIZATION` enqueues. Claim/pre-send recheck repeat observation;
  post-read trusted time guards cutoff/expiry; stale pending members withdraw.
- `deliver_review_batch(payload)` conditionally sends once using immutable
  `batch_id` as idempotency key; returns verified terminal receipt or `None`.
- `observe_review_delivery(payload)` reads receipts only. Unknown returns `None`.
  Confirmed NOT_DELIVERED must fence delayed sends before allowing a retry.

Lane E supplies transport and receipt authentication. The host serializes cycles
and bounds adapter calls. Without delivery ports, collection persists without
claiming. Restart reads unresolved receipts without resending. Refused pre-send
checks retain unresolved claims for verified reconciliation. Held/unknown reviews
never gate implementation, tests, independent review, amendments or hourly reports.
Safety and action alerts stay immediate. No port grants merge authority.
Delivery/OFF qualification, current-head independent review, specialists,
final gate and owner review remain required for FULL closure.
