# Reporting 1.9.6: operator contract

## Settings and construction

Reporting defaults OFF: no writes or secret reads. Disabling records suspension
to exclude disabled periods. Reporting grants no chat, accounting, dispatch,
Jira, merge or activation authority.

Operating `reporting.mode` selects `off`, `teams`, `slack`, `both`.
`reporting.bindings` admits one destination per platform: `project`, `repository`,
`scope_revision`, `platform`, `destination`, `revision`, `secret_ref`. Missing,
duplicate or foreign bindings refuse before credential access. Rotation cannot
retarget old intents or reset attempts. New channels require explicit binding.

`ReportingSettingsRegistry`/`observe_owner_direction` authenticate persisted
settings. Configuration cannot register executable ports.

`configure_reporting` requires reviewed
`reporting.slack`/`reporting.teams` `context` and `sender` bindings
(workspace/user or tenant/delegated-user). Slack destinations are channel IDs;
Teams destinations are `team-ID/channel-ID`. The reference resolver supports
`env:VARIABLE`; other references require a host resolver. Values never belong in
configuration, prompts, payloads, receipts, diagnostics or logs.

## Sources, periods and priority

Reference ports re-read digest-bound evidence. `reporting_sources` binds scope
and up to 100 PRs. Inventory grants no history, estimates, Done or review authority.
Unenrolled metrics and UNKNOWN ticket details stay unavailable; observed work
remains visible. Verifiers bind snapshots, evidence, times and detail links.

AWF-244 supplies timezone/EoD calendar: hourly reporting continues 24/7 through
weekends, idle/blocked/paused states and EoD, default 17:00 Europe/London. UTC
identities distinguish DST folds.

After downtime, retain every snapshot. Send one labelled hourly CATCH-UP
summary naming boundaries/span and older daily dates, plus at most two EoDs.
Generation processes seven days/cycle; the cursor retains further work.
Archives carry `LISTED_IN_CATCH_UP`. EoD computes today independently; missing
coverage stays unknown. Historical views exclude later merges. Invalid/future
merge times leave daily views unavailable. Other rendering errors roll back.

## Delivery, recovery and rollback

One controller owns snapshots/intents/transactions. Migration adds
`controller_reports`; reports never enter the status outbox. Pre-release
REPORT rows relocate byte-for-byte. Immutability and protected SQLite checks remain.

Dispatch precedes source collection/delivery. Sources have a **30-second**
deadline; trusted ports use `source_timeout()`, including pagination. Expiry
retains verified inventory/PARTIAL coverage; PR totals stay unavailable. Failure
reports STALE_OR_UNAVAILABLE. Neither spends delivery admission. Reviews are
re-observed first; withdrawal corrects each destination once in its original thread.

Each platform has an independent non-daemon thread and budget; slow Teams never
holds Slack. Alerts precede periodic work; newest hourly precedes backlog.
Historical UNKNOWN rows rotate by observation count.

A cycle budget shared with reviews admits four advances, one
historical reconciliation and 32 HTTP calls per platform. Admission stops after
30 seconds. Each request has a 15-second monotonic deadline covering DNS through
body; expiry shuts down sockets. The concurrent delivery stage, including joins,
is bounded by **45 seconds**, source plus delivery by **75 seconds**, excluding
local storage/host time. Threads join before return; slow destinations can delay
the NEXT cycle by that maximum. Injected transports must honour these bounds.
Direct TLS ignores ambient proxies; redirects/retries are disabled. Timed-out
DNS cannot later connect/send.

Destination verification, payload and HTTP-budget admission precede reservation.
Candidate/window checks repeat at POST, including retries. Only actual send start
reserves an attempt. Deadline expiry is ambiguous: reconcile before any retry.
Terminal rejection/absence permits one retry, two sends maximum across restart/
concurrency. HTTP 429 persists `retry_not_before` from `Retry-After` seconds
(missing/invalid: 60; Slack `ratelimited`: 60). Cooldowns persist in
`controller_report_cooldown` across reports, destinations, sender/configuration changes and restarts:
Slack keys workspace/method; Graph conservatively groups all tenant resources
and methods. Every request checks cooldown before HTTP/attempt admission,
including verification, pagination and readback. Admission defers without sleeping.
THROTTLED retains SEND/RECONCILE recovery; read retries preserve the reconciliation
cap and consume no send attempts. Actual throttled POSTs count: two become FAILED
with `TWO_SEND_ATTEMPTS_EXHAUSTED`. Missing listings and API
acceptance (including Graph's 201/chatMessage) prove no landing. JSONL receipts
remain `LOCAL_RECEIPT`, `channel_delivery:false`.

The shared credential filter protects sources, snapshots, journals, reports,
outbox, sender/POST, readbacks and status, checking host grammar and resolved
values. Only references are cached. Withholding uses a fixed marker; unsafe
journals refuse before writes. Slack readback IDs contain bounded ASCII
seconds plus six fractional digits; Teams IDs must be bounded ASCII decimals.
Invalid IDs become UNKNOWN. Confirmation requires sender, destination/thread,
the exact final reserved delivery/hash marker and normalized-content hash.
Composition escapes `AWF delivery:` as `AWF delivery (quoted):`; direct payloads
containing it refuse. Slack normalizes CRLF, its three escaping entities and
explicit Unicode/colon emoji aliases in `SLACK_EMOJI`; unsupported conversions
remain UNKNOWN. Graph normalizes HTML text/whitespace and preserves link targets.
Membership/message pagination allows ten pages within existing HTTP budgets. Next links must
retain HTTPS origin and decoded resource segments, accepting encoded/unescaped
channel IDs without accepting a different resource.

Before rollback: disable reporting, quiesce, reconcile, back up state, run migration
and verify no REPORT rows remain in `controller_outbox`. The actual merge-base
controller consumes legacy digests and leaves reports intact. Never delete intents
or reset attempts.

Assign recovery owners. Disable before rotation/revocation. Inspect UNKNOWN/FAILED using
read-only `workflow.py reporting-status --state STATE --binding BINDING`.
Accounting reads only the existing attributed ledger; OFF/unknown is unavailable.
Both destinations need channel evidence for review acknowledgement. Supervised
channel qualification, independent specialists, final gate and owner review remain.
