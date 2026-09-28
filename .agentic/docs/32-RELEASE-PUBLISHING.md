# Release publication

Version 1.9.3. Publication is an owner-controlled operation from the exact clean commit being tagged.

## Preconditions

`scripts/publish_release.py` reports every dirty-source problem together: a different HEAD, tracked modifications, untracked files, stale generated manifests and version disagreements. `VERSION`, runtime/workflow versions, schema revision constants, README, changelog, portable skill and manifest header must agree. The regenerated manifest must equal the committed bytes.

Supply an independently pinned `awf-clean-windows-portable-check-1` record for the same commit. It must record PASS for portable build, host-skill installation and host-skill verification. Publication is refused without it. This is release evidence, not authority to publish.

```text
python -B scripts/publish_release.py --commit SHA --output-dir <new-external-output> --clean-windows-check <record.json> --expected-clean-windows-check-sha256 SHA256 --dry-run
```

Dry-run builds and validates locally but creates no tag, push or hosted release. A real run creates an annotated `vX.Y.Z` tag, pushes that tag, then asks `gh release create --draft`; the owner reviews and publishes the draft.

## Reproducible assets

The source, portable-skill and complete-distribution ZIPs use sorted `/` member names, `ZIP_STORED`, the commit time converted to a UTC DOS timestamp, explicit `0644` metadata, and empty extra/comment fields. Generated JSON and checksum text use deterministic ordering, UTF-8 and LF. Source validation rejects checkout CRLF/BOM bytes. Archive metadata ignores locale, umask and filesystem timestamps; output paths are not embedded.

The tag message and release body contain one canonical record binding the commit, `MANIFEST.json` SHA-256, every asset SHA-256, validation counts and the pinned clean-Windows check.

```text
python -B scripts/publish_release.py verify-release --tag vX.Y.Z --output-dir <new-external-output>
```

Verification rebuilds the tagged commit and compares its manifest and assets with both the annotated tag record and assets downloaded from the hosted release. Any byte difference fails.

## Release sources

For activation, `workflow.py status --release-source <path> --expected-manifest-sha256 SHA256` accepts either a closed manifest-verified extraction without `.git` or the release ZIP itself. The pin authenticates neither publisher nor Git provenance. A caller may check Git provenance separately; it is never a precondition for the archive trust path. The source must remain outside the governed project.
