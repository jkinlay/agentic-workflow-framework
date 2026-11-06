# Reporting 1.9.6: operator contract

## Settings and construction

Reporting defaults OFF: no writes or secret reads. Disabling records suspension
and excludes disabled periods. Reporting grants no chat, accounting, dispatch,
Jira, merge or activation authority.

Authenticated ReportingSettingsRegistry/observe_owner_direction persists
reporting.mode: off/teams/slack/both. Each platform needs an explicit binding:
project, repository, scope_revision, platform, destination, revision, secret_ref.
Missing, duplicate or foreign bindings refuse before credential access. Rotation
cannot retarget old intents, reset attempts or authorize new channels.

configure_reporting requires reviewed Slack workspace/user or Teams
tenant/delegated-user context/sender bindings. Destinations are Slack channel IDs
or Teams team-ID/channel-ID. env:VARIABLE uses the reference resolver; other
references require a host resolver. Configuration cannot register executable ports.
Sender owners manage least permissions, liveness and recovery; disable before
rotation/revocation. Values never belong in configuration, prompts or output.

## Sources and periods

Reference ports reread digest-bound evidence. reporting_sources binds scope and
at most 100 PRs. Inventory grants no history, estimates, Done or review authority.
Unknown ticket details/unenrolled metrics remain unavailable; observed work stays
visible. Verifiers bind snapshots, evidence, timestamps and detail links.
Accounting reads only the existing attributed ledger; OFF/unknown is unavailable.

AWF-244 supplies configurable timezone/EoD calendar, default 17:00 Europe/London.
Hourly reports continue 24/7, including weekends, idle/blocked/paused work and EoD.
UTC identities distinguish DST folds. Downtime retains every snapshot, sending one
labelled hourly CATCH-UP with boundaries/span and older daily dates, plus at most
two EoDs. Generation processes seven days/cycle; the cursor retains further work.
Archives carry LISTED_IN_CATCH_UP. EoD computes today independently, excludes later
merges and labels invalid/future merge times unavailable. Rendering errors roll back.

## Delivery and deadlines

One controller owns snapshots/intents/transactions. Migration adds controller_reports,
relocates pre-release REPORT rows byte-for-byte and preserves SQLite protections.
Reports never enter the status outbox. Dispatch precedes collection/delivery.

Sources have 30 seconds; trusted ports use source_timeout(), including pagination.
Expiry retains verified inventory/PARTIAL coverage; PR totals remain unavailable.
Failure reports STALE_OR_UNAVAILABLE. Sources do not spend delivery admission.
Previously delivered reviews are reobserved; withdrawal corrects each original thread once.

Independent non-daemon destination threads prioritize alerts, then newest hourly,
then history rotating by observation count. A budget shared with review delivery
allows four advances, one historical reconciliation and 32 HTTP calls/platform.
Admission ends after 30 seconds. Candidate rechecks consume this same deadline:
observation ports use the remaining timeout, capped at 15 seconds; checks run
before/after each observation, outside SQLite write transactions.

Each HTTP request has a monotonic 15-second maximum covering DNS through body;
expiry closes sockets. Direct TLS ignores ambient proxies; redirects/retries are
disabled; timed-out DNS cannot later connect/send. The concurrent delivery stage,
including cancellation and joins, remains bounded by **45 seconds**, source plus
delivery by **75 seconds**, excluding local storage/host time. Trusted injected
ports must honor deadlines. At admission expiry, cancellation revokes reservation
and transport entry under each destination's send gate; workers join within the
remaining bound. A noncooperative worker is durably recorded in controller_meta as
REPORT_DESTINATION_WORKER_NOT_QUIESCENT with cancellation_revoked; its token bars
further sends even after its observation returns. Slow delivery delays the next cycle.

Verification, payload and HTTP-budget admission precede reservation. Candidate/window
checks repeat immediately before reservation/POST, including retries; remaining HTTP
time is recomputed afterward. API acceptance, including Graph 201/chatMessage, proves
no landing. Timeouts require reconciliation; terminal rejection/absence permits one
retry, two sends maximum across restart/concurrency. Missing listings remain UNKNOWN.

HTTP 429 persists Retry-After seconds (missing/invalid: 60; Slack ratelimited: 60).
controller_report_cooldown covers reports, sender/destination changes and restarts:
Slack workspace/method; Graph all tenant resources/methods. Verification, pagination
and readback check cooldown before admission. THROTTLED preserves SEND/RECONCILE;
read retries consume no send attempt. Two actual throttled POSTs become FAILED with
TWO_SEND_ATTEMPTS_EXHAUSTED.

## Retention and recovery

The shared credential filter checks grammar and resolved values throughout retention
and delivery. Both public enqueue paths validate the binding, transiently resolve its
credential and inspect every intent field before hashing/writing. Exact matches refuse;
only references are retained. Custom resolvers must accompany persistence admission.

Confirmation requires sender, destination/thread, exact final reserved delivery/hash
marker and normalized-content hash. Direct payloads containing AWF delivery: refuse;
composition quotes it. Slack supports bounded ASCII timestamp IDs, three escaping
entities and explicit SLACK_EMOJI aliases; Teams IDs are bounded ASCII decimals and
HTML normalization preserves links. Unknown conversions/invalid IDs remain UNKNOWN.
Pagination permits ten pages within HTTP budgets; Graph links retain origin/resource.
JSONL is LOCAL_RECEIPT, channel_delivery:false. Both destinations need channel evidence
for review acknowledgement; specialists, final gate and owner review remain mandatory.

Rollback: disable, quiesce, reconcile, back up, migrate and verify no REPORT remains
in controller_outbox. The merge-base controller preserves reports. Never delete
intents/reset attempts. Inspect UNKNOWN/FAILED with read-only workflow.py
reporting-status --state STATE --binding BINDING. Supervised platform qualification
and project acceptance remain separate.
