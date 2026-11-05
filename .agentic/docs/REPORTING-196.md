# Reporting 1.9.6: operator contract

AWF-241 is Tier 3/FULL. Offline tests establish wiring, not supervised platform
qualification, authenticated UI control, release acceptance or activation.

## Settings and construction

Reporting defaults OFF. Omitted/default settings cause no reporting transaction,
cursor, metadata, byte changes or secret reads. Explicitly disabling an existing
bound session records suspension so disabled periods are excluded on re-enable.
Reporting grants no chat, accounting, dispatch, Jira, merge or activation authority.

Operating `reporting.mode` selects `off`, `teams`, `slack`, `both`.
`reporting.bindings` contains at most one destination per platform, with `project`,
`repository`, `scope_revision`, `platform`, `destination`, `revision`, `secret_ref`.
Project/repository/scope must match accepted controller identity. Missing, duplicate
or foreign bindings refuse before credential access. Rotation cannot retarget old
intents or reset attempts.

Authenticated infrastructure supplies `ReportingSettingsRegistry` through
`reporting_registry`; its independent `observe_owner_direction` authenticates the
exact settings and destination approval. Validation, operating locks, immutable
observations and journal recovery persist admission. Standalone mutations refuse;
configuration cannot register executable providers.

The CLI passes the reference factory's `configure_reporting(operating,binding)`
port into the production cycle. Reviewed adapter configuration supplies
`reporting.slack` and/or `reporting.teams`, each containing `context` and `sender`:
approved workspace/user or tenant/delegated-user identities. Slack destinations
are channel IDs; Teams destinations are `team-ID/channel-ID`. Missing transport
bindings refuse with `REPORT_TRANSPORT_BINDING_REQUIRED`. The factory constructs
the shipped adapters and Slack Web API / Microsoft Graph transports each cycle.

The reference resolver supports `env:VARIABLE` only, resolving per operation after
admission. Other hosts may register resolvers for `vault:`/`secret:`; the reference
factory refuses these with `REPORT_SECRET_RESOLVER_REQUIRED`. Values never belong
in configuration, prompts, payloads, receipts, diagnostics or logs.

## Sources, periods and priority

Reference source ports retain and independently re-read digest-bound evidence
from the current authenticated controller inventory. Optional adapter
`reporting_sources` contains exact `binding` and `pull_requests` (up to 100 approved
PR numbers); only these repository PR observations enter reporting. Without this
scope enrollment, PR metrics are unavailable. Inventory provides no project
history, estimates, Jira Done or review authority; unsupported values stay unknown.
`observe_reporting_sources`/`verify_reporting_sources` bind identical snapshot,
evidence ID, identity and observation time. Links require independent verification.

AWF-244 supplies timezone/EoD calendar: hourly reporting continues 24/7 through
weekends, idle/blocked/paused states and EoD, default 17:00 Europe/London. UTC
identities distinguish DST folds. The host must keep the sole controller running.

After downtime, retain every period and its source snapshot. Send one labelled
hourly CATCH-UP summary per platform naming hourly boundaries and their span;
send at most two separate EoDs, listing older daily dates in the summary.
Generation processes at most seven calendar days per cycle; the durable cursor
retains further work. Archived periods have explicit `LISTED_IN_CATCH_UP` policy.
Owner actions and safety/readiness corrections drain before periodic backlog.
First EoD computes today independently; absent prior-report coverage is unknown.
Later valid merges are excluded from historical views. Invalid/future observation
timestamps fail the daily projection explicitly, retain an unavailable record and
permit other periods and cursor progress. Other rendering failures roll back.

## Delivery, recovery and rollback

One controller owns snapshots, delivery intents and transactions. Additive migration
adds `controller_reports`; reporting never places new payloads in the legacy status
outbox. Pre-release REPORT rows relocate byte-for-byte, preserving their real kind
and delivery history. Snapshot/intent immutability and protected SQLite checks remain.

The controller owns a small daemon worker per database/platform, with no queue,
service or scheduler. A shared cycle budget permits four advances and 250 ms of
network waiting per destination, including review delivery. A blocked platform
cannot hold the other destination or dispatch. An outstanding operation cannot be
taken over. Late readback evidence is consumed for the same intent/attempts.
Interrupted sends remain UNKNOWN and reconcile before any retry. Restart preserves
reservations. Only fenced terminal absence/rejection permits one retry: two sends
maximum per report/platform. Exhaustion stays FAILED/UNKNOWN. Missing API listings
are UNKNOWN; HTTP acceptance, including 202, proves no channel landing.

Verify channel, sender, full content/correlation and stable message/readback identity.
Local JSONL `DELIVERED` receipts say `LOCAL_RECEIPT`, `channel_delivery:false`.
Transports enforce TLS/origin, response/page/timeout limits, no redirects or automatic
retries. Oversize bodies refuse; detailed links require verified hosting.

Before rollback: disable reporting, quiesce the controller/workers, reconcile sends,
back up state and run this version's migration. Verify no REPORT rows remain in
`controller_outbox`, then start the merge-base controller. It consumes only legacy
status digests and leaves reporting tables intact. Restore current code to resume
reconciliation; never delete intents or reset attempts. Rollback is tested against
the actual merge-base controller source.

Name sender owner, credential administrator and recovery contact; use least
permissions. Disable admission before rotation/revocation. Inspect pending age,
liveness, UNKNOWN/FAILED via read-only `workflow.py reporting-status --state STATE
--binding BINDING`, without migration/provider calls. Accounting reads only the
existing attributed AWF-sub-agent ledger; OFF/unknown is unavailable, never zero.
Both destinations need channel evidence before review-batch acknowledgement;
candidate/window/expiry checks repeat before sends/retries. Supervised channel
qualification, independent review, specialists, final gate and owner review remain.
