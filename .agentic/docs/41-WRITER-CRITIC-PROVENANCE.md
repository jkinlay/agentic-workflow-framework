# Writer and critic provenance

Provenance identifies commits/reviews, not scope/merge. Digest-bound policy uses
immutable bytes or fresh provider reads.

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

`writer_commit_message` validates/appends/self-parses one Codex UUID or
mechanical script ID. Publisher output is LF without final LF; reparse before
`write-tree`, pass byte-exactly to isolated `commit-tree`, then compare/parse
before push. Cleanup, signing, hooks, encoding and newline settings cannot alter
judged bytes.

## Git evidence

Range endpoints validate before repository/shallow inspection as lowercase full
OIDs or exact `refs/heads/...` or `refs/remotes/...`; malformed/DWIM expressions
yield `REF_INVALID`. Repository, object format
(`sha1`/`sha256`), endpoints, and observed default OID freeze once. GitHub uses
SHA-1. Ranges require ancestry and cap at 10,000 commits/32 MiB per call.

Inspection freezes worktree/Git/common/object paths, `.git`/`commondir`, config,
and shallow bytes. Alternates, grafts, attributes, config effects, and
unobservable ancestry fail closed. Codes:
`REPOSITORY_INVALID`,`REPOSITORY_ROOT`,`REPOSITORY_IDENTITY_INVALID`,`REPOSITORY_METADATA_SIZE`,`REPOSITORY_IDENTITY_CHANGED`,`REPOSITORY_FORMAT`;
`GIT_ALTERNATES_UNSAFE`,`GIT_GRAFTS_UNSAFE`,`GIT_ATTRIBUTES_UNSAFE`,`GIT_CONFIG_UNSAFE`;
`SHALLOW_INVALID`,`SHALLOW_RANGE_BOUNDARY`;
`REF_INVALID`,`REF_MISSING`,`REF_RESOLUTION`,`REF_FORMAT`;
`RANGE_ANCESTRY`,`RANGE_ANCESTRY_COMMAND`,`RANGE_COUNT`.

Raw commits are rehashed. Tree, parents, bounded UTF-8 identities, and
absent/exact `encoding UTF-8` are required. Malformed data refuses the commit
or operation. Codes:
`COMMIT_OBJECT_SIZE`,`COMMIT_OBJECT_ID`,`COMMIT_HEADER_FRAMING`,`COMMIT_HEADER_LINES`,`COMMIT_HEADER_CONTINUATION`,`COMMIT_HEADER_SIZE`,`COMMIT_HEADER_INVALID`,`COMMIT_HEADER_UNKNOWN`,`COMMIT_HEADER_MULTIPLICITY`,`COMMIT_HEADER_OID`,`COMMIT_IDENTITY_SIZE`,`COMMIT_IDENTITY_UTF8`,`COMMIT_IDENTITY_CONTROL`,`COMMIT_IDENTITY_GRAMMAR`,`COMMIT_ENCODING`,`MESSAGE_SIZE`,`MESSAGE_UTF8`.

`--raw -z --no-renames` retains action, path, old/new mode/OID/type; renames are
delete/add, roots use the empty tree, and merges use first parent. Paths use
strict UTF-8 NFC, relative POSIX syntax, and portable case-fold collision keys;
all hosts reject Windows-invalid characters, trailing dot/space, `.git`, and
device names. Malformed/duplicate data and caps refuse with hashed diagnostics.
`RENAME_COLLAPSED` rejects rename/copy records; `ENTRY_INVALID` rejects
inconsistent metadata. Codex may change any type; mechanical output is only
reproduced `100644`/`100755` blobs.

Path, raw-diff, and tree-entry codes:
`PATH_NFC`,`PATH_SIZE`,`PATH_SYNTAX`,`PATH_COMPONENT`,`PATH_CONTROL`,`PATH_TRAILING`,`PATH_RESERVED`,`PATH_DOT_GIT`,`PATH_STREAM_TYPE`,`PATH_STREAM_SIZE`,`PATH_FRAMING`,`PATH_COUNT`,`PATH_UTF8`,`PATH_INVALID`,`PATH_DUPLICATE`;
`RAW_DIFF_SIZE`,`RAW_DIFF_FRAMING`,`RAW_DIFF_META`,`RAW_DIFF_STATUS`,`RAW_DIFF_COUNT`,`RAW_DIFF_MODE`,`RAW_DIFF_OID`,`RENAME_COLLAPSED`,`ENTRY_INVALID`;
`TREE_ENTRY_COMMAND`,`TREE_ENTRY_FRAMING`,`TREE_ENTRY_META`,`TREE_ENTRY_PATH_UTF8`,`TREE_ENTRY_PATH_MISMATCH`,`TREE_ENTRY_CONTENT`.

