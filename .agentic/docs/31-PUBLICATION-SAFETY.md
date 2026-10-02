# Publication safety

Version 1.9.3. Publication is blocked unless the exact candidate history and provider text pass a history-aware scan. `git diff --check` checks whitespace only and is never evidence that content is safe to publish.

## Operator-local deny mapping

The default mapping is `.agentic-state/publication-deny.json`. The installed `.gitignore` rule keeps it untracked. For the external review host, the same filename lives in its configured external state directory. Use version 1 JSON with these optional fields:

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

Aliases such as `{raw_estate}` and `{output_root}` are safe tracked forms. `publication-render --input REPORT` replaces mapped private values with their aliases before a report or prompt is saved. A repository-relative mapping is accepted only when Git proves it is both ignored and untracked; a force-tracked mapping is rejected. Lexical/resolved identities are checked against the index and `HEAD`, including Windows case, hard-link, symlink, junction, and reparse aliases. Local `builtin_allow` entries can tune false positives by detector id (`unc_path`, `windows_absolute`, `home_path`, `private_ipv4`, `private_ipv6`) or `all`; tracked configuration cannot disable built-ins. Named regular expressions use a bounded subset: at most 256 characters; no grouping, alternation, optional/open repetition, lookaround, backreferences, or empty matches; at most one ranged repeat; and positive repeat bounds may not exceed 64.

Projects may add the optional tracked `publication` object with `deny_literals`, named `deny_regexes`, and `internal_hostnames`. These only add detectors. Keep actual machine paths and network mappings in the operator-local file.

## Scan and receipt

Run:

```text
python -B .agentic/scripts/workflow.py publication-scan --base BASE --head HEAD --pr-body PR_BODY --json
```

The scan covers every strict-UTF-8 commit message; every added and deleted line in each commit relative to each parent, including parentless roots and merges; the complete head content of every path touched anywhere in the range; and supplied PR bodies/comments. Thus generated reports and captured command output are covered when committed or supplied as provider text. Exact, case-sensitive detector/value pairs proven in the accepted base are reported as `PRE_EXISTING`; candidate additions are always `BLOCKING`, including additions that duplicate a base value. A deleted or retained exact base value remains visible as nonblocking `PRE_EXISTING`. Bounded non-text base blobs are searched losslessly for exact membership. If any base content cannot be classified, every known exact positive is retained and the unknown coverage blocks the scan. Untouched pre-existing files are inspected only to establish classification membership. Renames use both old and new paths. Git blob size is checked before content is requested; provider files are read and hashed in bounded chunks. Binary, invalid-UTF-8, or oversize candidate content is reported as unscanned and blocks a pass.

Built-ins detect network-share locators, absolute drive paths, common absolute home-directory paths, private IPv4 ranges and unique-local IPv6 ranges. Local aliases/literals/regexes/hostnames and project declarations add exact detectors. Findings contain the commit, path or provider-text channel, line when available, detector id, and only a full SHA-256 match digest—never a match prefix.

The publisher preserves the JSON receipt. Its resolved base and head and `pr_body_sha256` must match the pushed candidate and exact posted body. The receipt binds `total_findings`, `blocking_findings`, `pre_existing_findings`, and `unscanned_count` to their arrays. A `PASS` may contain only `PRE_EXISTING` findings; a blocking finding, unscanned item, malformed or inconsistent count, body change, head change, or base change invalidates it. Scan a prospective comment with `--comment FILE` before posting it.

## Unpublished rewrite

`publication-rewrite --base BASE --branch BRANCH --commits 1 --message-file FILE` creates and scans one quarantined replacement commit, verifies tree equality, and inventories its objects. Under the repository-wide AWF rewrite lock, it snapshots all refs, pseudorefs, linked-worktree HEADs, reflogs, indexes, worktrees, alternates, object format, canonical object-directory identity, and objects; installs recorded objects; performs an exact compare-and-swap; and proves the final census. Every AWF writer must honor this lock. It refuses upstreams, remote branches, dirty state, unsupported commit counts, and old commits reachable from another ref. Published-history rewriting requires an owner decision.

After an installation or failed CAS, cleanup is limited to a recorded object that was absent from the initial inventory, remains an unclaimed canonical primary loose object, and has no packed, alternate, linked-worktree, pseudoref, ref, or reflog ambiguity. Exact snapshot restoration reports `PRE_CAS_FAILED_RECOVERED` or `CAS_FAILED_RECOVERED`; incomplete proof retains every uncertain object and reports the corresponding `*_RECOVERY_REQUIRED` code, exact IDs, and non-destructive inspection commands. After a successful CAS, any failed full proof triggers an exact-CAS rollback. Exact restoration reports `POST_CAS_PROOF_FAILED_RECOVERED`; rollback or recovery failure reports `POST_CAS_RECOVERY_REQUIRED`. Recovery-required results never claim unchanged state.

## Remaining L9 work

This change supplies scanning and alias rendering. The later P1 half must retrofit alias rendering into every existing report/prompt producer and add external-resource admission, lifetime, remap, and bounded-observation controls. Until then, producers call `publication-render` explicitly and the publication scan remains the final blocking control.
