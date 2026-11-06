# Release publication

Version 1.9.6. Publication is an owner-controlled operation from the exact clean commit being tagged.

## Preconditions

`scripts/publish_release.py` reports every dirty-source problem together: a different HEAD, tracked modifications, untracked files, stale generated contracts, examples, prompts or manifests, and version disagreements. `VERSION`, runtime/workflow versions, schema revision constants, README, changelog, portable skill and manifest header must agree. Every regenerated file must equal the committed bytes.

Before any tag, push or hosted-release call, the publisher invokes the materialized commit's `workflow.py publication-scan` for the exact release commit. Its base is the highest earlier canonical `vMAJOR.MINOR.PATCH` tag reachable from that commit; no such tag fails closed. Publication requires exit zero and a UTF-8 JSON `PASS` bound to that base and commit, with zero blocking and unscanned entries. `BLOCKED`, command errors, malformed output, mismatched identities, contradictory counts and nonzero PASS exits all refuse before release-target resolution or mutation.

Supply an independently pinned `awf-clean-windows-portable-check-1` record for the same commit. It must record PASS for portable build, host-skill installation and host-skill verification. Publication is refused without it. This is release evidence, not authority to publish.

```text
python -B scripts/publish_release.py --commit SHA --output-dir <new-external-output> --clean-windows-check <record.json> --expected-clean-windows-check-sha256 SHA256 --dry-run
```

Dry-run performs and reports the same scan without remote changes. Both modes preserve its exact bytes as external `publication-scan.json`. A real run validates the target and freezes the credential-isolated push plan before tagging, pushing and creating the owner-controlled draft release. For HTTPS, only the tag push receives a command-scoped credential-free URL and `gh auth git-credential` helper. Any visible `http.extraHeader` key or inconclusive inspection refuses before tagging; diagnostics contain only key, scope and origin. Push and recovery reset generic, URL-scoped and included-local headers and helpers. Git configuration and credentials are never persisted or included in release output.

If the push fails, the local annotated tag remains, the remote tag status is unknown, and no GitHub release is created. The error suppresses raw Git output so a credential-bearing URL cannot be disclosed, instructs the operator to check `gh auth status`, and gives this exact retry (substitute the reported tag):

```text
git -c credential.helper= -c remote.origin.pushurl= -c remote.origin.pushurl=https://github.com/OWNER/REPOSITORY.git -c http.extraHeader= -c http.https://github.com/OWNER/REPOSITORY.git.extraHeader= -c credential.https://github.com/OWNER/REPOSITORY.git.helper= -c 'credential.https://github.com/OWNER/REPOSITORY.git.helper=!gh auth git-credential' push origin refs/tags/vX.Y.Z
```

Recovery uses the resolved, credential-free HTTPS origin and repeats an empty `-c` reset for every safe local header key, excluding path-specific Authorization. Failures, timeouts and launch errors suppress subprocess output and header values.

Validation runs in a temporary detached Git worktree of the same commit, checked byte for byte against the raw tree and removed afterwards, because self-test cases inspect Git.

## Reproducible assets

Release materialization reads raw modes, object IDs and blob bytes from the tagged commit with replacement objects disabled. It avoids `git archive`, so `export-ignore` cannot omit files. Before creating output it rejects non-regular modes, nonportable names, trailing-dot/space aliases and case-folding collisions. Exclusive, handle-relative creation pins prefixes without following links or reparse points; raced aliases and hardlinks fail before bytes can escape or another inode can be overwritten. The completed projection is checked against the raw inventory, modes and bytes.

The source, portable-skill and complete-distribution ZIPs use sorted `/` member names, `ZIP_STORED`, the commit time converted to a UTC DOS timestamp, exact `100644`/`100755` metadata from the tagged raw tree for every tagged member, and empty extra/comment fields. Shebang content never changes a mode. The projector supplies a closed raw-mode manifest to both builders; a tagged member absent from that map or a Git mode the ZIP contract cannot represent is rejected. Builder-generated portable members use an explicit `100644` policy. Portable skill and distribution manifest inputs are ordered by repository-relative POSIX path strings, independent of native `Path` comparison rules. Generated JSON and checksum text use deterministic ordering, UTF-8 and LF. Source validation rejects checkout CRLF/BOM bytes. Archive metadata ignores locale, umask and filesystem timestamps; output paths are not embedded.

The tag message and release body contain one canonical record binding the commit, `MANIFEST.json` SHA-256, every asset SHA-256, validation counts, the pinned clean-Windows check, and the publication scan's `PASS` status, resolved base/head and SHA-256 of the exact preserved scan-report bytes.

```text
python -B scripts/publish_release.py verify-release --tag vX.Y.Z --output-dir <new-external-output>
```

Verification rebuilds the tagged commit and compares its manifest and assets with both the annotated tag record and assets downloaded from the hosted release. Any byte difference fails.

## Release sources

For activation, `workflow.py status --release-source <path> --expected-manifest-sha256 SHA256` accepts either a closed manifest-verified extraction without `.git` or the release ZIP itself. The pin authenticates neither publisher nor Git provenance. A caller may check Git provenance separately; it is never a precondition for the archive trust path. The source must remain outside the governed project.

Installed-host trust uses an atomically staged receipt outside the candidate project. It closes the tree and verifies package plus preserved-local file digests before status reports the resolved Codex home and host-skill paths.
