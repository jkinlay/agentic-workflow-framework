# Release publication

Version 1.9.4. Publication is an owner-controlled operation from the exact clean commit being tagged.

## Preconditions

`scripts/publish_release.py` reports every dirty-source problem together: a different HEAD, tracked modifications, untracked files, stale generated contracts, examples, prompts or manifests, and version disagreements. `VERSION`, runtime/workflow versions, schema revision constants, README, changelog, portable skill and manifest header must agree. Every regenerated file must equal the committed bytes.

Supply an independently pinned `awf-clean-windows-portable-check-1` record for the same commit. It must record PASS for portable build, host-skill installation and host-skill verification. Publication is refused without it. This is release evidence, not authority to publish.

```text
python -B scripts/publish_release.py --commit SHA --output-dir <new-external-output> --clean-windows-check <record.json> --expected-clean-windows-check-sha256 SHA256 --dry-run
```

Dry-run validates without remote changes. A real run tags, pushes, then creates a draft release for owner publication. Reads, builds and verification stay isolated. For HTTPS, only the tag push receives a command-scoped, credential-free push URL and `gh auth git-credential` helper. Git reads local HTTP `extraHeader` key names, never values, and empties the generic, repository and every local key for that command, covering paths such as `/info/refs`. Unsafe names are rejected without disclosure. Exact helper resets override generic and URL-scoped helpers. Nothing writes Git configuration, persists a credential or puts one in release output.

If the push fails, the local annotated tag remains, the remote tag status is unknown, and no GitHub release is created. The error suppresses raw Git output so a credential-bearing URL cannot be disclosed, instructs the operator to check `gh auth status`, and gives this exact retry (substitute the reported tag):

```text
git -c credential.helper= -c remote.origin.pushurl= -c remote.origin.pushurl=https://github.com/OWNER/REPOSITORY.git -c http.extraHeader= -c http.https://github.com/OWNER/REPOSITORY.git.extraHeader= -c credential.https://github.com/OWNER/REPOSITORY.git.helper= -c 'credential.https://github.com/OWNER/REPOSITORY.git.helper=!gh auth git-credential' push origin refs/tags/vX.Y.Z
```

Recovery uses the resolved, credential-free HTTPS origin and repeats an empty `-c` reset for every safe local header key, excluding path-specific Authorization. Failures, timeouts and launch errors suppress subprocess output and header values.

Validation runs in a temporary detached Git worktree of the same commit, checked byte for byte against the raw tree and removed afterwards, because self-test cases inspect Git.

## Reproducible assets

Release materialization reads the tagged commit through an isolated Git environment with replacement objects disabled and enumerates its raw tree modes, object IDs and blob bytes. It does not use `git archive`, so committed `export-ignore` attributes cannot remove files. Before any destination path is created it rejects symlinks, gitlinks and other non-regular modes, nonportable or Windows-reserved names, trailing-dot/space aliases, and case-folding collisions at every path prefix. Creation is exclusive and handle-relative: prefixes are pinned without following links/reparse points, POSIX file bytes are completed in an unnamed inode before its exclusive link, and Windows directories/files remain exclusively handle-pinned through writes. A raced symlink, junction/reparse point or pre-existing hardlink therefore fails before release bytes can escape or another inode can be overwritten. The completed projection is checked back against the raw inventory, regular modes and bytes.

The source, portable-skill and complete-distribution ZIPs use sorted `/` member names, `ZIP_STORED`, the commit time converted to a UTC DOS timestamp, exact `100644`/`100755` metadata from the tagged raw tree for every tagged member, and empty extra/comment fields. Shebang content never changes a mode. The projector supplies a closed raw-mode manifest to both builders; a tagged member absent from that map or a Git mode the ZIP contract cannot represent is rejected. Builder-generated portable members use an explicit `100644` policy. Portable skill and distribution manifest inputs are ordered by repository-relative POSIX path strings, independent of native `Path` comparison rules. Generated JSON and checksum text use deterministic ordering, UTF-8 and LF. Source validation rejects checkout CRLF/BOM bytes. Archive metadata ignores locale, umask and filesystem timestamps; output paths are not embedded.

The tag message and release body contain one canonical record binding the commit, `MANIFEST.json` SHA-256, every asset SHA-256, validation counts and the pinned clean-Windows check.

```text
python -B scripts/publish_release.py verify-release --tag vX.Y.Z --output-dir <new-external-output>
```

Verification rebuilds the tagged commit and compares its manifest and assets with both the annotated tag record and assets downloaded from the hosted release. Any byte difference fails.

## Release sources

For activation, `workflow.py status --release-source <path> --expected-manifest-sha256 SHA256` accepts either a closed manifest-verified extraction without `.git` or the release ZIP itself. The pin authenticates neither publisher nor Git provenance. A caller may check Git provenance separately; it is never a precondition for the archive trust path. The source must remain outside the governed project.

Installed-host trust uses an atomically staged receipt outside the candidate project. It closes the tree and verifies package plus preserved-local file digests before status reports the resolved Codex home and host-skill paths.