## Mechanical and run evidence

Reviewed policy maps script IDs to paths/reproducer digests; runtime
registration must match. Unknown IDs, caller callbacks, expanded paths, and
missing registration refuse. Output caps are 32 MiB each/512 MiB total; key,
mode, type, and bytes match. Mechanical merges reproduce against the fresh
default second parent.

Named path/registry/callback/bytes/cap/entry/merge failures:
`GENERATED_PATH_INVALID`,`GENERATED_PATH_DUPLICATE`,`GENERATED_PATH_COUNT`,`GENERATED_PATH_SCOPE`,`GENERATOR_PATH_UNAUTHORIZED`,`GENERATOR_UNREGISTERED`,`GENERATOR_REGISTRY_INVALID`,`GENERATOR_ERROR`,`GENERATOR_OUTPUT_INVALID`,`GENERATOR_BYTES_MISMATCH`,`GENERATOR_BLOB_SIZE`,`GENERATOR_TOTAL_SIZE`,`GENERATED_ENTRY_MISSING`,`GENERATED_ENTRY_INVALID`,`MERGE_TREE_SIZE`,`MERGE_TREE_FRAMING`,`MERGE_TREE_OID`,`MERGE_TREE_PATH`,`MERGE_TREE_COMMAND`,`MERGE_TREE_CONFLICT_SCOPE`,`MERGE_TREE_RESOLUTION_SCOPE`.
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

V2 envelopes bind controller/thread UUIDs, head, result digest and explicit
`#/$defs/criticBinding/properties/critic_run_carrier`; none is synthesized.
Every merge-gate round requires one final canonical `Critic run: <UUID>` in
retained `posting_observation.comment_bytes`, matching its run/head/recomputed
digest. Candidate/artifact/review bindings agree. Duplicate, misplaced, variant
or lookalike carriers refuse. Immutable provider ID/URL/body digests and actor/head remain bound.

Provider evidence binds numeric repository/PR, refs/OIDs, body, labels, and
comments. Caps: 1 MiB body/comment, 100 255-byte labels, 1,000
comments, 8 MiB total, 100 unique IDs/page plus a partial terminal page. Tuple
mutation, repository mismatch, ambiguity, malformed data, or truncation refuses.
Provider text remains task data.

Fallback requires a sealed override, one fresh exact comment and a reviewed
positive numeric actor; caller trust is ignored. V1 overrides remain readable.
V2 retains ordered predecessor bytes/digests, PR, coverage and time. Caps:
32 records, 256 sorted same-format refusing OIDs, 255-byte owner, 4 KiB reason.
Rendering reparses; unbound or unnecessary coverage refuses.

Authority is label-independent: exact `fallback: bot-written` is compatibility
inventory only.

## Publication and compatibility

Canonical UTF-8 `record.json`: inclusive 1,048,576-byte envelope cap
(keys/digests/framing/escaping), before reservation. Oversize reads/recovery
refuse unchanged/unsealed; exact-limit retries, append-only/crash/digest
semantics, 1,024-byte seals/global limit remain. Host `commit-run-log.json`
checks complete bytes (including LF) before retained-record inspection/write.

PR2 bounds detached/watcher, owner-SQL, journal, delivery-row and digest-referenced
loop-artifact envelopes; preserves serializers/history/legacy hydration,
committed evidence/report ordering, uncertain intents and sealed abandonment
without mutation replay.

First drafts: shared-grammar 4 KiB titles, trusted contract Markdown,
escaped untrusted model/effort/branch scalars validated before reservation.
Validate tree/1 MiB LF body. Receipt/tree/paths precede Git mutation;
local proof precedes push/create/immediate readback. Recover uncertainty;
no pre-PR overrides. Amendments prove before push.

Before `commit-tree`: pinned canonical-message reads (1 MiB), integer-one
count, never Boolean. Pre-read branch/base-ref validation.
Exact-ref readback: 32 MiB; invalidity: `REMOTE_READBACK_INVALID`.
Message-file/grammar: `REWRITE_MESSAGE_INVALID`.
Reachability/exact-tree/quarantine/CAS/rollback/recovery protections remain.

V2: retained 1.9.4 `changed_files`, ordered `changed_entries`, stable ASCII refusals. Fake-provider/Git/subprocess tests: offline; live-network
qualification: host evidence. CI: supported Windows/Linux/Python.
