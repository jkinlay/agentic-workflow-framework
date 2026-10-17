# AWF-50 remaining-scope design plan

Tests precede each implementation. This plan covers only the five undelivered Jira items; existing `operating.py`, planning primitives, and on-demand roles remain unchanged.

## Tests-first design

### 1. Configured ceilings above six

Add three tests to `test_planning.py`: `test_generated_schemas_accept_complete_a_to_j_catalogue`, `test_generated_schema_requires_reviewer_and_accepts_epic_override_above_f`, and `test_missing_max_lanes_keeps_six_and_enabled_broker_still_lowers_it`. Load the generated schemas directly with `Draft202012Validator`, then run existing semantic validation. The project fixture sets `execution.max_lanes: 12`; its operating fixture has count ten, A-J worker/reviewer routes, no shared critic, and a `PROJ-123` override with J worker/reviewer routes. A derived negative fixture removes J's reviewer. Separate fixtures omit `max_lanes` and enable a four-worker broker. Main rejects `max_lanes`, count ten, G-J top-level routes, and override J, while its reviewer catalogue stops at F. Passing means the complete fixture validates raw and end to end, missing J reviewer fails, omission yields six, and the broker yields four.

Change `scripts/generate_contracts.py` on every catalogue surface: declare optional positive `execution.max_lanes`; remove `streams.count`'s static maximum; replace literal A-F top-level stream properties and Epic-override pattern with the same uppercase spreadsheet-label grammar used by `stream_labels()` and the shared stream-route schema; replace the A-F reviewer-condition loop with a label-agnostic condition requiring every declared stream route's reviewer when `critic` is absent. Existing semantic validation supplies the configuration-specific active labels and binds count to `max_lanes` (default six) and the enabled broker. Regenerate both config schemas; data flows from project config plus operating snapshot into that validation and the effective-ceiling calculation.

### 2. Controller wiring

Add `test_project_start_and_intake_plan_before_dispatch` and `test_controller_recomputes_for_merge_files_ticket_and_override_without_mid_round_reassignment` to `test_continuous_controller_planning.py`. A temporary controller store, authenticated inventory/resource observations, and dispatch spies exercise every trigger. Main fails because `production_controller_cycle` never calls planning. Passing requires planning before initial/intake dispatch, recomputation after PR merge, changed files, ticket add/close, or override change, and a durable pending trigger—not reassignment—during an active round.

Wire `planning.recompute_plan` through `.agentic/lib/agentic/continuous_controller.py`; expose only the closed observation inputs in `.agentic/scripts/continuous_controller.py`. Trigger evidence flows from observed controller events to recomputation, then the next-round lane selection.

### 3. Read-only Jira backlog input

Add `test_authenticated_backlog_snapshot_collects_brief_pr_and_diff_paths` and `test_unauthenticated_backlog_snapshot_fails_before_graph_build` to `test_planning.py`. Inline provider fixtures use `src/brief.py`, `src/pr.py`, and `src/dirty.py`, plus foreign, stale, and unsigned variants. Main has no reader. Passing means a new `tickets_from_provider_snapshot` in `planning.py` verifies provider/repository bindings, canonicalises exact paths from briefs, open-PR file lists, and worktree diffs, and supplies source-attributed tickets to `build_backlog_graph`; any unauthenticated component refuses before graph creation. The adapter remains read-only.

### 4. Durable plan records

Add `test_plan_record_round_trips_immutably_through_awf91_state_store` to `test_continuous_controller_planning.py`. Its temporary-store fixture writes a plan, reopens it, then attempts replacement, replay, and byte tampering. Main keeps records only in caller dictionaries. Passing means controller persistence uses the merged AWF-91 state-store API, verifies `plan_sha256` on write and every read, survives restart, and refuses mutation or duplicate identity before dispatch. No `store.py` change is planned.

### 5. Operator documentation

Add `test_operating_doc_covers_max_lanes_overrides_and_migration` to `test_planning.py`, reading Doc 29 as UTF-8. Main lacks all three topics. Passing requires only: how `execution.max_lanes` bounds stream count, how authenticated fixed/max owner overrides interact with sealed safe capacity and round boundaries, and how an existing omitted setting retains six during migration. Then update `.agentic/docs/29-OPERATING-CONFIGURATION.md`; release hygiene must retain its word limit.

## Declared implementation map

| Item | Planned paths |
| --- | --- |
| 1 | `scripts/generate_contracts.py`; `.agentic/schemas/operating-config.schema.json`; `.agentic/schemas/project-config.schema.json`; `.agentic/tests/test_planning.py` |
| 2 | `.agentic/lib/agentic/continuous_controller.py`; `.agentic/scripts/continuous_controller.py`; `.agentic/tests/test_continuous_controller_planning.py` |
| 3 | `.agentic/lib/agentic/planning.py`; `.agentic/tests/test_planning.py` |
| 4 | `.agentic/lib/agentic/continuous_controller.py`; `.agentic/tests/test_continuous_controller_planning.py` |
| 5 | `.agentic/docs/29-OPERATING-CONFIGURATION.md`; `.agentic/tests/test_planning.py` |

Every slice also regenerates `MANIFEST.json` and `MANIFEST.md` last. These are all AWF-50 allowed paths. No `operating.py`, `installer.py`, `canonical.py`, adapter, fixture, or other document changes are planned.

## Dependencies and serial slices

Start from fresh main after AWF-88 leftovers and AWF-91c/PR #178 merge. Items 1 and 2 wait for AWF-88's generator/schema and controller results; item 4 waits for #178's final store API. The generator slot is strictly `AWF-115 -> AWF-36 -> AWF-88 -> AWF-117 -> AWF-50 -> AWF-54 -> AWF-34`.

Suggested small PRs are: (A) item 1 after AWF-117; (B) item 3; (C) items 2 and 4 after B and #178; (D) item 5. A/B/D share `test_planning.py`; C shares controller paths with AWF-91c and AWF-34. Their substantive paths are otherwise disjoint, but all remain serial because generated manifests overlap every lane.

## Open owner questions

- What authenticates backlog snapshots, their freshness, and PR/diff-to-ticket attribution?
- Which exact controller events, deduplication keys, and transaction ordering define each trigger?
- Which #178 namespace/API stores plans, and what migration applies to existing in-memory history?
- What migration wording and treatment apply to existing stream counts above six?
