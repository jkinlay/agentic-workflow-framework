# Reporting 1.9.6 implementation ports

AWF-241, Tier 3, FULL. This slice is PARTIAL. It supplies offline record,
metric, delivery and platform ports. It is not an enabled service or qualified
Teams/Slack implementation. No real-platform access or channel qualification
has occurred. Existing AWF-55 supplied-record reporting remains supported.

## Records and source authority

`reporting.records.prepare_snapshot(request, accepted_binding, verify_sources=...)`
creates `awf-status-report-record-2`. The closed request includes project,
repository, scope revision, exact PR/head/base, included/excluded keys, report
class, UTC-bounded period and catch-up flag, baseline/forecast revisions, source
field/time/staleness and ticket observations. Report classes are on-demand,
owner-action, backlog-review, implementation-ready, hourly, eod and correction.
Class names describe the requested report, not proof of readiness.

The executable source verifier belongs to the trusted controller host. It must
independently observe and return the entire supplied request under the accepted
binding. Missing verification, missing ticket provenance or stale ticket sources
produce UNKNOWN metrics. Supplied `stale: false` alone establishes no trust.
The host must compute staleness using its accepted freshness policy. Hashes
prove replay consistency only; they do not authenticate observations.

`status_report_store.replay_report` dispatches version 2 to deterministic replay;
version 1 replay and the existing CLI/input/schema remain unchanged. Append
version 2 through the existing `ReportStore.append` event journal. Repeated
identical records are idempotent; identity reuse with different bytes refuses.
The store remains outside worktrees, must exist already, and retains immutable
event triggers. No report initializes or repairs that database.

`readiness_current` checks tuple freshness only. The sole controller must still
observe final gates, owner approval, merge and Jira reconciliation separately.
Head/base/scope changes require a new report and a correction of the prior
notification. Never promote a status message, green CI or draft flag into
readiness or merge authority.

## Metrics and known gaps

The implemented counting rule is implementation leaves: a parent with observed
children is not counted again. Each leaf has one Epic owner or Unassigned;
distinct keys prevent shared dependencies from entering scope totals twice.
Conditional leaves are separate from the required denominator. Cancellation is
not delivery. Priority and size are supplied business categories, independent
of AWF risk tier. Nulls appear as Unknown/Unestimated. Per-Epic totals reconcile
with required leaves; no shirt-size arithmetic or unit conversion is inferred.

Reconciled delivery requires Done and all declared required PR merges plus a
reconciliation timestamp. Comparable cohorts are explicitly named by the source.
Elapsed samples use first WORKER_STARTED through final required PR merge, with
mean, median, IQR, n and timestamp coverage. Reconciliation delay is separate.
Missing start time reduces elapsed coverage without erasing known delivery.
Seconds use exact integer ratios, including fractional seconds. Elapsed time
includes nights and waits; no claim about engineering effort follows.

Accounting and forecasts are explicitly UNAVAILABLE. This slice does not yet
consume the enrolled-agent accounting snapshot or accepted graph/capacity
scenario model. It cannot enable accounting. Caps are never consumption or ETA.
Review/wait component coverage, original estimate comparisons, all-ticket/Epic
date dependency/rollup validation, full detailed reports and milestone scenario
forecasts remain required work. Basic missing/inverted leaf planned dates are
currently planning defects in the snapshot; no Jira field is written.

## Durable delivery port

`reporting.delivery.DeliveryOutbox(controller_store)` consumes the existing
controller store's `transaction()` context manager, yielding a SQLite connection
inside BEGIN IMMEDIATE with commit/rollback. It owns no connection, scheduler,
Jira client or usage ledger. The controller migration must explicitly call
`install_delivery_schema(db)` inside that same database's accepted migration.
Construction itself performs no DDL. Do not put this state in a worker database.

