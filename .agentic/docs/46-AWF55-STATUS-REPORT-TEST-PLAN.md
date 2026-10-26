# AWF-55 status-report tests-first plan

Baseline: `18de14c01a2cd3df66c469bca2ff85f80b00d98b`. D-1025-04:
writer approved, scoped to PLAN F978EC87 only, including the PR2 template widening,
for the exact files and sections the plan names; no cap may be raised.
Pinned writer template: `B052B510`. Tier 3, round 2 of 3 permits the retained AC34
packaging fixture to derive modes from the tested tree; all its assertions remain.
D-1026-04: persist identity-failure UNKNOWN; preserve protections. Activation deferred to 1.9.6;
AWF-192's legacy ETA fixture excluded.

## Acceptance criteria

| ID | Required outcome | Planned tests |
| --- | --- | --- |
| AC1 | Outstanding tickets exclude terminal states. | T1 |
| AC2 | Priority is grouped by AWF-48 tier and cap. | T2 |
| AC3 | Maximum available lanes show recommendation, owner override, effective value, and binding constraint. | T3-T4 |
| AC4 | Configured model/effort declarations show sources; incomplete effective context is UNKNOWN. | T5 |
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

## Coverage and remaining qualification

| ID | Existing coverage / additive verification | Limit |
| --- | --- | --- |
| T1 | Existing counts; admitted inventory exact terminal normalization. | File-only inventory is unverified. |
| T2 | Existing groups; AWF-48 record verification and incomplete/duplicate refusal. | Supplied records only. |
| T3 | Existing constraints; bound AWF-50 plan verification. | No replanning. |
| T4 | Existing fixed/max/ties/rejected evidence; owner-chain reauthentication. | CLI lacks authenticator. |
| T5 | Config defaults, operating/stream declarations, sources and unknown effective proposals. | No execution attestation. |
| T6 | Existing enabled train/cadence/schedules; independent source callback. | CLI train UNKNOWN. |
| T7 | Existing disabled/default train projections and normal-tier review policy. | No inferred live state. |
| T8 | Existing graph arithmetic; AWF-115 replay, per-quantile rounds/touchpoints. | Graph/lane/inventory/cap must agree. |
| T9 | Original snapshot hash, detached projection and record replay. | Missing anchor remains UNKNOWN. |
| T10 | Identity failures append reasoned UNKNOWN; replay rejects promotion. | No fallback authority. |
| T11 | Generated closed report/request boundaries, exact float pointers, independent text parity. | Producer records need semantic verifiers. |
| T12 | Opt-in report shape; activation trigger is library-only. | Automatic emission deferred. |
| T13 | Existing AWF event journal, immutable update/delete triggers, stable retry/collision. | No implicit database creation. |
| T14 | ACTIVE/CONFIGURED/null-state, ordinary/required exits, report success/unknown/error. | Legacy status exceptions unchanged. |
| T15 | Existing operational fail-on-call matrix; one report append, no ticket/config changes. | Trusted host state remains external. |
| T16 | Whole-catalog parity, both catalog modes, generator checks. | No catalog assertion weakened. |
| F01 | Generated report/input forms checked with strict installed-self-test form logic. | Wrapper cannot validate as evidence. |
| T17 | Temporary paths and portable fixtures. | Linux installed-runtime execution requires its runner. |

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

Generate contracts/examples through their generators, then manifests last
through `scripts/build_release.py --manifest-only`. Run both generator checks,
`--check-manifest`, release hygiene and the approved runtime/source suites.
No cap, policy, timeout or self-test exemption may change.

release_hygiene.py must PASS on the writer's tree. Bind that PASS and all required
checks to `tested_tree` in the writer report under pinned template `B052B510`.
Route (a): AWF commits the tested tree outside the sandbox, then one exact-commit
critic; no push. The writer makes no commit.

## Scope limits

See [activation status](32-ACTIVATION-STATUS.md).
CLI: `status --report --report-input --report-store`;
Identity UNKNOWN retains reasons; argument omissions stay NOT_RUN. Legacy exits and `SUPPLIED_RECORDS_ONLY` remain.
No live collectors, authenticators, retention purge or automatic hooks. Additional
operating/controller/schema-reference/changelog paths require owner scope
approval. Linux qualification and exact-commit review remain external.
