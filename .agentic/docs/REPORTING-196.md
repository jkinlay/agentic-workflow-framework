# Reporting 1.9.6: operator contract

## Settings and construction

Reporting defaults OFF: no transactions, cursors, metadata, byte changes
or secret reads. Disabling records suspension to exclude disabled
periods. Reporting grants no inbound chat, accounting, dispatch, Jira, merge or
activation authority.

Operating `reporting.mode` selects `off`, `teams`, `slack`, `both`.
`reporting.bindings` admits one destination per platform: `project`, `repository`,
`scope_revision`, `platform`, `destination`, `revision`, `secret_ref`. Missing,
duplicate or foreign bindings refuse before credential access. Rotation cannot
retarget old intents or reset attempts. New channels require explicit binding.

`ReportingSettingsRegistry` and independent `observe_owner_direction`
authenticate settings/destinations. Operating locks
and journal recovery persist admission. Configuration cannot register executable ports.

`configure_reporting` requires reviewed
`reporting.slack`/`reporting.teams` `context` and `sender` bindings
(workspace/user or tenant/delegated-user). Slack destinations are channel IDs;
Teams destinations are `team-ID/channel-ID`. The reference resolver supports
`env:VARIABLE`; other references require a host resolver. Values never belong in
configuration, prompts, payloads, receipts, diagnostics or logs.

## Sources, periods and priority

Reference ports independently re-read digest-bound evidence. Optional
`reporting_sources` binds scope and up to 100 PR numbers; unenrolled PR metrics
stay unavailable. Inventory grants no history, estimates, Jira Done or review
authority. Verifiers bind identical snapshots, evidence identity and observation
time. Detailed links require independent verification.

AWF-244 supplies timezone/EoD calendar: hourly reporting continues 24/7 through
weekends, idle/blocked/paused states and EoD, default 17:00 Europe/London. UTC
identities distinguish DST folds. Keep the sole controller running.

After downtime, retain every period and source snapshot. Send one labelled hourly
CATCH-UP summary per platform naming hourly boundaries/span, plus at most two
separate EoDs; list older daily dates in the summary. Generation processes at most
seven calendar days per cycle; the cursor retains further work. Archived periods
carry `LISTED_IN_CATCH_UP`. First EoD computes today independently; missing prior
coverage stays unknown. Historical views exclude later merges. Invalid/future
merge timestamps retain unavailable daily views while other periods progress.
Other rendering errors roll back.

## Delivery, recovery and rollback

One controller owns snapshots, intents and transactions. Additive migration adds
`controller_reports`; new reports never enter the legacy status outbox.
Pre-release REPORT rows relocate byte-for-byte, preserving kind/history.
Snapshot/intent immutability and protected SQLite checks remain.

Dispatch completes before source collection and delivery. Sources have a separate
**30-second** deadline; trusted source ports use `source_timeout()` for network
operations. The reference collector bounds its pagination subprocess by the
remaining allowance. Expiry retains verified inventory with PARTIAL coverage and
unavailable PR totals; failed collection reports STALE_OR_UNAVAILABLE. Neither
spends delivery admission. Delivered review identities are re-observed first;
withdrawal corrects each confirmed destination once, using its original thread.

Each platform has a non-daemon thread,
ordering and budget; slow Teams never holds Slack. Alerts
drain before periodic work within each platform. Newest hourly work precedes
backlog. Historical UNKNOWN rows rotate by observation count.

A cycle budget shared with review delivery/retries admits four advances, one
historical reconciliation and 32 HTTP calls per platform. Admission stops after
30 seconds. Each HTTP request has an overall monotonic deadline of at most 15
seconds covering DNS, connect, TLS, send, headers and the entire body. Expiry
shuts down sockets, including slow-drip reads. The concurrent delivery network
stage is bounded by **45 seconds**; joins use the same deadline. Source plus
delivery network stages total at most **75 seconds**. Local storage/host time is
separate. Joined threads finish before return; a slow
destination can delay the NEXT cycle by up to that maximum. Trusted injected
transports must honour this contract. Direct TLS ignores ambient proxy settings; redirects/retries are disabled.
A timed-out DNS lookup cannot later connect or send.

Destination verification, payload and HTTP-budget admission precede reservation.
Candidate/window checks repeat at POST, including retries. Only actual send start
reserves an attempt. Deadline expiry is ambiguous: reconcile before any retry.
Terminal rejection/absence permits exactly one retry, two sends maximum across
restart/concurrency. Missing listings and API acceptance (including 202) prove
no landing. Local JSONL receipts remain `LOCAL_RECEIPT`, `channel_delivery:false`.

The shared admission filter protects source evidence, snapshots, journal appends,
reports, public outbox admission, sender/POST boundaries, readbacks and status. It checks
the host credential grammar and resolved sender values; only references are
cached. Withholding keeps a fixed marker, never secret excerpts. Unsafe journal
records refuse before writing. Slack readback IDs must contain bounded ASCII
seconds plus six fractional digits; Teams IDs must be bounded ASCII decimals.
Invalid IDs become UNKNOWN and are never retained verbatim. Match channel,
sender, complete content/correlation and stable readback identity.

Before rollback: disable reporting, quiesce, reconcile, back up state, run migration
and verify no REPORT rows remain in `controller_outbox`. The actual merge-base
controller consumes legacy digests and leaves reports intact. Never delete intents
or reset attempts.

Assign sender, credential and recovery owners. Disable
admission before rotation/revocation. Inspect UNKNOWN/FAILED and pending age using
read-only `workflow.py reporting-status --state STATE --binding BINDING`.
Accounting reads only the existing attributed ledger; OFF/unknown is unavailable.
Both destinations need channel evidence for review acknowledgement. Supervised
channel qualification, independent specialists, final gate and owner review remain.
