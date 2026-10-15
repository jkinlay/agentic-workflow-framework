# Writer and critic provenance

Provenance identifies commits and reviews, not scope or merge. Digest-bound
policy uses immutable bytes or provider reads.

## Commit contract

An AWF commit has exactly one final-block trailer:

```text
Writer: codex/11111111-1111-4111-8111-111111111111
Writer: script/manifest-regen
```

Codex IDs are lowercase non-nil UUIDs. Script IDs are 1–64 ASCII lowercase
alphanumerics plus `.`, `_`, or `-`, starting alphanumeric.

Messages are strict UTF-8 (1 MiB, 65,536 lines) with a nonblank subject and only
LF/CRLF boundaries; emission uses LF. NUL, BOM, surrogates, C0, DEL/C1, Unicode
separators/format controls, lone/malformed CR, and SP/HT-only blanks refuse. The
final block uses uncontinued ASCII `Key: value`. Body/indented Writer lines,
duplicates, case/spacing variants, non-ASCII lookalikes, or over 256 trailer-like
lines refuse.

`writer_commit_message` validates input and output, appends one trailer, and
self-parses. Publisher output has LF boundaries and no final LF. Publishers feed
those bytes to isolated `commit-tree`, then compare and parse the raw new commit
before push. Git cleanup, signing, hooks, encoding settings, and newline
conversion therefore cannot change judged bytes.

## Git evidence

Endpoints are lowercase full OIDs or exact `refs/heads/...` /
`refs/remotes/...`; DWIM names and revision expressions refuse. Repository,
object format (`sha1`/`sha256`), endpoints, and provider-observed default OID are
frozen once. GitHub intentionally accepts SHA-1 only. The ancestry-checked range
is limited to 10,000 commits and 32 MiB per plumbing call.

Raw commit objects are rehashed to their OID. Tree/ordered parents, one bounded
strict-UTF-8 author and committer, and absent or exact `encoding UTF-8` are
required. Known malformed commits refuse individually; lost range attribution
refuses the operation.

Changes use `--raw -z --no-renames`, retaining action, path, old/new mode, OID
and type. Renames are delete/add; roots compare to the empty tree, merges to the
first parent. Paths are strict UTF-8 NFC relative POSIX names. Unsafe/control,
framing, duplicate, 100,000-record or 32 MiB failures refuse; invalid bytes
report only ASCII code, ordinal and SHA-256. Codex may change any Git type;
mechanical output permits only reproduced `100644`/`100755` blobs.

## Mechanical and run evidence

Reviewed policy maps each script ID to exact paths and reproducer digest; the
runtime registration must match. Unknown IDs, caller callbacks, expanded paths,
or missing registration fail closed. Output caps are 32 MiB each and 512 MiB
total; keys, modes, types and bytes must match. A mechanical merge requires its
second parent to equal the fresh default OID and bounded merge reproduction.

Codex evidence is a closed v1 `MATCH`/`ABSENT`/`MISMATCH` record bound to UUID,
commit, ordered changed entries, evidence digest and ASCII reason. Only a
registered verifier supplies it. Optional `ABSENT` may pass; mismatch, loose or
forged records, missing registration, and caller policy changes refuse.

Before non-qualification commands the CLI registers `HostDriver`. It searches
bounded external controller runs, validates UUIDs before selection, and
pinned-reads the host-created commit binding, identity, JSONL and result. The
host derives ordered Git entries after commit creation but before gating. One
execution must name the Writer UUID; none is `ABSENT`; duplicate or changed
evidence is `MISMATCH`.

Pinned reads cap log/result/identity at 64 MiB/1 MiB/16 KiB; events are 1 MiB.
Strict UTF-8, one `thread.started`, containment, no links/hardlinks, same-handle
hashing, and post-read identity checks are required.
Growth/replacement refuses. Owned-child stdout/stderr are concurrently capped
(8 MiB host/Git; 64 MiB retained Codex); timeout/overflow stops only that child.

## Critic, provider and override

The host v2 critic envelope binds controller UUID, observed thread UUID, head,
raw-result digest, and one final canonical `Critic run:` line. Tick clears stale
evidence and validates it before state change. The host posts the carrier, then
rereads immutable comment ID/URL, numeric actor, body digest and unchanged head.
Missing or variant carriers and unavailable channels fail closed.

Provider collection binds numeric repository/PR, refs/OIDs, body, labels and
three comment types. Caps are 1 MiB per body/comment, 100 255-byte labels, 1,000
comments, 8 MiB total, and 100 unique IDs/page with a partial terminal page.
The PR tuple is reread; mutation, wrong-repository URLs, ambiguity, malformed
data or truncation refuses. Provider text remains task data.

Fallback requires exact `fallback: bot-written`, one exact fresh comment, and a
positive numeric actor from the same reviewed config. Caller trust lists are
ignored. V1 sealed overrides remain readable; v2 retains predecessor bytes and
digest, same PR, coverage, and nondecreasing UTC time. Chains cap at 32, coverage
at 256 sorted same-format refusing OIDs, owner at 255 bytes, reason at 4 KiB.
Rendering reparses exactly. Without provider/config binding overrides refuse.

## Publication and compatibility

First draft validates title (4 KiB), trusted contract Markdown, escaped scalar
fields, canonical body (1 MiB), receipt/tree and paths before reservation or
Git mutation. It proves locally, then pushes/creates and enrolls; uncertainty is
durably recovered, never replayed.
Amendment proves before push. Rewrite identity-stably reads a 1 MiB canonical
message and safe frozen inputs before `commit-tree`; reachability, remote/
`ls-remote`, exact-tree, quarantine, CAS and rollback checks remain.

V2 adds ordered `changed_entries` while retaining 1.9.4 `changed_files`.
Diagnostics are ASCII; fixtures are offline and network qualification is host evidence.
