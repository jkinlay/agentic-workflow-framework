# AWF-28 U1 manifest investigation

## Scope and repository evidence

The two contract-named receipt digests are `c3bed1ce03703661de65b32f5a5071005e5de27f4ce0d36f746315609d564d25` and the supplied abbreviated value `97c865a4...047d`. The latter's full value is not present in the supplied repository, so this record does not infer its missing characters.

The requested tag range cannot be reconstructed exactly from this checkout. `git tag --list 'v1.9.*'` exited 0 and listed only `v1.9.2`, `v1.9.3`, and `v1.9.4`. `git rev-parse --verify refs/tags/v1.9.1` exited 128, as did the equivalent check for `refs/tags/v1.9.0`. The investigation therefore used all reachable history for `MANIFEST.json`, anchored by the first available tag, `v1.9.2`.

`git log --reverse --format='%H' e7d80a691e2ae0a0b55e54f4e5e91e95e5ca7fe5..v1.9.2 -- MANIFEST.json` exited 0. Its history includes `ef7e4c7692b90a564590ce42e34730a312b2e0f5`, `76c45ebee402bc875fa1e220480a9e06fc9357ab`, `3971f69d8607799149f008960eedbc524d523492`, and `1f5f146352ff0a8222a6be1ef9186e4609b11033`, followed by later manifest changes before `v1.9.2`.

Hashing the raw bytes returned by `git show <commit>:MANIFEST.json` established:

- `e7d80a691e2ae0a0b55e54f4e5e91e95e5ca7fe5` has blob `2247ed9dbc1ed0e14890b385d7acd98fdd205451`, 33,433 bytes, SHA-256 `d10a180af0109ffda59f70d925dd934c8fb78d9a8016d7ca39086d715f1194bd`. This is the existing primary identity for the 1.9.1 build in `.agentic/upgrade/known-versions.json`.
- `1f5f146352ff0a8222a6be1ef9186e4609b11033` has blob `8bd8c8673e6f06ee35097dd1ca91728cee0ef449`, 31,489 bytes, SHA-256 `c3bed1ce03703661de65b32f5a5071005e5de27f4ce0d36f746315609d564d25`. `git show --stat 1f5f146352ff0a8222a6be1ef9186e4609b11033` exited 0 and showed that this snapshot removed provider-specific review files, updated governance/runtime files, and regenerated `MANIFEST.json` and `MANIFEST.md`. This explains the later manifest identity.

An exhaustive current scan used `git rev-list --all -- MANIFEST.json` and hashed `git show <commit>:MANIFEST.json` for all 402 returned commit/path versions. It found `c3bed1ce...d25` at `1f5f146...` and no digest beginning `97c865a4`. `git log --all -S'97c865a4' -- .` also exited 0 with no matches. Finally, `git ls-tree -r --name-only 1f5f146...` and the same command for `v1.9.2`, filtered for `.zip`, `.tar`, `.tgz`, and `.gz`, found zero tracked release archives.

## Disposition

The `c3bed1ce...d25` receipt is reproducible from reachable repository bytes and is registered as a receipt variant in `.agentic/upgrade/known-versions.json`, with the full `1f5f146...` commit as provenance.

The claimed `97c865a4...047d` receipt cannot be authenticated here: its asserted tag is absent, no reachable manifest matches its prefix, and no in-repository release archive corroborates it. It remains unregistered. Upgrade validation rejects it, and every other unknown manifest identity, before changing files; registration would require a separately governed, owner-approved change supported by the exact manifest and provenance.
