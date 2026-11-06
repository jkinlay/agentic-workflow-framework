# Reporting 1.9.6: operator contract

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

Infrastructure supplies `ReportingSettingsRegistry`; independent
`observe_owner_direction` authenticates settings/destinations. Operating locks and
journal recovery persist admission. Configuration cannot register executable ports.

The CLI uses the reference factory's `configure_reporting` port. Reviewed
`reporting.slack`/`reporting.teams` configuration supplies `context` and `sender`
(workspace/user or tenant/delegated-user). Slack uses channel IDs; Teams uses
`team-ID/channel-ID`. Missing transport bindings refuse before credentials.

The reference resolver supports `env:VARIABLE` after admission; other references
require a host resolver and refuse with `REPORT_SECRET_RESOLVER_REQUIRED`. Values never belong
in configuration, prompts, payloads, receipts, diagnostics or logs.

## Sources, periods and priority

Reference source ports independently re-read digest-bound controller evidence.
Optional `reporting_sources` binds scope and up to 100 PR numbers; unenrolled PR
metrics stay unavailable. Inventory supplies no history, estimates, Jira Done or
review authority. Source verifiers bind identical snapshot, evidence identity and
time. Links require independent verification.

AWF-244 supplies timezone/EoD calendar: hourly reporting continues 24/7 through
weekends, idle/blocked/paused states and EoD, default 17:00 Europe/London. UTC
identities distinguish DST folds. Keep the sole controller running.

After downtime, retain every period and its source snapshot. Send one labelled
hourly CATCH-UP summary per platform naming hourly boundaries and their span;
send at most two separate EoDs, listing older daily dates in the summary.
Generation processes at most seven calendar days per cycle; the durable cursor
retains further work. Archived periods have explicit `LISTED_IN_CATCH_UP` policy.
Owner actions and safety/readiness corrections drain before periodic backlog.
First EoD computes today independently. Missing prior coverage stays unknown.
Historical views exclude later merges; invalid/future merge times retain an
unavailable daily view while other periods progress. Other rendering errors roll back.

## Delivery, recovery and rollback

One controller owns snapshots, delivery intents and transactions. Additive migration
adds `controller_reports`; reporting never places new payloads in the legacy status
outbox. Pre-release REPORT rows relocate byte-for-byte, preserving their real kind
and delivery history. Snapshot/intent immutability and protected SQLite checks remain.

Dispatch decisions and launches complete before delivery. Each destination has
its own non-daemon thread; all threads join before the cycle/CLI returns. There
is no background queue, scheduler or surviving send. A shared cycle budget,
including review delivery/retries, admits four advances, at most one historical
reconciliation and 32 HTTP calls per destination. Admission stops after 30 seconds;
every HTTP request has an explicit timeout of at most 15 seconds. The delivery
stage network maximum is therefore **45 seconds**, with destinations concurrent,
not additive. Trusted transport ports must honour the timeout contract. Local
storage/host observation time is separate. Alerts precede periodic work; newest
hourly reports precede backlog. Historical UNKNOWN rows rotate by observation
count and remain durably recorded without monopolizing fresh sends.

Destination verification, payload and HTTP-budget admission precede reservation.
Readiness/candidate/window checks repeat at the POST boundary, including retries.
Only an actual send start reserves an attempt. Timeouts are ambiguous and enter
reconciliation; they never authorize a blind resend. Terminal rejections, however
late, are persisted before return. Only terminal absence/rejection permits one
retry: two sends maximum per report/platform, across restart/concurrency. Exhaustion
stays FAILED/UNKNOWN. Missing listings are UNKNOWN; API acceptance, including 202,
proves no channel landing.

Before persistence, reporting withholds credential-like source strings/keys using
the existing host retention detector. Withholding records contain a fixed marker,
never values/excerpts. Read-only projections apply the same guard. Jira
reconciliation coverage is separate from an observed count: unsupported/partial
evidence displays UNAVAILABLE, while complete observed empty coverage permits zero.

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
Both destinations need channel evidence for review acknowledgement. Supervised channel
qualification, independent review, specialists, final gate and owner review remain.
