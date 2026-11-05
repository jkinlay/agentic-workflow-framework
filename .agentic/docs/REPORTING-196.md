# Reporting 1.9.6: offline implementation and integration contract

AWF-241, Tier 3, FULL. Stage two supplies report bodies, pure event/period
projections, metrics/scenarios and standard-library HTTP transports. Reporting
remains default OFF. Full ticket closure is PARTIAL: no real platform,
authenticated UI, integrated controller or installed runtime was qualified here.

## Records and delivery

Version-1 status reports and version-2 supplied snapshots retain their prior
replay behavior. `reporting.records.prepare_snapshot` and the existing
`status_report_store.ReportStore.append` remain the independently verified source
admission and immutable journal boundaries. Hashes prove consistency, not trust.
Use stage-two projections for expanded reports; do not reinterpret historical
version-2 metric bytes. Report bodies below are rendering artifacts, not a new
source admission, lifecycle event, usage ledger or scheduler.

`DeliveryOutbox(existing_controller_store)` uses its existing serialized
BEGIN IMMEDIATE transaction port. Call `install_delivery_schema(db)` only in the
accepted controller migration. Construction performs no DDL. Retain the existing
protected database, integrity, ownership and backup controls.

`delivery_intent(report_id, approved_binding, payload)` freezes project,
repository, scope, platform, destination, binding revision, secret reference and
payload. Its report/platform identity excludes destination/configuration so
changes cannot reset the attempt ceiling. Changed bytes under one identity
refuse. Never create fresh IDs for retries or mode changes.

`advance` commits a reservation before one possible network send and reconciles
channel visibility. Reservations mean attempted-or-possibly-attempted, not proof
of actual calls. At most two reservations per report/platform survive restarts.
UNKNOWN/IN_FLIGHT reconcile before any new send; there is no timeout takeover.
Only proven terminal absence of every reserved request permits the second send.
Two terminal absences become FAILED; ambiguity remains UNKNOWN. Slack and Teams
have independent delivery states. HTTP/API acceptance, including 202, echoed
responses and local JSONL read-back are not channel delivery.

## Bodies and layouts

`compose.compose_report(kind, supplied, accepted_binding, verify_sources=...,
verify_link=...)` is the pure composition entry point. It accepts a superset of
the version-2 snapshot input, computes scope/elapsed/usage/scenario projections,
builds the per-type sections and attaches full detail_metrics and a source hash.
Its independent verifier must return the exact supplied bytes under the accepted
binding. Missing verification or missing/stale sources produce explicit unknown
content, never a live-progress claim. Optional supplied forecast, accounting,
review/owner queues and current/next keys are documented in its docstring.
The controller supplies observations and persists the result; it does not have
to implement metric calculations or report-content construction again.

`bodies.prepare_body(kind, data, verify_link=...)` creates a detached, hashed
`awf-report-body-1`. Required data: project, observed_at, timezone, scope,
candidate, summary and sections. Retain its bytes beside the admitted source
snapshot/event. Kinds: hourly, eod, owner-action, final-human-review, jira-review.
`LAYOUTS` exports exact section order; templates/reporting/layouts.md lists fields.

`render_body(body, platform)` produces Slack mrkdwn or the Teams Markdown
fallback, with actual newlines, bold labels and spaced entries. Graph transport
converts the constrained Teams formatting to escaped HTML. No Adaptive Card,
Block Kit or live rendering qualification is claimed. Unknown sections remain
explicit; None requires an explicit observed empty list. Raw supplied markup and
mentions are suppressed. PR entries retain every verified Jira association or
say Jira association unverified. URLs need a trusted existence/access verifier.

Typed lists are prs (number/url/tickets/status/head/merged_at/merge_commit),
current/next (key/title/url/planned_start/planned_end/stage/actor/elapsed/next_gate/
activity/conditional) and epics (key/title/url/planned_start/planned_end/owner/
remaining/eta/tokens/assumptions). `sections` holds admitted metric narratives;
include cohort n/coverage, baseline/forecast revision, source times/staleness,
blocker owner/age/resume and finding dispositions. `overhead` explains common
usage and scope reconciliation. Missing displayed dates are planning defects.

Immediate alerts begin Decision / readiness, Owner action, Impact and independent
work, Evidence. Populate decision, decision_owner, requested_response, options,
recommendation, delay_consequence, independent_work, resume, stage and next_gate.
The final-human-review body also contains the full Jira implementation-review
sections in one combined notification. Backlog approval remains separate.
Readiness, owner approval, observed merge and reconciled Done stay distinct.

