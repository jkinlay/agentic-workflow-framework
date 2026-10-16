# Publication safety

Version 1.9.4. Publication is blocked unless the exact candidate history and
provider text pass a history-aware scan. `git diff --check` checks whitespace
only and is never publication-safety evidence.

## Deny configuration

The operator-local mapping is `.agentic-state/publication-deny.json`, kept
ignored and untracked. Version 1 supports aliases, deny literals, named deny
regexes, internal hostnames, and narrow `builtin_allow` entries. Aliases such as
`{raw_estate}` are safe tracked forms; `publication-render --input REPORT`
substitutes them. Identity checks reject tracked aliases, links, junctions, and
reparse points. Regexes use a bounded linear grammar; lookaround,
backreferences, empty matches, nested repeats, and unsafe forms are refused.

The reviewed `.agentic/PROJECT_CONFIG.yaml` may contain:

```yaml
publication:
  deny_literals: []
  deny_regexes: []
  internal_hostnames: []
  large_file_allowances:
    - path: artifacts/existing.dat
      sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      reason: Reviewed historical artifact
      approver: owner-identity
```

An allowance names one canonical repository-relative POSIX path, its exact
lower-case SHA-256 content digest, a trimmed review reason, and a trimmed
approver identity. The list is ordered, closed, and path-unique. Unknown or
malformed entries fail configuration loading without echoing their metadata.
Absent and empty lists grant no authority.

Only the exact project-config blob at the resolved candidate head grants an
allowance. A caller-supplied list is an equality assertion, including explicit
`[]`; it cannot add or reorder entries. An explicit filesystem config remains
an additive detector source only. Its allowances are ignored, and its raw
digest is recorded as `supplemental_project_config_sha256`.

## Scan and receipt

```text
python -B .agentic/scripts/workflow.py publication-scan --base BASE --head HEAD --pr-body PR_BODY --json
```

The scan covers strict-UTF-8 commit messages, every parent's added and deleted
lines (including roots and merges), complete head content for touched paths,
and supplied provider text. Detector/value pairs proven in the base are
`PRE_EXISTING`; candidate additions are `BLOCKING`. Unknown coverage, binary,
invalid-UTF-8, or ordinary content over 32 MiB blocks a pass. Renames cover
both paths.

A large-file allowance applies only when its path is an oversized regular blob
already present in the selected base and its bytes still match the configured
digest. New, moved, deleted, type-changed, small, changed, or
changed-then-reverted files block. Hashing re-checks Git object size and content
with replacement objects disabled. One scan permits at most 512 MiB per file,
2 GiB across unique blob OIDs, and 60 seconds for the shared hashing phase.
Digests and sizes are cached by OID. Size disagreement, overflow, timeout,
stream failure, or child failure blocks; it never passes on partial evidence.

The receipt binds the raw candidate config with `project_config_sha256` and its
parsed value with `project_config_binding_sha256`. Ordered
`large_file_allowance_accounting` records every declaration, base disposition,
and candidate event. Paths have full digests and safe display forms; reason and
approver text never appear in receipts or rendered output.

Preserve the JSON receipt. Base, head, body digest, arrays, and counts must
match the pushed candidate. `PASS` permits only `PRE_EXISTING` findings, full
base allowance matches, no allowance events, and no unscanned content. Scan
prospective comments with `--comment FILE`.

## Draft-to-ready authorization

A scan PASS, current validation, or green CI never authorizes a draft-state
change. Mark-ready requires an explicit owner/manager authorization bound to
the exact PR and head, a pre-issued request and expiry, and an authenticated
immutable provider observation binding accepted configuration and trusted
time. Persist the authorized lifecycle transition before mutation and observe
`draft_cleared` afterward. Otherwise persist refusal, retain draft state, and
do not dispatch review.

`PRE_EXISTING` applies only here. Private deny baseline acceptance is the
separate [`37-PRIVATE-DENY-SCAN.md`](37-PRIVATE-DENY-SCAN.md) gate.

## Unpublished rewrite

`publication-rewrite --base BASE --branch BRANCH --commits 1 --message-file FILE`
scans a quarantined one-commit replacement and proves tree equality. Under the
repository-wide lock it snapshots refs, worktree state, reflogs, indexes, and
object storage; performs exact compare-and-swap; then proves the final census.
Upstreams, remote branches, dirty state, unsupported counts, or other old-tip
claimants are refused. Published-history rewriting remains an owner decision.

The replacement uses validated repository-local author identity. Stale-tip and
post-CAS failures retain recovery evidence and name the claimant; never delete
retained objects manually. Git children strip inherited `GIT_*` values and add
fixed config, hook, replacement-object, protocol, and lazy-fetch defences.
