# Release publication

Version 1.9.6. Owners publish the exact clean commit. Release publishing and `verify-release` run on Linux and Windows. On macOS both fail closed at materialization (no `O_TMPFILE`); publish from Linux or Windows.

## Preconditions

`scripts/publish_release.py` reports together: different HEAD, tracked modifications, untracked files, stale generated contracts/examples/prompts/manifests, and version disagreements. `VERSION`, runtime/workflow versions, schema revision constants, README, changelog, portable skill and manifest header must agree. Regenerated files must equal committed bytes.

Before tagging, pushing or hosted-release calls, the publisher invokes the materialized commit's `workflow.py publication-scan` for the exact release commit. The base is the highest earlier canonical `vMAJOR.MINOR.PATCH` tag reachable from that commit; absence refuses. Require exit zero and UTF-8 JSON `PASS` bound to base/commit, zero blocking/unscanned entries. `BLOCKED`, command errors, malformed output, mismatched identities, contradictory counts and nonzero PASS exits refuse before release-target resolution or mutation.

Supply an independently pinned `awf-clean-windows-portable-check-1` record for the same commit: PASS for portable build, host-skill installation and verification. Absence refuses; evidence grants no publication authority.

```text
python -B scripts/publish_release.py --commit SHA --output-dir <new-external-output> --clean-windows-check <record.json> --expected-clean-windows-check-sha256 SHA256 --dry-run
```

Dry-run reports the scan without remote changes; both modes preserve exact external `publication-scan.json`, `tag-message-scan.json` and `release-body-scan.json` bytes. Real runs validate the target and freeze the credential-isolated push plan before mutation. HTTPS tag push alone receives a command-scoped credential-free URL and `gh auth git-credential` helper. Visible `http.extraHeader` keys or inconclusive inspection refuse before tagging; diagnostics contain only key, scope and origin. Push/recovery reset generic, URL-scoped and included-local headers/helpers. Git configuration/credentials are never persisted or included in release output.

Both modes scan frozen tag and body provider text before mutation; consumers receive verified stdin bytes; uploads use verified staging copies. Tag-object mismatch leaves the local tag: owner inspects/removes it before retry. Changed files after tagging/pushing refuse: inspect/remove the local tag or reconcile the pushed tag before retry. Real runs create owner-controlled draft releases. Failed creation may leave a partial draft: delete it and inspect/reconcile retained tags. Post-upload draft readback refuses mismatched/unavailable assets. Windows locks staged copies against modification/deletion. Linux POSIX limitation: a same-user process can alter staged copies during upload; detection precedes `DRAFT_CREATED`. The draft stays unpublished; owner must delete it and inspect the retained tag before retrying. POSIX protection excludes same-user readback/publisher tampering; independent owner review required.

Push failure retains the local annotated tag, leaves remote status unknown, and creates no GitHub release. Errors suppress raw Git output to protect credential-bearing URLs, instruct checking `gh auth status`, and say "after fixing authentication, retry exactly:" (substitute reported origin, object ID and tag):

```text
git -c credential.helper= -c remote.origin.pushurl= -c remote.origin.pushurl=https://github.com/OWNER/REPOSITORY.git -c http.extraHeader= -c http.https://github.com/OWNER/REPOSITORY.git.extraHeader= -c credential.https://github.com/OWNER/REPOSITORY.git.helper= -c 'credential.https://github.com/OWNER/REPOSITORY.git.helper=!gh auth git-credential' push origin <object-id>:refs/tags/vX.Y.Z
```

Recovery uses the resolved credential-free HTTPS origin, repeating empty `-c` resets for every safe local header key, excluding path-specific Authorization. Failures, timeouts and launch errors suppress subprocess output and header values.

Validation checks a temporary detached Git worktree of that commit against raw tree bytes, removing it; self-tests inspect Git.

## Reproducible assets

Materialization reads tagged raw modes/object IDs/blobs with replacement objects disabled; avoiding `git archive` prevents `export-ignore` omissions. Before output creation, non-regular modes, nonportable names, trailing-dot/space aliases and case-folding collisions refuse. Exclusive, handle-relative creation pins prefixes without following links/reparse points; raced aliases/hardlinks fail before escape or overwriting another inode. Projection must match raw inventory, modes and bytes.

Source/portable-skill/complete-distribution ZIPs use sorted `/` names, `ZIP_STORED`, UTC DOS commit timestamps, exact tagged-tree `100644`/`100755` metadata per tagged member, and empty extra/comment fields. Shebangs never change modes. Both builders receive the projector's closed raw-mode manifest; a tagged member absent from that map or an unrepresentable Git mode refuses. Builder-generated portable members use explicit `100644`. Portable skill and distribution manifest inputs are ordered by repository-relative POSIX path strings, independent of native `Path` comparisons. JSON/checksums use deterministic ordering, UTF-8 and LF. Source validation rejects checkout CRLF/BOM bytes. Archive metadata ignores locale, umask and filesystem timestamps; output paths are not embedded.

Tag message and release body contain one canonical record binding commit, `MANIFEST.json` SHA-256, every asset SHA-256, validation counts, the pinned clean-Windows check, and the publication scan's `PASS` status, resolved base/head and SHA-256 of the exact preserved scan-report bytes.

```text
python -B scripts/publish_release.py verify-release --tag vX.Y.Z --output-dir <new-external-output>
```

Verification rebuilds the tagged commit, comparing manifest/assets with the annotated tag record and hosted-release downloads; byte differences fail.

## Release sources

For activation, `workflow.py status --release-source <path> --expected-manifest-sha256 SHA256` accepts either a closed manifest-verified extraction without `.git` or the release ZIP itself. The pin authenticates neither publisher nor Git provenance. A caller may check Git provenance separately; it is never a precondition for the archive trust path. The source must remain outside the governed project.

Installed-host trust atomically stages a receipt outside the candidate project, closing the tree and verifying package/preserved-local file digests before status reports resolved Codex home and host-skill paths.
