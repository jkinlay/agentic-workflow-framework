# Release consumer bytes

Q14 and Q15 freeze and scan the same tag-message and release-body files;
Q15 obtains fresh receipts. The consumers receive the verified bytes on stdin:
Git uses `tag -a --cleanup=verbatim -F -`, and gh uses `--notes-file -`.
Before pushing, the publisher reads the stored tag object, compares its message
bytes and release-commit target, and pushes that checked object ID. A mismatch
leaves the local tag untouched: the owner inspects and removes the mismatching
local tag before retrying. No push occurs. gh offers no local byte-exact draft
body readback without a provider request; the publisher binds its stdin bytes
and does not claim provider-side body equality.

Upload copies live in a new private staging directory. Windows reuses safeio
handles with read sharing only, denying writes and deletion from final
descriptor verification through gh's reads; pinned directory ancestry also
prevents path replacement. This protects against ordinary concurrent filesystem
writers, not a process able to tamper with the publisher or bypass OS controls.
POSIX uses a process-created 0700 directory, read-only copies and descriptor
verification. These modes cannot exclude another process running as the same
user; that process can race replacement or modification before gh reads.
After upload the publisher downloads and hashes every draft asset, checking
the complete inventory before reporting `DRAFT_CREATED` on either platform.
Mismatch or unavailable readback refuses success; the draft stays unpublished,
and the owner deletes the draft and inspects the retained tag before retrying.
POSIX readback detects substituted uploaded bytes; it does not prevent those
bytes reaching the draft. A same-user process able to tamper with readback or
the publisher itself remains outside this protection. Independent owner review
is still required before publication.
