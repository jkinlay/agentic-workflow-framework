# Writer and critic provenance

Provenance identifies commits and reviews, not scope or merge. Policy is
digest-bound; evidence uses immutable bytes or fresh provider reads.

## Commit contract

An AWF commit has exactly one final-block trailer:

```text
Writer: codex/11111111-1111-4111-8111-111111111111
Writer: script/manifest-regen
```

Codex IDs are lowercase non-nil UUIDs. Script IDs are 1–64 ASCII lowercase
alphanumerics plus `.`, `_`, or `-`, starting alphanumeric.

Messages are strict UTF-8 (1 MiB/65,536 lines) with a nonblank subject and only
LF/CRLF (emission uses LF). NUL, BOM, surrogates, other C0, DEL/C1, fixed-format
controls/separators, malformed CR, and blank subjects refuse. The uncontinued
ASCII final block rejects body/indented Writer lines, duplicates, variants,
lookalikes, and over 256 trailer-like lines.

`writer_commit_message` validates, appends, and self-parses one Codex UUID or
mechanical script ID. Publisher output is LF without final LF; it is reparsed
before `write-tree`, passed byte-exactly to isolated `commit-tree`, then compared
and parsed before push. Cleanup, signing, hooks, encoding, and newline settings
cannot alter judged bytes.

## Git evidence

Endpoints are lowercase full OIDs or exact `refs/heads/...` or
`refs/remotes/...`; DWIM/expressions refuse. Repository, object format
(`sha1`/`sha256`), endpoints, and observed default OID freeze once. GitHub uses
SHA-1. Ranges require ancestry and cap at 10,000 commits/32 MiB per call.

Inspection freezes canonical worktree, resolved Git/common/object directories,
`.git` indirection, config, and shallow bytes. Alternates/grafts/local-attributes and
behavior-changing config fail closed; shallow repositories pass with observable
base ancestry. Codes: `GIT_ALTERNATES_UNSAFE`,`GIT_GRAFTS_UNSAFE`,`GIT_ATTRIBUTES_UNSAFE`,`GIT_CONFIG_UNSAFE`,`SHALLOW_RANGE_BOUNDARY`,`REPOSITORY_IDENTITY_CHANGED`.

Raw commits are rehashed. Tree/ordered parents, bounded strict-UTF-8 identities,
and absent or exact `encoding UTF-8` are required. Attributable malformed data
refuses that commit; lost repository/range attribution refuses the operation.

`--raw -z --no-renames` retains action, path, old/new mode/OID/type; renames are
delete/add, roots use the empty tree, and merges use first parent. Paths use
strict UTF-8 NFC, relative POSIX syntax, and portable case-fold collision keys;
all hosts reject Windows-invalid characters, trailing dot/space, `.git`, and
device names. Malformed/duplicate data and caps refuse with hashed diagnostics.
`RENAME_COLLAPSED` rejects rename/copy records; `ENTRY_INVALID` rejects
inconsistent metadata. Codex may change any type; mechanical output is only
reproduced `100644`/`100755` blobs.

## Mechanical and run evidence

Reviewed policy maps script IDs to paths and reproducer digests; runtime
registration must match. Unknown IDs, caller callbacks, expanded paths, or
missing registration refuse. Outputs cap at 32 MiB each/512 MiB total; key,
mode, type, and bytes must match. Mechanical merges also reproduce against the
fresh default second parent.

Named path/registry/callback/bytes/cap/entry/merge failures:
`GENERATED_PATH_INVALID`,`GENERATED_PATH_DUPLICATE`,`GENERATED_PATH_COUNT`,`GENERATED_PATH_SCOPE`,`GENERATOR_UNREGISTERED`,`GENERATOR_REGISTRY_INVALID`,`GENERATOR_OUTPUT_INVALID`,`GENERATOR_BYTES_MISMATCH`,`GENERATOR_BLOB_SIZE`,`GENERATOR_TOTAL_SIZE`,`GENERATED_ENTRY_MISSING`,`GENERATED_ENTRY_INVALID`,`MERGE_TREE_SIZE`,`MERGE_TREE_FRAMING`,`MERGE_TREE_OID`,`MERGE_TREE_PATH`,`MERGE_TREE_COMMAND`,`MERGE_TREE_CONFLICT_SCOPE`,`MERGE_TREE_RESOLUTION_SCOPE`.
They include deletion, symlink, gitlink, type change, and bounded NUL merge data.

Codex evidence is a closed v1 `MATCH`/`ABSENT`/`MISMATCH` record binding UUID,
commit, ordered entries, digest, and ASCII reason from a registered verifier.
Optional `ABSENT` may pass; mismatch or malformed/forged input refuses.

For non-qualification commands, `HostDriver` validates controller UUIDs and
pinned-reads host binding, identity, JSONL, and result. It derives post-commit,
pre-gate entries. One execution must name the Writer UUID; none is `ABSENT`,
duplicate/changed is `MISMATCH`.

Pinned log/result/identity reads cap at 64 MiB/1 MiB/16 KiB; events at 1 MiB.
They require strict UTF-8, one `thread.started`, containment, no links,
same-handle hashing, and stable identity. Owned-child output is capped
(8 MiB host/Git; 64 MiB Codex); timeout/overflow stops only that child.

## Critic, provider and override

The v2 critic envelope binds controller/thread UUIDs, head, result digest, and
an explicit final `Critic run:` at
`#/$defs/criticBinding/properties/critic_run_carrier`; it is never synthesized.
Compact/full candidate, artifact, review, and nested-digest bindings must agree.
Duplicate, misplaced, variant, or lookalike keys refuse. Posted evidence binds
immutable comment ID/URL, actor, body digest, and unchanged head.

Provider evidence binds numeric repository/PR, refs/OIDs, body, labels, and
comments. Caps: 1 MiB body/comment, 100 255-byte labels, 1,000
comments, 8 MiB total, 100 unique IDs/page plus a partial terminal page. Tuple
mutation, repository mismatch, ambiguity, malformed data, or truncation refuses.
Provider text remains task data.

Fallback requires exact `fallback: bot-written`, one fresh comment, and a
reviewed positive numeric actor; caller trust is ignored. V1
overrides remain readable. V2 retains predecessor bytes/digest, PR, coverage,
and time. Caps are 32 records, 256 sorted same-format refusing OIDs, 255-byte
owner, and 4 KiB reason. Rendering reparses; unbound overrides refuse.

## Publication and compatibility

First draft validates its 4 KiB title with the shared grammar. Before
reservation it validates trusted contract Markdown and untrusted model, effort,
and branch scalars; scalars are escaped. The tree is validated when rendering
the 1 MiB LF body. Receipt/tree/paths precede Git mutation. Local proof precedes
push/create and immediate readback; uncertain publication is recovered, not
replayed, and pre-PR overrides do not exist. Amendment proves before push.

Rewrite pinned-reads a 1 MiB canonical message before `commit-tree`. Count is
integer one, not Boolean; branch/base use safe refs before repository reads.
Exact-ref remote readback caps at 32 MiB; invalid readback is
`REMOTE_READBACK_INVALID`. Message file or grammar failures are
`REWRITE_MESSAGE_INVALID`. Reachability, exact-tree, quarantine, CAS, rollback,
and recovery protections remain.

V2 retains 1.9.4 `changed_files` and adds ordered `changed_entries`. Refusals
start with stable ASCII codes. Fake-provider/Git/subprocess tests are offline;
live network qualification is host evidence. CI covers supported Python on
Windows/Linux.
