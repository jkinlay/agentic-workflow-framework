# Activation status and capabilities

Version 1.9.4. `workflow.py status` is a read-only observation. It creates no directory or lock and evaluates independent stages into `checks`. Each row has a stable `code`, `stage`, `state`, `evidence`, `remedy` and `observed_at`.

Check states are `PASS`, `INVALID`, `MISMATCH`, `UNAVAILABLE`, `UNOBSERVED` and `NOT_APPLICABLE`. An unavailable later observation never lowers a state already established by earlier stages. Text output lists every blocker before one `Next command`; JSON uses the [activation-status schema](../schemas/activation-status.schema.json).

Every GitHub REST request uses the centrally defined API version `2022-11-28`, which GitHub documents as supported through March 2028 in its [REST API version documentation](https://docs.github.com/en/rest/about-the-rest-api/api-versions). The trusted `gh` child projects each adoption-PR file page through a controller-owned expression to exactly `filename`, `status` and immutable blob `sha` before stdout enters verifier byte accounting; patch text and other provider fields are neither accepted nor recorded. Pagination remains complete within five 100-entry requests: every filename is unique, every row is typed, and the fifth page must be partial (at most 499 files). A missing page, a full fifth page, malformed output, or a missing exact added/modified receipt fails closed.

Merge identity requires REST and GraphQL to agree on the numeric and immutable repository IDs, PR number and immutable ID, base ref, head ref/SHA/repository, merged state and merge time. Missing corresponding facts are `UNOBSERVED`; conflicts are `MISMATCH`. A null or absent REST merge SHA may fall back to a complete GraphQL merge commit, but both absent remain `UNOBSERVED`. The observation records whether GraphQL or REST+GraphQL supplied the accepted identity and records the REST API version.

Status exit codes are:

| Code | Meaning |
| --- | --- |
| 0 | Ordinary status established at least INSTALLED, or `--require-active` established ACTIVE. |
| 2 | Installation/status input is invalid or no project state can be established. |
| 4 | `status --require-active` observed a state other than ACTIVE. |

`ACTIVE` remains the accepted installation/adoption state; it is not a claim that every operating capability is ready. The `capabilities` matrix reports `local_work`, `external_data_read`, `branch_publication`, `pr_creation`, `independent_review`, `jira_read`, `jira_write` and `merge_execution`. Rows use `AVAILABLE`, `UNAVAILABLE`, `UNOBSERVED` or `NOT_APPLICABLE`, with evidence and observation time.

Publication and PR readiness are completed by L3, external data by K14/L6, and immutable Jira identity by K13. Until those observers provide evidence, status says `UNOBSERVED`. Dispatch code calls `agentic.activation.require_capabilities(matrix, required)` and refuses any requirement that is not explicitly `AVAILABLE`.

Accepted-checkout verification compares the remote accepted tree with local `HEAD` and index Git object IDs and separately requires clean paths. Git attribute conversion is diagnostic, not a mismatch. Missing paths, dirty paths, index mismatch and remote-tree mismatch remain distinct. Immutable installed AWF members continue to receive manifest byte verification.

Operating reads use two pinned snapshots with before/after identity comparison. A journal reports a pending transaction; differing snapshots report `CONCURRENT_CHANGE`. Filesystem access failures report `ACCESS_UNAVAILABLE` with a safe logical path and OS error code. Mutating operating commands continue to hold the exclusive writer lock.

## Opt-in project report

The release 1.9.5 slice adds explicit on-demand reporting:

```text
python -B .agentic/scripts/workflow.py status --report --report-input INPUT.json --report-store STATE.db --json
```

Use an existing controller-owned `STATE.db` outside worktrees. Start from the generated [input form](../templates/status-report-input.yaml), fill/extract `record`, and validate against [status-report-input](../schemas/status-report-input.schema.json). Supply a UUID `record_id`, RFC3339 `created_at`, immutable provider observations and the exact project/repository/Jira/release binding. The release digest hashes project ID, expected workflow version, Jira enablement and scope selector; it introduces no configuration field.

JSON adds `status_report`; its `report` values validate against [status-report](../schemas/status-report.schema.json). Text appends independently labelled values. Both retain the legacy status exit, including report failure. Without `--report`, output is unchanged.

Reports cover outstanding tickets, tier/cap groups, safe/recommended/effective lanes and owner decision, model/effort declarations and sources, train state/cadence, and reproducible P50/P80 completion with basis/confidence. Exact configured Done and `Cancelled` labels normalize to terminal states; other spellings remain outstanding.

Source mode is `SUPPLIED_RECORDS_ONLY`: AWF-48 assignment, AWF-50 plan/override and AWF-115 graph verifiers run before projection. This does not observe current Jira/plans or recompute lanes. Inventory/train authentication and owner-source reauthentication require trusted host callbacks, unavailable through this file-only CLI; their dependent values remain coded `UNKNOWN`. Model declarations do not establish execution-effective proposals; missing dispatch context stays `UNKNOWN`.

Identity rejection, missing observations or disabled Jira returns `NOT_RUN` without a record. Accepted reports append one immutable AWF event retaining original request/configuration/operating snapshots, their hash, verified projection, values, timestamp and trigger. Identical retries retain sequence; changed content under the same UUID rejects. `replay_report` reproduces values from retained inputs/projection. No database is created, no source is mutated, and no adapter is enabled. Automatic activation emission remains open for release 1.9.6; an activation-format fixture proves no automatic hook.
