# Writer and critic provenance

Provenance identifies commits/reviews, never scope/merge. Policy binds immutable
bytes/fresh provider reads.

## Commit contract

Each commit requires one final-block trailer:

```text
Writer: codex/11111111-1111-4111-8111-111111111111
Writer: script/manifest-regen
```

Codex IDs: lowercase non-nil UUIDs. Script IDs: 1-64 lowercase ASCII
alphanumerics/`._-`, initially alphanumeric.

Messages require strict UTF-8, <=1 MiB/65,536 lines, nonblank subject, LF/CRLF (emit
LF). Refuse NUL/BOM/surrogates/C0/DEL/C1/fixed-format controls/separators/malformed CR.
Uncontinued ASCII final blocks reject body/indented Writer lines,
duplicates/variants/lookalikes, >256 trailer-like lines.

`writer_commit_message` validates/appends/self-parses one Codex UUID/mechanical script
ID. Publisher emits LF without final LF; reparse before `write-tree`, pass exact bytes
to isolated `commit-tree`, compare/parse before push.
Cleanup/signing/hooks/encoding/newlines cannot alter judged bytes.

## Git evidence

Validate endpoints before repository/shallow inspection: lowercase full OIDs/exact
`refs/heads/...`/`refs/remotes/...`; malformed/DWIM yields `REF_INVALID`. Freeze
repository, format (`sha1`/`sha256`; GitHub SHA-1), endpoints/observed-default OID.
Require ancestry; cap 10,000 commits/32 MiB/call.

Freeze worktree/Git/common/object paths, `.git`/`commondir`, config/shallow bytes. Alternates/grafts/attributes/config effects/unobservable ancestry fail closed. Codes:
`REPOSITORY_INVALID`,`REPOSITORY_ROOT`,`REPOSITORY_IDENTITY_INVALID`,`REPOSITORY_METADATA_SIZE`,`REPOSITORY_IDENTITY_CHANGED`,`REPOSITORY_FORMAT`;
`GIT_ALTERNATES_UNSAFE`,`GIT_GRAFTS_UNSAFE`,`GIT_ATTRIBUTES_UNSAFE`,`GIT_CONFIG_UNSAFE`;
`SHALLOW_INVALID`,`SHALLOW_RANGE_BOUNDARY`;
`REF_INVALID`,`REF_MISSING`,`REF_RESOLUTION`,`REF_FORMAT`;
`RANGE_ANCESTRY`,`RANGE_ANCESTRY_COMMAND`,`RANGE_COUNT`.

Rehash raw commits; require tree/parents/bounded UTF-8 identities, absent/exact `encoding UTF-8`. Malformed data refuses commit/operation. Codes:
`COMMIT_OBJECT_SIZE`,`COMMIT_OBJECT_ID`,`COMMIT_HEADER_FRAMING`,`COMMIT_HEADER_LINES`,`COMMIT_HEADER_CONTINUATION`,`COMMIT_HEADER_SIZE`,`COMMIT_HEADER_INVALID`,`COMMIT_HEADER_UNKNOWN`,`COMMIT_HEADER_MULTIPLICITY`,`COMMIT_HEADER_OID`,`COMMIT_IDENTITY_SIZE`,`COMMIT_IDENTITY_UTF8`,`COMMIT_IDENTITY_CONTROL`,`COMMIT_IDENTITY_GRAMMAR`,`COMMIT_ENCODING`,`MESSAGE_SIZE`,`MESSAGE_UTF8`.

`--raw -z --no-renames` retains action/path/old-new mode/OID/type. Renames: delete/add;
roots: empty tree; merges: first parent. Paths require strict UTF-8 NFC/relative
POSIX/case-fold collision keys. Reject Windows-invalid characters, trailing dot/space,
`.git`, devices, malformed/duplicate/over-cap data (hashed diagnostics).
`RENAME_COLLAPSED` rejects rename/copy; `ENTRY_INVALID` rejects inconsistent metadata.
Codex permits any type; mechanical output: reproduced `100644`/`100755` blobs.

