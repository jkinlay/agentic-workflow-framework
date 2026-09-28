# Publication safety

Version 1.9.3. Exact candidate history and provider text must pass. `git diff --check` is whitespace-only evidence.

## Operator-local deny mapping

The untracked default is `.agentic-state/publication-deny.json`; an external review host uses its state directory. Version 1 fields are:

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

Aliases such as `{raw_estate}` are safe tracked forms; `publication-render --input REPORT` substitutes them before saving. `builtin_allow` tunes named built-ins or `all`. The scanner refuses a mapping tracked in the index or scanned head, including a force-added ignored file. Tracked content cannot disable built-ins.

Tracked `publication` declarations (`deny_literals`, named `deny_regexes`, `internal_hostnames`) only add detectors. Private mappings remain local. Regexes are limited to 512 characters; backreferences, group extensions except non-capturing groups, and repeated groups containing repetition or alternation are refused. Lines exceeding 16,384 characters are `UNSCANNED` and block.

## Scan and receipt

Run:

```text
python -B .agentic/scripts/workflow.py publication-scan --base BASE --head HEAD --pr-body PR_BODY --json
```

The scan covers all commit messages; added/deleted lines against every parent, including roots and merges; complete head content of touched paths; and supplied PR bodies/comments. Committed reports/output are therefore covered. Renames use both paths. Binary, oversize, invalid UTF-8 or overlong-line content is `UNSCANNED` and blocks.

Every occurrence is retained. `PRE_EXISTING` means the same detector/value was positively observed in the bounded base-tree search; it is counted but does not block, including deletion. Everything else is `BLOCKING`, including add-then-remove history. `PASS` means zero blocking findings and zero unscanned items.

Base membership visits sorted paths and reads duplicate blobs once: at most 100,000 blobs, 256 MiB total, 32 MiB per blob and 16,384 characters per line. Binary/invalid or per-blob bounded content marks the search incomplete but does not prevent later blobs being checked within the global bounds. Positively observed detector/value pairs remain proven; unseen values receive no exemption. Receipts record completion, reason, work and bounds. Cost is one bounded full-base read.

Built-ins detect network shares, absolute drive/home paths and private/unique-local IPs. Local and project declarations add detectors. Findings retain location, classification, detector id and SHA-256 match digest only.

The receipt's base, head and `pr_body_sha256` must match publication. Blocking findings, unscanned content or tuple/body changes invalidate it. Scan comments before posting.

## Unpublished rewrite

`publication-rewrite --base BASE --branch BRANCH --commits 1 --message-file FILE` scans one same-tree replacement before an atomic branch update. It checks every fetch/push URL, and refuses upstreams, matching remote refs, a remotely present branch, dirty state, unsupported counts, or old commits reachable from heads, tags or remote refs. Lookups fail closed. Published-history rewriting is out of scope.

The isolated replacement object uses same-directory temp/rename. Failed ref updates remove it; ambiguous updates reconcile old/new tips and roll back by compare-and-swap. `ROLLBACK_FAILED` reports the recovery command. Tree, count, scan, URL absence and reachability are proved before the sole mutation.

A successful rewrite does not erase reflogs or unreachable objects. When a value must also leave the local object store, an operator applies an approved retention policy to expire the relevant reflogs and prune unreachable objects after preserving required recovery evidence.

## Remaining L9 work

This change supplies scanning and alias rendering. The later P1 half must retrofit alias rendering into every existing report/prompt producer and add external-resource admission, lifetime, remap, and bounded-observation controls. Until then, producers call `publication-render` explicitly and the publication scan remains the final blocking control.
