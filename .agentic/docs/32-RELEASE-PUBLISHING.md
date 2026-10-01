# Release publication

Version 1.9.3. Publication is an owner-controlled operation from the exact clean commit being tagged.

## Preconditions

`scripts/publish_release.py` reports every dirty-source problem together: a different HEAD, tracked modifications, untracked files, stale generated contracts, examples, prompts or manifests, and version disagreements. `VERSION`, runtime/workflow versions, schema revision constants, README, changelog, portable skill and manifest header must agree. Every regenerated file must equal the committed bytes.

Supply an independently pinned `awf-clean-windows-portable-check-1` record for the same commit. It must record PASS for portable build, host-skill installation and host-skill verification. Publication is refused without it. This is release evidence, not authority to publish.

```text
python -B scripts/publish_release.py --commit SHA --output-dir <new-external-output> --clean-windows-check <record.json> --expected-clean-windows-check-sha256 SHA256 --dry-run
```

Dry-run builds and validates locally but creates no tag, push or hosted release. A real run creates an annotated `vX.Y.Z` tag, pushes that tag, then asks `gh release create --draft`; the owner reviews and publishes the draft.

## Reproducible assets

Release materialization reads the tagged commit with replacement objects disabled and enumerates its raw tree modes, object IDs and blob bytes. It does not use `git archive`, so committed `export-ignore` attributes cannot remove files. Before any destination path is created it rejects symlinks, gitlinks and other non-regular modes, nonportable or Windows-reserved names, trailing-dot/space aliases, and case-folding collisions at every path prefix. The completed projection is checked back against the raw inventory, regular modes and bytes.

The source, portable-skill and complete-distribution ZIPs use sorted `/` member names, `ZIP_STORED`, the commit time converted to a UTC DOS timestamp, content-defined `0755` metadata for shebang executables and `0644` for other files, and empty extra/comment fields. Portable skill and distribution manifest inputs are ordered by repository-relative POSIX path strings, independent of native `Path` comparison rules. Generated JSON and checksum text use deterministic ordering, UTF-8 and LF. Source validation rejects checkout CRLF/BOM bytes. Archive metadata ignores locale, umask and filesystem timestamps; output paths are not embedded.

The tag message and release body contain one canonical record binding the commit, `MANIFEST.json` SHA-256, every asset SHA-256, validation counts and the pinned clean-Windows check.

```text
python -B scripts/publish_release.py verify-release --tag vX.Y.Z --output-dir <new-external-output>
```

Verification rebuilds the tagged commit and compares its manifest and assets with both the annotated tag record and assets downloaded from the hosted release. Any byte difference fails.

## Release sources

For activation, `workflow.py status --release-source <path> --expected-manifest-sha256 SHA256` accepts either a closed manifest-verified extraction without `.git` or the release ZIP itself. The pin authenticates neither publisher nor Git provenance. A caller may check Git provenance separately; it is never a precondition for the archive trust path. The source must remain outside the governed project.

Installed-host trust uses an atomically staged receipt outside the candidate project. It closes the tree and verifies package plus preserved-local file digests before status reports the resolved Codex home and host-skill paths.
