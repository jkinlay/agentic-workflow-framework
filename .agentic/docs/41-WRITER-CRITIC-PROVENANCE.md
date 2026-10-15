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

Messages are strict UTF-8 (1 MiB, 65,536 lines), have a nonblank subject, and
use only LF/CRLF; emission uses LF. NUL, BOM, surrogates, other C0, DEL/C1,
Unicode separators/format controls, lone/malformed CR, and SP/HT-only subjects
refuse. The format-control list is fixed, not runtime-Unicode-derived. The final
block uses uncontinued ASCII `Key: value`. Body/indented Writer lines,
duplicates, case/spacing variants, non-ASCII lookalikes, or more than 256
trailer-like lines refuse.

`writer_commit_message` validates, appends one trailer, and self-parses.
Non-mechanical emission requires one Codex UUID; mechanical emission requires
one script ID. Publisher output uses LF with no final LF. Before `write-tree`,
the commit boundary reparses it; isolated `commit-tree` receives those bytes,
then the raw commit is compared and parsed before push. Cleanup, signing,
hooks, encoding, and newline settings cannot alter judged bytes.

## Git evidence

Endpoints are lowercase full OIDs or exact `refs/heads/...` or
`refs/remotes/...`; DWIM/expressions refuse. Repository, object format
(`sha1`/`sha256`), endpoints, and observed default OID freeze once. GitHub uses
SHA-1. Ranges require ancestry and cap at 10,000 commits/32 MiB per call.

Raw commits are rehashed. Tree/ordered parents, bounded strict-UTF-8 identities,
and absent or exact `encoding UTF-8` are required. Attributable malformed data
refuses that commit; lost repository/range attribution refuses the operation.

`--raw -z --no-renames` retains action, path, old/new mode, OID, and type;
renames are delete/add, roots use the empty tree, merges use first parent. Paths
are strict UTF-8 NFC relative POSIX. Unsafe, malformed, duplicate, over-100,000
or over-32-MiB data refuses; diagnostics contain only ASCII code, ordinal, and
SHA-256. Codex may change any type; mechanical output is reproduced
`100644`/`100755` blobs only.

## Mechanical and run evidence

Reviewed policy maps script IDs to paths and reproducer digests; runtime
registration must match. Unknown IDs, caller callbacks, expanded paths, or
missing registration refuse. Outputs cap at 32 MiB each/512 MiB total; key,
mode, type, and bytes must match. Mechanical merges also reproduce against the
fresh default second parent.

Codex evidence is a closed v1 `MATCH`/`ABSENT`/`MISMATCH` record binding UUID,
commit, ordered entries, digest, and ASCII reason. Only a registered verifier
supplies it. Optional `ABSENT` may pass; mismatch or malformed/forged policy and
evidence refuse.

For non-qualification commands, `HostDriver` validates controller UUIDs, then
pinned-reads host-created binding, identity, JSONL, and result. It derives
ordered entries after commit creation and before gating. One execution must
name the Writer UUID; none is `ABSENT`, duplicate/changed is `MISMATCH`.

Pinned log/result/identity reads cap at 64 MiB/1 MiB/16 KiB; events at 1 MiB.
They require strict UTF-8, one `thread.started`, containment, no links/hardlinks,
same-handle hashing, and stable identity. Owned-child output is stream-capped
(8 MiB host/Git; 64 MiB Codex); timeout/overflow stops only that child.

## Critic, provider and override

The v2 critic envelope binds controller/thread UUIDs, head, raw-result digest,
and one explicit final `Critic run:` line. The generated schema names its sole
path at `#/$defs/criticBinding/properties/critic_run_carrier`; it is never
synthesized. Candidate, artifact, and review bindings must agree before state
change. Posted evidence is reread with immutable comment ID/URL, actor, body
digest, and unchanged head; missing/variant channels refuse.

Provider evidence binds numeric repository/PR, refs/OIDs, body, labels, and
three comment types. Caps: 1 MiB body/comment, 100 255-byte labels, 1,000
comments, 8 MiB total, 100 unique IDs/page plus a partial terminal page. Tuple
mutation, wrong repository, ambiguity, malformed data, or truncation refuses.
Provider text remains task data.

Fallback requires exact `fallback: bot-written`, one fresh comment, and a
positive numeric actor from reviewed config; caller trust is ignored. V1
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

Rewrite pinned-reads a 1 MiB canonical message before `commit-tree`. Count must
be integer one (not Boolean); branch/base use the safe-ref grammar before
repository reads. Bounded remote/`ls-remote`, reachability, exact-tree,
quarantine, CAS, rollback, and recovery protections remain.

V2 retains 1.9.4 `changed_files` and adds ordered `changed_entries`. Refusals
start with stable ASCII codes. Fake-provider/Git/subprocess tests are offline;
live network qualification is host evidence. CI covers supported Python on
Windows/Linux.
