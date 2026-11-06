# Reporting 1.9.6: operator contract

## Construction

Reporting defaults OFF; disabling records suspension and excludes disabled periods.
Reporting grants no chat, accounting, dispatch,
Jira, merge or activation authority.

Authenticated ReportingSettingsRegistry/observe_owner_direction persists
off/teams/slack/both and explicit project/repository/scope/platform/destination/
revision/secret_ref bindings. Missing, duplicate or foreign bindings refuse before
credential access. Rotation cannot retarget intents or reset attempts.

configure_reporting requires reviewed context/sender bindings and Slack channel
or Teams team/channel IDs. env:VARIABLE uses the reference resolver; other
references need a host resolver. Configuration cannot register ports. Sender
owners manage least permissions, liveness, recovery and credential rotation.

## Sources and periods

Ports reread digest-bound evidence; reporting_sources binds scope and at most 100 PRs.
Unknown details/metrics remain unavailable; observed work stays visible.
Accounting reads the attributed ledger; OFF/unknown is unavailable.

AWF-244 supplies timezone/calendar, default EoD 17:00 Europe/London. Hourly reports
continue 24/7; UTC identities distinguish DST folds. Downtime retains snapshots,
sending labelled hourly CATCH-UP and at most two EoDs. Generation advances seven
days/cycle. EoD excludes later merges; invalid times are unavailable. Rendering
errors roll back.

## Delivery and deadlines

One controller owns snapshots/intents/transactions; migration preserves REPORT
bytes/SQLite protections. Dispatch precedes reporting.

Sources have 30 seconds via source_timeout(). Expiry retains PARTIAL coverage;
unknown totals remain unavailable. Sources cannot spend delivery admission.
One delivered batch/cycle is reobserved with a durable rotating cursor. Withdrawal
corrects each delivery once; delivered corrections seed restorations through the
review port, preserving attempts and rechecking before each POST.

Destination threads admit alerts, hourly, EoD, then review.
Four advances, one historical reconciliation and 32 HTTP
calls/platform share a 30-second admission deadline. One advance is protected for
review. Alternate review turns restrict periodic admission to ten seconds,
with joins ending five seconds before the common admission deadline. Phases share
counters/gates; phase cancellation cannot revoke the reserved review allowance.
Overflow classes rotate; deferred classes receive the next turn after alerts.
New candidate discovery and historical review priority
alternate across restarts; batch/restoration work and readback also rotate. New
batches contain one candidate. Both restoration destinations share one turn.
Restorations use the same concurrent destination drain as ordinary reports.
Observers cap at 15 seconds, checking deadlines before/after calls. Exact
candidate/window checks immediately precede reservation/POST; observations run outside SQLite
write transactions.

HTTP has a monotonic 15-second maximum, DNS through body; expiry closes sockets.
Direct TLS ignores proxies; redirects/retries are disabled. Delivery, cancellation
and joins remain bounded by **45 seconds**, sources plus delivery by **75 seconds**,
excluding storage/host time. Ports must honor deadlines. Cancellation fences
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

The credential filter checks grammar/resolved values throughout retention/delivery.
Enqueue validates bindings and transiently checks every intent field before writing;
only references persist. Custom resolvers must accompany admission.

Confirmation requires sender, destination/thread, final reserved delivery/hash
marker and normalized-content hash. Embedded markers are quoted. Invalid IDs/unknown
normalization remain UNKNOWN. Pagination permits ten budgeted pages;
Graph links retain origin/resource.
JSONL is LOCAL_RECEIPT, channel_delivery:false. Both destinations need channel evidence
for successful review acknowledgement; review/merge authority remains separate.

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

PENDING/ABSENT retry through rotation and fresh gates; THROTTLED waits for its
durable cooldown. IN_FLIGHT/UNKNOWN reconcile without blind reposts. Missing gate,
changed calendar or observation-budget refusal retains owner-visible deferred
evidence; restore valid evidence/calendar or present a newly verified tuple.
Restoration readback proceeds after withdrawal/cutoff; sends require fresh gates.
Landed restorations seed withdrawal corrections. Correction/restoration chains
retain original channel roots separately from reply receipts across restarts.
Pending schedules are recomputed after settings recovery. WITHDRAWN/EXHAUSTED and
terminal batches retain history and cannot revive by replay. An unavailable
provider may require owner repair, but cannot block another destination/candidate.

Recovery: disable, quiesce, reconcile, back up before migration. Never delete
intents/reset attempts. Inspect: read-only workflow.py reporting-status
--state STATE --binding BINDING. Supervised platform acceptance remains separate.
