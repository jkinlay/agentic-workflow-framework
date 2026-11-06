# Reporting 1.9.6: operator contract

## Construction

Reporting defaults OFF; suspension excludes disabled periods.
Reports grant no chat/accounting/dispatch/Jira/merge/activation authority.

Authenticated ReportingSettingsRegistry/observe_owner_direction persists
off/teams/slack/both and project/repository/scope/platform/destination/revision/
secret_ref bindings. Missing/duplicate/foreign bindings refuse before credentials;
rotation cannot retarget intents/reset attempts.

configure_reporting requires reviewed context/sender and channel IDs.
env:VARIABLE uses the reference resolver; other references need a host resolver.
Configuration cannot register ports. Sender owners manage permissions, liveness,
recovery and rotation.

## Sources and periods

Ports reread digest-bound evidence; reporting_sources binds scope and at most 100 PRs.
Unknown details/metrics remain unavailable; observed work stays visible.
Accounting reads the actual setting and attributed ledger; reporting never enables
it. Measured runs supply completed-ticket means/Epic totals; forecasts require
canonical observation IDs. Unobserved model/input/output stays unknown;
OFF/unknown stays unavailable.

AWF-244 supplies timezone/calendar, default EoD 17:00 Europe/London. Hourly reports
continue 24/7; UTC identities distinguish DST folds. Downtime retains snapshots,
sending labelled hourly CATCH-UP and at most two EoDs. Generation advances seven
days/cycle. EoD excludes later merges. Invalid ticket times/order or forecast
inputs make affected figures unavailable without suppressing reports/alerts.
Unknown inventory states are unclassified, not known remaining work; unobserved
planning dates cannot establish defects. Elapsed coverage uses completed cohorts.
Displayed periods use local dates/timezones; elapsed figures use hours.

Limits remain 12,000 Slack / 20,000 Teams UTF-8 bytes. Compact output prioritizes
owner decisions, counts omissions, and retains complete hashed bodies through
reporting-status/detailed artifacts. Identity/unverified-link failures refuse
atomically. Resolved actions read resolved.

## Delivery and deadlines

One controller owns snapshots/intents/transactions; migration preserves REPORT/SQLite
protections. Dispatch precedes reporting.

Sources have 30 seconds via source_timeout(); expiry retains PARTIAL coverage,
unavailable totals and separate delivery admission. One delivered batch/cycle
is reobserved via durable rotation. Withdrawal corrects each delivery once;
delivered corrections seed review-port restorations, preserving attempts and
rechecking before POST.

Destination threads admit alerts, hourly, EoD, then review: four advances,
one historical reconciliation and 32 HTTP calls/platform per 30-second admission
deadline. Review reserves one advance. Alternate review turns limit periodic
admission to ten seconds; joins end five seconds before the common deadline.
Phases share counters/gates; cancellation preserves reserved review admission.
Overflow/deferred classes rotate after alerts. New/historical reviews alternate
across restarts; batch/restoration/readback rotate. Batches contain one candidate;
restoration destinations share one concurrent-drain turn. Observers cap at 15
seconds, checking before/after calls. Candidate/window checks precede reservation/POST;
observations occur outside SQLite write transactions.

HTTP has a monotonic 15-second maximum, DNS through body; expiry closes sockets.
Direct TLS ignores proxies; redirects/retries are disabled. Delivery, cancellation
and joins remain bounded by **45 seconds**, sources plus delivery by **75 seconds**,
excluding storage/host time. Cancellation fences
reservation/transport entry; workers join within the bound. Noncooperative workers
are durably recorded REPORT_DESTINATION_WORKER_NOT_QUIESCENT with revoked tokens.

Verification, payload and HTTP-budget admission precede reservation. Candidate/window
checks repeat immediately before reservation/POST, including retries; remaining HTTP
time is recomputed afterward. API acceptance, including Graph 201/chatMessage, proves
no landing. Timeouts require reconciliation; terminal rejection/absence permits one
retry, two sends maximum across restart/concurrency. Missing listings remain UNKNOWN.

HTTP 429 persists Retry-After seconds (missing/invalid: 60; Slack ratelimited: 60).
controller_report_cooldown covers reports, sender/destination changes and restarts:
Slack workspace/method; Graph tenant resources/methods. Verification, pagination
and readback check cooldown before admission. THROTTLED preserves SEND/RECONCILE;
read retries consume no send attempt. Two actual throttled POSTs become FAILED with
TWO_SEND_ATTEMPTS_EXHAUSTED.

## Retention and recovery

Credential filtering checks grammar/resolved values throughout retention/delivery.
Enqueue validates bindings/intent fields before writing; only references persist.
Custom resolvers accompany admission.

Confirmation requires sender, destination/thread, final reserved delivery/hash
marker and normalized-content hash. Embedded markers are quoted. Invalid IDs/unknown
normalization remain UNKNOWN. Pagination permits ten budgeted pages;
Graph links retain origin/resource.
JSONL is LOCAL_RECEIPT, channel_delivery:false. Review acknowledgement requires both
destinations' channel evidence; review/merge authority remains separate.

A CLAIMED batch transfers to REPORTING after exact-payload retention in the outbox.
REPORTING permits newer batches. Crash recovery creates missing reports under
their original batch identities, retaining attempts.
DELIVERED/FAILED are terminal destination outcomes. All-terminal batches with a
failure close FAILED with a destination outcome record; members become EXHAUSTED,
never re-enqueued. Success on one platform remains delivered when the other fails.
Reports list delivery attention; exhausted review delivery generates
one deduplicated owner-action alert. Failed alerts appear in periodic diagnostics
without recursive alerts. Compact diagnostics list four identities and the total;
reporting-status retains every identity.

PENDING/ABSENT retry through rotation/fresh gates; THROTTLED waits for cooldown.
IN_FLIGHT/UNKNOWN reconcile without reposts. Missing gates, changed calendar or
observation-budget refusal retain visible deferred evidence; restore evidence/calendar
or present a newly verified tuple.
Restoration readback proceeds after withdrawal/cutoff; sends require fresh gates.
Landed restorations seed withdrawal corrections. Correction/restoration chains
retain original channel roots separately from reply receipts across restarts.
Pending schedules are recomputed after settings recovery. WITHDRAWN/EXHAUSTED and
terminal batches retain history and cannot revive by replay. An unavailable
provider may require owner repair, but cannot block another destination/candidate.

Recovery: disable, quiesce, reconcile, back up before migration. Never delete
intents/reset attempts. Inspect: read-only workflow.py reporting-status
--state STATE --binding BINDING. Supervised platform acceptance remains separate.
