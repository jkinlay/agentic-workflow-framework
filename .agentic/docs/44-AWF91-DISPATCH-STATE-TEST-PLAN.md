# AWF-91 dispatch-state test plan

Tests-first design only, based on `main` at `de76c490a490c5c244094f45adc0760ace502170`.
No runtime, schema, adapter, or test changes belong in this slice.

## Acceptance criteria (verbatim)

1. "Existing 1.9.4 durable dispatch rows and adapter calls migrate or are refused explicitly, with tests over legacy payloads."
2. "Provider observation happens outside the write transaction; lane-start waiver evidence is persisted before start and survives a crash."
3. "Parked and worktree_diff reservations come from authenticated, canonicalised sources and block start even with an allowance."
4. "No refill before replan for every non-terminal state (PENDING, IN_FLIGHT, UNKNOWN, launched); triggers are authenticated (test_pending_replan_is_sealed_before_round_boundary_refill)."

Closure is `FULL`: no skips; refusals precede provider mutation/launch. Fixtures
use temporary directories and POSIX paths. Legacy databases use frozen 1.9.4
DDL, not the new writer. Crash tests reopen the store.

## Named tests

| Test / target | Fixture | Action | Expected failure on main | Pass condition |
| --- | --- | --- | --- | --- |
| `test_legacy_194_dispatch_rows_are_migrated_or_explicitly_refused` / `.agentic/tests/test_continuous_controller.py` | Literal 1.9.4 `PENDING`, `IN_FLIGHT`, `UNKNOWN` rows. | Open/recover. | No migration; rows are silently current. | Atomic identity-preserving migration or state-specific refusal; no deletion/reset/call. |
| `test_legacy_194_mixed_migration_is_atomic` / `.agentic/tests/test_continuous_controller.py` | Two valid legacy rows and one ambiguous payload. | Initialize, fail, reopen. | No migration transaction exists. | All rows migrate or the byte-equivalent before-image remains. |
| `test_legacy_adapter_payload_is_refused_before_runner_launch` / `.agentic/tests/test_reference_controller_adapter.py` | Old call payloads; runner spy. | Invoke dispatch/observe. | Old shape reaches preparation or fails generically. | Migrate/refuse explicitly; replay cannot replace state; zero runner calls. |
| `test_lane_start_provider_observation_occurs_outside_write_transaction` / `.agentic/tests/test_continuous_controller_planning.py` | Authenticated allowance observer opens a second SQLite writer. | Run PLAN/LANE_START. | Main lacks the path; #166 has no regression guard. | Observer writes successfully; validation/persistence occur afterward. |
| `test_lane_start_waiver_is_persisted_before_dispatch_begin` / `.agentic/tests/test_continuous_controller_planning.py` | Valid allowance; dispatch spy reads protected state. | Start one lane. | #166 stores no dispatch-bound waiver. | Before `begun_at`/call, evidence binds dispatch, ticket, stream, tuple, allowance, time, and observation digest. |
| `test_lane_start_waiver_survives_crash_before_receipt` / `.agentic/tests/test_continuous_controller_planning.py` | Adapter crashes immediately after durable evidence assertion. | Restart and reconcile. | `UNKNOWN` lacks per-launch waiver proof. | Exact evidence survives; observation, never relaunch, receives its digest; mismatch blocks. |
| `test_authenticated_parked_reservation_blocks_start_even_with_allowance` / `.agentic/tests/test_continuous_controller_planning.py` | Provider-bound parked `src/shared.py`; valid exact allowance. | Schedule overlapping tickets. | #166 considers active declared paths only. | Both starts block; unrelated work continues. |
| `test_authenticated_worktree_diff_blocks_start_even_with_allowance` / `.agentic/tests/test_continuous_controller_planning.py` | Repository/head/tree-bound diff for `src/shared.py`; valid allowance. | Canonicalize and schedule. | Dirty paths are absent from reservations. | Canonical diff reservation overrides allowance. |
| `test_reservation_sources_are_authenticated_and_git_canonical` / `.agentic/tests/test_continuous_controller.py` | Caller assertions, foreign/stale/partial evidence, case/separator/traversal/link aliases, and one valid NFC path. | Admit inventory. | No closed parked/diff source boundary. | Invalid evidence refuses before scheduling; aliases fail or collapse; valid reservation blocks. |
| `test_pending_replan_is_sealed_before_round_boundary_refill` / `.agentic/tests/test_continuous_controller.py` | `PENDING` round boundary, successor, authenticated trigger; crash after sealing. | Refill, restart, resume. | Pending can be cancelled/replaced without a seal. | No intent before durable seal; restart consumes the inventory/tuple-bound seal once. |
| `test_all_nonterminal_states_block_refill_until_authenticated_replan` / `.agentic/tests/test_continuous_controller.py` | Subtests for `PENDING`, `IN_FLIGHT`, `UNKNOWN`, and adapter `launched`; invalid trigger variants. | Run refill/cycle. | Reservations lack a round-boundary gate; accepted receipt can hide launched work. | Every state blocks; invalid/replayed triggers create no seal; exact fresh trigger permits one refill. |
| `test_launched_state_survives_restart_as_nonterminal` / `.agentic/tests/test_reference_controller_adapter.py` | Durable launched session without terminal proof. | Restart adapter/controller and refill. | Receipt is treated as completed dispatch. | Launch consumes lane/capacity until authenticated terminal proof and sealed replan. |

## Traceability

`AWF50B-A-005` maps to authenticated parked/diff reservations;
`AWF50B-A-007` to non-terminal replan gating. Step B `#6` maps to legacy
state/calls, `#10` to observation outside transactions, `#11` to durable
lane-start evidence, `#14` to reservation precedence, and `#22` to the map
below. Criteria 1-4 map to their same-order three-row groups above.

## Declared changed-file map (Step B #22)

- `.agentic/lib/agentic/continuous_controller.py`: state, migration, seals, gates.
- `.agentic/scripts/continuous_controller.py`: closed CLI/adapter wiring.
- `.agentic/adapters/reference_controller_adapter.py`: versioned calls and observations.
- `.agentic/tests/test_continuous_controller.py`: store/state regressions.
- `.agentic/tests/test_continuous_controller_planning.py`: allowance/planning regressions.
- `.agentic/tests/test_reference_controller_adapter.py`: adapter/restart regressions.
- `.agentic/docs/34-CONTINUOUS-CONTROLLER.md`: normative behavior.
- `MANIFEST.json`, `MANIFEST.md`: generated last, never hand-edited.

No other path is declared; stop for an owner-approved scope amendment if the
merged API requires one.

## Sequence and owner questions

Observe AWF-150/PR #166 merged, branch from fresh `main`, reconcile this map,
then add each test and record its red result before fixes. Implement
in criterion order, preserve #166 regressions, run the three focused modules
and release hygiene, then generate manifests last. This prep does not close
AWF-91 or authorize Jira/PR/merge actions.

Owner decisions needed before implementation: migrate versus refuse each
legacy state; define whether `launched` is a controller status or adapter
substate; name authoritative parked/diff artifacts; enumerate authenticated
replan triggers; choose a dispatch field versus append-only waiver table; and
confirm the finding mapping above against the retained full finding text.
