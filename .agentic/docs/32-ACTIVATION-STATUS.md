# Activation status and capabilities

Version 1.9.3. `workflow.py status` is a read-only observation. It creates no directory or lock and evaluates independent stages into `checks`. Each row has a stable `code`, `stage`, `state`, `evidence`, `remedy` and `observed_at`.

Check states are `PASS`, `INVALID`, `MISMATCH`, `UNAVAILABLE`, `UNOBSERVED` and `NOT_APPLICABLE`. An unavailable later observation never lowers a state already established by earlier stages. Text output lists every blocker before one `Next command`; JSON uses the [activation-status schema](../schemas/activation-status.schema.json).

Every GitHub REST request uses the centrally defined API version `2022-11-28`, which GitHub documents as supported through March 2028 in its [REST API version documentation](https://docs.github.com/en/rest/about-the-rest-api/api-versions). Merge identity requires REST and GraphQL to agree on the numeric and immutable repository IDs, PR number and immutable ID, base ref, head ref/SHA/repository, merged state and merge time. Missing corresponding facts are `UNOBSERVED`; conflicts are `MISMATCH`. A null or absent REST merge SHA may fall back to a complete GraphQL merge commit, but both absent remain `UNOBSERVED`. The observation records whether GraphQL or REST+GraphQL supplied the accepted identity and records the REST API version.

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