Hourly/EoD include period/freshness and catch_up. EoD also includes today,
since_previous and delivered; overlapping reconciliation views are never added.
Include complete matrices, date tables, cohorts, forecast schedule and source
records as additional body data; `render_detail` preserves all fields in a
versioned canonical JSON artifact for approved hosting. The host must verify
upload and reader access before supplying detail_url. No hosted link is invented.

No silent shortening/split occurs. Rendering refuses above conservative UTF-8
application limits (Slack 12000, Teams 20000). Transport independently measures
the complete wire body, including correlation marker and Teams HTML expansion.
Surface oversize refusal; prepare an explicitly reviewed compact summary and
verified full artifact rather than retrying identical oversize bytes.

## Pure events and calendar

`owner_events(scope, actions, previous)` takes a complete observed open-action
set, returns events and versioned state with scope and monotonic sequence.
Unchanged actions deduplicate; changes/resolutions notify once; reopening receives
a fresh identity. An unavailable source must never be supplied as an empty set.
Persist returned state and event intents atomically in the sole controller.

`readiness_events(candidates, previous)` takes a complete current candidate set
with project/repository/PR/head/base/scope_revision/gate. Exact owner-ready stage
and literal current critic/specialist/final_gate success produce one combined
final-human-review event, includes_jira_review=true, merge_authorized=false.
The trusted host must independently collect these facts; supplied booleans are
not authentication. Green CI/draft flags do not qualify. Head/base/scope/gate
changes, gate failures or missing observations correct earlier readiness.
`approval_event` requires supplied verified approval evidence for the exact
revision. Preserve original baselines and correlate corrections with originals.

`due_periods(after, now, operating_root, effective_calendar=...)` consumes the
injected AWF-244 `effective_calendar(root)` callable. The contract is
`.agentic/docs/operating-calendar.md` on the AWF-244 branch. Its default EoD is
EVERY day, 17:00 Europe/London. This lane copies neither calendar configuration
validation nor review-batching code. No scheduler starts here.

Boundaries in (after, now] have stable UTC identities, named timezone and offset
labels. Repeated DST hours are distinct; nonexistent hours are not invented.
Late ticks label catch-up and current observation honestly. Hourly cadence is
independent of weekends, paused/idle/blocked states, EoD and review batching.
EoD uses the effective time/day list; folds choose later, gaps advance to the
first valid minute. Missing IANA data refuses explicitly. Calls are bounded to
366 days; longer downtime needs contiguous windows, not a discarded cursor.
Namespace period/event IDs by admitted project/repository/scope for report IDs.

`daily_views` returns Today from local midnight and Since previous daily report
as explicitly nonadditive overlapping views. `daily_metrics` deduplicates actual
merged PR records and separately counts reconciled tickets, merged/pending work,
and daily/project elapsed cohorts. Periods use (start, end] consistently. A
merged PR is not a delivered ticket and post-17:00 work is not discarded.
Routine ready presentation belongs to AWF-244 ReviewBatchStore and its live
observer/immediate pre-send recheck, never a second reporting scheduler.

## Metrics and forecasts

`analysis.scope_metrics` counts distinct required implementation leaves and
Epics, ticket priority/size, separate Epic priority/size, per-Epic states and
Unknown/Unestimated/Unassigned categories. Shared keys have one primary Epic;
parents/subtasks are not double counted. Cancellation, deferral/out-of-scope and
conditional work are visible dispositions, never delivered. Every ticket/Epic
planned Start/End is retained in planned_dates. `planning_defects` checks missing/
inverted dates, due/baseline mismatches, predecessor scheduling, unknown same-day
offsets and mandatory-child Epic windows. Conditional children do not extend
mandatory windows. Defects require planning correction, not reporting Jira writes.

Existing project_metrics retains comparable-cohort mean/median/IQR, n/coverage.
`elapsed_cohorts` keeps declared comparable groups separate in composed reports;
an undeclared cohort is unavailable, never pooled with measured cohorts.
`elapsed_comparison` adds measured execution/review/external/owner-wait components
and matched original-estimate variance only in compatible calendar-second units.
Elapsed means first WORKER_STARTED to final required PR merge; reconciliation
delay stays separate. Missing timestamps lower coverage, never become zero.
Engineer-days are not converted by guesswork; original/revised estimates remain
in the supplied history. Daily and project-to-date cohorts remain separate.

