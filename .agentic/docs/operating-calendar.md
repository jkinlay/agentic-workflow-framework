# Operating calendar and routine final-review batching

AWF-244: Tier 3, FULL. Offline helpers activate no scheduler or adapter.
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

Omissions resolve in memory; explicit nulls/unknown keys refuse. Times require
local `HH:MM`, presentation before cutoff. Days require nonempty unique ISO
integers 1-7, excluding booleans; effective lists sort. Only `continuous` work
is supported. Disabled batching presents immediately without granting authority.
EoD days are independent; changes neither enable reporting nor suppress hourly
reports. Legacy files, explicit settings, hashes and history remain unchanged.

`effective_calendar(root)` returns detached validated defaults; unrelated root
fields still require existing validation. `validate_calendar(subtree)` returns
the same complete shape or raises `ValidationError`.
`next_review_presentation(ready_at, subtree)` accepts offset RFC3339 or aware
`datetime`. Results contain original `ready_at` (datetimes normalize to UTC),
UTC-Z `presentation_at` (parse with `utc_instant`), domain-separated effective
`calendar_sha256`, `held`, and always-false `merge_authorized`.

The window includes 07:00, excludes 17:00. Earlier weekdays wait until morning;
later readiness waits for the next review day. Friday evening/weekends wait
until Monday. Late ticks recheck the current window. IANA rules resolve ambiguous
boundaries to the later occurrence, gaps to the first valid instant; collapsed
windows skip. Bounded search includes the next weekly recurrence.

The platform-independent lock pins `tzdata==2026.6` and its reviewed SHA-256.
Offline wheelhouses must include it; installed import/version inventories admit
it. Windows needs no `PYTHONTZPATH` override. Missing/invalid IANA data refuses
with `ValidationError`, including disabled batching; no offset fallback exists.
Tests require London/New York data.

## Persistence and delivery

`ReviewBatchStore(existing_continuous_controller_store)` retains protected-path
integrity, full-synchronous SQLite and serialized `BEGIN IMMEDIATE`. Versioned
intent/outbox/check/receipt tables remain separate from `controller_outbox`,
dispatch, lifecycle and reporting; the existing digest reader lacks kind filtering.

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
   Payload/identity remain immutable. Sender must serialize check/mutation and
   retain provider authority controls; checks are never reusable send permits.
5. Only verified readback calls `acknowledge`/`reconcile`. Closed receipts require
   matching `batch_id`, `payload_sha256`, status, nonempty `provider_receipt_id`
   and `observed_at` not before preparation. Identical receipts deduplicate;
   conflicts/reused identities refuse. DELIVERED completes; confirmed
   NOT_DELIVERED requeues once. Two failures exhaust; newer tuples take precedence.

After restart, `pending_batches()` permits readback only. Unknown sends and
refused rechecks remain unresolved until verified delivery/non-delivery; no blind
resend, expiry retry, operator reset or timeout release exists.

AWF-241 owns OFF/Teams/Slack/both, destination receipt namespaces and verified
per-platform two-attempt ceilings. Partial/ambiguous delivery must reconcile
before logical terminal receipts; one failed destination cannot mean NOT_DELIVERED.
Never multiply retry allowances. Offline helpers claim no live-channel,
reporting-OFF or unattended-host qualification.

## Integration still required

Lane A owns operating validation/set/render: reuse lock/journal/audit/recovery,
validate complete mixed roots, preserve omitted defaults and twelve-stream
adoption behavior. Authenticated owner ingress needs actual trusted evidence and
restart/interruption/mixed-root tests. Centrally extend the closed operating
schema, regenerate examples/defaults, preserve installed settings and regenerate
release inventories; this lane changes no generated files.

Controller/AWF-241 must wire protected persistence, trusted time/current-gate
observation, pre-send checks, readback/reconciliation and effective EoD settings.
Held presentation must never block dispatch, reviews or reporting; reporting stays
default-OFF. Qualify bootstrap, real hourly/EoD delivery and disabled reporting;
extend operating documentation. FULL closure still requires integration,
current-head independent critic/specialists, final gate and owner review.
