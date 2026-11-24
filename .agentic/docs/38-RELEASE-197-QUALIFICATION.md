# 1.9.7 release and upgrade qualification

AWF-310, Epic AWF-306. Writer contract: Tier 2, FULL. This is a qualification
plan, not release acceptance. No real adopter upgrade, adapter activation,
publication or tagging is authorized by this document.

## Prepared routes

One owner-reviewed change set upgrades 1.9.5 through 1.9.6 to 1.9.7; 1.9.6
upgrades directly. Authenticate historical membership, bytes, receipts and
provenance; unknown or tampered installations refuse before mutation.

The active registry remains aligned with the current VERSION. The separate
`../upgrade/known-versions-1.9.7.json` is prepared for the release version-bump
step. Its 1.9.6 identity is computed from local tag v1.9.6 and its raw commit,
using the release materializer/verifier and historical managed-manifest digest
tooling. Local tag verification establishes neither independent release trust
nor publication. Recompute/check that registry on the release host before
promoting its bytes to the active registry with VERSION 1.9.7.

Crossing 1.9.6 creates the budget compatibility disposition once, retaining its
1.9.6 milestone identity. A 1.9.6 baseline preserves existing dispositions and
creates none. Operating settings and accounting history remain owner-controlled.

## Final release checklist: all pending

- Merge AWF-307 O1b/O2, Sprint Mode and AWF-259; freeze the actual release tree.
- Promote the prepared registry during the version bump; regenerate and check
  contracts, examples and manifests. Authenticate both baseline tags again.
- Run `qualify_upgrade.py installed` from the source-only release scripts with
  `--baseline-version 1.9.5` and `--baseline-version 1.9.6`, separate new external
  work directories, verified local wheels and external reports. Require
  QUALIFIED_DISPOSABLE_UPGRADE, CONFIGURED baseline/upgrade receipts, zero exits,
  integrity true, ACCEPTED configuration and all preservation checks PASS.
- Run the installed self-test after each upgrade, with both AWF-307 opt-outs
  unset and set. Verify unset defaults are null, Sprint Mode remains OFF when
  absent, and the shipped immutable PROJECT_CONFIG default is installed.
- Account explicitly for absent versus explicit-null policy keys: they hash
  differently. Preserve adopter configuration; derive and review new policy
  observations rather than reusing a prior accepted-policy digest.
- Repeat regressions on the final candidate; require hosted CI green on that
  exact head, current-head independent review and owner acceptance. Development
  checks against constructed temporary targets satisfy none of these release gates.

## Retained limits and successors

The [1.9.6 qualification limits](38-RELEASE-196-QUALIFICATION.md) still apply:
quiescence, exclusive custody, retained recovery evidence and no recursive
runtime deletion. AWF-258 object-bound closeout remains a 1.9.8 successor.
The 1.9.6 migration and reporting guides already name 1.9.8 for their deferred
work; historical release evidence remains unchanged.