`delivery_intent(report_id, approved_binding, payload)` freezes report/platform,
payload hash and exact approved project/repository/scope/destination/binding
revision/secret reference. The delivery identity excludes destination and binding
revision so changing them cannot reset the report/platform attempt ceiling.
Enqueueing changed bytes under that identity refuses; destination corrections
need operator reconciliation, not a new identity to evade the ceiling.
The controller must derive stable report IDs from accepted event/candidate/period
identities; callers must not assign fresh IDs to retries or duplicate final-review
events. Combining implementation-ready and final-review alerts is pending there.

Each `advance` makes at most one send. It commits an attempt reservation before
calling the provider, then reconciles visibility. Crash reservations consume
attempts conservatively: the durable record cannot tell whether a crashed send
actually began. The reservation means attempted-or-possibly-attempted, never
proof of an actual platform call. There is no lease expiry or blind takeover.
Unknown and IN_FLIGHT deliveries are reconciled before any new send. A fencing
token rejects stale observer results; parallel callers cannot reserve the same
attempt. History retains reservations and safe observations. Intent update,
delete and replacement are blocked to preserve identity/attempt history.

Channel evidence is a closed envelope from a trusted executable provider:

- `delivery_id`, `platform`, `destination`, `payload_sha256`, and the complete
  ordered `attempt_ids` must match the intent and all current reservations.
- `evidence_kind` must be CHANNEL_READBACK. Supply an immutable `observation_id`
  and RFC3339 `observed_at` for independently retained readback evidence.
- VISIBLE additionally needs a stable platform `message_id` and observation of
  the intended payload in the exact channel. HTTP 200/202, workflow acceptance,
  local JSONL/file receipts and response bodies establish no channel delivery.
- ABSENT additionally requires `terminal: true`: every reserved request has
  definitively finished or been fenced at the provider, and no delayed effect
  can appear. A missing post in an eventually consistent listing is insufficient.
  If the selected transport cannot prove terminal absence, return UNKNOWN.

Only terminal absence permits the second send. Two reservations is the permanent
ceiling per report/platform, across restart and binding changes. Terminal absence
after attempt two becomes FAILED; unresolved ambiguity stays UNKNOWN. The host
must surface either state and must not create a replacement intent to repost.
Teams/Slack are independent rows. No failed destination should suppress report
generation or delivery to the other destination.

The provider's evidence collector and its terminal-absence guarantee still need
independent review and real-platform qualification. Evidence-kind strings are
not attestations. Protect this database with the existing controller's filesystem,
integrity, backup and host-ownership controls.

## Adapters, presentation and operations

`teams_reporting_adapter.ReportingAdapter` and the Slack counterpart accept only
an injected transport; neither contains an SDK/network client. Default mode is
off; other modes are teams/slack/both. The host supplies authenticated effective
settings, approved binding and a sender-side secret resolver. Binding equality,
mode and payload size checks precede secret resolution. There is no cross-project
or channel-name fallback. Real bindings stay in project-owned protected state.

Transport methods are `verify_destination(binding, secret)`,
`send(intent, attempt_id, secret)` and `reconcile(intent, attempts, secret)`.
Verify the stable destination, workspace/tenant context, sender identity and
membership before any send/readback. The resolver runs per operation, allowing
credential rotation. Use a least-permission sender with only approved channel
send/readback capabilities. Secret values never enter outgoing report payloads,
returned receipts or retained provider errors; errors use fixed diagnostic codes
and suppress provider exception chains. Do not log transport arguments or locals.

`reporting.render.render_channel` provides compact Markdown/plain text fallbacks,
not a qualified Adaptive Card or platform formatting claim. Required provenance,
candidate, owner action and explicit unknowns survive rendering. Oversize output
refuses without truncation; limits are conservative application UTF-8 byte limits
(Slack 12000, Teams 20000), not asserted API limits. Detail URLs require HTTPS and
a trusted existence/access callback bound to the report scope. Supplied markup
and mentions are suppressed. Full specified per-type layouts and detailed report
hosting remain unfinished; never silently advertise unsupported sections.

