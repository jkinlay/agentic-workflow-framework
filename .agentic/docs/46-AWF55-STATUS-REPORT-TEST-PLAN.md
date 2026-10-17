# AWF-55 status-report tests-first plan

Design basis: `main` at `121944bb17e4c6c1a8b92f6f2b474770de14ad34`.
No runtime/schema/test changes. Implementation is Tier 3, `FULL` closure.

## Acceptance criteria

| ID | Required outcome | Planned tests |
| --- | --- | --- |
| AC1 | Outstanding tickets exclude terminal states. | T1 |
| AC2 | Priority is grouped by AWF-48 tier and cap. | T2 |
| AC3 | Maximum available lanes show recommendation, owner override, effective value, and binding constraint. | T3-T4 |
| AC4 | Each agent type shows model/effort and source; route meaning awaits the ruling below. | T5 |
| AC5 | Merge-train on/off and cadence. AWF-54 Q2=B means adopter default on, AWF repository default off; Q4=B means source owner review only when its tier's normal final gate requires it. | T6-T7 |
| AC6 | Expected completion includes deterministic basis and confidence. | T8-T9 |
| AC7 | Unavailable inputs are reasoned `UNKNOWN`, never guessed. | T10 |
| AC8 | JSON validates against the closed schema and text/JSON values agree. | T11 |
| AC9 | Immutable records; only a new report record may be written. | T13, T15 |
| AC10 | Activation/on-demand formats agree; no-report output and legacy exits 0/2/4, including `status --require-active`, remain unchanged. | T12, T14 |
| AC11 | Generated schema/catalog checks are clean. | T16 |
| AC12 | Portable fixtures pass Linux and Windows installed-runtime CI. | T17 |

Fixtures use `TemporaryDirectory` and POSIX-style member names. `S` means
`.agentic/tests/test_status_report.py`, `A` means
`.agentic/tests/test_adoption_status.py`, and `G` means
`scripts/tests/test_generate_contracts_status_report.py`.

## Named tests

| ID / test | Fixture | Expected failure on main | Pass condition | Target |
| --- | --- | --- | --- | --- |
| T1 `test_report_counts_outstanding_excludes_done_cancelled` | Bound mixed-status inventory. | No report builder. | Only non-terminal tickets counted. | S |
| T2 `test_priority_groups_by_tier_and_cap` | Mixed Tier 1/2/3 AWF-48 records. | No grouping. | Exact tier/cap groups and sources. | S |
| T3 `test_lanes_recommendation_without_override` | Verified plan without decision. | No lane metric. | Recommendation is effective with constraint. | S |
| T4 `test_lanes_owner_override_fixed_count_and_max_cap_records_who_when_and_binding_constraint` | Fixed/count-cap decisions. | No override model. | Actor, time, override, effective value, constraint. | S |
| T5 `test_models_per_agent_type_from_defaults_and_overrides_with_source` | Stable routing/governance/operating and selector variants. | No route report. | Sources cited; incomplete effective inputs are `UNKNOWN`. | S |
| T6 `test_merge_train_on_with_cadence` | Verified enabled AWF-54 record. | No train metric. | On and cadence reproduced. | S |
| T7 `test_merge_train_off` | AWF-repository and adopter-default records. | No train metric. | Off/on defaults and Q4 review rule reproduced. | S |
| T8 `test_eta_deterministic_from_fixture_graph_with_p50_p80_and_basis` | Graph, rounds, durations, lanes, touchpoints. | No ETA. | Repeatable P50/P80, basis, confidence. | S |
| T9 `test_eta_reproducible_from_recorded_inputs_hash` | Same snapshot twice. | No record hash. | Same normalized input hash and ETA. | S |
| T10 `test_missing_jira_or_plan_inputs_reported_unknown_with_reason` | Missing/invalid optional sources. | No metric envelope. | Each affected metric is reasoned `UNKNOWN`. | S |
| T11 `test_report_json_validates_against_schema` | Complete known report. | Schema absent. | Closed-schema validation; text/JSON parity. | S |
| T12 `test_on_demand_matches_activation_format` | One snapshot, two triggers. | Hooks absent. | Same contract; only trigger/time differs. | S |
| T13 `test_state_record_immutable` | External `state.db`, retry/collision/rewrite. | Store absent. | Rewrite/delete refused; retry stable; new report appends. | S |
| T14 `test_activation_require_active_exit_codes_and_output_unchanged_apart_from_report` | ACTIVE, non-ACTIVE, and no-state snapshots. | Report option absent. | No-report bytes unchanged; report appended; exits 0/4/2 unchanged. | A |
| T15 `test_report_is_read_only` | Spies for Jira/config/tier/lane sources and report store. | No boundary. | Zero source mutations; one permitted append. | S |
| T16 `test_generator_check_clean_for_status_report_schema` | Generated report/input schemas and catalog. | Members absent. | Generation and `--check` match. | G |
| T17 `test_status_report_fixtures_are_portable` | Temporary root, POSIX members. | Suite absent. | Same Linux/Windows assertions; no drive/UNC fixture. | S |

## Planned changed-file map and order

- Tests: `S`, `A`, `G`, and `scripts/tests/test_generate_examples_status_report.py`.
- Runtime: `.agentic/lib/agentic/status_report.py`, `status_report_store.py`, and
  `cli.py`.
- Generated: `.agentic/schemas/status-report{,-input}.schema.json`,
  `.agentic/templates/status-report{,-input}.yaml`,
  `.agentic/examples/status-report.json`, `scripts/generate_contracts.py`, and
  `scripts/generate_examples.py`.
- Guidance: `.agentic/SPECIFICATION.md`, `.agentic/docs/20-NEW-PROJECT-SETUP.md`,
  and `.agentic/docs/32-ACTIVATION-STATUS.md`.
- Inventory: `MANIFEST.json` and `MANIFEST.md`, regenerated last.

The single-lane generator slot is **after AWF-54 and before AWF-34**. Any
required path outside this map needs an owner-approved scope amendment.

## Open owner contract questions

1. CLI/alias shape and activation trigger.
2. `NOT_RUN`/`FAILED` rendering and shell-exit behavior, especially for an alias.
3. Accepted project and release identity.
4. Terminal-ticket semantics and the disabled-Jira inventory authority.
5. AWF-54 cadence, ETA range/basis, and confidence thresholds.
6. Lane-override presentation when it exceeds the safe recommendation.
7. Immutable-record retention, archival, and authorized purge.
8. Declared versus execution-effective routing semantics and the binding tuple.
9. AWF-50 versus AWF-115 as the planning source, or a validated combination.
