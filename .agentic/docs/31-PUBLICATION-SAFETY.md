# Publication safety

Version 1.9.3. Publication is blocked unless the exact candidate history and provider text pass a history-aware scan. `git diff --check` checks whitespace only and is never evidence that content is safe to publish.

## Operator-local deny mapping

The default mapping is `.agentic-state/publication-deny.json`. The installed `.gitignore` rule keeps it untracked. External review hosts use the same filename in their configured state directory. Version 1 JSON supports these optional fields:

```json
{
  "version": 1,
  "aliases": {"raw_estate": ["<operator-private-value>"]},
  "deny_literals": ["<operator-private-literal>"],
  "deny_regexes": [{"id": "private_locator", "pattern": "<operator-private-regex>"}],
  "internal_hostnames": ["<operator-private-hostname>"],
  "builtin_allow": [{"id": "unc_path", "pattern": "<explicit-local-exception>"}]
}
```

Aliases such as `{raw_estate}` and `{output_root}` are safe tracked forms. `publication-render --input REPORT` substitutes private mappings before saving reports or prompts. Mappings stay ignored and untracked; identity checks reject index/`HEAD` aliases, hard links, symlinks, junctions and reparse points. Tracked config cannot disable built-ins; operator-local `builtin_allow` can suppress matching findings. Regexes are limited to 256 characters and reject lookaround, backreferences and empty matches. The default subset permits at most one ranged repeat (bounds 1–64); multiple exact repeats are allowed, with no groups, alternation or open repeats. An AST-checked linear subset allows up to 16 flat branches and four terminal one-character open repeats with a lower bound at most 64; nested or suffixed repeats remain refused.

Projects may add the optional tracked `publication` object with `deny_literals`, named `deny_regexes`, and `internal_hostnames`. These only add detectors. Keep actual machine paths and network mappings in the operator-local file.

## Scan and receipt

Run:

```text
python -B .agentic/scripts/workflow.py publication-scan --base BASE --head HEAD --pr-body PR_BODY --json
```

The scan covers every strict-UTF-8 commit message; every added and deleted line relative to each parent, including roots and merges; complete head content for every touched path; and supplied PR bodies/comments. Generated reports and captured output are covered when committed or supplied. Exact, case-sensitive detector/value pairs proven in the accepted base are `PRE_EXISTING`; candidate additions are always `BLOCKING`, including duplicates of base values. Deleted or retained base values remain visible as nonblocking `PRE_EXISTING`. Bounded non-text base blobs are searched losslessly. Unknown base coverage blocks the scan while retaining known positives. Untouched base files establish membership only. Renames use old and new paths. Git checks blob size before requesting content; provider files are read and hashed in bounded chunks. Binary, invalid-UTF-8, or oversize candidate content is unscanned and blocks a pass.

Built-ins detect network-share locators, absolute drive paths, common absolute home-directory paths, private IPv4 ranges and unique-local IPv6 ranges. Local aliases/literals/regexes/hostnames and project declarations add exact detectors. Findings contain the commit, path or provider-text channel, line when available, detector id, and only a full SHA-256 match digest—never a match prefix.

Published historical exceptions are code-owned exact full-line SHA-256 identities scoped to one tracked path and detector. They exist only for reviewed synthetic test fixtures that have since moved to `EX-*` labels. A changed line, path, detector or value remains blocked; provider text never receives this exception. Operator mappings cannot add or broaden historical exceptions.

The publisher preserves the JSON receipt. Its resolved base and head and `pr_body_sha256` must match the pushed candidate and exact posted body. The receipt binds `total_findings`, `blocking_findings`, `pre_existing_findings`, and `unscanned_count` to their arrays. A `PASS` may contain only `PRE_EXISTING` findings; a blocking finding, unscanned item, malformed or inconsistent count, body change, head change, or base change invalidates it. Scan a prospective comment with `--comment FILE` before posting it.

## Unpublished rewrite

`publication-rewrite --base BASE --branch BRANCH --commits 1 --message-file FILE` creates and scans one quarantined replacement commit, verifies tree equality, and inventories its objects. Under the repository-wide AWF rewrite lock, it snapshots all refs, pseudorefs, linked-worktree HEADs, reflogs, indexes, worktrees, alternates, object format, canonical object-directory identity, every possible fanout, and objects; installs recorded objects; performs an exact compare-and-swap; and proves the final census. Every AWF writer must honor this lock. It refuses upstreams, remote branches, dirty state, unsupported commit counts, and old commits reachable from another ref. Published-history rewriting requires an owner decision.

Under the lock, the snapshot controls object and fanout provenance. New loose objects use atomic create-if-absent hard links. Before CAS, AWF inventories the replacement commit's complete closure and records bounded type, content, and recomputed-OID evidence. Primary loose snapshot members also bind no-follow storage identity; packed or alternate members use bounded replacement-disabled Git. Shallow boundaries and legacy grafts are refused because they alter reachability without changing commit bytes. After CAS, AWF recomputes the closure and requires exact inventory and evidence. Failures retain created objects/fanouts and report `*_RECOVERY_REQUIRED` with recovery commands. Snapshot-present, packed, alternate, linked, and ambiguous objects are never removed. Recovery-required never claims unchanged state.

## Remaining L9 work

This change supplies scanning and alias rendering. Later P1 work must retrofit alias rendering into report/prompt producers and add external-resource admission, lifetime, remap, and observation controls. Until then, producers call `publication-render` explicitly and the publication scan remains the final blocking control.
