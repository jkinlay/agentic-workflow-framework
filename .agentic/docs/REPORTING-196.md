# Reporting 1.9.6: operator contract

AWF-241 is Tier 3/FULL, but this stage is PARTIAL: it qualifies offline bodies,
projections, metrics/scenarios and standard-library transports only, not a real
platform, authenticated UI, integrated controller or installed runtime. Reporting
and transport remain default OFF.

## Records, bodies and delivery

Version-1 reports and version-2 snapshots retain replay behavior. Use
`reporting.records.prepare_snapshot` and `status_report_store.ReportStore.append`
are the immutable source/journal boundary; hashes prove consistency, not trust.

`DeliveryOutbox(existing_controller_store)` uses the existing serialized BEGIN
IMMEDIATE port. `install_delivery_schema(db)` runs only in the accepted controller
migration; construction does no DDL. Preserve protected database, integrity,
ownership and backup controls. A `delivery_intent(report_id, approved_binding,
payload)` freezes project/repository/scope/platform/destination/revision/secret
reference/payload. Its report/platform identity excludes destination/configuration;
changed bytes refuse; retries and mode changes never create fresh IDs.

`advance` reserves before a possible send and reconciles visibility; reservations
mean attempted-or-possibly-attempted, not an actual call. At most two survive
restart per report/platform. Reconcile UNKNOWN/IN_FLIGHT first; no timeout
takeover. Only proven terminal absence permits the second send; two absences are
FAILED, ambiguity is UNKNOWN. Slack and Teams state is independent. HTTP/API
acceptance, including 202, echo responses and local JSONL read-back is not
delivery. Verify sender, channel, complete content/correlation and Teams links.

`compose.compose_report(kind, supplied, accepted_binding, verify_sources=...,
verify_link=...)` is pure and computes scope, elapsed, usage and scenarios; its
verifier must return supplied bytes under the binding. Missing/stale
verification is explicit unknown, never live progress. `bodies.prepare_body(kind,
data, verify_link=...)` creates hashed `awf-report-body-1`; fields are
project, observed_at, timezone, scope, candidate, summary and sections. Kinds are
hourly, eod, owner-action, final-human-review and jira-review. `LAYOUTS` defines
section order. Rendering produces Slack mrkdwn or Teams Markdown/escaped HTML with
newlines, bold labels, suppressed markup/mentions and explicit unknowns; URLs need
trusted verification and Jira associations remain labelled when unverified.

Typed prs, current/next and epics retain cohorts, coverage, revisions, blockers
and findings. Alerts contain Decision/readiness, Owner action, Impact, independent
work and Evidence. Combine final-human-review Jira
sections, but keep backlog approval, readiness, owner approval, observed merge
and reconciled Done distinct. Hourly/EoD retain period/freshness/catch-up; EoD
also has today, since_previous and delivered nonadditively. Verify detail links.
Never silently shorten/split: refuse above UTF-8 limits Slack
12000 or Teams 20000, measure complete wire content including correlation and
Teams expansion, then use a reviewed compact summary plus verified full artifact.

## Calendar, metrics and forecasts

`owner_events` requires a complete observed action set: unchanged actions
deduplicate, changes/resolutions notify once, reopening gets a fresh identity,
and unavailable input is never empty. `readiness_events` requires complete
project/repository/PR/head/base/scope_revision/gate facts and literal current
critic/specialist/final-gate success; it emits one combined event with
`includes_jira_review=true` and `merge_authorized=false`. Supplied booleans,
green CI or draft flags are not authentication. Drift, failure or missing facts
corrects prior readiness; approval requires verified evidence for the exact
revision.

`due_periods(after, now, operating_root, effective_calendar=...)` consumes
AWF-244 `effective_calendar(root)`. Default EoD is every day at 17:00
Europe/London; no scheduler or fixed-offset fallback. Preserve
stable UTC period/event identities, timezone/offsets, distinct repeated DST hours,
honest catch-up, hourly cadence across weekends/idle/blocked/paused/EoD,
and explicit refusal for missing IANA data. Calls are bounded to 366 days;
longer downtime uses contiguous windows. Periods use (start,end]; merged PRs are
not delivered tickets, and post-17:00 work stays.

Metrics count distinct implementation leaves/Epics, priority/size, per-Epic
states and Unknown/Unestimated/Unassigned; shared keys have one primary Epic and
parents/subtasks are not double counted. Preserve planned dates. Keep
cohorts separate; elapsed means first
WORKER_STARTED to final required PR merge, reconciliation delay separate, missing
timestamps reduce coverage, and estimates are never observed effort. Forecasts
schedule unique dependency ancestors once with deterministic resource constraints,
reviewer/rework/UAT/production nodes, observed capacity and reservations. Missing
inputs, cycles or invalid capacity refuse dates; external waits are
null/CONDITIONAL; these are not percentiles and caps never forecast.

Accounting stays OFF unless authenticated owner direction enables it. Only enrolled,
attributed AWF sub-agents count; controller/main chat and independent/ULTRA agents
do not. OFF/unknown is UNAVAILABLE, never zero/fabricated. Deduplicate usage IDs,
retain unknown charges/coverage, reconcile overhead once, preserve original
forecast/baseline bytes, and never create a second ledger.

## Transports and qualification boundary

Adapters export `Transport` beside injected `ReportingAdapter`; exact binding,
resolver and explicit mode are required, otherwise OFF.
Resolve secrets per operation; values never enter settings, payloads, receipts,
diagnostics or logs. HTTP uses TLS, fixed origins, 15-second timeout, 2 MiB
responses, no redirects or automatic retries, and at most ten reconciliation
pages; absence/exhaustion is UNKNOWN. These APIs never return ABSENT; a
qualified terminal collector is required before retry. Slack uses approved
read/history and chat:write scopes; Teams uses delegated Graph identity,
membership, send and listing reads with current administrator consent. Use a
dedicated service sender, never the human merge owner; no live activation occurs.

Before onboarding name service owner, credential administrator, recovery contact
and host. Rotate references without replacing intents; disable authenticated
settings, reconcile unresolved sends, revoke credentials, and expose liveness,
pending age, UNKNOWN and FAILED per platform.
Qualification requires UI persistence/mode isolation, real Slack/Teams visibility,
freshness, size/redaction/202/timeout/retry/restart/concurrency/DST/weekend
checks, truthful coverage and owner/final review. Offline tests establish no FULL
closure, release readiness or activation.