Path/raw-diff/tree-entry codes:
`PATH_NFC`,`PATH_SIZE`,`PATH_SYNTAX`,`PATH_COMPONENT`,`PATH_CONTROL`,`PATH_TRAILING`,`PATH_RESERVED`,`PATH_DOT_GIT`,`PATH_STREAM_TYPE`,`PATH_STREAM_SIZE`,`PATH_FRAMING`,`PATH_COUNT`,`PATH_UTF8`,`PATH_INVALID`,`PATH_DUPLICATE`;
`RAW_DIFF_SIZE`,`RAW_DIFF_FRAMING`,`RAW_DIFF_META`,`RAW_DIFF_STATUS`,`RAW_DIFF_COUNT`,`RAW_DIFF_MODE`,`RAW_DIFF_OID`,`RENAME_COLLAPSED`,`ENTRY_INVALID`;
`TREE_ENTRY_COMMAND`,`TREE_ENTRY_FRAMING`,`TREE_ENTRY_META`,`TREE_ENTRY_PATH_UTF8`,`TREE_ENTRY_PATH_MISMATCH`,`TREE_ENTRY_CONTENT`.

## Mechanical/run evidence

Reviewed policy binds script IDs/paths/reproducer digests to runtime registration.
Unknown IDs/caller callbacks/expanded paths/missing registration refuse. Output
key/mode/type/bytes must match; caps: 32 MiB/output, 512 MiB/total. Mechanical merges
reproduce against fresh default second parent.

Path/registry/callback/bytes/cap/entry/merge failures:
`GENERATED_PATH_INVALID`,`GENERATED_PATH_DUPLICATE`,`GENERATED_PATH_COUNT`,`GENERATED_PATH_SCOPE`,`GENERATOR_PATH_UNAUTHORIZED`,`GENERATOR_UNREGISTERED`,`GENERATOR_REGISTRY_INVALID`,`GENERATOR_ERROR`,`GENERATOR_OUTPUT_INVALID`,`GENERATOR_BYTES_MISMATCH`,`GENERATOR_BLOB_SIZE`,`GENERATOR_TOTAL_SIZE`,`GENERATED_ENTRY_MISSING`,`GENERATED_ENTRY_INVALID`,`MERGE_TREE_SIZE`,`MERGE_TREE_FRAMING`,`MERGE_TREE_OID`,`MERGE_TREE_PATH`,`MERGE_TREE_COMMAND`,`MERGE_TREE_CONFLICT_SCOPE`,`MERGE_TREE_RESOLUTION_SCOPE`.
Includes deletion/symlink/gitlink/type-change/bounded-NUL-merge-data.

Codex evidence: closed v1 `MATCH`/`ABSENT`/`MISMATCH`, binding UUID/commit/ordered
entries/digest/ASCII reason from registered verifier. Optional valid `ABSENT` may pass;
mismatch/malformed/forged input refuses.

Non-qualification `HostDriver` validates controller UUIDs; pinned-reads host
binding/identity/JSONL/result; derives post-commit/pre-gate entries. One execution names
Writer UUID; none=`ABSENT`, duplicate/changed=`MISMATCH`.

Pinned log/result/identity reads: 64 MiB/1 MiB/16 KiB; events: 1 MiB. Require strict
UTF-8, one `thread.started`, containment/no links/same-handle hashing/stable identity.
Owned-child caps: 8 MiB host/Git, 64 MiB Codex; timeout/overflow stops only that child.

## Critic/provider/override

V2 critic envelopes bind controller/thread UUIDs, head/result digest, explicit final
`Critic run:` at `#/$defs/criticBinding/properties/critic_run_carrier`; never
synthesize. Compact/full candidate/artifact/review/nested-digest bindings agree.
Duplicate/misplaced/variant/lookalike keys refuse. Posted evidence binds immutable
comment ID/URL/actor/body digest/unchanged head.