`milestone_forecast(nodes, milestones, capacity, as_of, reservations=...,
previous=...)` schedules unique remaining dependency ancestors once using a
deterministic resource-constrained list schedule. Nodes have key, dependencies,
resource and remaining_seconds optimistic/base/conservative. Optional conflicts
are exclusive files/resources; external_wait names an unpriced gate, owner and
next_gate explain responsibility. Include explicit reviewer/rework nodes and
separate code/API/fixture, UAT and production milestones. Only retained WIP remains.

Capacity is safe OBSERVED slots, never configured stream ceilings. Reservations
are unavailable seconds per resource slot. Missing durations/dependencies,
cycles or invalid capacity refuse unsupported dates. Retain the input hash,
previous forecast and node start/finish/resource/slot schedule. Dependency paths
alone do not represent resource queues: show the retained schedule too. External
waits make affected ETA null/CONDITIONAL, with separately labelled conditional
date excluding that wait and named missing decisions. Continuous calendar-second
capacity is an assumption; holidays/unavailable time need reservations or waits.
These scenarios are not statistical percentiles; caps never enter forecasts.

`token_metrics(mode, observations, enrolled, ...)` consumes only trusted supplied
enrollment/attributed usage. OFF/unknown is UNAVAILABLE; enabled with no records
has no consumed total. Exclude controller/main chat and independent/ULTRA agents.
Deduplicate observation IDs exactly; actual review/retry/failure runs have unique
IDs. Sum provider input/output totals, never cached/reasoning subsets twice.
Retain unknown charges, model mix, known subtotal and coverage. Completed-ticket
means use only complete observed accounting; WIP/shared overhead remain separate.
Reporting cannot enable accounting or construct a second ledger.

`observed_token_forecast(work, history)` uses completely attributed comparable
history min/mean/max, n and remaining WIP fractions. Retained work, review, rework
and shared overhead have unique component IDs; absent history is unavailable.
`token_rollup` reconciles primary Epic rows plus shared/unallocated once to the
scope consumed/remaining/expected scenarios. Unknown usage propagates to unknown
expected totals. Do not include review/overhead both in a work cohort and in a
separate component. The optional token_metrics forecast input requires measured
basis observation IDs. Preserve original forecast/baseline bytes in the journal.

## Standard-library real transports, default OFF

Each adapter exports Transport alongside the unchanged injected ReportingAdapter.
Transport(context=..., sender=...) pins approved workspace/tenant and service
sender IDs. ReportingAdapter requires the exact project-owned binding, reference
resolver and explicit effective mode; omission remains OFF. Protect transport
registration/context/sender/resolver configuration. Binding/mode/size admission
precedes secret resolution, which runs anew each operation for rotation. Secret
values never enter settings, payloads, receipts, diagnostics or logs. Provider
errors use fixed codes and suppressed exception chains; do not log HTTP locals.

The HTTP client uses TLS, fixed provider origins, 15-second timeout, bounded
2 MiB responses, no redirects and no automatic retries. Reconciliation inspects
at most ten pages; absence/exhaustion remains UNKNOWN. Read-back checks sender,
channel, full intended content/correlation and Teams link destinations, returning
a stable message ID and observation fingerprint. These APIs cannot prove
terminal absence of delayed POSTs: the transports deliberately NEVER return
ABSENT. A separately qualified terminal collector is needed to permit retry.

Slack uses auth.test, conversations.info, chat.postMessage and
conversations.history. It verifies workspace, sender and exact member channel.
Approve chat:write plus the selected public/private channel read/history scopes;
no cross-project fallback or chat:write.public is needed. Teams uses delegated
Graph /me, /organization, team/channel/membership reads, channel POST and message
listing. Ordinary application-only migration sends are not used. The trusted
resolver owns tenant issuer validation and delegated-token acquisition/refresh.
Selected capabilities require administrator approval: User.Read,
Organization.Read.All, Team.ReadBasic.All, Channel.ReadBasic.All,
ChannelMember.Read.All, ChannelMessage.Send and ChannelMessage.Read.All, subject
to current provider consent. Use a dedicated approved service sender, not the
human merge-owner credential. No token acquisition or live activation occurs here.

