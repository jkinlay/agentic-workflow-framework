# External resources

Version 1.9.4. Observations grant no authority. Admit every required resource
for the exact task, principal and application session before dispatch.

## Workspace roles

Keep one writable repository root. External data is read-only. A governed
output root is separate. Never add a read-only estate as another writable
project root: restricted-token hosts may refuse split writable-root sets.

The project-owned registry contains logical names and bounded probe policy:

```json
{
  "format": "awf-external-resource-registry-1",
  "resources": {
    "raw_estate": {
      "access": "read_only",
      "required_by": ["EX-6"],
      "host_mapping": "operator_local",
      "sensitivity": "restricted",
      "probe": {
        "sample_relative_path": "sentinel/sample.bin",
        "listing_limit": 20,
        "timeout_seconds": 10
      }
    }
  }
}
```

The mapping contains the host path and owner-supplied canonical locator. Keep it
and receipts outside the repository. Never publish either value.

```json
{
  "format": "awf-external-resource-mapping-1",
  "resources": {
    "raw_estate": {
      "local_path": "SYNTHETIC_DRIVE_OR_LOCAL_PATH",
      "canonical_locator": "SYNTHETIC_OWNER_SUPPLIED_CANONICAL_LOCATOR",
      "mapping_kind": "mapped_drive"
    }
  }
}
```

AWF never guesses a UNC locator. A fallback uses only the owner-supplied value.
The private fingerprint detects remaps; tracked evidence contains only the
alias, observation time and result.

## Admission and error layers

Admission uses `Get-PSDrive` for a mapped drive, a root listing capped by
`listing_limit`, sample-file metadata and one read-only byte. It never writes to
the resource. The executing task's own token runs the check; controller,
parent-task and critic observations are not inherited.

States retain the layer that was actually observed:

| State | Observation |
| --- | --- |
| `UNOBSERVED` | No check ran for this task and principal. |
| `SANDBOX_BLOCKED` | The host sandbox refused the command before PowerShell ran. |
| `PROCESS_START_FAILED` | The host tried but could not start PowerShell. |
| `COMMAND_NONZERO` | PowerShell ran and returned an otherwise classified non-zero exit. |
| `MAPPING_MISSING` | PowerShell ran but the mapped drive was not visible. |
| `PATH_NOT_FOUND` | The mapping existed but the requested path or sample did not. |
| `ACCESS_DENIED` | Windows or the file server denied the executing identity. |
| `READ_VERIFIED` | The bounded listing, metadata query and read succeeded. |
| `STALE` | The receipt belongs to another task, principal or session, or expired. |
| `MAPPING_CHANGED` | The observed mapping identity differs from the private expected identity. |

`SANDBOX_BLOCKED` is never translated to `PATH_NOT_FOUND`. If a host can offer a
narrow approval, it calls `agentic.external_resources.admit_resource` with an
approval callback. The callback binds read-only access to the command digest;
AWF retries the identical command tuple once and records both comparable
attempts. AWF itself cannot change host sandbox policy.

Every receipt records whether the underlying permission covers one command, a
task, an application session or a project, plus its expiry. The observation is
still task-, principal- and session-bound. A new task runs admission again. A
one-command grant proves that probe only and cannot admit subsequent worker
work.

Before dispatch, call `require_admissions` with every resource the task needs.
It returns `REFUSED`, `before_work_started: true` for missing, stale, changed or
wrong-principal receipts. The controller must not launch the worker on refusal.

## Bounded scans and claims

Estate scans require all of: recursion choice, maximum depth, file limit, date
range and timeout. Recursive depth zero and any omitted or invalid bound are
refused. An accepted scan record repeats the exact bounds; it does not execute
the scan or claim the whole estate was examined.

Evidence claims stay proportional:

- one successful byte read confirms accessibility only;
- data validity stays unconfirmed without schema, provenance, manifest, hash
  and quality evidence;
- a bounded listing proves neither completeness nor absence;
- a resource receipt grants no execution, merge, Jira or publication authority.

`validate_claim` rejects confirmed completeness or absence derived from an
admission read or bounded listing. Use `public_evidence` when a tracked record
is needed; never track the full operator-local receipt.

## Commands

The entry point emits parseable JSON and returns exit 2 for refused or invalid
requests:

```text
python -B .agentic/scripts/external_resources.py validate-registry --registry RESOURCE_REGISTRY.json
python -B .agentic/scripts/external_resources.py admit --project-root PROJECT --registry RESOURCE_REGISTRY.json --mapping PRIVATE_MAPPING.json --resource raw_estate --task TICKET --principal WORKER --session SESSION
python -B .agentic/scripts/external_resources.py require --project-root PROJECT --receipt PRIVATE_RECEIPT.json --mapping PRIVATE_MAPPING.json --resource raw_estate --task TICKET --principal WORKER --session SESSION
python -B .agentic/scripts/external_resources.py public-evidence --project-root PROJECT --receipt PRIVATE_RECEIPT.json
python -B .agentic/scripts/external_resources.py validate-scan --request SCAN_REQUEST.json
python -B .agentic/scripts/external_resources.py validate-claim --project-root PROJECT --receipt PRIVATE_RECEIPT.json --claim CLAIM.json
```

The CLI refuses mapping and receipt files inside the repository. Host preflight
accepts the logical registry and private mapping through its trusted adapter and
reports process launch, repository writability, split-root policy and each
external resource as separate rows. Its output uses `{alias}` and never emits a
private path or canonical locator.
