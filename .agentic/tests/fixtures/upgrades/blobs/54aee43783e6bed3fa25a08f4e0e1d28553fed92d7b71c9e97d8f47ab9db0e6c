# Private deny scan receipts

The private deny gate is independent of publication scanning. Its receipt
schema is `schemas/private-deny-scan.schema.json`; a one-candidate baseline
authorization uses `schemas/private-deny-baseline-authorization.schema.json`.
The matcher and mapping may remain operator-private. Receipts must never contain
matched text or regex source.

## Accepted results

`NO_MATCHES` is a normal `PASS` only when base and head contain no matches and
history, added lines, deleted lines, provider body and unscanned counts are all
zero.

`BASE_PREEXISTING_UNCHANGED` is a terminal `PASS` only under
`exact-current-tree-baseline-v1` and a separately pinned human authorization.
The authorization and scan receipt must bind the same complete candidate tuple,
exact provider-body SHA-256, operator mapping SHA-256 and pinned scanner
SHA-256. The trusted controller supplies the raw receipt, expected mapping and
scanner digests and separately trusted
authorization digest to the five-slot validator. A worker-supplied digest or
authorization claim is not proof of human authority.

The scanner must completely cover commit messages, every commit's added and
deleted lines (including each merge-parent transition), both full trees and the
exact provider body. History, diff and body counts must be zero. Each matched
path must exist in base and head with an identical exact matched-value
multiset, preserving duplicate counts. A new, moved, deleted, added, removed,
count-changed or value-changed match blocks. So do unreadable, unsupported,
oversize, ambiguous or unscanned inputs, timeouts, malformed receipts, and any
tuple, body, mapping or authorization mismatch. Binary blobs are scanned in
replacement-decoded UTF-8 and one-byte-per-character views; null-heavy blobs are
also checked as UTF-16 in both byte orders. Matches are scoped by view so a hit
seen in only one interpretation remains visible in the path multiset. Unsupported
decodings and scan failures leave the blob unscanned and blocking.

Receipts expose only tuple and digest bindings, scan-channel counts, matched
paths, per-path multiplicities, scanner digest, and completeness status. They
contain no matched values. The validator requires a closed schema, sorted unique
safe paths, exact count sums, all clear non-tree channels and a false
`execution_authority`. It does not implement the private matcher or authenticate
the human; the trusted controller must pin authorization from the human
authorization channel. This is a one-candidate disposition, never a reusable
`allow_preexisting` switch.

Publication-scanner `PRE_EXISTING` semantics are unrelated and cannot authorize
this gate. Conversely, this receipt cannot waive publication findings. Any
candidate, body or mapping change requires fresh authorization and scans.