Official contracts checked: [Slack send](https://docs.slack.dev/reference/methods/chat.postmessage),
[Slack history](https://docs.slack.dev/reference/methods/conversations.history/),
[Graph send](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0),
[Graph history](https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0).
Fake HTTP is implementation evidence only, not real-platform qualification.

Before onboarding name the service owner, credential administrator, recovery
contact and approved report host. Rotate values behind references, never replace
retained intents to reset attempts. Disable with authenticated mode settings,
reconcile unresolved sends, then revoke credentials as appropriate. Controller
liveness exposes last period/read-back, pending age and UNKNOWN/FAILED separately
per platform. Recovery reconciles effects first and labels missed-period catch-up;
use only an independently approved fallback.

## Requests to other lanes: apply in order

- [ ] **Operating/config writer:** admit default-OFF mode off/teams/slack/both and
  exact project/repository/scope/platform/destination/revision/secret-reference
  bindings through existing validation/lock/journal/audit/recovery. Authenticate
  actual owner UI instructions and show persisted effective settings after restart.
  Preserve adopter settings; enable no chat/accounting/Jira/dispatch implicitly.
- [ ] **Calendar/runtime writer:** merge AWF-244 and inject effective_calendar(root).
  Preserve EoD all-seven-days default and continuous hourly cadence. Provision
  reviewed IANA tzdata in the installed Windows runtime; no fixed-offset fallback.
- [ ] **Controller database writer:** migrate delivery schema in the existing
  protected database; atomically persist event state, cursor and report intents.
  Retain immutable admitted source/body/baseline/forecast bytes in its journal.
  Namespace stable event/period report IDs by accepted scope, preserving per-platform
  attempt identity through restart/mode/binding changes. No second scheduler.
- [ ] **Controller observation writer:** collect current source scope/times, exact
  PR/head/base/gate and independent critic/specialist/final-gate observations.
  Feed complete action/readiness snapshots, accepted date fields/baselines,
  graph/capacity/reservations, real merge/closeout/reconciliation and AWF-242
  enrolled attributed usage. Missing sources stay unknown, never empty by guess.
- [ ] **Controller report writer:** call compose_report for every report type
  with admitted source observations and retain its body/detail metrics. Supply
  narrative business context and observations, not invented metrics. Verify
  detail upload/access before links. Correlate corrections and
  resolutions with originals. Send one combined final/Jira implementation report;
  keep backlog acceptance, owner approval, merge and reconciled Done distinct.
- [ ] **Review-batching integration:** enqueue owner-ready tuples in AWF-244's
  ReviewBatchStore, use trusted fresh observation and immediate pre-send recheck,
  withdraw drift. Connect one logical batch to independent per-platform intents
  without multiplying retries; reconcile both before logical terminal receipt.
  Held reviews must not hold urgent actions/hourly/EoD/work.
- [ ] **Sender/reference-adapter writer:** label JSONL handoff LOCAL_RECEIPT. Register
  qualified transports and protected project context/sender/binding/resolvers.
  Call advance only for enabled modes; surface size refusal and UNKNOWN/FAILED,
  reconcile after restart, never turn HTTP 200/201/202 into delivered or repost
  ambiguous attempts. Correlate platform message IDs for correction threads.
- [ ] **CLI/UI writer:** add authenticated reporting setting admission/effective
  show through existing operating/CLI commands; expose pending delivery/coverage.
  Add off/Teams/Slack/both persistence, mixed-update/refusal/restart integration
  tests; do not create another daemon or permit free-text authority.
- [ ] **Generator/release writer:** extend generator-owned settings/report contracts
  preserving historical replay/adopter settings; centrally regenerate examples,
  schemas and inventories. No generated files changed in this lane.
- [ ] **Qualification owner:** integrated controller/settings/calendar checks,
  supervised real Slack/Teams visibility/format/link/identity/rotation/failure
  acceptance and separately authorized pilot. Retain current-head independent
  critic, security, operations, data/accounting/migration and public-interface
  review, final gate and human owner review. Offline tests establish no FULL
  closure, release readiness or live activation.

## Stage-two validation evidence

Focused offline runs: `test_reporting_*.py` (48 tests), `test_status_report.py`
(42 tests), `test_status_report_contract_boundaries.py` (21 tests), all passed,
zero skips. Commands used unittest discovery with `-B`, test root and top level
both `.agentic/tests`. Initial stage-two red run had eight missing-module errors;
subsequent red regressions exposed reopened-action deduplication, missing schedule
retention, altered-link read-back, incomplete rollups, scope cycles and missing
composition. Each was fixed without weakening existing assertions. The immediate
header-order test was tightened to match bold section labels, excluding the report
title. Real HTTP effects were faked; no Slack/Graph API or pilot was contacted.
`git diff --check` passed. No commits or generated outputs were made.
