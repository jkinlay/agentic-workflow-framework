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

Aliases such as `{raw_estate}` and `{output_root}` are safe tracked forms. `publication-render --input REPORT` replaces mapped private values with their aliases before a report or prompt is saved. Local `builtin_allow` entries can tune false positives by detector id (`unc_path`, `windows_absolute`, `home_path`, `private_ipv4`, `private_ipv6`) or `all`; tracked configuration cannot disable built-ins.

Projects may add the optional tracked `publication` object with `deny_literals`, named `deny_regexes`, and `internal_hostnames`. These only add detectors. Keep actual machine paths and network mappings in the operator-local file.

## Scan and receipt

Run:

```text
python -B .agentic/scripts/workflow.py publication-scan --base BASE --head HEAD --pr-body PR_BODY --json
```

The scan covers every commit message; every added and deleted line in each commit relative to each parent, including merges; current content of paths changed across the range; and supplied PR bodies/comments. Thus generated reports and captured command output are covered when committed or supplied as provider text. Context lines and untouched pre-existing files are not scanned. Renames use both old and new paths. Binary or oversize blobs are reported as unscanned and block a pass.

Built-ins detect network-share locators, absolute drive paths, common absolute home-directory paths, private IPv4 ranges and unique-local IPv6 ranges. Local aliases/literals/regexes/hostnames and project declarations add exact detectors. Findings contain the commit, path or provider-text channel, line when available, detector id, and only a redacted fingerprint.

The publisher preserves the JSON receipt. Its resolved base and head and `pr_body_sha256` must match the pushed candidate and exact posted body. Any finding, unscanned content, body change, head change, or base change invalidates it. Scan a prospective comment with `--comment FILE` before posting it.

## Unpublished rewrite

`publication-rewrite --base BASE --branch BRANCH --commits 1 --message-file FILE` creates one replacement commit from the same final tree with `commit-tree`, scans it, checks tree equality with `agentic.gittree`, and atomically updates the branch only after all checks pass. It refuses upstreams, matching remote-tracking refs, a branch present on any configured remote, dirty state, unsupported commit counts, and old commits reachable from another local branch or tag. Rewriting published history is an owner decision and is out of scope.

A successful rewrite does not erase reflogs or unreachable objects. When a value must also leave the local object store, an operator applies an approved retention policy to expire the relevant reflogs and prune unreachable objects after preserving required recovery evidence.

## Remaining L9 work

This change supplies scanning and alias rendering. The later P1 half must retrofit alias rendering into every existing report/prompt producer and add external-resource admission, lifetime, remap, and bounded-observation controls. Until then, producers call `publication-render` explicitly and the publication scan remains the final blocking control.
