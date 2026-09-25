# Direct upgrade to AWF 1.9.3

Fresh installation of 1.9.3 never requires an earlier AWF version. Use verified 1.9.3 source and `bootstrap_project.py --mode install`; the portable distribution can likewise install into an empty skills root.

Direct upgrade recognizes exactly 1.8.3, 1.8.9, 1.9.1 and 1.9.2. Identification is receipt- and byte-based: the registered source-manifest digest, managed-file manifest and every managed file must agree. Installation-level receipt fields such as `install_id` and `initial_config_sha256` do not identify a template version. A version string alone is insufficient.

Run a non-writing plan first, then prepare one reviewed upgrade PR:

```text
python -B scripts/bootstrap_project.py --dest PROJECT --mode upgrade --expected-manifest-sha256 SHA256 --dry-run
python -B scripts/bootstrap_project.py --dest PROJECT --mode upgrade --expected-manifest-sha256 SHA256
```

The plan reports the detected version, every chain step, per-step and total configuration diff, managed additions/changes/removals, every path/action the real run will write, operating-configuration creation/audit, and state actions. Steps change the unique `template.expected_workflow_version` scalar; 1.8.3→1.8.9 also adds the routing policy that became required, deterministically derived from retained legacy role allowlists and budget ceilings. It preserves comments, ordering, line endings, owner values and an existing valid `OPERATING_CONFIG.yaml`; no new-adoption default is injected. Operating configuration is semantically validated before any managed write, and a missing file plus its bootstrap audit are staged in the same rollback-capable transaction.

The installer may merge AWF's operating ignore block into project-owned `.gitignore`. This is strictly append-only: every pre-upgrade byte remains the exact prefix, including CRLF/LF choices and a missing final newline. Existing lines are never reordered, removed or deduplicated. If the required block is not already the suffix, the report and `--dry-run` list the lines appended. All other project-owned files remain byte-identical except the documented configuration version scalar and installer receipt/provenance metadata.

Saved state is recognized by schema, never by parseability or a SQLite header. Routing ledgers require the registered table/column shape; operating-change, critic-review and lifecycle records require their versioned record shapes. Compatible records are listed for every adjacent step and retained byte-for-byte. An incompatible or unknown record receives a read-only hash-bound archive copy and manifest entry and remains at its source path; no state record is deleted. A header-only SQLite file and valid JSON/YAML with an incompatible shape are archived rather than passed through as compatible.

| Start | Chain | Notes |
| --- | --- | --- |
| 1.8.3 | 1.8.3 → 1.8.9 → 1.9.1 → 1.9.2 → 1.9.3 | A synthetic legacy receipt is matched through its registered source and managed-manifest digests. Routing policy is derived from retained roles/budgets; a missing operating file is created from those routes with an audit. |
| 1.8.9 | 1.8.9 → 1.9.1 → 1.9.2 → 1.9.3 | Existing operating choices and fixed reviewer count remain owner policy. |
| 1.9.1 | 1.9.1 → 1.9.2 → 1.9.3 | Token/run budgets and monetary ceilings remain unchanged. |
| 1.9.2 | 1.9.2 → 1.9.3 | Existing routes and budgets remain unchanged. |

The installer refuses, without writing, an unknown receipt; an in-range version absent from the table; any version below the 1.8.0 floor; a missing or locally modified managed file (with expected/actual hashes); a non-deterministic configuration with a named owner question; and a project-owned file at a newly managed path. Resolve the reported condition and repeat the dry run. Never edit a receipt or rehash changed managed content to force a match.

Every adjacent step emits a receipt and provenance record valid for its target: the target source-manifest digest, embedded manifest and managed-file hashes are replaced together while installation identity is retained. The adjacent migration guides remain the historical semantic references. To add a recoverable version, obtain its distribution, add one ordered entry to `.agentic/upgrade/known-versions.json` (including its embedded manifest where that receipt schema requires it), add its fixture, and rerun the matrix. The entry pins provenance, source and managed-manifest digests, receipt/config schema identifiers and its next version-only step; installer code does not require a version-specific branch.
