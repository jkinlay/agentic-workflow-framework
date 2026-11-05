# Operating calendar and routine final-review batching

AWF-244, Tier 3, FULL closure. These library helpers do not activate a scheduler
or adapters. Settings persistence, authenticated Codex UI ingress, production
observation, delivery and integrated qualification require the wiring below.

Work, implementation, tests, independent review and enabled hourly reporting
continue 24/7. Only routine presentation of candidates already at
`READY_FOR_OWNER_AUTHORIZATION` waits for this calendar. Draft-to-ready
authorization, safety escalation and explicit authenticated owner overrides
retain their own policy. A presentation or delivery receipt never authorizes
merge, release, Jira writes or adapter activation.

## Stable calendar contract

The optional `operating_calendar` subtree belongs in the existing version 1
`OPERATING_CONFIG.yaml`. No second settings file is introduced.

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
    "eod": {
      "time": "17:00",
      "weekdays": [1, 2, 3, 4, 5, 6, 7]
    }
  }
}
```

Every field can be omitted and resolves in memory to its default. Explicit
`null` and unknown subtree keys refuse. Times are strict local `HH:MM`, with
presentation strictly before cutoff. Day lists are nonempty, unique ISO integers
1 through 7; booleans are not integers. Effective day lists sort for stable
identity. The only supported work mode is `continuous`. Disabling review batching
presents routine ready candidates immediately, including weekends; it grants no
other authority. EoD defaults to every day, including weekends, independently of
review weekdays. EoD changes do not enable reporting or suppress hourly reports.

`effective_calendar(config)` takes the operating **root** mapping and returns a
detached, validated, complete calendar mapping. It ignores unrelated root fields;
the existing operating validator must still validate those fields. It never
rewrites a legacy file, hashes, audit history or explicit settings.

`validate_calendar(value)` takes a partial **subtree**, rejects invalid inputs
with `ValidationError`, and returns the same complete calendar shape.

`next_review_presentation(ready_at, calendar)` takes an RFC3339 instant with an
explicit offset (or an aware `datetime`) and a partial/effective **subtree**.
Its stable result fields are:

| Field | Meaning |
| --- | --- |
| `ready_at` | Original RFC3339 input text; datetime inputs normalize to UTC |
| `presentation_at` | RFC3339 UTC instant with `Z`; parse with `utc_instant` for an aware UTC datetime |
| `calendar_sha256` | Domain-separated fingerprint of all effective calendar settings |
| `held` | Whether presentation is later than original readiness |
| `merge_authorized` | Always `false` |

On configured weekdays, 07:00 inclusive to 17:00 exclusive presents immediately.
Before 07:00 waits for that morning. At or after 17:00 waits for the next
configured review day. Friday evening, Saturday and Sunday wait for Monday
07:00 under the defaults. Late ticks also respect the current window: a missed
Monday opening does not permit presentation on Monday evening.

IANA timezone rules determine UTC instants; no fixed offsets are used. An
ambiguous local boundary uses its later occurrence. A nonexistent local boundary
uses the first valid local instant after the gap. A window collapsed by that
resolution is skipped. The bounded search includes the next weekly recurrence.

Python on Windows often has no IANA database. The runtime lock declares
`tzdata==2026.6` with the reviewed wheel SHA-256 for every platform, so a runtime
installed from that lock supplies IANA data without a `PYTHONTZPATH` override.
Offline runtime wheelhouses must include that pinned wheel. The installed-runtime
dependency allowlist and import/version validation must also admit `tzdata`.
Missing or invalid timezone data still raises an explicit
`ValidationError` and refuses scheduling, even with batching disabled. There is
no UTC-offset fallback. Tests need real IANA data for London and New York.

## Controller persistence and delivery boundary

Construct `ReviewBatchStore(existing_continuous_controller_store)`. It uses that
store's protected path, integrity checks, full-synchronous SQLite and serialized
`BEGIN IMMEDIATE` transactions. Its versioned intent, outbox, pre-send check and
receipt-identity tables coexist in the controller database. They do not write
`controller_outbox`, dispatch, lifecycle or reporting state. The existing status
digest reader selects pending rows without kind filtering, so review payloads
must remain separate until an integration writer changes that behavior.

1. `enqueue(candidate, ready_at, calendar)` persists first readiness and original
   schedule. The closed candidate mapping requires positive integer
   `repository_id` and `pull_request`, lowercase 40-hex `head_sha`, `base_sha`,
   `head_tree_sha`, and 64-hex `final_gate_sha256`. The last field must pin the
   independently validated, current final-gate evidence. Identity covers every
   field. Repeated events do not reset readiness or repeat acknowledged output.
   A new tuple withdraws pending older tuples for that repository/PR.
2. `claim(now, calendar, observer)` recomputes pending schedules using current
   effective settings and atomically claims one digest of due candidates.
   A concurrent tick cannot claim the same members. The original schedule,
   current schedule, readiness, current observation, attempt number and
   timestamped `catch_up` indication persist in immutable payload bytes. An
   unresolved claim blocks further review digests, while other work continues.
3. The trusted `observer(candidate)` must independently re-observe numeric
   repository/PR identity, head/base/tree, open/merged status, lifecycle readiness
   and current final-gate evidence. Its closed mapping has `candidate`,
   `observed_at`, `expires_at`, `open`, `merged`, `readiness`, and
   `final_gate_valid`. `observed_at` must equal the trusted tick's `now` and
   expiry must be later. Only literal `open: true`, `merged: false`,
   `final_gate_valid: true` and exact owner-ready state qualify. The adapter must
   perform fresh reads during that tick; echoing stored JSON is not observation
   or authentication. Invalid observations withdraw the intent; observer
   exceptions roll back the tick. Withdrawn identities cannot be revived by
   replay; fresh qualification must produce a new final-gate/candidate pin.
4. Immediately before the one initial send, call
   `recheck(batch_id, now, calendar, observer)`. It re-observes every member,
   checks the current window/calendar and persists fresh evidence. Drift refuses
   the entire digest. Payload bytes and identity remain unchanged. This is not
   a send permit to cache or reuse: sender execution must serialize the check
   and mutation and apply its existing provider safety/authority boundaries.
5. Only verified provider readback may call `acknowledge(receipt)` or
   `reconcile(receipt)`. The closed receipt has `batch_id`, `payload_sha256`,
   `status`, `observed_at`, and a nonempty `provider_receipt_id`. Identity and
   digest must match; time cannot precede preparation. Identical acknowledgements
   dedup; conflicting readback and reused provider receipt identities refuse.
   `DELIVERED` completes members. Confirmed `NOT_DELIVERED` can requeue once;
   two confirmed failed attempts exhaust the intent. A newer pending tuple takes
   precedence over an older failed send.

After restart, `pending_batches()` returns unresolved identities **only for
provider readback**, never blind resend. A crash before the actual mutation and
a crash after successful delivery are deliberately indistinguishable without
readback. Unknown outcomes never expire into retry. A refused pre-send recheck
also leaves the claim unresolved until verified non-delivery or delivery is
recorded. There is no operator-authored reset or timeout-based release API.

This outbox represents one logical presentation. The AWF-241 sender owns
OFF/Teams/Slack/both selection, destination/platform receipt namespaces, verified
delivery and per-platform two-attempt ceilings. It must reconcile partial or
ambiguous multi-platform outcomes before recording a logical terminal receipt;
`NOT_DELIVERED` must never mean that only one destination failed. It must not
multiply this module's retry allowance into extra transport attempts. No
reporting OFF, real-channel delivery or unattended-host qualification is claimed
by these offline helpers.

## Requests to other lanes

- In `operating.py`, admit the optional subtree in the existing validator,
  invoke `validate_calendar`, expose its dotted paths to `_path`/set parsing and
  show `effective_calendar` in render output. Reuse the existing lock, journal,
  audit and recovery transaction for mixed updates. Validate the complete staged
  root before publication. Do not rewrite omitted legacy defaults. Preserve
  PR #271's twelve-stream source defaults and retained adoption behavior.
- The authenticated owner instruction ingress must bind calendar changes to
  actual trusted owner evidence. Plain instruction text grants no authority.
  Add restart/interrupted-update and mixed-root rejection integration tests;
  the supplied settings tests cover the pure subtree only.
- In `scripts/generate_contracts.py`, extend the version 1 operating schema with
  this optional closed subtree and strict leaf types. Preserve legacy omission;
  cross-field ordering and timezone existence require semantic validation.
  Regenerate `.agentic/schemas/operating-config.schema.json` centrally.
- In `scripts/generate_examples.py`, add fresh calendar defaults and regenerate
  `OPERATING_CONFIG.yaml`, `.agentic/examples/OPERATING_CONFIG.yaml` and
  `.agentic/templates/source-config/OPERATING_CONFIG.yaml`. Preserve historical
  installed settings. No generated files were changed by this lane.
- AWF-241/continuous-controller integration must instantiate the wrapper on the
  existing protected store, enqueue only owner-ready candidates, wire a trusted
  observer, perform immediate pre-send rechecks, verify readback and reconcile
  unresolved claims. Keep dispatch/review/hourly/EoD paths independent and keep
  default-OFF reporting OFF. A held presentation must not block the controller
  cycle. Supply trusted time and a live final-gate validator, not stored flags.
- AWF-241 must consume `effective_calendar(root)` for EoD timezone/time/day
  scheduling. EoD weekdays default to all seven days. It must independently
  qualify real hourly/EoD delivery and disabled reporting.
- Runtime dependency packaging must provision reviewed IANA data, including in
  the installed interpreter on Windows: add a reviewed version/hash-pinned
  `tzdata` entry to `.agentic/requirements.lock` (already included by
  `requirements.txt`) and qualify installation through the normal bootstrap.
  Extend operating documentation in
  `.agentic/docs/29-OPERATING-CONFIGURATION.md` and regenerate release inventories
  `MANIFEST.json` and `MANIFEST.md` centrally after all lane changes.

Focused offline regression files are `test_awf244_calendar.py`,
`test_awf244_settings.py` and `test_awf244_review_batching.py`. Complete ticket
acceptance still requires settings/controller/transport integration and frozen
current-head independent review, specialists, final gate and owner review.