Provider evidence binds numeric repository/PR, refs/OIDs/body/labels/comments. Caps: 1
MiB/body/comment, 100 255-byte labels, 1,000 comments/8 MiB total, 100 unique IDs/page
plus partial terminal page. Tuple mutation/repository
mismatch/ambiguity/malformed/truncated data refuses. Provider text is task data.

Fallback authority requires a sealed owner override and a fresh exact comment
from a reviewed positive numeric actor. Provider labels, including
`fallback: bot-written`, are compatibility inventory only and grant no authority.
V1 overrides remain readable. V2 retains predecessor bytes/digest, PR, coverage,
and time. Caps are 32 records, 256 sorted same-format refusing OIDs, 255-byte
owner, and 4 KiB reason. Rendering reparses; unbound overrides refuse.

## Publication/compatibility

First draft validates shared-grammar 4 KiB title. Before reservation, validate trusted
contract Markdown/untrusted model/effort/branch scalars, then escape scalars. Validate
tree/1 MiB LF body. Receipt/tree/paths precede Git mutation; local proof precedes
push/create/immediate readback. Recover uncertain publication; never replay. No pre-PR
overrides. Amendments prove before push.

Before `commit-tree`, rewrite pinned-reads canonical message <=1 MiB; require
integer-one count, never Boolean. Validate branch/base refs before repository reads.
Exact-ref remote readback: <=32 MiB; invalid=`REMOTE_READBACK_INVALID`.
Message-file/grammar failure=`REWRITE_MESSAGE_INVALID`. Retain
reachability/exact-tree/quarantine/CAS/rollback/recovery protections.

V2 retains 1.9.4 `changed_files`, adds ordered `changed_entries`, stable ASCII refusals.
Fake-provider/Git/subprocess tests: offline; live-network qualification: host evidence.
CI: supported Windows/Linux Python.

 ## Final-gate commit provenance

The final gate recomputes commit provenance with `check_commit_range` on exactly
`candidate.merge_base_sha..candidate.head_sha`. It fails for a missing Writer
trailer (untagged commit), and refuses a non-mechanical script commit that is
not reproducibly mechanical. Ancestry cannot authenticate the merge base;
observed target-base OID supplies the checker's default input.

Process-local `_awf_runtime.commit_provenance` contains exactly `reviewed_policy`,
`provider_observation`, `runtime_registry`. Invalid/missing acquisition, provider
disagreement, validation failure or checker REFUSE fails provenance/readiness.
Provider repository/PR/target-branch/target-base/head must match candidate;
base ref matches policy `default_ref`. Only registered reproducers matching
reviewed digests/declared paths run. Registered verifiers supply strict run
evidence; optional valid ABSENT may pass, mismatch/malformed evidence fails.

Optional project `commit_provenance`: non-null closed policy containing exactly
`require_run_logs`, `default_ref`, `generated_paths`, `generators`; it must equal
acquired policy. Omission preserves explicit runtime-policy input, never
acquisition/PASS. Paths: strict UTF-8 NFC, 1-4096 encoded bytes, relative POSIX
components <=255 encoded bytes; unsafe separators/components, reserved names, trailing
dot/space, U+0000-U+001F/U+007F refuse. Collision identity: NFC/case-fold;
generator paths must be subsets of generated paths.

Complete checker return R yields exactly one
`urn:awf:input:commit_provenance:` + `fingerprint("gate-input", R)` reference,
hashing all ten fields/nested contents. Pre-return failure yields none; provenance
PASS requires one. Existing references remain. Optional bundle
`commit_provenance` requires exactly `result_ref`; mismatch fails, retaining actual
reference. PASS overrides no other provenance/gate problem. References identify
evaluated content, authenticate neither acquisition nor archived dereferenceable
bytes. `execution_authority` stays false; transport forwards no override authority.

Revision-3 schema identities/candidate contracts remain unchanged. Optional inputs
preserve omission compatibility with new validators; older closed validators reject
them. Never strip/downgrade them silently.