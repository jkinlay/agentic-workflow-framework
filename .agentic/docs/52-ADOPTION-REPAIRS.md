# Adoption repairs and fresh defaults

The source repair completes native A-L identity handling in model selection,
routing-ledger draining, planning, owner publication and host handoff. Existing
capacity, independent-review, reservation and authorization checks still apply.
Twelve configured workers require fourteen observed slots with the controller
and one shared critic; additional simultaneous specialist work needs capacity.

New project templates select twelve streams. Ordinary workers, controller,
critic and specialists use `gpt-6.1-sol/high`; qualifying simple tasks use
`gpt-6-luna/high`. Tier 1 alone does not select the simple route. Every simple
eligibility predicate still applies, and review/risk floors remain Sol/high.
Missing host support refuses without fallback. The canonical release defaults
are immutable `.agentic/defaults/routing.yaml`; an adopter's `.agentic/routing.yaml`
remains project-owned and is preserved through upgrades.

Fresh bootstrap consumes the packaged operating template. Existing operating
bytes, audit history, pins and reviewer allocation are retained. Missing
operating files on older upgrades retain the conservative three-stream
initialization, and omitted older `execution.max_lanes` still means six. Fresh
governance explicitly supplies twelve rather than widening an old implicit cap.
The optional five-slot acceleration contract remains a separate topology.

Project shell launchers belong in `PROJECT_LAUNCH_SURFACES.json`; do not edit the
pinned framework inventory. Calls under production script roots must still use
`child_env(...)` directly, retain necessary application variables, and read the
intended project's exclusions. Validate actual credential stripping and error
handling; a declaration does not prove runtime behavior or grant authority.

Legacy routing algorithm tests use an explicit immutable fixture, so an
adopter's accepted custom models do not change synthetic test assumptions.
Fresh-default tests separately check the requested release policy and templates.

This repair does not replace an already published release. Release acceptance,
a new version identity, independently verified distribution pins, adoption
review and current ACTIVE evidence remain required before live operation.
Installation remains quiescent; automation switches are enabled only after
bootstrap under accepted project governance. Jira identities and ticket scope
are supplied at project startup; an unbound Jira target permits no writes.

## Twelve-stream regression evidence

The identity source is `lib/agentic/lane_labels.py::STREAM_LABELS`. Supported
operating labels and the schema generator consume it; the public spreadsheet
helper still supports labels beyond L for generic callers. Structural labels
never establish observed host capacity. The planner must retain an explicitly
reviewed `execution.max_lanes` while normalizing policy; dropping it incorrectly
restores the legacy six-lane ceiling.

Paths below are relative to `.agentic/` unless they begin with `scripts/`.

| Criteria | Consumer | Regression evidence |
| --- | --- | --- |
| A1/A2 | `operating.py`, `scripts/generate_contracts.py` at repository root | `test_twelve_streams.test_supported_operating_and_schema_labels_are_canonical`; preserves Z/AA/AB helper behavior |
| A1/A2 | `streams.py`, `plan_streams.py` | `test_twelve_streams.test_planner_describes_all_supported_labels`; `test_plan_streams.test_cli_twelve_stream_json_and_markdown_match` |
| A2/A4 | `model_routing.py` | `test_twelve_streams`: worker/fix/critic A-L matrix, omitted stream refusal, malformed labels including containers/booleans, unavailable Sol/high and below-floor reviewer pins |
| A1/A2 | `handoff.py` | `test_handoff.test_upper_global_and_epic_routes_round_trip_and_reject_unknown_labels`: G-L route pins survive projection; M/empty/multichar labels refuse in global and Epic route sets |
| A3 | Routing ledger | `test_operating_integration.test_upper_streams_continue_only_retained_ticket_and_owner_after_settlement`: G-L admission, settlement, count reduction, same-owner continuation, new-ticket/replacement-owner refusal |
| A3/A6 | Planner capacity | `test_operating_integration.test_twelve_stream_host_boundary_and_upper_paused_owners`: controller/critic reservations, synthetic observed host boundaries, draining G-L and paused L reservation |
| A3/A6 | Protected ceilings and exclusive resources | `test_operating_integration.test_twelve_labels_do_not_override_project_or_broker_ceilings`; `test_upper_workers_share_one_exclusive_resource_without_capacity_bypass` |
| A1 | Owner publication | Existing `test_owner_publication.test_twelve_stream_owner_handoff_retains_every_identity` (separate consumer qualification) |
| A5 | Upgrade preservation | Existing `test_upgrade_195.py` and `test_upgrade_196.py`; real installed qualification remains a release responsibility |
| A7 | Published failure | `test_twelve_streams.test_published_195_runtime_retains_original_upper_label_refusal` executes archived published routing in an isolated interpreter, verifies the original manifest pin, retains A-F success and G-L refusal |

The published regression retains commit
`8ad399943c8f5e4f17db72f3e321b9365ee27782` and manifest SHA-256
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
It does not install or modify published bytes. Closed distributions without
historical source explicitly skip that source-only regression.

Remaining six-lane constants in `streams.py::DEFAULT_NATIVE_EXECUTION` and
`operating.py::DEFAULT_MAX_LANES` are compatibility defaults. Explicit legacy
A-F test scenarios retain compatibility coverage. Historical design discussion
and content-addressed published fixtures remain unchanged; hexadecimal A-F
patterns are unrelated to stream identities.

Local regression evidence does not establish installed remediation. Central
integration must regenerate manifests and check schema/example parity from the
combined sources, then run installed-runtime and release qualification. The
reported PR #271 Windows Python 3.11 bootstrap subprocess exhausted its existing
180-second limit; the supplied summary does not locate the slow child phase.
Retain that limit and assertions, and rerun the integrated shard with phase
elapsed-time evidence. A label-helper microbenchmark cannot establish bootstrap
performance or explain a hosted timeout.
