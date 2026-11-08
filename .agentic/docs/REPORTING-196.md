# Reporting 1.9.6: operator contract

## Construction

Default OFF; existing scopes retain suspension with cleared/absent bindings.
Restart consumes that marker without backfilling disabled hours. No chat/accounting/dispatch/Jira/merge/activation authority.

Owner-merged PRs select off/teams/slack/both and complete project/repository/scope/
platform/destination/revision/secret_ref bindings. `show` reports fetched settings/commit
and safe differences. Working/branch edits, including OFF,
have no effect; merge/fetch applies next load. Unverified/malformed policy refuses.
`operating validate --file` checks shapes offline; reachability/membership remains
UNOBSERVED. Invalid bindings refuse before credentials.
Never retarget corrections/reset attempts.

configure_reporting requires a trusted repository root matching snapshot provenance. The provider factory requires a validated
snapshot, governance and that root; it rederives accepted policy itself.
Reviewed context/sender/channel IDs are required.
env:VARIABLE resolves references; owners manage permissions/rotation.

## Sources/periods

Ports reread digest-bound evidence; reporting_sources binds scope and at most 100 PRs.
Periodic reports use AWF-244's read-only held/withdrawn/exhausted PR view,
preserving identities/recovery reasons.
Accounting uses the accepted setting/attributed ledger. Completed cohorts supply
means/totals; forecasts require canonical observations. Missing data remains unavailable.

AWF-244 supplies timezone/calendar, default EoD 17:00 Europe/London. Hourly reports
continue 24/7; UTC identities distinguish DST folds. Downtime retains snapshots,
sending labelled hourly CATCH-UP and at most two EoDs. Generation advances seven
days/cycle. EoD excludes later merges. Invalid ticket times/order or forecast
inputs invalidate affected figures without suppressing reports/alerts.
Unknown inventory/dates establish no defects; elapsed statistics identify cohort coverage.

Limits: 12,000 Slack / 20,000 Teams UTF-8 bytes. Compact reports prioritize
owner decisions/count omissions; reporting-status retains complete hashed bodies. Identity/link failures refuse atomically.

## Delivery/deadlines

One controller owns snapshots/intents.

Sources have 30 seconds via source_timeout(); expiry retains PARTIAL coverage,
unavailable totals and separate delivery admission. Delivered batches rotate:
one reobservation/cycle. Withdrawal corrects each delivery once;
delivered corrections seed restorations.

Destination threads admit alerts, hourly, EoD, then review: four advances,
one historical reconciliation and 32 HTTP calls/platform per 30-second admission
deadline. Review reserves one advance. Alternate review turns limit periodic
admission to ten seconds; joins end five seconds before the common deadline.
Phases share counters/gates; cancellation preserves reserved review admission.
Overflow/deferred classes rotate after alerts; new/historical reviews alternate
across restarts. Batches contain one candidate; restoration destinations share
one drain turn. Observers cap at 15 seconds, checking before/after
calls outside SQLite write transactions.

HTTP's monotonic 15-second maximum spans DNS through body; expiry closes sockets.
Direct TLS ignores proxies; redirects/retries are disabled. Delivery, cancellation
and joins stay within **45 seconds**, sources plus delivery within **75 seconds**,
excluding storage/host time. Cancellation fences
reservation/transport entry and bounds worker joins. Noncooperative workers
are durably recorded REPORT_DESTINATION_WORKER_NOT_QUIESCENT with revoked tokens.

Verification/payload/HTTP-budget admission precedes reservation. Validate immutable
policy before delivery. Controller sends recheck default-ref identity/bindings
before POST, including injected senders. Library cycles defer by default;
explicit sends require a fresh policy callback/loader.
Factory checks and review gates remain. Changed/unverified
identity forces full derivation. OFF/changed bindings refuse without resetting state. Candidate/window checks also repeat; remaining HTTP
time is recomputed afterward. API acceptance, including Graph 201/chatMessage, proves
no landing. Timeouts require reconciliation; terminal rejection/absence permits one
retry, two sends maximum across restart/concurrency. Missing listings remain UNKNOWN.

HTTP 429 persists Retry-After seconds (missing/invalid: 60; Slack ratelimited: 60).
controller_report_cooldown covers reports, sender/destination changes and restarts:
Slack workspace/method; Graph tenant resources/methods. Verification, pagination
and readback check cooldown before admission. THROTTLED preserves SEND/RECONCILE;
read retries consume no send attempt. Two actual throttled POSTs become FAILED with
TWO_SEND_ATTEMPTS_EXHAUSTED.

## Retention/recovery

Credential filtering checks grammar/resolved values throughout retention/delivery.
Enqueue validates bindings/intent fields before writing; only references persist.
Custom resolvers accompany preparation outside controller transactions. Link verification,
filtering and rendering prepare immutable safe bytes; short write transactions
recheck exact cursor/report/delivery/review dependencies before atomic persistence.
REPORT_PROJECTION_STATE_CHANGED refuses stale preparation until the next cycle;
unrelated controller metadata does not invalidate it. Unprepared transactional enqueue refuses.

Confirmation requires sender, destination/thread, final reserved delivery/hash
marker and normalized-content hash. Embedded markers are quoted. Invalid IDs/unknown
normalization remain UNKNOWN. Pagination permits ten budgeted pages;
Graph links retain origin/resource.
JSONL is LOCAL_RECEIPT, channel_delivery:false. Review acknowledgement requires both
destinations' channel evidence; review/merge authority remains separate.

A CLAIMED batch transfers to REPORTING after exact-payload outbox retention.
REPORTING permits newer candidates. Crash recovery retains send-started identities/attempts
when creating missing reports; provably unsent PREPARED claims release for fresh preparation.
Terminal failures close batches FAILED and members EXHAUSTED; platform successes survive.
Exhausted reviews generate one deduplicated owner-action alert. Failed alerts
enter periodic diagnostics without recursion. Diagnostics list four identities/total; reporting-status retains all.

PENDING/ABSENT retry through rotation/fresh gates; THROTTLED waits for cooldown.
IN_FLIGHT/UNKNOWN reconcile without reposts. Missing gates, changed calendar or
observation-budget refusal retain visible deferred evidence; restore evidence/calendar
or present a newly verified tuple.
Restoration readback survives withdrawal/cutoff; sends atomically charge windows
with fresh gates; exhaustion visibly defers until reopening.
Landed restorations seed withdrawal corrections. Correction/restoration chains
retain original channel roots separately from reply receipts across restarts.
WITHDRAWN/EXHAUSTED and
terminal batches retain history and cannot revive by replay. Provider failures cannot block unrelated work.

Before migration disable/quiesce/reconcile/back up; preserve intents/attempts.
Inspect: workflow.py reporting-status --state STATE --binding BINDING.
