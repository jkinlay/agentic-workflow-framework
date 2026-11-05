# Reporting 1.9.6: operator contract

AWF-241 is Tier 3/FULL. Stage 3 supplies offline settings/controller wiring,
not real-platform qualification, authenticated UI control, release acceptance or
activation. Reporting defaults OFF. No service was contacted.

## Settings and admission

Optional `reporting` has `mode`: `off`, `teams`, `slack`, `both`; and `bindings`,
a maximum of one approved destination per platform. Each binding contains
`project`, `repository`, `scope_revision`, `platform`, `destination`, `revision`,
`secret_ref`. Project/repository match accepted governance; scope_revision matches
the controller's accepted scope_sha256. Enabling requires every selected binding.
Missing, duplicate, foreign or inconsistent bindings refuse before secret resolution.
Secret references use `env:`, `vault:` or `secret:`; never supply credential values.

Set `reporting.mode` and JSON `reporting.bindings` through operating settings.
Trusted infrastructure injects `ReportingSettingsRegistry` as `reporting_registry`;
its `observe_owner_direction(project, prior_hash, changes)` authenticates the owner
and exact destination approval independently. Instructions authenticate nobody.
Admission follows complete validation under the existing operating lock; immutable
observations survive journal/audit/pinned recovery. Standalone CLI mutations refuse.
Legacy omission resolves OFF without rewriting bytes. No Epic overrides exist.
Reporting enables no chat, accounting, dispatch, Jira writes, merge or adapters.

## Sole controller and retained evidence

The existing `ContinuousControllerStore` migration calls `install_delivery_schema`;
construction of `DeliveryOutbox` performs no DDL. Protected paths, integrity checks,
full-synchronous transactions and ownership remain required.
`reporting_cycle` atomically retains event state, cadence cursors, immutable report
snapshots/source records and stable per-platform intents through that store's
serialized BEGIN IMMEDIATE transaction and existing `controller_outbox`. Rendering
failure rolls everything back. Status digests never consume reporting rows.
There is no second scheduler, usage ledger or Jira client.

`production_controller_cycle` resolves effective settings each cycle. Hourly reports
run on the hour 24/7, including weekends, idle, blocked and paused states.
AWF-244's effective calendar supplies timezone and EoD time/days: every day at
17:00 Europe/London by default. Hourly reporting continues afterward. UTC identities
distinguish repeated DST hours; missing IANA data refuses. Downtime uses contiguous
bounded calendar windows and labelled CATCH-UP periods with current observations;
disabled periods are excluded. The host must keep the sole controller running.
Generation precedes sending; failed destinations cannot starve other reports or dispatch.

Reviewed adapter code injects `reporting_providers`, keyed by platform. Configuration
cannot register executable providers. `observe_reporting_sources(request)` returns
exactly binding, observed_at, records, owner_actions and authentication/evidence_id;
`verify_reporting_sources(authentication, request)` independently returns VERIFIED,
the evidence_id and identical snapshot bytes excluding authentication. Binding/time
must match. Unavailable actions never become an empty/resolved set.
`verify_reporting_link` verifies source/detail links. Current gates, retained streams,
authenticated graph/capacity and coverage accompany reports; unavailable observations
remain explicit. Source-supplied readiness candidates produce immediate corrections;
final-review notices are presented only with the batch.

Lane C's `deliver_review_batch`/receipt-only `observe_review_delivery` seam uses
`ReviewReportingPort`. Candidate/window/expiry checks repeat before sends and retries.
Owner-action alerts remain immediate and deduplicated. Both selected destinations
must have channel evidence before acknowledging a batch. Partial/exhausted delivery
never returns NOT_DELIVERED or multiplies retry allowances.

## Delivery and operations

`delivery_intent` freezes report, scope, destination, revision, secret reference and
payload. Its report/platform identity excludes destination/configuration: rotation
cannot reset attempts or retarget historical intents. Each platform retains at most
two attempted-or-possibly-attempted reservations across restart. Reconcile UNKNOWN
before retry; only fenced terminal absence/rejection permits one retry. Two failures
remain FAILED; ambiguity remains UNKNOWN. No timeout takeover or blind repost exists.

HTTP/API acceptance, including 202, is insufficient. Verify sender, channel, complete
content/correlation and message/readback identity through the reporting port.
The reference adapter's compatible `status: DELIVERED` means local JSONL completion
only: `evidence_kind: LOCAL_RECEIPT`, `channel_delivery: false`. It never proves channel delivery.

Platform adapters resolve secrets per operation after admission. Values never enter
payloads, receipts, diagnostics or logs. Transports use TLS, fixed origins, bounded
timeouts/responses/pages, no redirects or automatic retries. Missing listings remain
UNKNOWN. Name sender owner, credential administrator and recovery contact; use least
permissions. Disable authenticated settings, reconcile sends, rotate/revoke credentials;
never replace intents. Oversize bodies refuse without silent truncation; detailed
links require verified hosting. Preserve independent per-platform state and receipts.

## Accounting, CLI and qualification

Read-only `attributed_accounting` observes lane A's canonical routing ledger without
initialization, enrollment or settlement. Only explicitly enrolled AWF sub-agents
count, including review/retry/failure usage; controller/main chat and independent/ULTRA
agents are excluded. OFF/unknown is UNAVAILABLE, never zero. Unknown charges and
coverage survive; aggregate tokens never invent component splits or forecasts.

Operating show exposes effective settings. `workflow.py reporting-status --state
STATE --binding BINDING` reads delivery states, retained snapshots and coverage
without migration or provider calls. Supply accepted project/repository/scope identity.
Inspect pending age, liveness, UNKNOWN and FAILED before recovery.

Fake-provider tests establish no real Slack/Teams qualification or authenticated UI
control. Supervised platform evidence, independent current-head review, specialists,
final gate and owner review remain required for FULL closure.