Before live onboarding the owner/admin must approve the transport, exact project
and destination, sender/readback permissions, secret references, report location,
human service owner, credential administrator and recovery contact. Record actual
supervised visibility/readback on both platforms for the exact candidate.
No webhook/configuration file or fake-provider success substitutes for that.

To disable, stop new reporting admission through the host's authenticated mode
setting; do not enable inbound chat, budgeting, dispatch or Jira writes. Reconcile
existing UNKNOWN/in-flight attempts before credential revocation or recovery.
Rotation changes secret values behind references, never retained intent bytes.
Liveness monitoring belongs to the sole controller: expose last report period,
last actual readback, pending age and UNKNOWN/FAILED counts independently for
each platform. After downtime reconcile uncertainty first; report original missed
periods as catch-up with current freshness, without replaying stale owner alerts.
Use only an independently approved fallback for persistent failure.

## Requests to other lanes

- `.agentic/lib/agentic/continuous_controller.py` (lane B/integration writer):
  register the delivery schema in the existing controller database migration;
  instantiate DeliveryOutbox using the existing transaction port; persist stable
  event/period report IDs and source snapshots; consume enabled settings; call
  advance only for admitted enabled destinations; surface UNKNOWN/FAILED and
  retry readiness without resetting identities. Generate reports independently
  of one blocked delivery. Deduplicate final-review alerts and emit corrections
  after candidate/scope changes. Keep the existing sole scheduler and authority.
- `.agentic/adapters/reference_controller_adapter.py` (lane B): retain its local
  JSONL handoff as LOCAL_RECEIPT only. Route channel reporting through the above
  reporting provider port. Never map local readback to channel DELIVERED.
- `.agentic/lib/agentic/operating.py` and `.agentic/lib/agentic/cli.py` (assigned
  settings writer): authenticate owner UI settings; persist/show off/teams/slack/
  both and exact project-owned destination bindings using existing audit/recovery;
  preserve explicit prior settings; reporting changes must not enable any other
  subsystem. No authentication is inferred from free-text instructions.
- AWF-244 controller integration: consume `operating_calendar` from existing
  operating settings and lane C's effective/validated calendar helpers. Use its
  timezone, eod.time and explicit eod.weekdays; preserve continuous work and hourly
  reports 24/7 regardless of review_batching. Derive UTC period identities through
  DST folds/gaps and label missed periods honestly. Do not add another scheduler.
  The EoD weekday choice remains an owner decision; do not silently choose it.
- AWF-242: provide the existing ledger's verified, explicitly enrolled AWF-agent
  accounting projection with completeness/unknown charges and overhead allocation.
  Exclude the main controller/chat and independent agents. Reporting consumes it
  read-only; it cannot enable accounting or create another ledger.
- `scripts/generate_contracts.py`: add explicit version-2 record/input and delivery
  envelopes and reporting settings to the appropriate generator-owned contracts,
  preserving version 1. Central writer must regenerate the corresponding
  `.agentic/schemas/` outputs. None was regenerated in this slice.
- `scripts/generate_examples.py`, project/source-config defaults and operating
  schema writers: generate default-OFF reporting examples/settings after settings
  integration, preserving adopter-owned bindings/choices. Release lane must
  regenerate `MANIFEST.json` and `MANIFEST.md` for new files only after integration.

## Closure still required

Finish metrics/forecasts and all specified report bodies; wire events/settings/
calendar/controller migration; qualify candidate corrections, dedup, off/mode
persistence, DST/weekend/EoD continuity, rate limits and provider terminality;
perform real Slack and Teams channel qualification and separately authorized
project pilot acceptance. Run centrally authorized preservation/release checks,
current-head independent critic and triggered security/operations/data/public
interface reviews. FULL closure and release readiness are not established here.
