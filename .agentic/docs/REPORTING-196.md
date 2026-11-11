# Reporting 1.9.6: operator contract

## Construction

Default OFF; existing scopes retain suspension with cleared/absent bindings.
Restart consumes suspension without disabled-hour backfill. No chat/accounting/dispatch/Jira/merge/activation authority.

Owner-merged PRs select off/teams/slack/both with complete project/repository/scope/
platform/destination/revision/secret_ref bindings. `show`: fetched settings/commit
and safe differences. Working/branch edits, including OFF,
are ineffective; merge/fetch applies next load. Unverified/malformed policy refuses.
`operating validate --file` checks offline shapes; reachability/membership stays UNOBSERVED. Invalid bindings refuse before credentials.
Never retarget corrections/reset attempts.

configure_reporting requires a trusted repository root matching snapshot provenance. The provider factory requires validated
snapshot/governance and that root; it independently rederives accepted policy.
Require reviewed context/sender/channel IDs.
env:VARIABLE resolves references; owners manage permissions/rotation.

## Sources/periods

Ports reread digest-bound evidence; reporting_sources binds scope/at most 100 PRs.
Periodic reports use AWF-244's read-only held/withdrawn/exhausted PR view,
preserving identities/recovery reasons.
Accounting uses accepted setting/attributed ledger. Completed cohorts supply
means/totals; forecasts require canonical observations. Missing data stays unavailable.

AWF-244 supplies timezone/calendar, default EoD 17:00 Europe/London. Hourlies
continue 24/7; UTC identities distinguish DST folds. Downtime retains snapshots:
send labelled hourly CATCH-UP and at most two EoDs. Generation advances seven days/cycle. EoD excludes later merges. Invalid ticket times/order/forecast
inputs invalidate affected figures without suppressing reports/alerts.
Unknown inventory/dates establish no defects; elapsed statistics identify cohort coverage.

Limits: Slack 12,000/Teams 20,000 UTF-8 bytes. Compact reports prioritize
owner decisions/count omissions; reporting-status retains complete hashed bodies. Identity/link failures refuse atomically.

## Delivery/deadlines

One controller owns snapshots/intents.

Sources: 30s via source_timeout(); expiry retains PARTIAL coverage,
unavailable totals/separate delivery admission. Delivered batches rotate:
one reobservation/cycle. Withdrawal corrects each delivery once;
delivered corrections seed restorations.

Per-platform: four advances, one historical reconciliation, 32 HTTP calls;
platforms share a 30-second admission deadline. Alerts stay first; review reserves one advance.
NEW/RETRY preserve review admission. Retries overlap one periodic
class with observations under the original deadline. Durable review/periodic
readback priority and hourly/EoD selection rotate. Join before retention/exit; counters/gates remain shared. Batches: one candidate; restorations share one drain. Observations check before/after
calls outside write transactions; cap: 15s.

Sustained overload (restart, new owner alert/candidate/hourly each turn; Teams ~2.2s/call, Slack ~1.5s/call) may delay reviews many turns while alerts stay strictly first: fresh Teams alert (~26.4s) plus fresh review pre-POST checks (~21s) exceed 30s. Measured longest review-attempt gaps over 48 turns: 28 (Teams), 12 (Slack). Hourly/EoD reports, alert delivery, two-send ceiling, readback, reconciliation and charges are unaffected. Bounded fix: 1.9.8 follow-up.

HTTP's monotonic 15-second maximum spans DNS through body; expiry closes sockets.
Direct TLS ignores proxies; redirects/retries are disabled. Delivery/cancellation/joins stay within **45 seconds**, sources plus delivery within **75 seconds**,
excluding storage/host time. Cancellation fences
reservation/transport entry and bounds worker joins. Noncooperative workers
are durably recorded REPORT_DESTINATION_WORKER_NOT_QUIESCENT with revoked tokens.

Verification/payload/HTTP-budget admission precedes reservation. Validate immutable
policy before delivery. Controller rechecks default-ref identity/bindings pre-POST,
including injected senders. Library cycles default to deferral;
explicit sends require fresh policy callbacks/loaders.
Retain factory/review gates; changed/unverified
identity forces full derivation. OFF/changed bindings refuse without resetting state. Candidate/window checks also repeat; remaining HTTP
time recomputes afterward. API acceptance, including Graph 201/chatMessage, proves
no landing. Timeouts require reconciliation; terminal rejection/absence permits one
retry, two sends maximum across restart/concurrency. Missing listings: UNKNOWN.

Persist HTTP 429 Retry-After seconds (missing/invalid/Slack ratelimited: 60).
controller_report_cooldown covers reports, sender/destination changes and restarts:
Slack workspace/method; Graph tenant resources/methods. Verification, pagination
and readback check cooldown before admission. THROTTLED preserves SEND/RECONCILE;
read retries consume no send attempt. Two actual throttled POSTs become FAILED with
TWO_SEND_ATTEMPTS_EXHAUSTED.

## Retention/recovery

Filter credential grammar/resolved values throughout retention/delivery.
Enqueue validates bindings/intent fields pre-write; persist references only.
Custom resolvers accompany preparation outside controller transactions. Link verification/filtering/rendering prepares immutable safe bytes; short write transactions
recheck exact cursor/report/delivery/review dependencies before atomic persistence.
REPORT_PROJECTION_STATE_CHANGED refuses stale preparation until next cycle;
unrelated controller metadata cannot invalidate it. Unprepared transactional enqueue refuses.

Confirmation requires sender/destination/thread, final reserved delivery/hash
marker and normalized-content hash. Quote embedded markers. Invalid IDs/unknown
normalization stay UNKNOWN. Pagination: ten budgeted pages;
Graph links retain origin/resource.
JSONL: LOCAL_RECEIPT, channel_delivery:false. Review acknowledgement requires both
destinations' channel evidence; review/merge authority stays separate.

CLAIMED transfers to REPORTING after exact-payload outbox retention.
REPORTING permits newer candidates. Crash recovery preserves send-started identities/attempts
when creating missing reports; provably unsent PREPARED claims release for fresh preparation.
Live uninvoked retention failures release; restarts cannot infer proof.
Terminal failures close batches FAILED/members EXHAUSTED; platform successes survive.
Exhausted reviews generate one deduplicated owner-action alert. Failed alerts
enter nonrecursive periodic diagnostics. Diagnostics list four identities/total; reporting-status retains all.

PENDING/ABSENT retry via rotation/fresh gates; THROTTLED awaits cooldown.
IN_FLIGHT/UNKNOWN reconcile without reposts. Missing gates/changed calendar/observation-budget refusal retain visible deferred evidence; restore evidence/calendar
or present a newly verified tuple.
Restoration readback survives withdrawal/cutoff; sends atomically charge windows
with fresh gates; exhaustion visibly defers until reopening.
Landed restorations seed withdrawal corrections. Correction/restoration chains
retain original channel roots separately from reply receipts across restarts.
WITHDRAWN/EXHAUSTED/terminal batches retain history; replay cannot revive them. Provider failures cannot block unrelated work.

Before migration disable/quiesce/reconcile/back up; preserve intents/attempts.
Inspect: workflow.py reporting-status --state STATE --binding BINDING.
