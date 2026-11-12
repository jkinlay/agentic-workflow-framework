# Publication safety

Version 1.9.6. Publication is blocked unless the exact candidate history and
provider text pass a history-aware scan. `git diff --check` checks whitespace
only and is never publication-safety evidence.

## Operator-local deny mapping

The default mapping is `.agentic-state/publication-deny.json`; the installed
`.gitignore` keeps it untracked. External review hosts use the same filename in
their state directory. Version 1 supports:

```json
{
  "version": 1,
  "aliases": {"raw_estate": ["<operator-private-value>"]},
  "deny_literals": ["<operator-private-literal>"],
  "deny_regexes": [{"id": "private_locator", "pattern": "<operator-private-regex>"}],
  "internal_hostnames": ["<operator-private-hostname>"],
  "builtin_allow": [{"id": "unc_path", "pattern": "<explicit-local-exception>"}]
}
```

Aliases `{raw_estate}` and `{output_root}` are safe tracked forms;
`publication-render --input REPORT` substitutes them. Mappings must remain
Git-proven ignored and untracked. Identity checks reject index/`HEAD` aliases,
links, junctions and reparse points. Operator-local `builtin_allow` tunes
built-in false positives; tracked config cannot. Regexes are limited to 256
characters and a bounded linear grammar; lookaround, backreferences, empty
matches, nested repeats and other unsafe forms are refused.

Tracked `publication` may declare `deny_literals`, named `deny_regexes` and
`internal_hostnames`. These add detectors; keep machine paths/network mappings
operator-local.

## Baseline allowances

Optional `publication.large_file_allowances` entries require exact POSIX `path`,
lowercase `sha256`, `reason` and `approver` (maximum 256 entries; duplicate paths
and wildcards refused). Matching base blobs over 32 MiB are
`PRE_EXISTING`; unrecorded, changed or mismatched blobs block.

Tracked head bytes bind `project_config_sha256`. Supplemental bytes bind
`supplemental_project_config_sha256` only when supplied; they add detectors,
never allowance authority. Working-tree edits cannot grant allowances.

## Scan and receipt

```text
python -B .agentic/scripts/workflow.py publication-scan --base BASE --head HEAD --pr-body PR_BODY --json
```

Covers strict-UTF-8 messages, each parent's added/deleted lines (roots/merges),
touched head paths, and provider text. Base-proven pairs are `PRE_EXISTING`;
additions are `BLOCKING`. Unknown base coverage, binary, invalid-UTF-8 or
oversize blocks a pass. Renames cover both paths; reads/hashes bounded.

### Registered SQLite fixture

Only `.agentic-state/routing/ledger.sqlite` at pinned SHA-256 `c0b83f21…5f98dd`
(also `…/upgrades/blobs/<sha256>`) may leave the binary path after
UTF-8/schema/types/size/integrity checks; scan logical TEXT and raw
Latin-1/surrogateescape views. Other binaries stay blocked.

Built-ins detect network shares, absolute drive/home paths, private IPv4 and
unique-local IPv6. Findings name commit, path/channel, line, detector ID and
SHA-256 digest. Published-history exceptions are exact path+detector+full-line
SHA-256 identities (commit+source when closed); mappings cannot broaden them or
apply them to provider text. Pre-push defaults are portable empties from
worktree/PATH—no `awf194`/`awf195` paths.

Preserve the JSON receipt. Base, head and `pr_body_sha256` must match candidate
and body. Counts bind arrays. `PASS` allows only `PRE_EXISTING`; blocking,
unscanned, inconsistency or tuple/body change invalidates it. `--comment FILE`
scans comments.
## Draft-to-ready authorization

A publication-scan PASS, current validation or green CI never authorizes a
draft-state change. Mark-ready requires an explicit owner/manager authorization
record bound to the exact PR and head. Render the pre-issued request ID, issue
time and expiry before creating the owner comment. Require authenticated
immutable provider observation binding accepted configuration and trusted time;
comment time is observation, not signed input. Persist authorization before mutation and observe `draft_cleared`.
If authorization is absent, invalid, mismatched or only
offline-consistent, persist the refusal record, leave the PR draft and do not
dispatch review.

`PRE_EXISTING` applies only here. Private deny baseline acceptance is the
separate [`37-PRIVATE-DENY-SCAN.md`](37-PRIVATE-DENY-SCAN.md) gate; neither
receipt waives the other.

## Unpublished rewrite

`publication-rewrite --base BASE --branch BRANCH --commits 1 --message-file FILE`
scans a quarantined one-commit replacement and proves tree equality. Under the
repository-wide AWF rewrite lock it snapshots all refs, pseudorefs, worktree
HEADs/state, reflogs, indexes, object storage and objects; installs the recorded
objects; performs exact CAS; and proves the final census. Every AWF writer must
honor the lock. Upstreams, remote branches, dirty state, unsupported counts or
other claimants of old commits are refused. Published-history rewrites require
an owner decision.

The replacement uses repository-local `user.name` and `user.email`, validated
by Git and passed explicitly to `commit-tree`; global config remains isolated.
Missing or invalid values return `IDENTITY_REQUIRED` before snapshot/creation.

### Rewrite refusal and recovery

Pre/post-CAS checks share one tip set: every ref namespace, every non-target
worktree HEAD and every supported worktree pseudoref. `PRE_CAS_STALE_TIP` names
the claimant (for example `ORIG_HEAD` or `FETCH_HEAD`) and leaves the branch
unchanged. Inspect it, clear it only if disposable, and rerun. No tip is
implicitly acknowledged or deleted.

`POST_CAS_STALE_TIP_RECOVERY_REQUIRED` identifies race-window claimants.
Verify `old_head`, inspect the claimant, clear only disposable state, run the
reported non-destructive object check, then retry. Retain recovery objects/fanouts.

The locked snapshot controls object/fanout provenance. New loose objects use
atomic create-if-absent hard links. Before CAS, AWF records bounded type,
content and recomputed-OID evidence for the complete replacement closure;
primary loose members also bind no-follow storage identity. Shallow boundaries
and legacy grafts are refused. Post-CAS closure and evidence must match exactly.
Failures retain objects/fanouts and report `*_RECOVERY_REQUIRED`; existing,
alternate, linked or ambiguous objects are never removed.

Publication identity/byte reads use fresh Git children. Inherited `GIT_*` names
are removed case-insensitively before fixed isolation and replacement/lazy-fetch
defences are added. Thus repository, worktree, object/config redirection and
local replacement refs cannot substitute publisher, history or pinned workflow
bytes. The review host hashes the binary workflow blob; LF and CRLF differ.
